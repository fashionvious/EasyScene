"""
文生视频Celery任务
异步执行LangGraph工作流，避免阻塞API请求
"""
import os
import asyncio
import sys
import logging
import uuid
from typing import Dict, Any, Optional
from celery import Celery
from datetime import datetime
from pathlib import Path
from sqlmodel import Session

# Windows事件循环策略
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 本地导入
sys.path.append(str(Path(__file__).resolve().parents[3]))
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(env_path)

from app.agent.generateVideo.text2video import run_video_generation_workflow
from app.agent.utils.redis import (
    get_video_project_manager,
    ProjectStage,
    ProjectStatus,
    TaskStatus,
)
from app.core.db import engine
from app.models import CharacterInfo, ShotScript, Script

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
# Celery配置
# ============================================================================

class CeleryConfig:
    """Celery配置类"""
    CELERY_BROKER_URL="redis://:123456@localhost:6379/0"
    CELERY_RESULT_BACKEND="redis://:123456@localhost:6379/0"

# 创建Celery实例
celery_app = Celery(
    main="text2video_tasks",
    broker=CeleryConfig.CELERY_BROKER_URL,
    backend=CeleryConfig.CELERY_RESULT_BACKEND,
)

# 设置Celery配置参数
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1小时超时
    task_soft_time_limit=3000,  # 50分钟软超时
)


# ============================================================================
# Celery任务定义
# ============================================================================

@celery_app.task(bind=True)
def generate_video_workflow_task(
    self,
    script_id: str,
    script_name: str,
    script_content: str,
    user_id: str,
    db_uri: Optional[str] = None,
) -> Dict[str, Any]:
    """
    异步执行文生视频工作流
    
    参数:
    - script_id: 剧本ID
    - script_name: 剧本名称
    - script_content: 剧本内容
    - user_id: 用户ID
    - db_uri: 数据库连接字符串
    
    返回:
    - 工作流执行结果
    """
    # 修复：将 db_uri 的构建逻辑移到外部函数作用域
    # 这样 run() 函数内部只读取 db_uri，不会因为赋值操作导致 UnboundLocalError
    if not db_uri:
        postgres_server = os.getenv("POSTGRES_SERVER", "localhost")
        postgres_port = os.getenv("POSTGRES_PORT", "5432")
        postgres_db = os.getenv("POSTGRES_DB", "app")
        postgres_user = os.getenv("POSTGRES_USER", "postgres")
        postgres_password = os.getenv("POSTGRES_PASSWORD", "changethis")
        db_uri = f"postgresql://{postgres_user}:{postgres_password}@{postgres_server}:{postgres_port}/{postgres_db}"

    async def run():
        redis_manager = get_video_project_manager()
        
        try:
            # 更新项目状态为运行中
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                task_id=self.request.id,
            )
            
            # 创建角色提取任务
            char_task_id = await redis_manager.create_task(
                project_id=script_id,
                stage=ProjectStage.CHAR_DESC,
            )
            
            # 更新任务状态为运行中
            await redis_manager.update_task_state(
                task_id=char_task_id,
                status=TaskStatus.RUNNING,
            )
            
            # 运行工作流
            result = await run_video_generation_workflow(
                script_id=script_id,
                script_name=script_name,
                script_content=script_content,
                user_id=user_id,
                db_uri=db_uri,
                save_characters_callback=save_characters_to_db,
                save_shots_callback=save_shot_scripts_to_db,
                redis_update_callback=update_redis_stage_status_sync,  # 使用同步版本
            )
            
            # 检查是否有错误
            if result.get("error_message"):
                await redis_manager.update_task_state(
                    task_id=char_task_id,
                    status=TaskStatus.FAILED,
                    error_message=result["error_message"],
                )
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.FAILED,
                    error_message=result["error_message"],
                )
                return result
            
            # 更新角色提取任务状态为成功
            await redis_manager.update_task_state(
                task_id=char_task_id,
                status=TaskStatus.SUCCESS,
                result_summary=f"提取了{len(result.get('characters', []))}个角色",
            )
            
            # 注意：角色信息和分镜脚本已经在工作流内部保存到数据库了
            # 这里不需要再次保存
            
            # 创建分镜头脚本生成任务
            shot_task_id = await redis_manager.create_task(
                project_id=script_id,
                stage=ProjectStage.SHOTLIST_SCRIPT,
            )
            
            # 更新任务状态为运行中
            await redis_manager.update_task_state(
                task_id=shot_task_id,
                status=TaskStatus.RUNNING,
            )
            
            # 更新分镜头脚本任务状态为成功
            await redis_manager.update_task_state(
                task_id=shot_task_id,
                status=TaskStatus.SUCCESS,
                result_summary=f"生成了{len(result.get('shot_scripts', []))}个分镜",
            )
            
            # 设置项目为等待审核状态
            await redis_manager.set_project_waiting_review(
                project_id=script_id,
                task_id=self.request.id,
                message="角色信息和分镜头脚本已生成，请审核",
            )
            
            logger.info(f"工作流执行成功，剧本ID: {script_id}")
            
            return result
            
        except Exception as e:
            logger.error(f"工作流执行失败: {str(e)}")
            
            # 更新项目状态为失败
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.FAILED,
                error_message=str(e),
            )
            
            raise e
            
        finally:
            await redis_manager.close()
    
    return asyncio.run(run())


