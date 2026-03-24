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


# --- 请求模型 ---

class CreateScriptRequest(BaseModel):
    """创建剧本请求"""
    script_name: str = Field(..., min_length=1, max_length=30)
    script_content: str = Field(..., min_length=10, max_length=500)


class UpdateCharactersRequest(BaseModel):
    """更新角色信息请求"""
    characters: List[dict]


class UpdateShotsRequest(BaseModel):
    """更新分镜头脚本请求"""
    shot_scripts: List[dict]


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


class UpdateCharactersResponse(BaseModel):
    """更新角色信息响应"""
    message: str
    characters: List[CharacterInfoResponse]


class UpdateShotsResponse(BaseModel):
    """更新分镜头脚本响应"""
    message: str
    shot_scripts: List[ShotScriptResponse]


# --- API端点 ---

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
            # 检查是否正在生成角色信息
            if project_state.current_stage == ProjectStage.CHAR_DESC:
                if project_state.current_status in [ProjectStatus.RUNNING, ProjectStatus.INITIALIZED]:
                    is_generating_characters = True
            
            # 检查是否正在生成分镜头脚本
            if project_state.current_stage == ProjectStage.SHOTLIST_SCRIPT:
                if project_state.current_status == ProjectStatus.RUNNING:
                    is_generating_shots = True
        
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


@router.post("/text2video/update-characters/{script_id}", response_model=UpdateCharactersResponse)
async def api_update_characters(
    script_id: str,
    request: UpdateCharactersRequest,
    session: SessionDep,
    current_user: CurrentUser,
):
    """
    更新角色信息
    
    参数:
    - script_id: 剧本ID
    - characters: 角色信息列表
    
    返回:
    - message: 提示信息
    - characters: 更新后的角色信息
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
        
        # 删除旧的角色信息
        old_characters_statement = select(CharacterInfo).where(
            CharacterInfo.script_id == script_uuid
        )
        old_characters = session.exec(old_characters_statement).all()
        for old_char in old_characters:
            old_char.is_deleted = 1
            session.add(old_char)
        
        # 创建新的角色信息
        new_characters = []
        for char_data in request.characters:
            char = CharacterInfo(
                script_id=script_uuid,
                role_name=char_data.get("role_name", ""),
                role_desc=char_data.get("role_desc", ""),
                version=1,
            )
            session.add(char)
            new_characters.append(char)
        
        # 更新剧本状态
        if script.status == 0:
            script.status = 1  # characters generated
        script.last_editor_id = current_user.id
        script.update_time = datetime.utcnow()
        session.add(script)
        
        session.commit()
        
        # 刷新以获取ID
        for char in new_characters:
            session.refresh(char)
        
        # 更新Redis状态
        await redis_manager.complete_project_review(
            project_id=script_id,
            approved=True,
        )
        
        return UpdateCharactersResponse(
            message="角色信息更新成功",
            characters=[
                CharacterInfoResponse(
                    id=str(c.id),
                    role_name=c.role_name,
                    role_desc=c.role_desc,
                )
                for c in new_characters
            ],
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的剧本ID")
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"更新角色信息失败: {str(e)}")
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
