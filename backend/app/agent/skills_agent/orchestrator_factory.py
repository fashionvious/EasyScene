"""
StepOrchestrator 单例工厂 — 将现有依赖注入为完整可用的编排器。

被 videoagent.py 调用，在检测到分镜 JSON 后创建 EditPlan 并启动执行。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_orchestrator: Any = None
_lock = asyncio.Lock()


async def get_or_create_orchestrator() -> Any:
    """获取全局 StepOrchestrator 单例（已注入真实依赖）。"""
    global _orchestrator
    if _orchestrator is not None:
        return _orchestrator

    async with _lock:
        if _orchestrator is not None:
            return _orchestrator

        from app.agent.skills_agent.step_orchestrator import (
            StepOrchestrator, set_session_factory,
        )
        from app.agent.utils.redis import get_edit_task_redis_manager
        from app.core.db import engine
        from sqlmodel import Session

        # 注入 Session 工厂
        set_session_factory(lambda: Session(engine))

        # 获取 ToolRegistry（从 agent 中间件单例）
        from app.agent.skills_agent import get_middleware_instance
        mw = get_middleware_instance()
        tool_registry = mw.registry if mw is not None else None

        # Redis 实时状态管理
        redis_manager = None
        try:
            redis_manager = get_edit_task_redis_manager()
        except Exception:
            logger.warning("Redis 不可用，编辑任务将仅使用 PG 持久化")

        _orchestrator = StepOrchestrator(
            tool_registry=tool_registry,
            task_crud=None,     # 使用内联 crud 模块
            step_crud=None,     # 使用内联 crud 模块
            project_context_factory=None,
            redis_manager=redis_manager,
        )
        logger.info("StepOrchestrator 单例已创建")
        return _orchestrator


async def launch_edit_task(storyboard_json: dict, user_id: str, script_id: str) -> str:
    """解析分镜方案 → 持久化 → 启动后台执行。返回 task_id。"""
    import json
    import uuid as _uuid

    from app.agent.skills_agent.storyboard_parser import StoryboardParser
    from app import crud
    from app.models import EditTaskCreate
    from app.core.db import engine
    from sqlmodel import Session

    parser = StoryboardParser()
    errors = parser.validate(storyboard_json)
    if errors:
        raise ValueError(f"分镜方案校验失败: {errors}")

    plan = parser.parse(storyboard_json, user_id, script_id)

    # 持久化
    with Session(engine) as session:
        task_in = EditTaskCreate(
            user_id=_uuid.UUID(user_id),
            script_id=_uuid.UUID(script_id),
            total_steps=len(plan.steps),
            project_state_json=json.dumps(plan.project_state),
            status="planning",
        )
        task = crud.create_edit_task(session=session, task_in=task_in)

        for i, step_spec in enumerate(plan.steps):
            crud.upsert_edit_step(
                session=session, task_id=task.id, step_index=i,
                step_data={
                    "tool_name": step_spec.tool,
                    "args_json": json.dumps(step_spec.args),
                    "status": "pending",
                },
            )

        task_id = str(task.id)
        plan.task_id = task_id

    # 启动后台执行
    orchestrator = await get_or_create_orchestrator()
    asyncio.create_task(orchestrator.execute(plan))

    logger.info("编辑任务已创建并启动: task_id=%s, steps=%s", task_id, len(plan.steps))
    return task_id