@celery_app.task(bind=True)
def confirm_characters_task(
    self,
    script_id: str,
    characters: list,
    user_id: str,
) -> Dict[str, Any]:
    """
    确认角色信息
    
    参数:
    - script_id: 剧本ID
    - characters: 角色信息列表
    - user_id: 用户ID
    
    返回:
    - 确认结果
    """
    async def run():
        redis_manager = get_video_project_manager()
        
        try:
            # 更新项目状态
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                metadata={"characters_confirmed": True},
            )
            
            logger.info(f"角色信息确认成功，剧本ID: {script_id}")
            
            return {
                "success": True,
                "script_id": script_id,
                "message": "角色信息确认成功",
            }
            
        except Exception as e:
            logger.error(f"角色信息确认失败: {str(e)}")
            
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.FAILED,
                error_message=str(e),
            )
            
            raise e
            
        finally:
            await redis_manager.close()
    
    return asyncio.run(run())


@celery_app.task(bind=True)
def confirm_shots_task(
    self,
    script_id: str,
    shot_scripts: list,
    user_id: str,
) -> Dict[str, Any]:
    """
    确认分镜头脚本
    
    参数:
    - script_id: 剧本ID
    - shot_scripts: 分镜头脚本列表
    - user_id: 用户ID
    
    返回:
    - 确认结果
    """
    async def run():
        redis_manager = get_video_project_manager()
        
        try:
            # 更新项目状态
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                metadata={"shots_confirmed": True},
            )
            
            logger.info(f"分镜头脚本确认成功，剧本ID: {script_id}")
            
            return {
                "success": True,
                "script_id": script_id,
                "message": "分镜头脚本确认成功",
            }
            
        except Exception as e:
            logger.error(f"分镜头脚本确认失败: {str(e)}")
            
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.FAILED,
                error_message=str(e),
            )
            
            raise e
            
        finally:
            await redis_manager.close()
    
    return asyncio.run(run())


