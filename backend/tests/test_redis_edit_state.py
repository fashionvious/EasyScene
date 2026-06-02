"""
Tests for req_16 — EditTaskState model + EditTaskRedisManager.

All Redis operations mocked via AsyncMock — no real Redis needed.
Async methods invoked via asyncio.run() in sync test functions
to avoid requiring pytest-asyncio.
"""
import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# EditTaskState — model validation & defaults
# ---------------------------------------------------------------------------

class TestEditTaskState:
    """Pydantic 模型创建、默认值、序列化"""

    def test_create_minimal_state(self):
        from app.agent.utils.redis import EditTaskState

        state = EditTaskState(
            task_id="task-1", user_id="user-1", script_id="script-1",
        )

        assert state.task_id == "task-1"
        assert state.status == "planning"
        assert state.current_step == 0
        assert state.total_steps == 0
        assert state.step_name == ""
        assert state.progress_pct == 0.0
        assert state.celery_task_id is None
        assert state.error_message is None
        assert state.metadata == {}
        assert isinstance(state.updated_at, float)

    def test_create_full_state(self):
        from app.agent.utils.redis import EditTaskState

        now = time.time()
        state = EditTaskState(
            task_id="task-2", user_id="u2", script_id="s2",
            status="running", current_step=3, total_steps=8,
            step_name="smart_rough_cut", step_status="running",
            progress_pct=45.5, celery_task_id="celery-abc",
            metadata={"preview_path": "/tmp/preview.mp4"},
            updated_at=now,
        )

        assert state.status == "running"
        assert state.current_step == 3
        assert state.total_steps == 8
        assert state.step_name == "smart_rough_cut"
        assert state.progress_pct == 45.5
        assert state.celery_task_id == "celery-abc"
        assert state.metadata["preview_path"] == "/tmp/preview.mp4"

    def test_model_dump_json_roundtrip(self):
        from app.agent.utils.redis import EditTaskState

        original = EditTaskState(
            task_id="t1", user_id="u1", script_id="s1",
            status="paused", current_step=5, total_steps=10,
            error_message="TTS 合成超时",
        )
        json_str = original.model_dump_json()
        restored = EditTaskState.model_validate_json(json_str)

        assert restored.task_id == original.task_id
        assert restored.status == original.status
        assert restored.current_step == original.current_step
        assert restored.error_message == original.error_message

    def test_metadata_default_is_empty_dict(self):
        from app.agent.utils.redis import EditTaskState

        s1 = EditTaskState(task_id="a", user_id="b", script_id="c")
        s2 = EditTaskState(task_id="a", user_id="b", script_id="c")

        s1.metadata["key"] = "value"
        assert s2.metadata == {}
        assert "key" not in s2.metadata

    def test_missing_required_fields_raises(self):
        from app.agent.utils.redis import EditTaskState
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            EditTaskState()


# ---------------------------------------------------------------------------
# EditTaskRedisManager — create / get / delete
# ---------------------------------------------------------------------------

class TestEditTaskRedisManagerCRUD:
    """create_task_state / get_task_state / delete_task_state"""

    def test_create_stores_json_with_ttl(self):
        from app.agent.utils.redis import EditTaskState, EditTaskRedisManager

        mock_redis = AsyncMock()
        mgr = EditTaskRedisManager(mock_redis)
        state = EditTaskState(task_id="t1", user_id="u1", script_id="s1")

        asyncio.run(mgr.create_task_state(state))

        mock_redis.set.assert_awaited_once()
        args = mock_redis.set.call_args
        assert args[0][0] == "edit_task:t1"
        assert args[1]["ex"] == 86400 * 3  # 72h TTL

    def test_get_returns_state_when_exists(self):
        from app.agent.utils.redis import EditTaskState, EditTaskRedisManager

        mock_redis = AsyncMock()
        mgr = EditTaskRedisManager(mock_redis)
        original = EditTaskState(
            task_id="t1", user_id="u1", script_id="s1",
            status="running", current_step=2,
        )
        mock_redis.get.return_value = original.model_dump_json()

        result = asyncio.run(mgr.get_task_state("t1"))

        assert result is not None
        assert result.task_id == "t1"
        assert result.status == "running"
        assert result.current_step == 2

    def test_get_returns_none_when_not_exists(self):
        from app.agent.utils.redis import EditTaskRedisManager

        mock_redis = AsyncMock()
        mgr = EditTaskRedisManager(mock_redis)
        mock_redis.get.return_value = None

        result = asyncio.run(mgr.get_task_state("nonexistent"))
        assert result is None

    def test_delete_removes_key(self):
        from app.agent.utils.redis import EditTaskRedisManager

        mock_redis = AsyncMock()
        mgr = EditTaskRedisManager(mock_redis)

        result = asyncio.run(mgr.delete_task_state("t1"))

        assert result is True
        mock_redis.delete.assert_awaited_once_with("edit_task:t1")


