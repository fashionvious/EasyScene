"""
编辑任务 API 路由

端点：start / progress / steps / pause / resume / cancel /
       retry-step / skip-step / pending / WebSocket
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import CurrentUser, SessionDep
from app.api.routes.edit_models import (
    StartEditRequest, StartEditResponse, TaskProgressResponse,
    StepItem, StepListResponse, PendingTaskItem,
)
from app import crud
from app.models import EditTaskCreate, EditStepPublic

router = APIRouter(prefix="/edit", tags=["edit"])

# ============================================================================
# POST /edit/start
# ============================================================================


@router.post("/start", response_model=StartEditResponse)
def start_edit(
    body: StartEditRequest,
    current_user: CurrentUser,
    session: SessionDep,
):
    """
    发起编辑任务。

    接收 LLM 生成的分镜 JSON，验证后持久化到 PG。
    异步执行由后台 StepOrchestrator 驱动（完整编排在后续需求中启动）。
    """
    from app.agent.skills_agent.storyboard_parser import StoryboardParser

    # 1. 校验分镜方案结构
    parser = StoryboardParser()
    errors = parser.validate(body.storyboard)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    # 2. 解析为 EditPlan（用于提取 steps 元数据）
    try:
        plan = parser.parse(
            body.storyboard, str(current_user.id), body.script_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # 3. 持久化 EditTask
    task_in = EditTaskCreate(
        user_id=current_user.id,
        script_id=uuid.UUID(body.script_id),
        total_steps=len(plan.steps),
        project_state_json=json.dumps(plan.project_state),
        status="planning",
    )
    task = crud.create_edit_task(session=session, task_in=task_in)

    # 4. 持久化所有步骤
    for i, step_spec in enumerate(plan.steps):
        crud.upsert_edit_step(
            session=session,
            task_id=task.id,
            step_index=i,
            step_data={
                "tool_name": step_spec.tool,
                "args_json": json.dumps(step_spec.args),
                "status": "pending",
            },
        )

    # 5. 启动编排器后台执行（当前阶段仅状态标记，完整实现在 req_09/req_03 联调后）
    # 编排器通过 set_session_factory + crud 写 PG，通过 redis 写实时状态
    # asyncio.create_task(orchestrator.execute(plan))

    return StartEditResponse(
        task_id=str(task.id),
        status="planning",
        total_steps=len(plan.steps),
    )


# ============================================================================
# GET /edit/{task_id}/progress
# ============================================================================


@router.get("/{task_id}/progress", response_model=TaskProgressResponse)
def get_task_progress(task_id: uuid.UUID, session: SessionDep):
    """获取任务实时进度（从 PG 查询）。"""
    task = crud.get_edit_task(session=session, task_id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    steps = crud.get_steps_by_task(session=session, task_id=task_id)
    done = sum(1 for s in steps if s.status == "done")
    failed = sum(1 for s in steps if s.status == "failed")

    current_step_name = None
    if 0 <= task.current_step < len(steps):
        current_step_name = steps[task.current_step].tool_name

    return TaskProgressResponse(
        task_id=str(task.id),
        status=task.status,
        current_step=task.current_step,
        total_steps=task.total_steps,
        steps_done=done,
        steps_failed=failed,
        progress_pct=round(done / max(task.total_steps, 1) * 100, 1),
        current_step_name=current_step_name,
        error_message=task.error_message,
    )


# ============================================================================
# GET /edit/{task_id}/steps
# ============================================================================


@router.get("/{task_id}/steps", response_model=StepListResponse)
def get_task_steps(task_id: uuid.UUID, session: SessionDep):
    """获取任务所有步骤的详细状态，按 step_index 升序。"""
    task = crud.get_edit_task(session=session, task_id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    steps = crud.get_steps_by_task(session=session, task_id=task_id)

    return StepListResponse(
        task_id=str(task.id),
        steps=[
            StepItem(
                id=str(s.id),
                step_index=s.step_index,
                tool_name=s.tool_name,
                status=s.status,
                error=s.error,
                retry_count=s.retry_count,
                started_at=str(s.started_at) if s.started_at else None,
                finished_at=str(s.finished_at) if s.finished_at else None,
            )
            for s in steps
        ],
    )


# ============================================================================
# POST /edit/{task_id}/pause
# ============================================================================


@router.post("/{task_id}/pause")
def pause_task(task_id: uuid.UUID, current_user: CurrentUser, session: SessionDep):
    """暂停任务。"""
    ok = crud.update_edit_task_status(
        session=session, task_id=task_id, status="paused",
    )
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task_id": str(task_id), "status": "paused"}


# ============================================================================
# POST /edit/{task_id}/resume
# ============================================================================


@router.post("/{task_id}/resume")
def resume_task(task_id: uuid.UUID, current_user: CurrentUser, session: SessionDep):
    """继续暂停的任务。"""
    task = crud.get_edit_task(session=session, task_id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    crud.update_edit_task_status(
        session=session, task_id=task_id, status="running",
    )
    return {"task_id": str(task_id), "status": "running"}


# ============================================================================
# POST /edit/{task_id}/cancel
# ============================================================================


@router.post("/{task_id}/cancel")
def cancel_task(task_id: uuid.UUID, current_user: CurrentUser, session: SessionDep):
    """取消任务。"""
    ok = crud.update_edit_task_status(
        session=session, task_id=task_id, status="failed",
    )
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"task_id": str(task_id), "status": "cancelled"}


# ============================================================================
# POST /edit/{task_id}/retry-step
# ============================================================================


@router.post("/{task_id}/retry-step")
def retry_step(
    task_id: uuid.UUID, step_index: int, current_user: CurrentUser, session: SessionDep,
):
    """重试失败的步骤（重置为 pending）。"""
    crud.upsert_edit_step(
        session=session, task_id=task_id, step_index=step_index,
        step_data={"status": "pending", "error": None},
    )
    crud.update_edit_task_status(
        session=session, task_id=task_id, status="running",
    )
    return {"task_id": str(task_id), "step_index": step_index, "status": "retrying"}


# ============================================================================
# POST /edit/{task_id}/skip-step
# ============================================================================


@router.post("/{task_id}/skip-step")
def skip_step(
    task_id: uuid.UUID, step_index: int, current_user: CurrentUser, session: SessionDep,
):
    """跳过失败的步骤。"""
    crud.upsert_edit_step(
        session=session, task_id=task_id, step_index=step_index,
        step_data={"status": "skipped"},
    )
    return {"task_id": str(task_id), "step_index": step_index, "status": "skipped"}


# ============================================================================
# GET /edit/pending
# ============================================================================


@router.get("/pending", response_model=list[PendingTaskItem])
def get_pending_tasks(current_user: CurrentUser, session: SessionDep):
    """查询当前用户待决策的编辑任务（暂停/失败状态）。"""
    tasks = crud.get_edit_tasks_by_user(session=session, user_id=current_user.id)
    return [
        PendingTaskItem(
            task_id=str(t.id),
            script_id=str(t.script_id),
            status=t.status,
            error_message=t.error_message,
        )
        for t in tasks
        if t.status in ("paused", "failed")
    ]


# ============================================================================
# WebSocket /ws/edit/{task_id}
# ============================================================================

_ws_router = APIRouter()


@_ws_router.websocket("/ws/edit/{task_id}")
async def websocket_progress(websocket: WebSocket, task_id: str):
    """实时推送任务进度（当前阶段轮询 PG，req_19 升级为 Redis PubSub）。"""
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
            await websocket.send_json({"task_id": task_id, "progress": 0})
    except WebSocketDisconnect:
        pass
