"""
文生视频LangGraph工作流
使用LangGraph编排角色提取和分镜头脚本生成的业务流程
"""
import json
import re
import os
import sys
import asyncio
import logging
from typing import TypedDict, Annotated, List, Dict, Any, Optional


# 自定义 reducer 函数：用于合并 characters_to_review 的并发更新
def merge_character_queue(left: List[Dict[str, str]], right: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    合并角色审核队列的并发更新
    
    规则：
    - 如果 right 是空列表或 None，返回 left（保留原值）
    - 如果 right 是非空列表，返回 right（使用新值）
    - 这样可以确保 route_review 节点的更新生效，而 generate_shots 节点的不更新不影响
    """
    if not right:  # right 是空列表或 None
        return left
    return right


# 自定义 reducer 函数：用于合并 current_character 的并发更新
def merge_current_character(left: Optional[Dict[str, str]], right: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """
    合并当前审核角色的并发更新
    
    规则：
    - 如果 right 是 None，返回 left（保留原值）
    - 如果 right 不是 None，返回 right（使用新值）
    - 这样可以确保 route_review 和其他节点的更新生效，而不更新的节点不影响
    """
    if right is None:
        return left
    return right
from datetime import datetime
from pathlib import Path

# LangGraph相关导入
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Interrupt
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from langchain_core.messages import HumanMessage, AIMessage

# 本地导入
sys.path.append(str(Path(__file__).resolve().parents[3]))
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(env_path)

from app.agent.generatePic.llm_client import HelloAgentsLLM
from app.agent.generateVideo.seed_manager import (
    generate_global_seed,
    derive_character_seed,
    derive_scene_seed,
    derive_grid_seed,
    derive_first_frame_seed,
    derive_video_seed,
)

# 日志配置
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logger.addHandler(handler)


# ============================================================================
# 状态定义
# ============================================================================
class VideoGenerationState(TypedDict):
    """文生视频工作流状态"""
    # 输入
    script_id: str
    script_name: str
    script_content: str
    user_id: str
    
    # 角色信息
    characters: List[Dict[str, str]]
    characters_generated: bool
    characters_confirmed: bool
    
    # HITL相关 - 角色审核队列
    # 使用 Annotated 和自定义 reducer 来处理并发更新
    # 当多个节点并发更新时，使用 merge_character_queue 函数合并
    characters_to_review: Annotated[List[Dict[str, str]], merge_character_queue]  # 待审核的角色队列
    # 使用 Annotated 和自定义 reducer 来处理并发更新
    # 当多个节点并发更新时，使用 merge_current_character 函数合并
    current_character: Annotated[Optional[Dict[str, str]], merge_current_character]  # 当前正在审核的角色
    
    # 角色四视图
    character_three_views: List[Dict[str, str]]  # [{"role_name": "xxx", "three_view_prompt": "xxx", "three_view_image_path": "xxx"}]
    three_views_generated: bool
    
    # 分镜头脚本
    shot_scripts: List[Dict[str, Any]]
    shots_generated: bool
    shots_confirmed: bool
    
    # 场景背景图
    scene_backgrounds: List[Dict[str, Any]]  # [{"scene_group": 1, "scene_name": "xxx", "background_image_path": "xxx"}]
    backgrounds_generated: bool
    
    # 全局统一seed：用于人物/场景一致性，避免漂移
    global_seed: int
    
    # 错误信息
    error_message: Optional[str]
    
    # 元数据
    current_stage: str
    created_at: str
    updated_at: str


# ============================================================================
# Agent节点定义
# ============================================================================
from app.agent.generateVideo.character_extraction_agent import CharacterExtractionAgent
from app.agent.generateVideo.character_three_view_agent import CharacterThreeViewAgent

import requests
import time
import base64
import dashscope
from dashscope import MultiModalConversation

from app.agent.generateVideo.grid_image_agent import GridImageAgent

from app.agent.generateVideo.first_and_last_frame_agent import FirstAndLastFrameAgent

from app.agent.generateVideo.video_generation_agent import VideoGenerationAgent

from app.agent.generateVideo.scene_background_agent import SceneBackgroundAgent

from app.agent.generateVideo.shot_script_generation_agent import ShotScriptGenerationAgent

# ============================================================================
# HITL节点定义
# ============================================================================

def route_character_review_node(state: VideoGenerationState) -> dict:
    """
    节点：处理角色审核队列逻辑
    - 如果队列不为空：取出第一个角色，设置为 current_character
    - 如果队列为空：将 current_character 设置为 None
    返回状态更新字典。
    """
    characters_to_review = state.get("characters_to_review", [])
    
    if characters_to_review:
        # 取出第一个角色
        current_character = characters_to_review[0]
        # 剩余队列
        remaining_characters = characters_to_review[1:]
        
        logger.info(f"准备审核角色: {current_character.get('role_name', '未知')}")
        
        return {
            "current_character": current_character,
            "characters_to_review": remaining_characters
        }
    else:
        logger.info("所有角色已审核完毕，退出HITL循环")
        # 清空当前角色，确保路由走向 end_review
        return {"current_character": None}


def check_review_queue_router(state: VideoGenerationState) -> str:
    """
    路由函数：根据状态决定下一步
    - 如果 current_character 存在：进入 interrupt_for_review
    - 否则：结束循环 (END)
    """
    if state.get("current_character"):
        return "interrupt_for_review"
    else:
        return "end_review"


def character_review_interrupt(state: VideoGenerationState) -> dict:
    """
    HITL中断节点：使用 LangGraph 官方 interrupt API 暂停图执行，等待用户审核当前角色
    
    使用 interrupt() 函数会：
    1. 抛出 GraphInterrupt 异常，暂停图的执行
    2. 将当前状态保存到 checkpointer
    3. 等待用户通过 Command(resume=...) 恢复执行
    
    interrupt() 的参数会作为中断信息返回给调用者
    """
    current_character = state.get("current_character")
    if current_character:
        logger.info(f"HITL中断：等待用户审核角色 {current_character.get('role_name', '未知')}")
        
        # 使用 LangGraph 官方 interrupt API
        # interrupt 会暂停执行并返回给调用者
        # 用户审核后，通过 Command(resume=审核后的角色信息) 恢复
        user_reviewed_character = interrupt({
            "type": "character_review",
            "character": current_character,
            "message": f"请审核角色: {current_character.get('role_name', '未知')}"
        })
        
        # 用户恢复执行后，user_reviewed_character 包含审核后的角色信息
        logger.info(f"用户审核完成，角色: {user_reviewed_character.get('role_name', '未知')}")
        
        # 返回更新后的角色信息
        return {"current_character": user_reviewed_character}
    
    return {}


def process_reviewed_character(state: VideoGenerationState) -> dict:
    """
    处理用户审核后的角色：将当前角色更新到 characters 列表中
    """
    current_character = state.get("current_character")
    if current_character:
        logger.info(f"用户已审核角色: {current_character.get('role_name', '未知')}")
        characters = state.get("characters", [])
        # 创建新列表以避免原地修改
        new_characters = []
        updated = False
        for char in characters:
            if char.get("role_name") == current_character.get("role_name"):
                new_characters.append(current_character)
                updated = True
            else:
                new_characters.append(char)
        
        if updated:
            return {"characters": new_characters}
            
    return {}


# ============================================================================
# 工作流构建
# ============================================================================

def build_video_generation_graph(
    llm_client: HelloAgentsLLM,
    checkpointer=None,
    save_characters_callback=None,
    save_shots_callback=None,
    redis_update_callback=None,
    script_id: str = None
):
    """
    构建文生视频工作流图

    参数:
    - llm_client: LLM客户端
    - checkpointer: AsyncPostgresSaver实例，用于存储图的执行状态（短期记忆）
    - save_characters_callback: 角色信息保存到数据库的回调函数
    - save_shots_callback: 分镜脚本保存到数据库的回调函数
    - redis_update_callback: Redis状态更新回调函数
    - script_id: 剧本ID，用于创建按剧本区分的图片保存目录

    工作流设计:
    1. character_agent和shot_agent并行执行（通过条件边实现）
    2. character_agent完成后进入HITL循环，逐个审核角色
    3. 每个角色审核后生成四视图
    4. 所有角色审核完毕后，汇合到END
    """

    # 创建Agent节点
    character_agent = CharacterExtractionAgent(llm_client, save_characters_callback, redis_update_callback)
    three_view_agent = CharacterThreeViewAgent(llm_client, script_id)
    shot_agent = ShotScriptGenerationAgent(llm_client, save_shots_callback, redis_update_callback)
    
    # 创建状态图
    workflow = StateGraph(VideoGenerationState)
    
    # 添加节点
    workflow.add_node("extract_characters", character_agent)
    workflow.add_node("route_review", route_character_review_node) # 修改为新的节点函数
    workflow.add_node("interrupt_for_review", character_review_interrupt)
    workflow.add_node("process_reviewed", process_reviewed_character)
    workflow.add_node("generate_three_view", three_view_agent)
    workflow.add_node("generate_shots", shot_agent)
    
    # 设置入口点
    workflow.set_entry_point("extract_characters")
    
    # 并行执行设计：
    # 从extract_characters同时触发route_review和generate_shots
    def parallel_router(state: VideoGenerationState) -> List[str]:
        """并行路由：同时触发HITL循环和分镜头脚本生成"""
        return ["route_review", "generate_shots"]
    
    workflow.add_conditional_edges(
        "extract_characters",
        parallel_router,
        ["route_review", "generate_shots"]
    )
    
    # HITL循环
    # 修改：使用新的路由函数
    workflow.add_conditional_edges(
        "route_review",
        check_review_queue_router,
        {
            "interrupt_for_review": "interrupt_for_review",
            "end_review": END
        }
    )
    
    # 用户审核后恢复执行
    workflow.add_edge("interrupt_for_review", "process_reviewed")
    workflow.add_edge("process_reviewed", "generate_three_view")
    
    # 生成四视图后回到路由检查
    workflow.add_edge("generate_three_view", "route_review")
    
    # 分镜头脚本生成后直接结束
    workflow.add_edge("generate_shots", END)
    
    # 编译图
    if checkpointer:
        return workflow.compile(checkpointer=checkpointer)
    else:
        return workflow.compile()


async def run_video_generation_workflow(
    script_id: str,
    script_name: str,
    script_content: str,
    user_id: str,
    db_uri: Optional[str] = None,
    save_characters_callback=None,
    save_shots_callback=None,
    redis_update_callback=None,
    user_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    运行文生视频工作流
    
    参数:
    - script_id: 剧本ID
    - script_name: 剧本名称
    - script_content: 剧本内容
    - user_id: 用户ID
    - db_uri: 数据库连接字符串（用于checkpointer）
    - save_characters_callback: 角色信息保存到数据库的回调函数
    - save_shots_callback: 分镜脚本保存到数据库的回调函数
    - redis_update_callback: Redis状态更新回调函数
    - user_seed: 用户指定的seed（可选，用于覆盖自动生成的seed）
    
    返回:
    - 工作流执行结果
    
    注意：
    - 当工作流遇到 interrupt() 时，会抛出 GraphInterrupt 异常
    - 此时工作流暂停，状态保存到 checkpointer
    - 需要通过 resume_workflow() 函数恢复执行
    """
    # 初始化LLM客户端
    llm_client = HelloAgentsLLM()
    
    # 生成全局统一seed，实现人物/场景一致性
    global_seed = generate_global_seed(script_id, user_seed)
    logger.info(f"全局统一seed已生成: {global_seed} (script_id={script_id}, user_seed={user_seed})")
    
    # 初始化checkpointer（如果提供了db_uri）
    checkpointer = None
    pool = None
    
    if db_uri:
        try:
            # 创建数据库连接池
            pool = AsyncConnectionPool(
                conninfo=db_uri,
                min_size=5,
                max_size=10,
                kwargs={"autocommit": True, "prepare_threshold": 0}
            )
            await pool.open()
            
            # 创建checkpointer
            checkpointer = AsyncPostgresSaver(pool)
            await checkpointer.setup()
            
            logger.info("Checkpointer初始化成功")
        except Exception as e:
            logger.error(f"Checkpointer初始化失败: {str(e)}")
            # 如果checkpointer初始化失败，继续执行但不使用checkpointer
    
    try:
        # 构建工作流图
        app = build_video_generation_graph(
            llm_client,
            checkpointer,
            save_characters_callback,
            save_shots_callback,
            redis_update_callback,
            script_id  # 传递 script_id 用于创建按剧本区分的目录
        )
        
        # 初始化状态
        initial_state: VideoGenerationState = {
            "script_id": script_id,
            "script_name": script_name,
            "script_content": script_content,
            "user_id": user_id,
            "characters": [],
            "characters_generated": False,
            "characters_confirmed": False,
            "characters_to_review": [],
            "current_character": None,
            "character_three_views": [],
            "three_views_generated": False,
            "shot_scripts": [],
            "shots_generated": False,
            "shots_confirmed": False,
            "global_seed": global_seed,
            "error_message": None,
            "current_stage": "initialized",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        # 运行工作流
        # 注意：如果使用checkpointer，需要提供thread_id
        config = {"configurable": {"thread_id": script_id}}
        
        # 使用 ainvoke 运行工作流
        # 当遇到 interrupt() 时，会抛出 GraphInterrupt 异常
        result = await app.ainvoke(initial_state, config=config)
        
        # 检查结果是否包含 Interrupt 对象或其他不可序列化的对象
        # 如果结果中包含 Interrupt，说明工作流被中断了
        def has_interrupt(obj):
            """递归检查对象是否包含 Interrupt"""
            if isinstance(obj, Interrupt):
                return True
            if isinstance(obj, dict):
                return any(has_interrupt(v) for v in obj.values())
            if isinstance(obj, (list, tuple)):
                return any(has_interrupt(item) for item in obj)
            return False
        
        if has_interrupt(result):
            logger.info(f"工作流遇到HITL中断，等待用户审核，剧本: {script_name}")
            return {
                "interrupted": True,
                "script_id": script_id,
                "message": "工作流已暂停，等待用户审核角色信息",
                "characters": result.get("characters", []) if isinstance(result, dict) else [],
            }
        
        logger.info(f"工作流执行完成，剧本: {script_name}")
        return result
        
    except Interrupt as e:
        # 捕获 LangGraph 的 Interrupt 异常（HITL中断）
        logger.info(f"工作流遇到HITL中断，等待用户审核，剧本: {script_name}")
        # 返回中断信息，而不是错误
        # 前端应该根据这个信息提示用户进行审核
        return {
            "interrupted": True,
            "script_id": script_id,
            "message": "工作流已暂停，等待用户审核角色信息",
            "characters": [],  # 这里应该从状态中获取，但简化处理
        }
    
    except Exception as e:
        logger.error(f"工作流执行失败: {str(e)}")
        return {
            "error_message": str(e),
            "script_id": script_id,
        }
    
    finally:
        # 清理资源
        if pool:
            await pool.close()
            logger.info("数据库连接池已关闭")


async def resume_workflow_and_generate_three_view(
    script_id: str,
    character: dict,
    user_id: str,
    db_uri: Optional[str] = None,
    force_regenerate: bool = False,
    user_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    恢复工作流并生成角色四视图
    
    参数:
    - script_id: 剧本ID
    - character: 用户审核后的角色信息
    - user_id: 用户ID
    - db_uri: 数据库连接字符串
    - force_regenerate: 是否强制重新生成四视图（即使已存在）
    - user_seed: 用户指定的seed（可选，用于覆盖自动生成的seed）
    
    返回:
    - 工作流恢复执行结果
    """
    # 初始化LLM客户端
    llm_client = HelloAgentsLLM()
    
    # 生成全局统一seed（恢复时使用相同seed保证一致性）
    global_seed = generate_global_seed(script_id, user_seed)
    logger.info(f"恢复工作流，全局统一seed: {global_seed} (script_id={script_id})")
    
    # 初始化checkpointer
    checkpointer = None
    pool = None
    
    if db_uri:
        try:
            # 创建数据库连接池
            pool = AsyncConnectionPool(
                conninfo=db_uri,
                min_size=5,
                max_size=10,
                kwargs={"autocommit": True, "prepare_threshold": 0}
            )
            await pool.open()
            
            # 创建checkpointer
            checkpointer = AsyncPostgresSaver(pool)
            await checkpointer.setup()
            
            logger.info("Checkpointer初始化成功")
        except Exception as e:
            logger.error(f"Checkpointer初始化失败: {str(e)}")
    
    try:
        # 构建工作流图
        app = build_video_generation_graph(
            llm_client,
            checkpointer,
            script_id=script_id  # 传递 script_id 用于创建按剧本区分的目录
        )
        
        # 配置
        config = {"configurable": {"thread_id": script_id}}
        
        # 如果需要强制重新生成，在character中添加标记
        if force_regenerate:
            character_with_flag = {**character, "force_regenerate": True}
        else:
            character_with_flag = character
        
        # 使用 Command 恢复工作流
        # Command(resume=character) 会将 character 作为 interrupt() 的返回值
        from langgraph.types import Command
        
        result = await app.ainvoke(
            Command(resume=character_with_flag),
            config=config
        )
        
        # 检查结果是否包含 Interrupt 对象或其他不可序列化的对象
        def has_interrupt(obj):
            """递归检查对象是否包含 Interrupt"""
            if isinstance(obj, Interrupt):
                return True
            if isinstance(obj, dict):
                return any(has_interrupt(v) for v in obj.values())
            if isinstance(obj, (list, tuple)):
                return any(has_interrupt(item) for item in obj)
            return False
        
        if has_interrupt(result):
            logger.info(f"工作流恢复执行遇到HITL中断，等待用户审核，剧本: {script_name}")
            return {
                "interrupted": True,
                "script_id": script_id,
                "message": "工作流已暂停，等待用户审核角色信息",
            }
        
        logger.info(f"工作流恢复执行完成，剧本: {script_name}")
        
        # 提取四视图图片路径
        character_three_views = result.get("character_three_views", [])
        three_view_image_path = None
        for view in character_three_views:
            if view.get("role_name") == character.get("role_name"):
                three_view_image_path = view.get("three_view_image_path")
                break
        
        return {
            "success": True,
            "script_id": script_id,
            "character": character,
            "three_view_image_path": three_view_image_path,
        }
        
    except Interrupt as e:
        # 捕获 LangGraph 的 Interrupt 异常（HITL中断）
        logger.info(f"工作流恢复执行遇到HITL中断，等待用户审核，剧本: {script_name}")
        return {
            "interrupted": True,
            "script_id": script_id,
            "message": "工作流已暂停，等待用户审核角色信息",
        }
    
    except Exception as e:
        logger.error(f"工作流恢复执行失败: {str(e)}")
        return {
            "error_message": str(e),
            "script_id": script_id,
        }
    
    finally:
        # 清理资源
        if pool:
            await pool.close()
            logger.info("数据库连接池已关闭")