# ---------------------------------------------------------------------------
# EditTaskRedisManager — update variants
# ---------------------------------------------------------------------------

class TestEditTaskRedisManagerUpdate:
    """update_task_state / step_progress / set_error / set_celery / set_metadata"""

    def test_update_partial_fields(self):
        from app.agent.utils.redis import EditTaskState, EditTaskRedisManager

        mock_redis = AsyncMock()
        mgr = EditTaskRedisManager(mock_redis)
        existing = EditTaskState(task_id="t1", user_id="u1", script_id="s1",
                                 status="planning", current_step=0)
        mock_redis.get.return_value = existing.model_dump_json()

        result = asyncio.run(mgr.update_task_state(
            "t1", status="running", current_step=3,
        ))

        assert result is True
        stored_json = mock_redis.set.call_args[0][1]
        stored = EditTaskState.model_validate_json(stored_json)
        assert stored.status == "running"
        assert stored.current_step == 3

    def test_update_nonexistent_returns_false(self):
        from app.agent.utils.redis import EditTaskRedisManager

        mock_redis = AsyncMock()
        mgr = EditTaskRedisManager(mock_redis)
        mock_redis.get.return_value = None

        result = asyncio.run(mgr.update_task_state("ghost", status="running"))

        assert result is False
        mock_redis.set.assert_not_awaited()

    def test_update_step_progress_convenience(self):
        from app.agent.utils.redis import EditTaskState, EditTaskRedisManager

        mock_redis = AsyncMock()
        mgr = EditTaskRedisManager(mock_redis)
        existing = EditTaskState(task_id="t1", user_id="u1", script_id="s1")
        mock_redis.get.return_value = existing.model_dump_json()

        result = asyncio.run(mgr.update_step_progress(
            "t1", 3, "smart_rough_cut", "running", 45.0,
        ))

        assert result is True
        stored_json = mock_redis.set.call_args[0][1]
        stored = EditTaskState.model_validate_json(stored_json)
        assert stored.current_step == 3
        assert stored.step_name == "smart_rough_cut"
        assert stored.step_status == "running"
        assert stored.progress_pct == 45.0

    def test_set_error(self):
        from app.agent.utils.redis import EditTaskState, EditTaskRedisManager

        mock_redis = AsyncMock()
        mgr = EditTaskRedisManager(mock_redis)
        existing = EditTaskState(task_id="t1", user_id="u1", script_id="s1",
                                 status="running")
        mock_redis.get.return_value = existing.model_dump_json()

        result = asyncio.run(mgr.set_error("t1", "FFmpeg 转码超时"))

        assert result is True
        stored_json = mock_redis.set.call_args[0][1]
        stored = EditTaskState.model_validate_json(stored_json)
        assert stored.status == "failed"
        assert stored.error_message == "FFmpeg 转码超时"

    def test_set_celery_task_id(self):
        from app.agent.utils.redis import EditTaskState, EditTaskRedisManager

        mock_redis = AsyncMock()
        mgr = EditTaskRedisManager(mock_redis)
        existing = EditTaskState(task_id="t1", user_id="u1", script_id="s1")
        mock_redis.get.return_value = existing.model_dump_json()

        result = asyncio.run(mgr.set_celery_task_id("t1", "celery-task-123"))

        assert result is True
        stored_json = mock_redis.set.call_args[0][1]
        stored = EditTaskState.model_validate_json(stored_json)
        assert stored.celery_task_id == "celery-task-123"

    def test_set_metadata(self):
        from app.agent.utils.redis import EditTaskState, EditTaskRedisManager

        mock_redis = AsyncMock()
        mgr = EditTaskRedisManager(mock_redis)
        existing = EditTaskState(task_id="t1", user_id="u1", script_id="s1")
        mock_redis.get.return_value = existing.model_dump_json()

        result = asyncio.run(mgr.set_metadata(
            "t1", "preview_path", "D:/output/preview.mp4",
        ))

        assert result is True
        stored_json = mock_redis.set.call_args[0][1]
        stored = EditTaskState.model_validate_json(stored_json)
        assert stored.metadata["preview_path"] == "D:/output/preview.mp4"

    def test_set_metadata_nonexistent(self):
        from app.agent.utils.redis import EditTaskRedisManager

        mock_redis = AsyncMock()
        mgr = EditTaskRedisManager(mock_redis)
        mock_redis.get.return_value = None

        result = asyncio.run(mgr.set_metadata("ghost", "key", "value"))
        assert result is False


