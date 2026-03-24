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
            
            # 保存角色信息到数据库
            characters = result.get('characters', [])
            if characters:
                save_success = save_characters_to_db(script_id, characters)
                if not save_success:
                    logger.warning(f"角色信息保存到数据库失败，但继续执行后续流程")
            
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
            
            # 保存分镜头脚本到数据库
            shot_scripts = result.get('shot_scripts', [])
            if shot_scripts:
                save_success = save_shot_scripts_to_db(script_id, shot_scripts)
                if not save_success:
                    logger.warning(f"分镜头脚本保存到数据库失败，但继续执行后续流程")
            
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
