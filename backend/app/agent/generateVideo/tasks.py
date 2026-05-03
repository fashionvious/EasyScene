"""
文生视频Celery任务
纯 Celery 调度壳：只负责异步任务调度，业务逻辑全部委托给 service 层
"""
import asyncio
import sys
import logging
from typing import Dict, Any, Optional

# Windows事件循环策略
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.agent.generateVideo.celery_config import celery_app
from app.agent.generateVideo.service import (
    resolve_db_uri,
    update_redis_stage_status_sync,
    save_characters_to_db,
    save_shot_scripts_to_db,
    update_character_three_view_in_db,
    update_shot_grid_image_in_db,
    update_shot_first_frame_image_in_db,
    update_shot_last_frame_image_in_db,
    update_shot_video_path_in_db,
    save_scene_background_to_db,
    copy_last_frame_as_first_frame,
    get_prev_shot_in_scene_group,
    resume_workflow_and_generate_three_view,
    get_task_status,
)
from app.agent.utils.redis import (
    get_video_project_manager,
    ProjectStage,
    ProjectStatus,
    TaskStatus,
)
from app.agent.generateVideo.text2video import run_video_generation_workflow

logger = logging.getLogger(__name__)


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
    user_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """异步执行文生视频工作流"""
    db_uri = resolve_db_uri(db_uri)

    async def run():
        redis_manager = get_video_project_manager()

        try:
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                task_id=self.request.id,
            )

            char_task_id = await redis_manager.create_task(
                project_id=script_id,
                stage=ProjectStage.CHAR_DESC,
            )

            await redis_manager.update_task_state(
                task_id=char_task_id,
                status=TaskStatus.RUNNING,
            )

            result = await run_video_generation_workflow(
                script_id=script_id,
                script_name=script_name,
                script_content=script_content,
                user_id=user_id,
                db_uri=db_uri,
                save_characters_callback=save_characters_to_db,
                save_shots_callback=save_shot_scripts_to_db,
                redis_update_callback=update_redis_stage_status_sync,
                user_seed=user_seed,
            )

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

            await redis_manager.update_task_state(
                task_id=char_task_id,
                status=TaskStatus.SUCCESS,
                result_summary=f"提取了{len(result.get('characters', []))}个角色",
            )

            shot_task_id = await redis_manager.create_task(
                project_id=script_id,
                stage=ProjectStage.SHOTLIST_SCRIPT,
            )

            await redis_manager.update_task_state(
                task_id=shot_task_id,
                status=TaskStatus.RUNNING,
            )

            await redis_manager.update_task_state(
                task_id=shot_task_id,
                status=TaskStatus.SUCCESS,
                result_summary=f"生成了{len(result.get('shot_scripts', []))}个分镜",
            )

            await redis_manager.set_project_waiting_review(
                project_id=script_id,
                task_id=self.request.id,
                message="角色信息和分镜头脚本已生成，请审核",
            )

            logger.info(f"工作流执行成功，剧本ID: {script_id}")

            return result

        except Exception as e:
            logger.error(f"工作流执行失败: {str(e)}")

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
    """确认角色信息"""
    async def run():
        redis_manager = get_video_project_manager()

        try:
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
    """确认分镜头脚本"""
    async def run():
        redis_manager = get_video_project_manager()

        try:
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
    force_regenerate: bool = False,
    user_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """恢复角色审核工作流（HITL）或重新生成四视图"""
    db_uri = resolve_db_uri(db_uri)

    async def run():
        redis_manager = get_video_project_manager()

        try:
            project_state = await redis_manager.get_project_state(script_id)
            if not project_state:
                logger.warning(f"Redis中不存在项目状态，将创建新项目: project_id={script_id}")
                await redis_manager.create_project(
                    user_id=user_id,
                    project_id=script_id,
                    metadata={"current_character": character},
                )

            update_success = await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                metadata={"current_character": character, "force_regenerate": force_regenerate},
            )

            if not update_success:
                logger.warning(f"更新项目状态失败，但继续执行工作流恢复")

            logger.info(f"用户审核角色完成，准备恢复工作流，剧本ID: {script_id}, 强制重新生成: {force_regenerate}")

            result = await resume_workflow_and_generate_three_view(
                script_id=script_id,
                character=character,
                user_id=user_id,
                db_uri=db_uri,
                force_regenerate=force_regenerate,
                user_seed=user_seed,
            )

            if result.get("error_message"):
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.FAILED,
                    error_message=result["error_message"],
                )
                return result

            three_view_image_path = result.get("three_view_image_path")
            if three_view_image_path:
                update_success = update_character_three_view_in_db(
                    script_id=script_id,
                    role_name=character.get("role_name", ""),
                    three_view_image_path=three_view_image_path,
                )
                if not update_success:
                    logger.warning(f"角色四视图路径更新到数据库失败")

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