@celery_app.task(bind=True)
def resume_character_review_task(
    self,
    script_id: str,
    character: dict,
    user_id: str,
    db_uri: Optional[str] = None,
) -> Dict[str, Any]:
    """
    恢复角色审核工作流（HITL）
    
    参数:
    - script_id: 剧本ID
    - character: 用户审核后的角色信息
    - user_id: 用户ID
    - db_uri: 数据库连接字符串
    
    返回:
    - 恢复执行结果
    """
    # 构建数据库连接字符串（如果未提供）
    if not db_uri:
        postgres_server = os.getenv("POSTGRES_SERVER", "localhost")
        postgres_port = os.getenv("POSTGRES_PORT", "5432")
        postgres_db = os.getenv("POSTGRES_DB", "app")
        postgres_user = os.getenv("POSTGRES_USER", "postgres")
        postgres_password = os.getenv("POSTGRES_PASSWORD", "changethis")
        db_uri = f"postgresql://{postgres_user}:{postgres_password}@{postgres_server}:{postgres_port}/{postgres_db}"
    
    async def run():
        redis_manager = get_video_project_manager()
        
        try:
            # 检查项目是否存在，如果不存在则创建
            project_state = await redis_manager.get_project_state(script_id)
            if not project_state:
                logger.warning(f"Redis中不存在项目状态，将创建新项目: project_id={script_id}")
                # 创建项目状态
                await redis_manager.create_project(
                    user_id=user_id,
                    project_id=script_id,
                    metadata={"current_character": character}
                )
            
            # 更新项目状态
            update_success = await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                metadata={"current_character": character},
            )
            
            if not update_success:
                logger.warning(f"更新项目状态失败，但继续执行工作流恢复")
            
            logger.info(f"用户审核角色完成，准备恢复工作流，剧本ID: {script_id}")
            
            # 恢复LangGraph工作流并生成四视图
            result = await resume_workflow_and_generate_three_view(
                script_id=script_id,
                character=character,
                user_id=user_id,
                db_uri=db_uri,
            )
            
            # 检查是否有错误
            if result.get("error_message"):
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.FAILED,
                    error_message=result["error_message"],
                )
                return result
            
            # 更新角色四视图路径到数据库
            three_view_image_path = result.get("three_view_image_path")
            if three_view_image_path:
                update_success = update_character_three_view_in_db(
                    script_id=script_id,
                    role_name=character.get("role_name", ""),
                    three_view_image_path=three_view_image_path,
                )
                if not update_success:
                    logger.warning(f"角色四视图路径更新到数据库失败")
                
                # 更新Redis状态，标记四视图生成完成
                # 注意：这里不需要改变阶段，只需要确保状态是WAITING_REVIEW
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.WAITING_REVIEW,
                )
                logger.info(f"已更新Redis状态：四视图生成完成")
            
            logger.info(f"角色四视图生成完成，剧本ID: {script_id}")
            
            return {
                "success": True,
                "script_id": script_id,
                "character": character,
                "three_view_image_path": three_view_image_path,
                "message": "角色审核完成，四视图已生成",
            }
            
        except Exception as e:
            logger.error(f"恢复工作流失败: {str(e)}")
            
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.FAILED,
                error_message=str(e),
            )
            
            raise e
            
        finally:
            await redis_manager.close()
    
    return asyncio.run(run())


# ============================================================================
# Redis状态更新辅助函数
# ============================================================================

def update_redis_stage_status_sync(
    script_id: str,
    stage: str,
    status: str
) -> bool:
    """
    更新Redis中的阶段状态（同步版本）
    
    参数:
    - script_id: 剧本ID
    - stage: 阶段名称 ("char_desc" 或 "shotlist_script")
    - status: 状态 ("completed")
    
    返回:
    - 是否更新成功
    """
    async def _update():
        redis_manager = get_video_project_manager()
        
        try:
            # 根据阶段更新状态
            if stage == "char_desc":
                # 角色生成完成，更新阶段为SHOTLIST_SCRIPT
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.RUNNING,
                    metadata={"current_stage": ProjectStage.SHOTLIST_SCRIPT}
                )
                logger.info(f"已更新Redis状态：角色生成完成，进入分镜生成阶段")
            elif stage == "shotlist_script":
                # 分镜生成完成，更新阶段为WAITING_REVIEW
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.WAITING_REVIEW,
                    metadata={"current_stage": ProjectStage.WAITING_REVIEW}
                )
                logger.info(f"已更新Redis状态：分镜生成完成，等待审核")
            
            return True
            
        except Exception as e:
            logger.error(f"更新Redis阶段状态失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            await redis_manager.close()
    
    # 使用 asyncio.run() 在新的事件循环中运行
    # 这是最简单和最可靠的方法
    try:
        return asyncio.run(_update())
    except Exception as e:
        logger.error(f"运行Redis更新失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


async def update_redis_stage_status(
    script_id: str,
    stage: str,
    status: str
) -> bool:
    """
    更新Redis中的阶段状态（异步版本）
    
    参数:
    - script_id: 剧本ID
    - stage: 阶段名称 ("char_desc" 或 "shotlist_script")
    - status: 状态 ("completed")
    
    返回:
    - 是否更新成功
    """
    redis_manager = get_video_project_manager()
    
    try:
        # 根据阶段更新状态
        if stage == "char_desc":
            # 角色生成完成，更新阶段为SHOTLIST_SCRIPT
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                metadata={"current_stage": ProjectStage.SHOTLIST_SCRIPT}
            )
            logger.info(f"已更新Redis状态：角色生成完成，进入分镜生成阶段")
        elif stage == "shotlist_script":
            # 分镜生成完成，更新阶段为WAITING_REVIEW
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.WAITING_REVIEW,
                metadata={"current_stage": ProjectStage.WAITING_REVIEW}
            )
            logger.info(f"已更新Redis状态：分镜生成完成，等待审核")
        
        return True
        
    except Exception as e:
        logger.error(f"更新Redis阶段状态失败: {str(e)}")
        return False
    finally:
        await redis_manager.close()


