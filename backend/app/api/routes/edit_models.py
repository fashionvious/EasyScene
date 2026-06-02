"""编辑任务 API 的请求/响应 Pydantic 模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ============================================================================
# POST /edit/start
# ============================================================================


class StartEditRequest(BaseModel):
    storyboard: dict = Field(..., description="LLM 生成的分镜 JSON")
    script_id: str = Field(..., description="关联的剧本 ID")


class StartEditResponse(BaseModel):
    task_id: str
    status: str
    total_steps: int


# ============================================================================
# GET /edit/{task_id}/progress
# ============================================================================


class TaskProgressResponse(BaseModel):
    task_id: str
    status: str
    current_step: int
    total_steps: int
    steps_done: int
    steps_failed: int
    progress_pct: float
    current_step_name: str | None = None
    error_message: str | None = None


# ============================================================================
# GET /edit/{task_id}/steps
# ============================================================================


class StepItem(BaseModel):
    id: str
    step_index: int
    tool_name: str
    status: str
    error: str | None = None
    retry_count: int = 0
    started_at: str | None = None
    finished_at: str | None = None


class StepListResponse(BaseModel):
    task_id: str
    steps: list[StepItem]


# ============================================================================
# GET /edit/pending
# ============================================================================


class PendingTaskItem(BaseModel):
    task_id: str
    script_id: str
    status: str
    error_message: str | None = None