# ---------------------------------------------------------------------------
# EditTaskRedisManager — error resilience
# ---------------------------------------------------------------------------

class TestEditTaskRedisManagerResilience:
    """Redis 不可用时静默降级，不抛异常"""

    def test_create_returns_false_on_redis_error(self):
        from app.agent.utils.redis import EditTaskState, EditTaskRedisManager

        mock_redis = AsyncMock()
        mock_redis.set.side_effect = ConnectionError("Redis unreachable")
        mgr = EditTaskRedisManager(mock_redis)
        state = EditTaskState(task_id="t1", user_id="u1", script_id="s1")

        result = asyncio.run(mgr.create_task_state(state))
        assert result is False

    def test_get_returns_none_on_redis_error(self):
        from app.agent.utils.redis import EditTaskRedisManager

        mock_redis = AsyncMock()
        mock_redis.get.side_effect = ConnectionError("Redis unreachable")
        mgr = EditTaskRedisManager(mock_redis)

        result = asyncio.run(mgr.get_task_state("t1"))
        assert result is None

    def test_update_returns_false_on_redis_error(self):
        from app.agent.utils.redis import EditTaskRedisManager

        mock_redis = AsyncMock()
        mock_redis.get.side_effect = ConnectionError("Redis unreachable")
        mgr = EditTaskRedisManager(mock_redis)

        result = asyncio.run(mgr.update_task_state("t1", status="running"))
        assert result is False

    def test_delete_returns_false_on_redis_error(self):
        from app.agent.utils.redis import EditTaskRedisManager

        mock_redis = AsyncMock()
        mock_redis.delete.side_effect = ConnectionError("Redis unreachable")
        mgr = EditTaskRedisManager(mock_redis)

        result = asyncio.run(mgr.delete_task_state("t1"))
        assert result is False


# ---------------------------------------------------------------------------
# EditTaskStatus enum
# ---------------------------------------------------------------------------

class TestEditTaskStatus:
    def test_enum_values(self):
        from app.agent.utils.redis import EditTaskStatus

        assert EditTaskStatus.PLANNING == "planning"
        assert EditTaskStatus.RUNNING == "running"
        assert EditTaskStatus.PAUSED == "paused"
        assert EditTaskStatus.COMPLETED == "completed"
        assert EditTaskStatus.FAILED == "failed"

    def test_enum_is_str_subclass(self):
        from app.agent.utils.redis import EditTaskStatus
        assert issubclass(EditTaskStatus, str)


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

class TestFactoryFunction:
    def test_factory_exists_and_callable(self):
        from app.agent.utils.redis import get_edit_task_redis_manager
        assert callable(get_edit_task_redis_manager)
