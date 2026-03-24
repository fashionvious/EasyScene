"""
文生视频LangGraph工作流
使用LangGraph编排角色提取和分镜头脚本生成的业务流程
"""
import os
import sys
import asyncio
import logging
from typing import TypedDict, Annotated, List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path

# LangGraph相关导入
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from langchain_core.messages import HumanMessage, AIMessage

# 本地导入
sys.path.append(str(Path(__file__).resolve().parents[3]))
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(env_path)

from app.agent.generatePic.llm_client import HelloAgentsLLM

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
    
    # 分镜头脚本
    shot_scripts: List[Dict[str, Any]]
    shots_generated: bool
    shots_confirmed: bool
    
    # 错误信息
    error_message: Optional[str]
    
    # 元数据
    current_stage: str
    created_at: str
    updated_at: str


# ============================================================================
# Agent节点定义
# ============================================================================

class CharacterExtractionAgent:
    """角色提取Agent节点"""
    
    def __init__(self, llm_client: HelloAgentsLLM):
        self.llm_client = llm_client
    
    def __call__(self, state: VideoGenerationState) -> VideoGenerationState:
        """执行角色提取"""
        logger.info(f"开始提取角色信息，剧本ID: {state['script_id']}")
        
        try:
            # 构建提示词
            prompt = f"""根据用户上传的剧本文档{state['script_content']}，提取出有关主要人物的姓名、角色基础描述（一旦确定不会更改，如长相、性格）。如果剧本文档中信息不足，你有充分的发挥空间可以自行设计，但是不要脱离内容，要适合内容。回答格式要求如下(0<n<10)，角色1：名字{{xxx}}，角色基础描述{{xxx}}；角色2：名字{{xxx}}，角色基础描述{{xxx}}；……角色n：名字{{xxx}}，角色基础描述{{xxx}}。"""
            
            messages = [{"role": "user", "content": prompt}]
            
            # 调用LLM
            response = self.llm_client.think(messages=messages)
            
            if not response:
                state["error_message"] = "角色提取失败：LLM返回空结果"
                return state
            
            # 解析角色信息
            characters = self._parse_characters(response)
            
            state["characters"] = characters
            state["characters_generated"] = True
            state["updated_at"] = datetime.utcnow().isoformat()
            
            logger.info(f"角色提取完成，共提取{len(characters)}个角色")
            
        except Exception as e:
            logger.error(f"角色提取失败: {str(e)}")
            state["error_message"] = f"角色提取失败: {str(e)}"
        
        return state
    
    def _parse_characters(self, response: str) -> List[Dict[str, str]]:
        """解析LLM返回的角色信息"""
        characters = []
        
        # 简单的解析逻辑：按"角色"分割
        lines = response.split("角色")
        
        for line in lines[1:]:  # 跳过第一个空元素
            try:
                # 提取角色编号
                if "：" in line or ":" in line:
                    parts = line.split("：", 1) if "：" in line else line.split(":", 1)
                    if len(parts) >= 2:
                        content = parts[1]
                        
                        # 提取名字
                        name = ""
                        if "名字" in content:
                            name_start = content.find("名字")
                            name_end = content.find("，", name_start)
                            if name_end == -1:
                                name_end = content.find("；", name_start)
                            if name_end == -1:
                                name_end = len(content)
                            name = content[name_start+2:name_end].strip()
                            # 去除花括号
                            name = name.replace("{", "").replace("}", "").strip()
                        
                        # 提取描述
                        desc = ""
                        if "角色基础描述" in content or "描述" in content:
                            desc_keyword = "角色基础描述" if "角色基础描述" in content else "描述"
                            desc_start = content.find(desc_keyword)
                            desc_end = content.find("；", desc_start)
                            if desc_end == -1:
                                desc_end = len(content)
                            desc = content[desc_start+len(desc_keyword):desc_end].strip()
                            # 去除花括号
                            desc = desc.replace("{", "").replace("}", "").strip()
                        
                        if name:
                            characters.append({
                                "role_name": name,
                                "role_desc": desc or "暂无描述"
                            })
            except Exception as e:
                logger.warning(f"解析角色信息失败: {str(e)}")
                continue
        
        # 如果解析失败，返回一个默认角色
        if not characters:
            characters.append({
                "role_name": "主角",
                "role_desc": "主要角色"
            })
        
        return characters


