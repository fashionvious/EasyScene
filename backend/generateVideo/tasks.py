"""
文生视频Celery任务
异步执行LangGraph工作流，避免阻塞API请求
"""
import os
import asyncio
import sys
import logging
from typing import Dict, Any, Optional
from celery import Celery
from datetime import datetime
from pathlib import Path

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