@celery_app.task(bind=True)
def generate_scene_background_task(
    self,
    script_id: str,
    scene_group_no: int,
    scene_name: str,
    shot_scripts: list,
    user_id: str,
    user_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """为指定场景组生成背景图"""
    async def run():
        redis_manager = get_video_project_manager()

        try:
            project_state = await redis_manager.get_project_state(script_id)
            if not project_state:
                logger.warning(f"Redis中项目状态不存在，重新创建: project_id={script_id}")
                await redis_manager.create_project(
                    user_id=user_id,
                    project_id=script_id,
                )
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                task_id=self.request.id,
            )

            logger.info(f"开始为场景组{scene_group_no}生成背景图，剧本ID: {script_id}")

            from app.agent.generateVideo.scene_background_agent import SceneBackgroundAgent
            from app.agent.generatePic.llm_client import HelloAgentsLLM
            from app.agent.generateVideo.seed_manager import generate_global_seed

            llm_client = HelloAgentsLLM()
            global_seed = generate_global_seed(script_id, user_seed)
            logger.info(f"场景背景图生成，全局seed: {global_seed}")

            background_agent = SceneBackgroundAgent(llm_client, script_id)

            result = background_agent.generate_background_for_scene_group(
                scene_group_no=scene_group_no,
                scene_name=scene_name,
                shot_scripts=shot_scripts,
                script_id=script_id,
                global_seed=global_seed,
            )

            if result.get("error"):
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.FAILED,
                    error_message=result["error"],
                )
                return result

            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.WAITING_REVIEW,
            )

            background_image_path = result.get("background_image_path", "")
            if background_image_path:
                project_state = await redis_manager.get_project_state(script_id)
                if not project_state:
                    logger.warning(f"Redis中项目状态不存在，重新创建: project_id={script_id}")
                    await redis_manager.create_project(
                        user_id=user_id,
                        project_id=script_id,
                        metadata={"scene_backgrounds": [{
                            "scene_group": scene_group_no,
                            "scene_name": scene_name,
                            "background_image_path": background_image_path,
                        }]},
                    )
                else:
                    existing_backgrounds = []
                    if project_state.metadata:
                        existing_backgrounds = project_state.metadata.get("scene_backgrounds", [])
                    found = False
                    for bg in existing_backgrounds:
                        if bg.get("scene_group") == scene_group_no:
                            bg["background_image_path"] = background_image_path
                            bg["scene_name"] = scene_name
                            found = True
                            break
                    if not found:
                        existing_backgrounds.append({
                            "scene_group": scene_group_no,
                            "scene_name": scene_name,
                            "background_image_path": background_image_path,
                        })
                    await redis_manager.update_project_state(
                        project_id=script_id,
                        metadata={"scene_backgrounds": existing_backgrounds},
                    )

                save_scene_background_to_db(
                    script_id=script_id,
                    scene_group_no=scene_group_no,
                    scene_name=scene_name,
                    background_image_path=background_image_path,
                )

            logger.info(f"场景组{scene_group_no}背景图生成完成，剧本ID: {script_id}")

            return {
                "success": True,
                "script_id": script_id,
                "scene_group": scene_group_no,
                "scene_name": scene_name,
                "background_image_path": result.get("background_image_path", ""),
                "message": f"场景组{scene_group_no}背景图生成完成",
            }

        except Exception as e:
            logger.error(f"场景背景图生成失败: {str(e)}")

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
def generate_grid_image_task(
    self,
    script_id: str,
    shot_no: int,
    shot_script_text: str,
    scene_group_no: int,
    user_id: str,
    user_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """为指定分镜生成九宫格图片"""
    async def run():
        redis_manager = get_video_project_manager()

        try:
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                task_id=self.request.id,
            )

            logger.info(f"开始为分镜{shot_no}生成九宫格图片，剧本ID: {script_id}")

            from app.agent.generateVideo.grid_image_agent import GridImageAgent
            from app.agent.generatePic.llm_client import HelloAgentsLLM
            from app.agent.generateVideo.seed_manager import generate_global_seed

            llm_client = HelloAgentsLLM()
            global_seed = generate_global_seed(script_id, user_seed)
            logger.info(f"九宫格图片生成，全局seed: {global_seed}")

            grid_agent = GridImageAgent(llm_client, script_id)

            result = grid_agent.generate_grid_image(
                shot_no=shot_no,
                shot_script_text=shot_script_text,
                scene_group_no=scene_group_no,
                script_id=script_id,
                global_seed=global_seed,
            )

            if result.get("error"):
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.FAILED,
                    error_message=result["error"],
                )
                return result

            grid_image_path = result.get("grid_image_path", "")
            if grid_image_path:
                update_success = update_shot_grid_image_in_db(
                    script_id=script_id,
                    shot_no=shot_no,
                    grid_image_path=grid_image_path,
                )
                if not update_success:
                    logger.warning(f"九宫格图片路径更新到数据库失败")

            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.WAITING_REVIEW,
            )

            logger.info(f"分镜{shot_no}九宫格图片生成完成，剧本ID: {script_id}")

            return {
                "success": True,
                "script_id": script_id,
                "shot_no": shot_no,
                "grid_image_path": grid_image_path,
                "message": f"分镜{shot_no}九宫格图片生成完成",
            }

        except Exception as e:
            logger.error(f"九宫格图片生成失败: {str(e)}")

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
def generate_first_frame_image_task(
    self,
    script_id: str,
    shot_no: int,
    shot_script_text: str,
    scene_group_no: int,
    script_name: str,
    user_id: str,
    user_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """为指定分镜生成首帧图"""
    async def run():
        redis_manager = get_video_project_manager()

        try:
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                task_id=self.request.id,
            )

            logger.info(f"开始为分镜{shot_no}生成首帧图，剧本ID: {script_id}")

            from app.agent.generateVideo.first_and_last_frame_agent import FirstAndLastFrameAgent
            from app.agent.generatePic.llm_client import HelloAgentsLLM
            from app.agent.generateVideo.seed_manager import generate_global_seed

            llm_client = HelloAgentsLLM()
            global_seed = generate_global_seed(script_id, user_seed)
            logger.info(f"首帧图生成，全局seed: {global_seed}")

            first_frame_agent = FirstAndLastFrameAgent(llm_client, script_id)

            result = first_frame_agent.generate_first_frame_image(
                shot_no=shot_no,
                shot_script_text=shot_script_text,
                scene_group_no=scene_group_no,
                script_id=script_id,
                script_name=script_name,
                global_seed=global_seed,
            )

            if result.get("error"):
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.FAILED,
                    error_message=result["error"],
                )
                return result

            first_frame_image_path = result.get("first_frame_image_path", "")
            if first_frame_image_path:
                update_success = update_shot_first_frame_image_in_db(
                    script_id=script_id,
                    shot_no=shot_no,
                    first_frame_image_path=first_frame_image_path,
                )
                if not update_success:
                    logger.warning(f"首帧图路径更新到数据库失败")

            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.WAITING_REVIEW,
            )

            logger.info(f"分镜{shot_no}首帧图生成完成，剧本ID: {script_id}")

            return {
                "success": True,
                "script_id": script_id,
                "shot_no": shot_no,
                "first_frame_image_path": first_frame_image_path,
                "message": f"分镜{shot_no}首帧图生成完成",
            }

        except Exception as e:
            logger.error(f"首帧图生成失败: {str(e)}")

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
def generate_first_and_last_frame_task(
    self,
    script_id: str,
    shot_no: int,
    shot_script_text: str,
    scene_group_no: int,
    script_name: str,
    user_id: str,
    is_first_in_scene_group: bool,
    user_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """为指定分镜生成首帧图和尾帧图"""
    async def run():
        redis_manager = get_video_project_manager()

        try:
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                task_id=self.request.id,
            )

            logger.info(f"开始为分镜{shot_no}生成首帧+尾帧图，剧本ID: {script_id}")

            from app.agent.generateVideo.first_and_last_frame_agent import FirstAndLastFrameAgent
            from app.agent.generatePic.llm_client import HelloAgentsLLM
            from app.agent.generateVideo.seed_manager import generate_global_seed

            llm_client = HelloAgentsLLM()
            global_seed = generate_global_seed(script_id, user_seed)
            logger.info(f"首帧+尾帧生成，全局seed: {global_seed}")

            agent = FirstAndLastFrameAgent(llm_client, script_id)

            first_frame_image_path = ""

            if is_first_in_scene_group:
                ff_result = agent.generate_first_frame_image(
                    shot_no=shot_no,
                    shot_script_text=shot_script_text,
                    scene_group_no=scene_group_no,
                    script_id=script_id,
                    script_name=script_name,
                    global_seed=global_seed,
                )
                if ff_result.get("error"):
                    await redis_manager.update_project_state(
                        project_id=script_id,
                        status=ProjectStatus.FAILED,
                        error_message=ff_result["error"],
                    )
                    return ff_result
                first_frame_image_path = ff_result.get("first_frame_image_path", "")
            else:
                prev_shot = get_prev_shot_in_scene_group(
                    script_id=script_id,
                    scene_group_no=scene_group_no,
                    shot_no=shot_no,
                )

                if prev_shot:
                    first_frame_image_path = copy_last_frame_as_first_frame(
                        script_id=script_id,
                        prev_shot_no=prev_shot.shot_no,
                        current_shot_no=shot_no,
                        scene_group_no=scene_group_no,
                        script_name=script_name,
                    )
                else:
                    logger.warning(f"未找到场景组{scene_group_no}中分镜{shot_no}的上一条分镜，回退为生成首帧图")
                    ff_result = agent.generate_first_frame_image(
                        shot_no=shot_no,
                        shot_script_text=shot_script_text,
                        scene_group_no=scene_group_no,
                        script_id=script_id,
                        script_name=script_name,
                        global_seed=global_seed,
                    )
                    first_frame_image_path = ff_result.get("first_frame_image_path", "")

            if first_frame_image_path:
                update_shot_first_frame_image_in_db(
                    script_id=script_id,
                    shot_no=shot_no,
                    first_frame_image_path=first_frame_image_path,
                )

            lf_result = agent.generate_last_frame_image(
                shot_no=shot_no,
                shot_script_text=shot_script_text,
                scene_group_no=scene_group_no,
                script_id=script_id,
                script_name=script_name,
                first_frame_image_path=first_frame_image_path,
                global_seed=global_seed,
            )

            last_frame_image_path = lf_result.get("last_frame_image_path", "")

            if last_frame_image_path:
                update_shot_last_frame_image_in_db(
                    script_id=script_id,
                    shot_no=shot_no,
                    last_frame_image_path=last_frame_image_path,
                )

            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.WAITING_REVIEW,
            )

            logger.info(f"分镜{shot_no}首帧+尾帧图生成完成，剧本ID: {script_id}")

            return {
                "success": True,
                "script_id": script_id,
                "shot_no": shot_no,
                "first_frame_image_path": first_frame_image_path,
                "last_frame_image_path": last_frame_image_path,
                "message": f"分镜{shot_no}首帧+尾帧图生成完成",
            }

        except Exception as e:
            logger.error(f"首帧+尾帧图生成失败: {str(e)}")
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
def generate_video_task(
    self,
    script_id: str,
    shot_no: int,
    shotlist_text: str,
    script_name: str,
    user_id: str,
    user_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """为指定分镜生成视频"""
    async def run():
        redis_manager = get_video_project_manager()

        try:
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                task_id=self.request.id,
            )

            logger.info(f"开始为分镜{shot_no}生成视频，剧本ID: {script_id}")

            from app.agent.generateVideo.video_generation_agent import VideoGenerationAgent
            from app.agent.generatePic.llm_client import HelloAgentsLLM
            from app.agent.generateVideo.seed_manager import generate_global_seed

            llm_client = HelloAgentsLLM()
            global_seed = generate_global_seed(script_id, user_seed)
            logger.info(f"视频生成，全局seed: {global_seed}")

            video_agent = VideoGenerationAgent(llm_client, script_id)

            result = video_agent.generate_video(
                shot_no=shot_no,
                shotlist_text=shotlist_text,
                script_id=script_id,
                script_name=script_name,
                global_seed=global_seed,
            )

            if result.get("error"):
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.FAILED,
                    error_message=result["error"],
                )
                return result

            video_path = result.get("video_path", "")
            if video_path:
                update_success = update_shot_video_path_in_db(
                    script_id=script_id,
                    shot_no=shot_no,
                    video_path=video_path,
                )
                if not update_success:
                    logger.warning(f"视频路径更新到数据库失败")

            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.WAITING_REVIEW,
            )

            logger.info(f"分镜{shot_no}视频生成完成，剧本ID: {script_id}")

            return {
                "success": True,
                "script_id": script_id,
                "shot_no": shot_no,
                "video_path": video_path,
                "message": f"分镜{shot_no}视频生成完成",
            }

        except Exception as e:
            logger.error(f"视频生成失败: {str(e)}")

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
def generate_video_from_first_frame_task(
    self,
    script_id: str,
    shot_no: int,
    shotlist_text: str,
    script_name: str,
    user_id: str,
    user_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """基于首帧图生成视频（seedance模型）"""
    async def run():
        redis_manager = get_video_project_manager()

        try:
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                task_id=self.request.id,
            )

            logger.info(f"开始为分镜{shot_no}基于首帧图生成视频（seedance），剧本ID: {script_id}")

            from app.agent.generateVideo.video_generation_agent import VideoGenerationAgent
            from app.agent.generatePic.llm_client import HelloAgentsLLM
            from app.agent.generateVideo.seed_manager import generate_global_seed

            llm_client = HelloAgentsLLM()
            global_seed = generate_global_seed(script_id, user_seed)
            logger.info(f"seedance视频生成，全局seed: {global_seed}")

            video_agent = VideoGenerationAgent(llm_client, script_id)

            result = video_agent.generate_video_from_first_frame(
                shot_no=shot_no,
                shotlist_text=shotlist_text,
                script_id=script_id,
                script_name=script_name,
                global_seed=global_seed,
            )

            if result.get("error"):
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.FAILED,
                    error_message=result["error"],
                )
                return result

            video_path = result.get("video_path", "")
            if video_path:
                update_success = update_shot_video_path_in_db(
                    script_id=script_id,
                    shot_no=shot_no,
                    video_path=video_path,
                )
                if not update_success:
                    logger.warning(f"seedance视频路径更新到数据库失败")

            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.WAITING_REVIEW,
            )

            logger.info(f"分镜{shot_no}seedance视频生成完成，剧本ID: {script_id}")

            return {
                "success": True,
                "script_id": script_id,
                "shot_no": shot_no,
                "video_path": video_path,
                "message": f"分镜{shot_no}seedance视频生成完成",
            }

        except Exception as e:
            logger.error(f"seedance视频生成失败: {str(e)}")

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
def generate_video_from_first_and_last_frame_task(
    self,
    script_id: str,
    shot_no: int,
    shotlist_text: str,
    script_name: str,
    user_id: str,
    user_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """基于首尾帧图生成视频（seedance模型）"""
    async def run():
        redis_manager = get_video_project_manager()

        try:
            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.RUNNING,
                task_id=self.request.id,
            )

            logger.info(f"开始为分镜{shot_no}基于首尾帧生成视频（seedance），剧本ID: {script_id}")

            from app.agent.generateVideo.video_generation_agent import VideoGenerationAgent
            from app.agent.generatePic.llm_client import HelloAgentsLLM
            from app.agent.generateVideo.seed_manager import generate_global_seed

            llm_client = HelloAgentsLLM()
            global_seed = generate_global_seed(script_id, user_seed)
            logger.info(f"seedance首尾帧视频生成，全局seed: {global_seed}")

            video_agent = VideoGenerationAgent(llm_client, script_id)

            result = video_agent.generate_video_from_first_and_last_frame(
                shot_no=shot_no,
                shotlist_text=shotlist_text,
                script_id=script_id,
                script_name=script_name,
                global_seed=global_seed,
            )

            if result.get("error"):
                await redis_manager.update_project_state(
                    project_id=script_id,
                    status=ProjectStatus.FAILED,
                    error_message=result["error"],
                )
                return result

            video_path = result.get("video_path", "")
            if video_path:
                update_success = update_shot_video_path_in_db(
                    script_id=script_id,
                    shot_no=shot_no,
                    video_path=video_path,
                )
                if not update_success:
                    logger.warning(f"seedance首尾帧视频路径更新到数据库失败")

            await redis_manager.update_project_state(
                project_id=script_id,
                status=ProjectStatus.WAITING_REVIEW,
            )

            logger.info(f"分镜{shot_no}seedance首尾帧视频生成完成，剧本ID: {script_id}")

            return {
                "success": True,
                "script_id": script_id,
                "shot_no": shot_no,
                "video_path": video_path,
                "message": f"分镜{shot_no}seedance首尾帧视频生成完成",
            }

        except Exception as e:
            logger.error(f"seedance首尾帧视频生成失败: {str(e)}")

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
# 便捷启动函数（供 API 层调用）
# ============================================================================

def start_video_generation(
    script_id: str,
    script_name: str,
    script_content: str,
    user_id: str,
    db_uri: Optional[str] = None,
    user_seed: Optional[int] = None,
) -> str:
    """启动文生视频生成任务，返回任务ID"""
    task = generate_video_workflow_task.delay(
        script_id=script_id,
        script_name=script_name,
        script_content=script_content,
        user_id=user_id,
        db_uri=db_uri,
        user_seed=user_seed,
    )
    return task.id
