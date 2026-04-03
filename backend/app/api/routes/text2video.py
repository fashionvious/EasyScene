"""
文生视频API接口
提供剧本创建、角色信息生成、分镜头脚本生成的API端点
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
import uuid
import os
import logging

from sqlmodel import Session, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    Script,
    ScriptCreate,
    CharacterInfo,
    CharacterInfoCreate,
    ShotScript,
    ShotScriptCreate,
)
from app.agent.utils.redis import (
    get_video_project_manager,
    ProjectStage,
    ProjectStatus,
    TaskStatus,
)
from app.agent.generateVideo.tasks import start_video_generation, get_task_status

router = APIRouter(tags=["text2video"])
logger = logging.getLogger(__name__)


# --- 请求模型 ---

class CreateScriptRequest(BaseModel):
    """创建剧本请求"""
    script_name: str = Field(..., min_length=1, max_length=30)
    script_content: str = Field(..., min_length=10, max_length=500)


class UpdateSingleCharacterRequest(BaseModel):
    """更新单个角色信息请求"""
    role_name: str = Field(..., min_length=1, max_length=255)
    role_desc: str = Field(..., min_length=1)


class UpdateShotsRequest(BaseModel):
    """更新分镜头脚本请求"""
    shot_scripts: List[dict]


class ConfirmCharacterThreeViewRequest(BaseModel):
    """确认角色四视图请求"""
    character_id: str


# --- 响应模型 ---

class CreateScriptResponse(BaseModel):
    """创建剧本响应"""
    script_id: str
    script_name: str
    message: str


class CharacterInfoResponse(BaseModel):
    """角色信息响应"""
    id: str
    role_name: str
    role_desc: str
    three_view_image_path: Optional[str] = None


class SingleCharacterResponse(BaseModel):
    """单个角色信息响应"""
    id: str
    role_name: str
    role_desc: str
    message: str


class ConfirmThreeViewResponse(BaseModel):
    """确认角色四视图响应"""
    id: str
    role_name: str
    role_desc: str
    three_view_image_path: str
    message: str


class ShotScriptResponse(BaseModel):
    """分镜头脚本响应"""
    id: str
    shot_no: int
    total_script: str


class ScriptStatusResponse(BaseModel):
    """剧本状态响应"""
    script_id: str
    script_name: str
    script_content: str
    status: int
    characters: List[CharacterInfoResponse]
    shot_scripts: List[ShotScriptResponse]
    is_generating_characters: bool
    is_generating_shots: bool


class UpdateShotsResponse(BaseModel):
    """更新分镜头脚本响应"""
    message: str
    shot_scripts: List[ShotScriptResponse]


class ScriptListItem(BaseModel):
    """剧本列表项"""
    id: str
    script_name: str
    status: int
    create_time: datetime


class ScriptListResponse(BaseModel):
    """剧本列表响应"""
    scripts: List[ScriptListItem]
    total: int


# --- API端点 ---

@router.get("/text2video/scripts", response_model=ScriptListResponse)
async def api_get_scripts(
    session: SessionDep,
    current_user: CurrentUser,
):
    """
    获取当前用户的所有剧本列表
    
    返回:
    - scripts: 剧本列表
    - total: 总数
    """
    try:
        # 查询当前用户的所有剧本（未删除的）
        statement = select(Script).where(
            Script.creator_id == current_user.id,
            Script.is_deleted == 0
        ).order_by(Script.create_time.desc())
        scripts = session.exec(statement).all()
        
        return ScriptListResponse(
            scripts=[
                ScriptListItem(
                    id=str(s.id),
                    script_name=s.script_name,
                    status=s.status,
                    create_time=s.create_time,
                )
                for s in scripts
            ],
            total=len(scripts),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取剧本列表失败: {str(e)}")


@router.post("/text2video/create-script", response_model=CreateScriptResponse)
async def api_create_script(
    request: CreateScriptRequest,
    session: SessionDep,
    current_user: CurrentUser,
):
    """
    创建新剧本
    
    参数:
    - script_name: 剧本名称（1-30字）
    - script_content: 剧本内容（10-500字）
    
    返回:
    - script_id: 剧本ID
    - script_name: 剧本名称
    - message: 提示信息
    """
    redis_manager = get_video_project_manager()
    
    try:
        # 创建剧本记录
        script = Script(
            script_name=request.script_name,
            script_content=request.script_content,
            status=0,  # draft
            share_perm=0,  # private
            creator_id=current_user.id,
            last_editor_id=current_user.id,
        )
        session.add(script)
        session.commit()
        session.refresh(script)
        
        script_id = str(script.id)
        
        # 在Redis中创建项目状态
        await redis_manager.create_project(
            user_id=str(current_user.id),
            project_id=script_id,
            metadata={
                "script_name": request.script_name,
                "script_content": request.script_content,
            }
        )
        
        # 触发Celery任务异步生成角色信息和分镜头脚本
        db_uri = os.getenv("DATABASE_URL", "postgresql://postgres:changethis@localhost:5432/app")
        task_id = start_video_generation(
            script_id=script_id,
            script_name=request.script_name,
            script_content=request.script_content,
            user_id=str(current_user.id),
            db_uri=db_uri,
        )
        
        # 更新Redis中的任务ID
        await redis_manager.update_project_state(
            project_id=script_id,
            task_id=task_id,
        )
        
        return CreateScriptResponse(
            script_id=script_id,
            script_name=script.script_name,
            message="剧本创建成功，正在生成角色信息和分镜头脚本",
        )
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"创建剧本失败: {str(e)}")
    finally:
        await redis_manager.close()


@router.get("/text2video/script-status/{script_id}", response_model=ScriptStatusResponse)
async def api_get_script_status(
    script_id: str,
    session: SessionDep,
    current_user: CurrentUser,
):
    """
    获取剧本状态
    
    参数:
    - script_id: 剧本ID
    
    返回:
    - 剧本状态信息，包括角色信息和分镜头脚本
    """
    redis_manager = get_video_project_manager()
    
    try:
        # 查询剧本
        script_uuid = uuid.UUID(script_id)
        script = session.get(Script, script_uuid)
        
        if not script:
            raise HTTPException(status_code=404, detail="剧本不存在")
        
        # 检查权限
        if script.creator_id != current_user.id and script.share_perm == 0:
            raise HTTPException(status_code=403, detail="无权访问此剧本")
        
        # 查询角色信息
        characters_statement = select(CharacterInfo).where(
            CharacterInfo.script_id == script_uuid,
            CharacterInfo.is_deleted == 0
        )
        characters = session.exec(characters_statement).all()
        
        # 查询分镜头脚本
        shots_statement = select(ShotScript).where(
            ShotScript.script_id == script_uuid,
            ShotScript.is_deleted == 0
        ).order_by(ShotScript.shot_no)
        shot_scripts = session.exec(shots_statement).all()
        
        # 从Redis获取生成状态
        project_state = await redis_manager.get_project_state(script_id)

        is_generating_characters = False
        is_generating_shots = False

        if project_state:
            # 添加调试日志
            logger.info(f"[API] 项目状态: stage={project_state.current_stage}, status={project_state.current_status}")
            print(f"[DEBUG] 项目状态: stage={project_state.current_stage}, status={project_state.current_status}")

            # 检查是否正在生成角色信息
            if project_state.current_stage == ProjectStage.CHAR_DESC:
                if project_state.current_status in [ProjectStatus.RUNNING, ProjectStatus.INITIALIZED]:
                    is_generating_characters = True
                    logger.info(f"[API] 正在生成角色信息")
                    print(f"[DEBUG] 正在生成角色信息")

            # 检查是否正在生成分镜头脚本
            if project_state.current_stage == ProjectStage.SHOTLIST_SCRIPT:
                if project_state.current_status == ProjectStatus.RUNNING:
                    is_generating_shots = True
                    logger.info(f"[API] 正在生成分镜头脚本")
                    print(f"[DEBUG] 正在生成分镜头脚本")
        else:
            logger.warning(f"[API] Redis 中没有项目状态，script_id={script_id}")
            print(f"[DEBUG] Redis 中没有项目状态，script_id={script_id}")
        
        # 添加更多调试信息
        logger.info(f"[API] 返回状态: is_generating_characters={is_generating_characters}, is_generating_shots={is_generating_shots}, characters_count={len(characters)}, shots_count={len(shot_scripts)}")
        
        return ScriptStatusResponse(
            script_id=str(script.id),
            script_name=script.script_name,
            script_content=script.script_content,
            status=script.status,
            characters=[
                CharacterInfoResponse(
                    id=str(c.id),
                    role_name=c.role_name,
                    role_desc=c.role_desc,
                    three_view_image_path=c.three_view_image_path,
                )
                for c in characters
            ],
            shot_scripts=[
                ShotScriptResponse(
                    id=str(s.id),
                    shot_no=s.shot_no,
                    total_script=s.total_script,
                )
                for s in shot_scripts
            ],
            is_generating_characters=is_generating_characters,
            is_generating_shots=is_generating_shots,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的剧本ID")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取剧本状态失败: {str(e)}")
    finally:
        await redis_manager.close()


@router.patch("/text2video/character/{character_id}", response_model=SingleCharacterResponse)
async def api_update_single_character(
    character_id: str,
    request: UpdateSingleCharacterRequest,
    session: SessionDep,
    current_user: CurrentUser,
):
    """
    更新单个角色信息并恢复HITL工作流
    
    参数:
    - character_id: 角色ID
    - role_name: 角色名称
    - role_desc: 角色描述
    
    返回:
    - id: 角色ID
    - role_name: 更新后的角色名称
    - role_desc: 更新后的角色描述
    - message: 提示信息
    """
    redis_manager = get_video_project_manager()
    
    try:
        # 查询角色信息
        character_uuid = uuid.UUID(character_id)
        character = session.get(CharacterInfo, character_uuid)
        
        if not character:
            raise HTTPException(status_code=404, detail="角色不存在")
        
        # 检查是否已删除
        if character.is_deleted == 1:
            raise HTTPException(status_code=404, detail="角色不存在")
        
        # 通过角色关联查询剧本，进行权限校验
        script = session.get(Script, character.script_id)
        
        if not script:
            raise HTTPException(status_code=404, detail="关联的剧本不存在")
        
        # 权限校验：确保当前用户是该剧本的创建者
        if script.creator_id != current_user.id:
            raise HTTPException(status_code=403, detail="无权修改此角色")
        
        # 更新角色信息
        character.role_name = request.role_name
        character.role_desc = request.role_desc
        character.update_time = datetime.utcnow()
        session.add(character)
        
        # 更新剧本的最后编辑时间和编辑者
        script.last_editor_id = current_user.id
        script.update_time = datetime.utcnow()
        session.add(script)
        
        session.commit()
        session.refresh(character)
        
        # 触发HITL恢复任务
        from app.agent.generateVideo.tasks import resume_character_review_task
        
        # 构建数据库连接字符串
        postgres_server = os.getenv("POSTGRES_SERVER", "localhost")
        postgres_port = os.getenv("POSTGRES_PORT", "5432")
        postgres_db = os.getenv("POSTGRES_DB", "app")
        postgres_user = os.getenv("POSTGRES_USER", "postgres")
        postgres_password = os.getenv("POSTGRES_PASSWORD", "changethis")
        db_uri = f"postgresql://{postgres_user}:{postgres_password}@{postgres_server}:{postgres_port}/{postgres_db}"
        
        # 调用Celery任务恢复工作流
        # 检查角色是否已有四视图，如果有则强制重新生成
        has_existing_three_view = bool(character.three_view_image_path)

        task = resume_character_review_task.delay(
            script_id=str(script.id),
            character={
                "role_name": request.role_name,
                "role_desc": request.role_desc,
            },
            user_id=str(current_user.id),
            db_uri=db_uri,
            force_regenerate=has_existing_three_view,  # 如果已有四视图，则强制重新生成
        )
        
        # 更新Redis状态
        await redis_manager.update_project_state(
            project_id=str(script.id),
            status=ProjectStatus.RUNNING,
            task_id=task.id,
        )
        
        return SingleCharacterResponse(
            id=str(character.id),
            role_name=character.role_name,
            role_desc=character.role_desc,
            message="角色信息更新成功，正在生成四视图",
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的角色ID")
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"更新角色信息失败: {str(e)}")
    finally:
        await redis_manager.close()


@router.post("/text2video/confirm-character-three-view", response_model=ConfirmThreeViewResponse)
async def api_confirm_character_three_view(
    request: ConfirmCharacterThreeViewRequest,
    session: SessionDep,
    current_user: CurrentUser,
):
    """
    确认角色四视图并恢复HITL工作流
    
    参数:
    - character_id: 角色ID
    
    返回:
    - id: 角色ID
    - role_name: 角色名称
    - role_desc: 角色描述
    - three_view_image_path: 四视图路径
    - message: 提示信息
    """
    redis_manager = get_video_project_manager()
    
    try:
        # 查询角色信息
        character_uuid = uuid.UUID(request.character_id)
        character = session.get(CharacterInfo, character_uuid)
        
        if not character:
            raise HTTPException(status_code=404, detail="角色不存在")
        
        # 检查是否已删除
        if character.is_deleted == 1:
            raise HTTPException(status_code=404, detail="角色不存在")
        
        # 检查是否有四视图
        if not character.three_view_image_path:
            raise HTTPException(status_code=400, detail="角色还没有生成四视图")
        
        # 通过角色关联查询剧本，进行权限校验
        script = session.get(Script, character.script_id)
        
        if not script:
            raise HTTPException(status_code=404, detail="关联的剧本不存在")
        
        # 权限校验：确保当前用户是该剧本的创建者
        if script.creator_id != current_user.id:
            raise HTTPException(status_code=403, detail="无权确认此角色")
        
        # 不再修改图片文件名，因为每个角色的四视图最终只保存一张
        # 直接使用当前的图片路径
        
        # 更新剧本的最后编辑时间和编辑者
        script.last_editor_id = current_user.id
        script.update_time = datetime.utcnow()
        session.add(script)
        
        session.commit()
        session.refresh(character)
        
        # 触发HITL恢复任务
        from app.agent.generateVideo.tasks import resume_character_review_task
        
        # 构建数据库连接字符串
        postgres_server = os.getenv("POSTGRES_SERVER", "localhost")
        postgres_port = os.getenv("POSTGRES_PORT", "5432")
        postgres_db = os.getenv("POSTGRES_DB", "app")
        postgres_user = os.getenv("POSTGRES_USER", "postgres")
        postgres_password = os.getenv("POSTGRES_PASSWORD", "changethis")
        db_uri = f"postgresql://{postgres_user}:{postgres_password}@{postgres_server}:{postgres_port}/{postgres_db}"
        
        # 调用Celery任务恢复工作流
        task = resume_character_review_task.delay(
            script_id=str(script.id),
            character={
                "role_name": character.role_name,
                "role_desc": character.role_desc,
            },
            user_id=str(current_user.id),
            db_uri=db_uri,
        )
        
        # 更新Redis状态
        await redis_manager.update_project_state(
            project_id=str(script.id),
            status=ProjectStatus.RUNNING,
            task_id=task.id,
        )
        
        return ConfirmThreeViewResponse(
            id=str(character.id),
            role_name=character.role_name,
            role_desc=character.role_desc,
            three_view_image_path=character.three_view_image_path,
            message="角色四视图已确认，继续处理下一个角色",
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的角色ID")
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"确认角色四视图失败: {str(e)}")
    finally:
        await redis_manager.close()


@router.post("/text2video/update-shots/{script_id}", response_model=UpdateShotsResponse)
async def api_update_shots(
    script_id: str,
    request: UpdateShotsRequest,
    session: SessionDep,
    current_user: CurrentUser,
):
    """
    更新分镜头脚本
    
    参数:
    - script_id: 剧本ID
    - shot_scripts: 分镜头脚本列表
    
    返回:
    - message: 提示信息
    - shot_scripts: 更新后的分镜头脚本
    """
    redis_manager = get_video_project_manager()
    
    try:
        # 查询剧本
        script_uuid = uuid.UUID(script_id)
        script = session.get(Script, script_uuid)
        
        if not script:
            raise HTTPException(status_code=404, detail="剧本不存在")
        
        # 检查权限
        if script.creator_id != current_user.id and script.share_perm < 2:
            raise HTTPException(status_code=403, detail="无权修改此剧本")
        
        # 删除旧的分镜头脚本
        old_shots_statement = select(ShotScript).where(
            ShotScript.script_id == script_uuid
        )
        old_shots = session.exec(old_shots_statement).all()
        for old_shot in old_shots:
            old_shot.is_deleted = 1
            session.add(old_shot)
        
        # 创建新的分镜头脚本
        new_shots = []
        for idx, shot_data in enumerate(request.shot_scripts):
            shot = ShotScript(
                script_id=script_uuid,
                shot_no=shot_data.get("shot_no", idx + 1),
                total_script=shot_data.get("total_script", ""),
                version=1,
            )
            session.add(shot)
            new_shots.append(shot)
        
        # 更新剧本状态
        if script.status < 2:
            script.status = 2  # storyboard generated
        script.last_editor_id = current_user.id
        script.update_time = datetime.utcnow()
        session.add(script)
        
        session.commit()
        
        # 刷新以获取ID
        for shot in new_shots:
            session.refresh(shot)
        
        # 更新Redis状态
        await redis_manager.complete_project_review(
            project_id=script_id,
            approved=True,
        )
        
        return UpdateShotsResponse(
            message="分镜头脚本更新成功",
            shot_scripts=[
                ShotScriptResponse(
                    id=str(s.id),
                    shot_no=s.shot_no,
                    total_script=s.total_script,
                )
                for s in new_shots
            ],
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的剧本ID")
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"更新分镜头脚本失败: {str(e)}")
    finally:
        await redis_manager.close()