# ============================================================================
# 数据库操作辅助函数
# ============================================================================

def save_characters_to_db(script_id: str, characters: list) -> bool:
    """
    将角色信息保存到数据库
    
    参数:
    - script_id: 剧本ID
    - characters: 角色信息列表 [{"role_name": "xxx", "role_desc": "xxx"}, ...]
    
    返回:
    - 是否保存成功
    """
    try:
        with Session(engine) as session:
            # 将script_id转换为UUID
            script_uuid = uuid.UUID(script_id)
            
            # 先删除该剧本之前的角色信息（软删除）
            from sqlmodel import select
            statement = select(CharacterInfo).where(CharacterInfo.script_id == script_uuid)
            existing_chars = session.exec(statement).all()
            for char in existing_chars:
                char.is_deleted = 1
                char.update_time = datetime.utcnow()
                session.add(char)
            
            # 创建新的角色信息
            for char_data in characters:
                character = CharacterInfo(
                    script_id=script_uuid,
                    role_name=char_data.get("role_name", "未命名角色"),
                    role_desc=char_data.get("role_desc", "暂无描述"),
                    version=1,
                    create_time=datetime.utcnow(),
                    update_time=datetime.utcnow(),
                    is_deleted=0
                )
                session.add(character)
            
            # 更新剧本状态为"角色已生成"
            script = session.get(Script, script_uuid)
            if script:
                script.status = 1  # 1=characters generated
                script.update_time = datetime.utcnow()
                session.add(script)
            
            session.commit()
            logger.info(f"成功保存{len(characters)}个角色信息到数据库，剧本ID: {script_id}")
            return True
            
    except Exception as e:
        logger.error(f"保存角色信息到数据库失败: {str(e)}")
        return False


def save_shot_scripts_to_db(script_id: str, shot_scripts: list) -> bool:
    """
    将分镜头脚本保存到数据库
    
    参数:
    - script_id: 剧本ID
    - shot_scripts: 分镜头脚本列表 [{"shot_no": 1, "total_script": "xxx"}, ...]
    
    返回:
    - 是否保存成功
    """
    try:
        with Session(engine) as session:
            # 将script_id转换为UUID
            script_uuid = uuid.UUID(script_id)
            
            # 先删除该剧本之前的分镜头脚本（软删除）
            from sqlmodel import select
            statement = select(ShotScript).where(ShotScript.script_id == script_uuid)
            existing_shots = session.exec(statement).all()
            for shot in existing_shots:
                shot.is_deleted = 1
                shot.update_time = datetime.utcnow()
                session.add(shot)
            
            # 创建新的分镜头脚本
            for shot_data in shot_scripts:
                shot_script = ShotScript(
                    script_id=script_uuid,
                    shot_no=shot_data.get("shot_no", 1),
                    total_script=shot_data.get("total_script", ""),
                    version=1,
                    create_time=datetime.utcnow(),
                    update_time=datetime.utcnow(),
                    is_deleted=0
                )
                session.add(shot_script)
            
            # 更新剧本状态为"分镜头脚本已生成"
            script = session.get(Script, script_uuid)
            if script:
                script.status = 2  # 2=storyboard generated
                script.update_time = datetime.utcnow()
                session.add(script)
            
            session.commit()
            logger.info(f"成功保存{len(shot_scripts)}个分镜头脚本到数据库，剧本ID: {script_id}")
            return True
            
    except Exception as e:
        logger.error(f"保存分镜头脚本到数据库失败: {str(e)}")
        return False


