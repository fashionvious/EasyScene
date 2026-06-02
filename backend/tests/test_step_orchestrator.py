"""
Tests for step_orchestrator.py — StepSpec, EditPlan, StepOrchestrator.

All external deps (ToolRegistry, CRUD, Redis) mocked.
Async methods invoked via asyncio.run() — no pytest-asyncio needed.
"""
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.skills_agent.step_orchestrator import (
    StepSpec, EditPlan, StepOrchestrator,
    StepStatus, TaskStatus, set_session_factory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_tool_registry(tool_specs: list[dict]):
    """Create a mock ToolRegistry with given tool specs."""
    from app.agent.skills_agent.tool_registry import ToolSpec, ToolRegistry

    reg = ToolRegistry()
    for ts in tool_specs:
        spec = ToolSpec(
            name=ts["name"], handler=ts["name"],
            category=ts.get("category", "read"),
            func=ts["func"],
            exec_mode=ts.get("exec_mode", "sync"),
            concurrency_safe=ts.get("concurrency_safe", True),
        )
        reg.register(spec)
    return reg


def _mock_crud():
    """Return a mock CRUD module with async-compatible methods."""
    crud = MagicMock()
    crud.get_edit_task = MagicMock()
    crud.get_edit_tasks_by_user = MagicMock()
    crud.create_edit_task = MagicMock()
    crud.update_edit_task = MagicMock()
    crud.update_edit_task_status = MagicMock()
    crud.upsert_edit_step = MagicMock()
    crud.get_steps_by_task = MagicMock()
    crud.get_step_stats = MagicMock()
    return crud


def _setup_orchestrator(registry=None, task_crud=None, step_crud=None):
    """Create a StepOrchestrator with default mocks."""
    if registry is None:
        registry = _mock_tool_registry([])
    if task_crud is None:
        task_crud = _mock_crud()
    if step_crud is None:
        step_crud = _mock_crud()
    return StepOrchestrator(
        tool_registry=registry,
        task_crud=task_crud,
        step_crud=step_crud,
    )


# ---------------------------------------------------------------------------
# StepSpec / EditPlan — dataclass basics
# ---------------------------------------------------------------------------

class TestDataClasses:
    """StepSpec 和 EditPlan 构造和默认值"""

    def test_stepspec_defaults(self):
        s = StepSpec(tool="test_tool")
        assert s.tool == "test_tool"
        assert s.args == {}
        assert s.status == StepStatus.PENDING
        assert s.retry_count == 0
        assert s.started_at is None
        assert s.finished_at is None

    def test_editplan_defaults(self):
        plan = EditPlan(task_id="t1", user_id="u1", script_id="s1", steps=[])
        assert plan.task_id == "t1"
        assert plan.status == TaskStatus.PLANNING
        assert plan.current_index == 0
        assert plan.project_state == {}

    def test_stepspec_custom_id(self):
        s = StepSpec(tool="t", id="my-id")
        assert s.id == "my-id"


# ---------------------------------------------------------------------------
# StepOrchestrator.execute — happy path
# ---------------------------------------------------------------------------

class TestExecuteHappyPath:
    """正常流程：所有步骤成功执行"""

    def test_execute_all_steps_succeed(self):
        fn1 = MagicMock(return_value="result_1")
        fn2 = MagicMock(return_value="result_2")
        registry = _mock_tool_registry([
            {"name": "tool_a", "func": fn1},
            {"name": "tool_b", "func": fn2},
        ])

        orch = _setup_orchestrator(registry=registry)
        plan = EditPlan(
            task_id="t1", user_id="u1", script_id="s1",
            steps=[
                StepSpec(tool="tool_a", args={"x": 1}),
                StepSpec(tool="tool_b", args={"y": 2}),
            ],
        )

        result = asyncio.run(orch.execute(plan))

        assert result.status == TaskStatus.COMPLETED
        assert result.current_index == 2
        assert result.steps[0].status == StepStatus.DONE
        assert result.steps[0].result == "result_1"
        assert result.steps[1].status == StepStatus.DONE
        assert result.steps[1].result == "result_2"
        fn1.assert_called_once()
        fn2.assert_called_once()

    def test_skips_already_done_steps(self):
        fn1 = MagicMock(return_value="ok")
        registry = _mock_tool_registry([
            {"name": "tool_a", "func": fn1},
            {"name": "tool_b", "func": MagicMock(return_value="y")},
        ])

        orch = _setup_orchestrator(registry=registry)
        plan = EditPlan(
            task_id="t1", user_id="u1", script_id="s1",
            steps=[
                StepSpec(tool="tool_a", status=StepStatus.DONE, result="cached"),
                StepSpec(tool="tool_b"),
            ],
        )

        result = asyncio.run(orch.execute(plan))

        # Step 0 already done → skipped
        fn1.assert_not_called()
        assert result.steps[0].status == StepStatus.DONE
        assert result.steps[1].status == StepStatus.DONE

    def test_empty_plan_completes_immediately(self):
        orch = _setup_orchestrator()
        plan = EditPlan(task_id="t1", user_id="u1", script_id="s1", steps=[])

        result = asyncio.run(orch.execute(plan))

        assert result.status == TaskStatus.COMPLETED
        assert result.current_index == 0


# ---------------------------------------------------------------------------
# StepOrchestrator.execute — failure path
# ---------------------------------------------------------------------------

class TestExecuteFailure:
    """步骤失败时的状态机和错误传播"""

    def test_serial_step_failure_stops_execution(self):
        """非并发安全步骤失败时，后续步骤不执行（串行模式）。"""
        fn_fail = MagicMock(side_effect=ValueError("boom"))
        fn_never = MagicMock()
        registry = _mock_tool_registry([
            {"name": "bad_tool", "func": fn_fail, "concurrency_safe": False},
            {"name": "never_called", "func": fn_never, "concurrency_safe": False},
        ])

        orch = _setup_orchestrator(registry=registry)
        plan = EditPlan(
            task_id="t1", user_id="u1", script_id="s1",
            steps=[
                StepSpec(tool="bad_tool"),
                StepSpec(tool="never_called"),
            ],
        )

        result = asyncio.run(orch.execute(plan))

        assert result.status == TaskStatus.FAILED
        assert result.steps[0].status == StepStatus.FAILED
        assert "boom" in result.steps[0].error
        fn_never.assert_not_called()
        assert result.steps[1].status == StepStatus.PENDING

    def test_concurrent_step_failure_does_not_block_others(self):
        """并发批中某步骤失败，其他步骤正常完成。"""
        fn_fail = MagicMock(side_effect=ValueError("boom"))
        fn_ok = MagicMock(return_value="ok")
        registry = _mock_tool_registry([
            {"name": "bad_tool", "func": fn_fail, "concurrency_safe": True},
            {"name": "good_tool", "func": fn_ok, "concurrency_safe": True},
        ])

        orch = _setup_orchestrator(registry=registry)
        plan = EditPlan(
            task_id="t1", user_id="u1", script_id="s1",
            steps=[
                StepSpec(tool="bad_tool"),
                StepSpec(tool="good_tool"),
            ],
        )

        result = asyncio.run(orch.execute(plan))

        # Both execute in concurrent batch — one fails, one succeeds
        assert result.steps[0].status == StepStatus.FAILED
        assert result.steps[1].status == StepStatus.DONE
        assert result.steps[1].result == "ok"
        fn_ok.assert_called_once()

    def test_unknown_tool_fails(self):
        registry = _mock_tool_registry([])  # empty registry
        orch = _setup_orchestrator(registry=registry)
        plan = EditPlan(
            task_id="t1", user_id="u1", script_id="s1",
            steps=[StepSpec(tool="nonexistent")],
        )

        result = asyncio.run(orch.execute(plan))

        assert result.status == TaskStatus.FAILED
        assert "未知工具" in result.steps[0].error


# ---------------------------------------------------------------------------
# StepOrchestrator — pause / resume
# ---------------------------------------------------------------------------

class TestPauseResume:
    """暂停和恢复机制"""

    def test_pause_sets_status(self):
        orch = _setup_orchestrator()
        # setup: load returns a plan
        plan = EditPlan(task_id="t1", user_id="u1", script_id="s1", steps=[])
        orch._load_plan_from_checkpoint = AsyncMock(return_value=plan)
        orch._save_task_status = AsyncMock()

        result = asyncio.run(orch.pause("t1"))

        assert result is True
        assert plan.status == TaskStatus.PAUSED

    def test_pause_nonexistent_returns_false(self):
        orch = _setup_orchestrator()
        orch._load_plan_from_checkpoint = AsyncMock(return_value=None)

        result = asyncio.run(orch.pause("ghost"))

        assert result is False

    def test_resume_calls_execute(self):
        orch = _setup_orchestrator()
        plan = EditPlan(task_id="t1", user_id="u1", script_id="s1", steps=[])
        orch._load_plan_from_checkpoint = AsyncMock(return_value=plan)

        result = asyncio.run(orch.resume("t1"))

        assert result is not None
        assert result.status == TaskStatus.COMPLETED

    def test_resume_nonexistent_returns_none(self):
        orch = _setup_orchestrator()
        orch._load_plan_from_checkpoint = AsyncMock(return_value=None)

        result = asyncio.run(orch.resume("ghost"))
        assert result is None


# ---------------------------------------------------------------------------
# StepOrchestrator — retry_step
# ---------------------------------------------------------------------------

class TestRetryStep:
    """失败步骤重试"""

    def test_retry_resets_step_and_resumes(self):
        fn = MagicMock(return_value="recovered")
        registry = _mock_tool_registry([{"name": "tool_a", "func": fn}])

        orch = _setup_orchestrator(registry=registry)
        plan = EditPlan(
            task_id="t1", user_id="u1", script_id="s1",
            steps=[
                StepSpec(tool="tool_a", status=StepStatus.FAILED,
                         error="previous failure", retry_count=1),
            ],
        )
        orch._load_plan_from_checkpoint = AsyncMock(return_value=plan)

        result = asyncio.run(orch.retry_step("t1", 0))

        # After retry, step 0 was reset → pending → executed → done
        assert result is True
        assert plan.steps[0].status == StepStatus.DONE
        assert plan.steps[0].retry_count == 2  # incremented
        assert plan.steps[0].error is None      # cleared

    def test_retry_invalid_index_returns_false(self):
        orch = _setup_orchestrator()
        plan = EditPlan(task_id="t1", user_id="u1", script_id="s1", steps=[
            StepSpec(tool="t"),
        ])
        orch._load_plan_from_checkpoint = AsyncMock(return_value=plan)

        assert asyncio.run(orch.retry_step("t1", -1)) is False
        assert asyncio.run(orch.retry_step("t1", 99)) is False


# ---------------------------------------------------------------------------
# StepOrchestrator — Redis integration (optional, graceful)
# ---------------------------------------------------------------------------

class TestRedisIntegration:
    """Redis 管理器可选，不可用时不影响执行"""

    def test_execute_without_redis_does_not_crash(self):
        fn = MagicMock(return_value="ok")
        registry = _mock_tool_registry([{"name": "t", "func": fn}])
        orch = _setup_orchestrator(registry=registry)  # redis_manager=None

        plan = EditPlan(
            task_id="t1", user_id="u1", script_id="s1",
            steps=[StepSpec(tool="t")],
        )

        result = asyncio.run(orch.execute(plan))
        assert result.status == TaskStatus.COMPLETED

    def test_redis_error_is_suppressed(self):
        fn = MagicMock(return_value="ok")
        registry = _mock_tool_registry([{"name": "t", "func": fn}])
        mock_redis = AsyncMock()
        mock_redis.update_step_progress.side_effect = ConnectionError("down")

        orch = StepOrchestrator(
            tool_registry=registry,
            redis_manager=mock_redis,
        )

        plan = EditPlan(
            task_id="t1", user_id="u1", script_id="s1",
            steps=[StepSpec(tool="t")],
        )

        result = asyncio.run(orch.execute(plan))
        assert result.status == TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# StepOrchestrator — without ToolRegistry (error path)
# ---------------------------------------------------------------------------

class TestWithoutRegistry:
    """ToolRegistry 不存在时应报错"""

    def test_execute_without_registry_fails(self):
        orch = StepOrchestrator()  # no registry
        plan = EditPlan(
            task_id="t1", user_id="u1", script_id="s1",
            steps=[StepSpec(tool="any")],
        )

        result = asyncio.run(orch.execute(plan))

        assert result.status == TaskStatus.FAILED


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestEnums:
    def test_step_status_values(self):
        assert StepStatus.PENDING == "pending"
        assert StepStatus.RUNNING == "running"
        assert StepStatus.DONE == "done"
        assert StepStatus.FAILED == "failed"
        assert StepStatus.SKIPPED == "skipped"

    def test_task_status_values(self):
        assert TaskStatus.PLANNING == "planning"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.PAUSED == "paused"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
