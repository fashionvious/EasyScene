"""
Tests for req_12 — user interaction pending queue.

Covers: add_user_pending, remove_user_pending, get_user_pending,
get_pending_count, StepPausedError, and orchestrator integration.
All Redis ops mocked via AsyncMock — no real Redis needed.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest


# ---------------------------------------------------------------------------
# Pending queue CRUD (Redis mocked)
# ---------------------------------------------------------------------------

class TestUserPendingQueue:
    """add / remove / get / count"""

    @pytest.fixture
    def mgr(self):
        from app.agent.utils.redis import EditTaskRedisManager
        return EditTaskRedisManager(AsyncMock())

    def test_add_pending_writes_to_redis(self, mgr):
        result = asyncio.run(
            mgr.add_user_pending("user1", "task1", 2, "TTS合成超时")
        )
        assert result is True
        mgr.redis.sadd.assert_awaited_once()
        mgr.redis.set.assert_awaited_once()

    def test_add_pending_truncates_long_errors(self, mgr):
        long_error = "x" * 600
        asyncio.run(mgr.add_user_pending("u1", "t1", 0, long_error))

        # Verify stored error is truncated to 500
        set_call = mgr.redis.set.call_args
        stored = set_call[0][1]  # second positional arg = JSON data
        import json
        data = json.loads(stored)
        assert len(data["error"]) <= 500

    def test_add_pending_is_idempotent(self, mgr):
        asyncio.run(mgr.add_user_pending("u1", "t1", 1, "error1"))
        asyncio.run(mgr.add_user_pending("u1", "t1", 1, "error2"))

        # sadd called twice (same task_id added to set twice → no-op second time)
        # detail key overwritten
        assert mgr.redis.sadd.call_count == 2

    def test_get_pending_returns_list(self, mgr):
        mgr.redis.smembers.return_value = {"task1", "task2"}
        mgr.redis.get.side_effect = [
            '{"task_id": "task1", "step_index": 2, "error": "e1", "created_at": 1.0}',
            '{"task_id": "task2", "step_index": 1, "error": "e2", "created_at": 2.0}',
        ]

        items = asyncio.run(mgr.get_user_pending("user1"))

        assert len(items) == 2
        assert items[0]["task_id"] == "task1"
        assert items[1]["task_id"] == "task2"

    def test_get_pending_filters_expired(self, mgr):
        mgr.redis.smembers.return_value = {"task1", "task2"}
        # task1 detail exists, task2 detail expired (None)
        mgr.redis.get.side_effect = [
            '{"task_id": "task1", "step_index": 0, "error": "ok"}',
            None,
        ]

        items = asyncio.run(mgr.get_user_pending("user1"))

        assert len(items) == 1
        assert items[0]["task_id"] == "task1"
        # Expired task2 should be removed from set
        mgr.redis.srem.assert_awaited_once()

    def test_remove_pending_cleans_set_and_detail(self, mgr):
        result = asyncio.run(mgr.remove_user_pending("user1", "task1"))

        assert result is True
        mgr.redis.srem.assert_awaited_once()
        mgr.redis.delete.assert_awaited_once()

    def test_get_pending_count(self, mgr):
        mgr.redis.scard.return_value = 3

        count = asyncio.run(mgr.get_pending_count("user1"))

        assert count == 3


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------

class TestPendingQueueResilience:
    """Redis 不可用时静默降级"""

    def test_add_pending_returns_false_on_error(self):
        from app.agent.utils.redis import EditTaskRedisManager

        mock_redis = AsyncMock()
        mock_redis.sadd.side_effect = ConnectionError("down")
        mgr = EditTaskRedisManager(mock_redis)

        result = asyncio.run(
            mgr.add_user_pending("u1", "t1", 1, "error"),
        )
        assert result is False

    def test_get_pending_returns_empty_on_error(self):
        from app.agent.utils.redis import EditTaskRedisManager

        mock_redis = AsyncMock()
        mock_redis.smembers.side_effect = ConnectionError("down")
        mgr = EditTaskRedisManager(mock_redis)

        items = asyncio.run(mgr.get_user_pending("u1"))
        assert items == []

    def test_get_pending_count_returns_zero_on_error(self):
        from app.agent.utils.redis import EditTaskRedisManager

        mock_redis = AsyncMock()
        mock_redis.scard.side_effect = ConnectionError("down")
        mgr = EditTaskRedisManager(mock_redis)

        count = asyncio.run(mgr.get_pending_count("u1"))
        assert count == 0


# ---------------------------------------------------------------------------
# StepPausedError
# ---------------------------------------------------------------------------

class TestStepPausedError:
    def test_is_exception(self):
        from app.agent.skills_agent.step_orchestrator import StepPausedError

        with pytest.raises(StepPausedError):
            raise StepPausedError("test")

    def test_carries_message(self):
        from app.agent.skills_agent.step_orchestrator import StepPausedError

        err = StepPausedError("步骤需用户决策: TTS超时")
        assert "用户决策" in str(err)


# ---------------------------------------------------------------------------
# Orchestrator integration — NOTIFY_USER path
# ---------------------------------------------------------------------------

class TestOrchestratorPendingIntegration:
    """StepOrchestrator 集成：NOTIFY_USER 错误 → 写入 pending 队列"""

    def test_notify_user_error_writes_pending(self):
        from app.agent.skills_agent.step_orchestrator import (
            StepOrchestrator, StepSpec, EditPlan,
        )
        from app.agent.skills_agent.tool_registry import ToolRegistry, ToolSpec

        # Add jianying-editor-skill to path for DataError import
        import os, sys
        _skill_scripts = os.path.join(
            os.path.dirname(__file__), "..", "jianying-editor-skill",
        )
        sys.path.insert(0, os.path.abspath(_skill_scripts))
        from scripts.utils.errors import DataError

        def bad_func(**kw):
            raise DataError("invalid JSON format in storyboard")

        reg = ToolRegistry()
        reg.register(ToolSpec(
            name="bad_tool", handler="test", category="read",
            func=bad_func, concurrency_safe=False,
        ))

        mock_redis = AsyncMock()

        orch = StepOrchestrator(
            tool_registry=reg,
            redis_manager=mock_redis,
        )
        plan = EditPlan(
            task_id="t1", user_id="u1", script_id="s1",
            steps=[StepSpec(tool="bad_tool")],
        )

        result = asyncio.run(orch.execute(plan))

        # Plan paused, step failed
        assert result.status.value == "paused"
        assert result.steps[0].status.value == "failed"
        # Redis pending queue updated
        mock_redis.add_user_pending.assert_awaited_once()

    def test_normal_error_does_not_write_pending(self):
        """普通异常不写入 pending 队列（保持原有行为）。"""
        from app.agent.skills_agent.step_orchestrator import (
            StepOrchestrator, StepSpec, EditPlan,
        )
        from app.agent.skills_agent.tool_registry import ToolRegistry, ToolSpec

        def bad_func(**kw):
            raise ValueError("generic error")

        reg = ToolRegistry()
        reg.register(ToolSpec(
            name="bad_tool", handler="test", category="read",
            func=bad_func, concurrency_safe=False,
        ))

        mock_redis = AsyncMock()
        orch = StepOrchestrator(
            tool_registry=reg,
            redis_manager=mock_redis,
        )
        plan = EditPlan(
            task_id="t1", user_id="u1", script_id="s1",
            steps=[StepSpec(tool="bad_tool")],
        )

        result = asyncio.run(orch.execute(plan))

        # Plan failed (not paused), Redis NOT updated
        assert result.status.value == "failed"
        mock_redis.add_user_pending.assert_not_awaited()
