"""
文生视频业务逻辑层
包含 DB 操作、Redis 操作、工作流恢复逻辑，与 Celery 解耦，可独立测试和复用
"""
import os
import asyncio
import logging
import uuid
import shutil
from typing import Dict, Any, Optional
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from app.agent.utils.redis import (
    get_video_project_manager,
    ProjectStage,
    ProjectStatus,
    TaskStatus,
)
from app.core.db import engine
from app.models import CharacterInfo, ShotScript, Script, SceneGraph

logger = logging.getLogger(__name__)


# ============================================================================
# DB URI 构建
# ============================================================================

def build_db_uri() -> str:
    """从环境变量构建数据库连接字符串"""
    postgres_server = os.getenv("POSTGRES_SERVER", "localhost")
    postgres_port = os.getenv("POSTGRES_PORT", "5432")
    postgres_db = os.getenv("POSTGRES_DB", "app")
    postgres_user = os.getenv("POSTGRES_USER", "postgres")
    postgres_password = os.getenv("POSTGRES_PASSWORD", "changethis")
    return f"postgresql://{postgres_user}:{postgres_password}@{postgres_server}:{postgres_port}/{postgres_db}"


def resolve_db_uri(db_uri: Optional[str] = None) -> str:
    """解析 db_uri，如果未提供则从环境变量构建"""
    return db_uri if db_uri else build_db_uri()


# ============================================================================
# Redis 状态更新
# ============================================================================

async def update_redis_stage_status(
    script_id: str,
    stage: str,
    status: str,
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
        if stage == "char_desc":
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                metadata={"current_stage": ProjectStage.SHOTLIST_SCRIPT},
            )
            logger.info(f"已更新Redis状态：角色生成完成，进入分镜生成阶段")
        elif stage == "shotlist_script":
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.WAITING_REVIEW,
                metadata={"current_stage": ProjectStage.WAITING_REVIEW},
            )
            logger.info(f"已更新Redis状态：分镜生成完成，等待审核")

        return True

    except Exception as e:
        logger.error(f"更新Redis阶段状态失败: {str(e)}")
        return False
    finally:
        await redis_manager.close()