# ============================================================================
# 辅助函数
# ============================================================================

def start_video_generation(
    script_id: str,
    script_name: str,
    script_content: str,
    user_id: str,
    db_uri: Optional[str] = None,
) -> str:
    """
    启动文生视频生成任务
    
    返回:
    - 任务ID
    """
    task = generate_video_workflow_task.delay(
        script_id=script_id,
        script_name=script_name,
        script_content=script_content,
        user_id=user_id,
        db_uri=db_uri,
    )
    return task.id


def get_task_status(task_id: str) -> Dict[str, Any]:
    """
    获取任务状态
    
    返回:
    - 任务状态信息
    """
    task = celery_app.AsyncResult(task_id)
    
    return {
        "task_id": task_id,
        "status": task.status,
        "result": task.result if task.ready() else None,
        "error": str(task.result) if task.failed() else None,
    }


async def resume_workflow_and_generate_three_view(
    script_id: str,
    character: dict,
    user_id: str,
    db_uri: str,
) -> Dict[str, Any]:
    """
    恢复LangGraph工作流并生成角色四视图
    
    使用 LangGraph 官方 HITL API：
    - 使用 Command(resume=...) 恢复中断的工作流
    - Command 的 resume 参数会作为 interrupt() 的返回值
    
    参数:
    - script_id: 剧本ID
    - character: 用户审核后的角色信息
    - user_id: 用户ID
    - db_uri: 数据库连接字符串
    
    返回:
    - 包含四视图路径的结果字典
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool
    from langgraph.types import Command
    from app.agent.generateVideo.text2video import (
        build_video_generation_graph,
        VideoGenerationState,
    )
    from app.agent.generatePic.llm_client import HelloAgentsLLM
    
    pool = None
    
    try:
        # 初始化LLM客户端
        llm_client = HelloAgentsLLM()
        
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
        
        # 构建工作流图
        app = build_video_generation_graph(llm_client, checkpointer)
        
        # 配置
        config = {"configurable": {"thread_id": script_id}}
        
        logger.info(f"准备恢复工作流，角色: {character.get('role_name', '未知')}")
        
        # 使用 LangGraph 官方 HITL API 恢复工作流
        # Command(resume=character) 会将 character 作为 interrupt() 的返回值
        result = await app.ainvoke(
            Command(resume=character),
            config=config
        )
        
        # 提取四视图路径
        three_view_image_path = None
        if result and "character_three_views" in result:
            three_views = result["character_three_views"]
            # 查找当前角色的四视图
            for view in three_views:
                if view.get("role_name") == character.get("role_name"):
                    three_view_image_path = view.get("three_view_image_path")
                    break
        
        logger.info(f"工作流恢复执行完成，四视图路径: {three_view_image_path}")
        
        return {
            "success": True,
            "three_view_image_path": three_view_image_path,
        }
        
    except Exception as e:
        logger.error(f"恢复工作流并生成四视图失败: {str(e)}")
        return {
            "error_message": str(e),
        }
    
    finally:
        if pool:
            await pool.close()


def update_character_three_view_in_db(
    script_id: str,
    role_name: str,
    three_view_image_path: str,
) -> bool:
    """
    更新角色四视图路径到数据库
    
    参数:
    - script_id: 剧本ID
    - role_name: 角色名称
    - three_view_image_path: 四视图图片路径
    
    返回:
    - 是否更新成功
    """
    try:
        with Session(engine) as session:
            # 将script_id转换为UUID
            script_uuid = uuid.UUID(script_id)
            
            # 查找对应的角色
            from sqlmodel import select
            statement = select(CharacterInfo).where(
                CharacterInfo.script_id == script_uuid,
                CharacterInfo.role_name == role_name,
                CharacterInfo.is_deleted == 0
            )
            character = session.exec(statement).first()
            
            if character:
                character.three_view_image_path = three_view_image_path
                character.update_time = datetime.utcnow()
                session.add(character)
                session.commit()
                logger.info(f"成功更新角色 {role_name} 的四视图路径到数据库")
                return True
            else:
                logger.warning(f"未找到角色 {role_name}，无法更新四视图路径")
                return False
                
    except Exception as e:
        logger.error(f"更新角色四视图路径到数据库失败: {str(e)}")
        return False