class ShotScriptGenerationAgent:
    """分镜头脚本生成Agent节点"""
    
    def __init__(self, llm_client: HelloAgentsLLM):
        self.llm_client = llm_client
    
    def __call__(self, state: VideoGenerationState) -> VideoGenerationState:
        """生成分镜头脚本"""
        logger.info(f"开始生成分镜头脚本，剧本ID: {state['script_id']}")
        
        try:
            # 构建提示词
            prompt = f"""按照剧本{state['script_content']}描述每一个分镜头画面（如果剧本中的时间、场景、角色妆造没有变，请不要自行更改）生成分镜脚本，脚本中需要包含对布景和人物妆造的描述（不含长相），包括提升画面质量的提示词描述,描述事件尽量直观客观,避免华丽的辞藻，以便于后续用于AI生成分镜头九宫格图片,同时具备脚本的基本要素且每个脚本如果生成视频不可以超过15s，即事件属性包含要素不得过多。因为我生成图片是单独生成的，所以尽管各个分镜可能有重复的内容，你也不可以省略，分辨率统一使用2k。要求回答格式为：分镜1： 时间（粗略描述），室内/室外，场景，角色妆造分镜头，画面描述xxx，分镜2：时间（粗略描述），室内/室外，场景，角色妆造分镜头，画面描述xxx分镜n：时间（粗略描述），室内/室外，场景，角色妆造分镜头，画面描述xxx。"""
            
            messages = [{"role": "user", "content": prompt}]
            
            # 调用LLM
            response = self.llm_client.think(messages=messages)
            
            if not response:
                state["error_message"] = "分镜头脚本生成失败：LLM返回空结果"
                return state
            
            # 解析分镜头脚本
            shot_scripts = self._parse_shot_scripts(response)
            
            state["shot_scripts"] = shot_scripts
            state["shots_generated"] = True
            state["updated_at"] = datetime.utcnow().isoformat()
            
            logger.info(f"分镜头脚本生成完成，共生成{len(shot_scripts)}个分镜")
            
        except Exception as e:
            logger.error(f"分镜头脚本生成失败: {str(e)}")
            state["error_message"] = f"分镜头脚本生成失败: {str(e)}"
        
        return state
    
    def _parse_shot_scripts(self, response: str) -> List[Dict[str, Any]]:
        """解析LLM返回的分镜头脚本"""
        shot_scripts = []
        
        # 简单的解析逻辑：按"分镜"分割
        lines = response.split("分镜")
        
        for idx, line in enumerate(lines[1:], 1):  # 跳过第一个空元素
            try:
                # 提取分镜编号
                if "：" in line or ":" in line:
                    parts = line.split("：", 1) if "：" in line else line.split(":", 1)
                    if len(parts) >= 2:
                        content = parts[1].strip()
                        
                        shot_scripts.append({
                            "shot_no": idx,
                            "total_script": content
                        })
            except Exception as e:
                logger.warning(f"解析分镜头脚本失败: {str(e)}")
                continue
        
        # 如果解析失败，返回一个默认分镜
        if not shot_scripts:
            shot_scripts.append({
                "shot_no": 1,
                "total_script": response
            })
        
        return shot_scripts


# ============================================================================
# 工作流构建
# ============================================================================

def build_video_generation_graph(llm_client: HelloAgentsLLM):
    """构建文生视频工作流图"""
    
    # 创建Agent节点
    character_agent = CharacterExtractionAgent(llm_client)
    shot_agent = ShotScriptGenerationAgent(llm_client)
    
    # 创建状态图
    workflow = StateGraph(VideoGenerationState)
    
    # 添加节点
    workflow.add_node("extract_characters", character_agent)
    workflow.add_node("generate_shots", shot_agent)
    
    # 设置入口点
    workflow.set_entry_point("extract_characters")
    
    # 添加边
    workflow.add_edge("extract_characters", "generate_shots")
    workflow.add_edge("generate_shots", END)
    
    return workflow.compile()


# ============================================================================
# 执行函数
# ============================================================================

async def run_video_generation_workflow(
    script_id: str,
    script_name: str,
    script_content: str,
    user_id: str,
    db_uri: str = None,
) -> Dict[str, Any]:
    """
    运行文生视频工作流
    
    参数:
    - script_id: 剧本ID
    - script_name: 剧本名称
    - script_content: 剧本内容
    - user_id: 用户ID
    - db_uri: 数据库连接字符串（用于Checkpoint）
    
    返回:
    - 工作流执行结果
    """
    logger.info(f"开始运行文生视频工作流，剧本ID: {script_id}")
    
    # 初始化LLM客户端
    llm_client = HelloAgentsLLM(model="qwen-plus-latest")
    
    # 构建工作流
    app = build_video_generation_graph(llm_client)
    
    # 初始化状态
    initial_state: VideoGenerationState = {
        "script_id": script_id,
        "script_name": script_name,
        "script_content": script_content,
        "user_id": user_id,
        "characters": [],
        "characters_generated": False,
        "characters_confirmed": False,
        "shot_scripts": [],
        "shots_generated": False,
        "shots_confirmed": False,
        "error_message": None,
        "current_stage": "character_extraction",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    
    # 如果提供了数据库连接，使用PostgresSaver作为Checkpoint
    if db_uri:
        async with AsyncConnectionPool(
            conninfo=db_uri,
            min_size=1,
            max_size=5,
            kwargs={"autocommit": True, "prepare_threshold": 0}
        ) as pool:
            checkpointer = AsyncPostgresSaver(pool)
            
            # 运行工作流
            result = await app.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": script_id}}
            )
    else:
        # 不使用Checkpoint直接运行
        result = await app.ainvoke(initial_state)
    
    logger.info(f"文生视频工作流执行完成，剧本ID: {script_id}")
    
    return result