def update_redis_stage_status_sync(
    script_id: str,
    stage: str,
    status: str,
) -> bool:
    """
    更新Redis中的阶段状态（同步版本，供 callback 使用）

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
            if stage == "char_desc":
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.RUNNING,
                    metadata={"current_stage": ProjectStage.SHOTLIST_SCRIPT},
                )
                logger.info(f"已更新Redis状态：角色生成完成，进入分镜生成阶段")
            elif stage == "shotlist_script":
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.WAITING_REVIEW,
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

    try:
        return asyncio.run(_update())
    except Exception as e:
        logger.error(f"运行Redis更新失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================================
# 数据库操作
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
            script_uuid = uuid.UUID(script_id)

            statement = select(CharacterInfo).where(CharacterInfo.script_id == script_uuid)
            existing_chars = session.exec(statement).all()
            for char in existing_chars:
                char.is_deleted = 1
                char.update_time = datetime.utcnow()
                session.add(char)

            for char_data in characters:
                character = CharacterInfo(
                    script_id=script_uuid,
                    role_name=char_data.get("role_name", "未命名角色"),
                    role_desc=char_data.get("role_desc", "暂无描述"),
                    version=1,
                    create_time=datetime.utcnow(),
                    update_time=datetime.utcnow(),
                    is_deleted=0,
                )
                session.add(character)

            script = session.get(Script, script_uuid)
            if script:
                script.status = 1
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
            script_uuid = uuid.UUID(script_id)

            statement = select(ShotScript).where(ShotScript.script_id == script_uuid)
            existing_shots = session.exec(statement).all()
            for shot in existing_shots:
                shot.is_deleted = 1
                shot.update_time = datetime.utcnow()
                session.add(shot)

            for shot_data in shot_scripts:
                scene_group_val = shot_data.get("scene_group", 1)
                scene_id_val = None
                existing_sg = session.exec(
                    select(SceneGraph).where(
                        SceneGraph.script_id == script_uuid,
                        SceneGraph.scene_group == scene_group_val,
                        SceneGraph.is_deleted == 0,
                    )
                ).first()
                if existing_sg:
                    scene_id_val = existing_sg.id

                shot_script = ShotScript(
                    script_id=script_uuid,
                    shot_no=shot_data.get("shot_no", 1),
                    total_script=shot_data.get("total_script", ""),
                    scene_group=scene_group_val,
                    scene_name=shot_data.get("scene_name", "默认场景"),
                    shot_group=shot_data.get("shot_group", 1),
                    scene_id=scene_id_val,
                    version=1,
                    create_time=datetime.utcnow(),
                    update_time=datetime.utcnow(),
                    is_deleted=0,
                )
                session.add(shot_script)

            script = session.get(Script, script_uuid)
            if script:
                script.status = 2
                script.update_time = datetime.utcnow()
                session.add(script)

            session.commit()
            logger.info(f"成功保存{len(shot_scripts)}个分镜头脚本到数据库，剧本ID: {script_id}")
            return True

    except Exception as e:
        logger.error(f"保存分镜头脚本到数据库失败: {str(e)}")
        return False


def update_character_three_view_in_db(
    script_id: str,
    role_name: str,
    three_view_image_path: str,
) -> bool:
    """更新角色四视图路径到数据库"""
    try:
        with Session(engine) as session:
            script_uuid = uuid.UUID(script_id)

            statement = select(CharacterInfo).where(
                CharacterInfo.script_id == script_uuid,
                CharacterInfo.role_name == role_name,
                CharacterInfo.is_deleted == 0,
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


def update_shot_grid_image_in_db(
    script_id: str,
    shot_no: int,
    grid_image_path: str,
) -> bool:
    """更新分镜九宫格图片路径到数据库"""
    try:
        with Session(engine) as session:
            script_uuid = uuid.UUID(script_id)

            statement = select(ShotScript).where(
                ShotScript.script_id == script_uuid,
                ShotScript.shot_no == shot_no,
                ShotScript.is_deleted == 0,
            )
            shot = session.exec(statement).first()

            if shot:
                shot.grid_image_path = grid_image_path
                shot.update_time = datetime.utcnow()
                session.add(shot)
                session.commit()
                logger.info(f"成功更新分镜{shot_no}的九宫格图片路径到数据库")
                return True
            else:
                logger.warning(f"未找到分镜{shot_no}，无法更新九宫格图片路径")
                return False

    except Exception as e:
        logger.error(f"更新分镜九宫格图片路径到数据库失败: {str(e)}")
        return False


def update_shot_first_frame_image_in_db(
    script_id: str,
    shot_no: int,
    first_frame_image_path: str,
) -> bool:
    """更新分镜首帧图路径到数据库"""
    try:
        with Session(engine) as session:
            script_uuid = uuid.UUID(script_id)

            statement = select(ShotScript).where(
                ShotScript.script_id == script_uuid,
                ShotScript.shot_no == shot_no,
                ShotScript.is_deleted == 0,
            )
            shot = session.exec(statement).first()

            if shot:
                shot.first_frame_image_path = first_frame_image_path
                shot.update_time = datetime.utcnow()
                session.add(shot)
                session.commit()
                logger.info(f"成功更新分镜{shot_no}的首帧图路径到数据库")
                return True
            else:
                logger.warning(f"未找到分镜{shot_no}，无法更新首帧图路径")
                return False

    except Exception as e:
        logger.error(f"更新分镜首帧图路径到数据库失败: {str(e)}")
        return False


def update_shot_last_frame_image_in_db(
    script_id: str,
    shot_no: int,
    last_frame_image_path: str,
) -> bool:
    """更新分镜尾帧图路径到数据库"""
    try:
        with Session(engine) as session:
            script_uuid = uuid.UUID(script_id)

            statement = select(ShotScript).where(
                ShotScript.script_id == script_uuid,
                ShotScript.shot_no == shot_no,
                ShotScript.is_deleted == 0,
            )
            shot = session.exec(statement).first()

            if shot:
                shot.last_frame_image_path = last_frame_image_path
                shot.update_time = datetime.utcnow()
                session.add(shot)
                session.commit()
                logger.info(f"成功更新分镜{shot_no}的尾帧图路径到数据库")
                return True
            else:
                logger.warning(f"未找到分镜{shot_no}，无法更新尾帧图路径")
                return False

    except Exception as e:
        logger.error(f"更新分镜尾帧图路径到数据库失败: {str(e)}")
        return False


def update_shot_video_path_in_db(
    script_id: str,
    shot_no: int,
    video_path: str,
) -> bool:
    """更新分镜视频路径到数据库"""
    try:
        with Session(engine) as session:
            script_uuid = uuid.UUID(script_id)

            statement = select(ShotScript).where(
                ShotScript.script_id == script_uuid,
                ShotScript.shot_no == shot_no,
                ShotScript.is_deleted == 0,
            )
            shot = session.exec(statement).first()

            if shot:
                shot.video_path = video_path
                shot.update_time = datetime.utcnow()
                session.add(shot)
                session.commit()
                logger.info(f"成功更新分镜{shot_no}的视频路径到数据库")
                return True
            else:
                logger.warning(f"未找到分镜{shot_no}，无法更新视频路径")
                return False

    except Exception as e:
        logger.error(f"更新分镜视频路径到数据库失败: {str(e)}")
        return False


def save_scene_background_to_db(
    script_id: str,
    scene_group_no: int,
    scene_name: str,
    background_image_path: str,
) -> bool:
    """保存背景图到 scene_graph 数据库表，并更新关联的 shot_script"""
    try:
        script_uuid = uuid.UUID(script_id)
        with Session(engine) as db_session:
            existing_scene = db_session.exec(
                select(SceneGraph).where(
                    SceneGraph.script_id == script_uuid,
                    SceneGraph.scene_group == scene_group_no,
                    SceneGraph.is_deleted == 0,
                )
            ).first()

            if existing_scene:
                existing_scene.scene_image_path = background_image_path
                existing_scene.scene_name = scene_name
                existing_scene.update_time = datetime.utcnow()
                db_session.add(existing_scene)
                scene_id = existing_scene.id
            else:
                new_scene = SceneGraph(
                    script_id=script_uuid,
                    scene_group=scene_group_no,
                    scene_name=scene_name,
                    scene_image_path=background_image_path,
                    version=1,
                    create_time=datetime.utcnow(),
                    update_time=datetime.utcnow(),
                    is_deleted=0,
                )
                db_session.add(new_scene)
                db_session.commit()
                db_session.refresh(new_scene)
                scene_id = new_scene.id

            related_shots = db_session.exec(
                select(ShotScript).where(
                    ShotScript.script_id == script_uuid,
                    ShotScript.scene_group == scene_group_no,
                    ShotScript.is_deleted == 0,
                )
            ).all()
            for shot in related_shots:
                shot.scene_id = scene_id
                shot.update_time = datetime.utcnow()
                db_session.add(shot)

            db_session.commit()
            logger.info(
                f"场景组{scene_group_no}背景图已保存到数据库，关联{len(related_shots)}条分镜记录"
            )
            return True
    except Exception as db_err:
        logger.error(f"保存背景图到数据库失败: {str(db_err)}")
        return False


# ============================================================================
# 辅助函数
# ============================================================================

def copy_last_frame_as_first_frame(
    script_id: str,
    prev_shot_no: int,
    current_shot_no: int,
    scene_group_no: int,
    script_name: str,
) -> str:
    """
    将上一条分镜的尾帧图复制一份，改名为当前分镜的首帧图

    返回:
    - 新首帧图的路径（成功）或空字符串（失败）
    """
    try:
        with Session(engine) as session:
            script_uuid = uuid.UUID(script_id)
            statement = select(ShotScript).where(
                ShotScript.script_id == script_uuid,
                ShotScript.shot_no == prev_shot_no,
                ShotScript.is_deleted == 0,
            )
            prev_shot = session.exec(statement).first()

            if not prev_shot or not prev_shot.last_frame_image_path:
                logger.warning(f"上一条分镜{prev_shot_no}没有尾帧图，无法复制为首帧图")
                return ""

            prev_last_frame_path = Path(prev_shot.last_frame_image_path)
            if not prev_last_frame_path.exists():
                logger.warning(f"上一条分镜{prev_shot_no}的尾帧图文件不存在: {prev_last_frame_path}")
                return ""

            image_output_dir = prev_last_frame_path.parent
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            new_file_name = f"{script_name}_{scene_group_no}_{current_shot_no}_{timestamp}_ff.png"
            new_file_path = image_output_dir / new_file_name

            shutil.copy2(str(prev_last_frame_path), str(new_file_path))
            logger.info(f"已将分镜{prev_shot_no}的尾帧图复制为分镜{current_shot_no}的首帧图: {new_file_path}")

            return str(new_file_path)

    except Exception as e:
        logger.error(f"复制尾帧图为首帧图失败: {str(e)}")
        return ""


def get_prev_shot_in_scene_group(
    script_id: str,
    scene_group_no: int,
    shot_no: int,
) -> Optional[ShotScript]:
    """获取同一场景组中指定分镜号之前的最近一条分镜"""
    try:
        with Session(engine) as session:
            script_uuid = uuid.UUID(script_id)
            statement = select(ShotScript).where(
                ShotScript.script_id == script_uuid,
                ShotScript.scene_group == scene_group_no,
                ShotScript.shot_no < shot_no,
                ShotScript.is_deleted == 0,
            ).order_by(ShotScript.shot_no.desc())
            return session.exec(statement).first()
    except Exception as e:
        logger.error(f"获取前一分镜失败: {str(e)}")
        return None


# ============================================================================
# 工作流恢复业务逻辑
# ============================================================================

async def resume_workflow_and_generate_three_view(
    script_id: str,
    character: dict,
    user_id: str,
    db_uri: str,
    force_regenerate: bool = False,
    user_seed: Optional[int] = None,
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
    - force_regenerate: 是否强制重新生成四视图（即使已存在）
    - user_seed: 用户指定的seed（可选，用于覆盖自动生成的seed）

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
        llm_client = HelloAgentsLLM()

        pool = AsyncConnectionPool(
            conninfo=db_uri,
            min_size=5,
            max_size=10,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )
        await pool.open()

        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()

        app = build_video_generation_graph(llm_client, checkpointer)

        config = {"configurable": {"thread_id": script_id}}

        logger.info(f"准备恢复工作流，角色: {character.get('role_name', '未知')}, 强制重新生成: {force_regenerate}")

        if force_regenerate:
            character_with_flag = {**character, "force_regenerate": True}
        else:
            character_with_flag = character

        result = await app.ainvoke(
            Command(resume=character_with_flag),
            config=config,
        )

        three_view_image_path = None
        if result and "character_three_views" in result:
            three_views = result["character_three_views"]
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


# ============================================================================
# Celery 任务状态查询（无需 Celery worker 也能查询）
# ============================================================================

def get_task_status(task_id: str) -> Dict[str, Any]:
    """
    获取任务状态

    返回:
    - 任务状态信息
    """
    from app.agent.generateVideo.celery_config import celery_app

    task = celery_app.AsyncResult(task_id)

    return {
        "task_id": task_id,
        "status": task.status,
        "result": task.result if task.ready() else None,
        "error": str(task.result) if task.failed() else None,
    }
