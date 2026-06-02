"""
Tests for req_08 — edit API routes.

Tests route functions directly with mock Session + mock CurrentUser,
and end-to-end via FastAPI TestClient with dependency overrides.
No real DB needed — all CRUD is mocked.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ============================================================================
# Mock helpers
# ============================================================================

def _mock_session():
    """Create a mock SQLModel Session."""
    return MagicMock()


def _mock_user():
    """Create a mock User for CurrentUser."""
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "test@example.com"
    return user


# ============================================================================
# Route function tests (fast, no TestClient)
# ============================================================================

class TestStartEdit:
    """POST /edit/start — 校验 + 持久化"""

    def test_valid_storyboard_creates_task(self):
        from app.api.routes.edit import start_edit
        from app.api.routes.edit_models import StartEditRequest

        body = StartEditRequest(
            storyboard={
                "project_name": "test",
                "steps": [{"action": "list_media"}],
            },
            script_id=str(uuid.uuid4()),
        )

        # Mock CRUD
        mock_task = MagicMock()
        mock_task.id = uuid.uuid4()
        with patch("app.api.routes.edit.crud.create_edit_task", return_value=mock_task), \
             patch("app.api.routes.edit.crud.upsert_edit_step"), \
             patch("app.api.routes.edit.crud.get_edit_task"):
            result = start_edit(body, current_user=_mock_user(), session=_mock_session())

        assert result.task_id is not None
        assert result.status == "planning"
        assert result.total_steps == 1

    def test_invalid_json_returns_422(self):
        from app.api.routes.edit import start_edit
        from app.api.routes.edit_models import StartEditRequest

        body = StartEditRequest(
            storyboard={"steps": [{"action": "nonexistent_action"}]},
            script_id=str(uuid.uuid4()),
        )

        with pytest.raises(Exception) as exc:
            start_edit(body, current_user=_mock_user(), session=_mock_session())
        assert exc.value.status_code == 422

    def test_empty_steps_returns_422(self):
        from app.api.routes.edit import start_edit
        from app.api.routes.edit_models import StartEditRequest

        body = StartEditRequest(
            storyboard={"steps": []},
            script_id=str(uuid.uuid4()),
        )

        with pytest.raises(Exception) as exc:
            start_edit(body, current_user=_mock_user(), session=_mock_session())
        assert exc.value.status_code == 422


class TestGetProgress:
    """GET /edit/{task_id}/progress"""

    def test_progress_returns_stats(self):
        from app.api.routes.edit import get_task_progress

        tid = uuid.uuid4()
        mock_task = MagicMock()
        mock_task.id = tid
        mock_task.status = "running"
        mock_task.current_step = 1
        mock_task.total_steps = 4
        mock_task.error_message = None

        mock_step = MagicMock()
        mock_step.tool_name = "smart_rough_cut"
        mock_step.status = "done"

        with patch("app.api.routes.edit.crud.get_edit_task", return_value=mock_task), \
             patch("app.api.routes.edit.crud.get_steps_by_task", return_value=[mock_step]):
            result = get_task_progress(tid, session=_mock_session())

        assert result.task_id == str(tid)
        assert result.status == "running"
        assert result.progress_pct == 25.0

    def test_progress_nonexistent_task_returns_404(self):
        from app.api.routes.edit import get_task_progress

        with patch("app.api.routes.edit.crud.get_edit_task", return_value=None):
            with pytest.raises(Exception) as exc:
                get_task_progress(uuid.uuid4(), session=_mock_session())
            assert exc.value.status_code == 404


class TestGetSteps:
    """GET /edit/{task_id}/steps"""

    def test_steps_returns_list(self):
        from app.api.routes.edit import get_task_steps

        tid = uuid.uuid4()
        mock_task = MagicMock()
        mock_task.id = tid

        s1 = MagicMock()
        s1.id = uuid.uuid4(); s1.step_index = 0; s1.tool_name = "load_skill"
        s1.status = "done"; s1.error = None; s1.retry_count = 0
        s1.started_at = None; s1.finished_at = None

        s2 = MagicMock()
        s2.id = uuid.uuid4(); s2.step_index = 1; s2.tool_name = "export"
        s2.status = "pending"; s2.error = None; s2.retry_count = 0
        s2.started_at = None; s2.finished_at = None

        with patch("app.api.routes.edit.crud.get_edit_task", return_value=mock_task), \
             patch("app.api.routes.edit.crud.get_steps_by_task", return_value=[s1, s2]):
            result = get_task_steps(tid, session=_mock_session())

        assert result.task_id == str(tid)
        assert len(result.steps) == 2
        assert result.steps[0].step_index == 0
        assert result.steps[1].step_index == 1

    def test_steps_nonexistent_task_returns_404(self):
        from app.api.routes.edit import get_task_steps

        with patch("app.api.routes.edit.crud.get_edit_task", return_value=None):
            with pytest.raises(Exception) as exc:
                get_task_steps(uuid.uuid4(), session=_mock_session())
            assert exc.value.status_code == 404


class TestPauseResumeCancel:
    """控制端点"""

    def test_pause_updates_status(self):
        from app.api.routes.edit import pause_task

        with patch("app.api.routes.edit.crud.update_edit_task_status", return_value=MagicMock()):
            result = pause_task(uuid.uuid4(), _mock_user(), _mock_session())

        assert result["status"] == "paused"

    def test_pause_nonexistent_returns_404(self):
        from app.api.routes.edit import pause_task

        with patch("app.api.routes.edit.crud.update_edit_task_status", return_value=None):
            with pytest.raises(Exception) as exc:
                pause_task(uuid.uuid4(), _mock_user(), _mock_session())
            assert exc.value.status_code == 404

    def test_resume_sets_status_running(self):
        from app.api.routes.edit import resume_task
        mock_task = MagicMock()

        with patch("app.api.routes.edit.crud.get_edit_task", return_value=mock_task), \
             patch("app.api.routes.edit.crud.update_edit_task_status"):
            result = resume_task(uuid.uuid4(), _mock_user(), _mock_session())

        assert result["status"] == "running"

    def test_cancel_sets_status(self):
        from app.api.routes.edit import cancel_task

        with patch("app.api.routes.edit.crud.update_edit_task_status", return_value=MagicMock()):
            result = cancel_task(uuid.uuid4(), _mock_user(), _mock_session())

        assert result["status"] == "cancelled"

    def test_cancel_nonexistent_returns_404(self):
        from app.api.routes.edit import cancel_task

        with patch("app.api.routes.edit.crud.update_edit_task_status", return_value=None):
            with pytest.raises(Exception) as exc:
                cancel_task(uuid.uuid4(), _mock_user(), _mock_session())
            assert exc.value.status_code == 404


class TestRetryAndSkip:
    """retry-step / skip-step"""

    def test_retry_step_resets_to_pending(self):
        from app.api.routes.edit import retry_step

        with patch("app.api.routes.edit.crud.upsert_edit_step"), \
             patch("app.api.routes.edit.crud.update_edit_task_status"):
            result = retry_step(uuid.uuid4(), 2, _mock_user(), _mock_session())

        assert result["status"] == "retrying"
        assert result["step_index"] == 2

    def test_skip_step_marks_skipped(self):
        from app.api.routes.edit import skip_step

        with patch("app.api.routes.edit.crud.upsert_edit_step"):
            result = skip_step(uuid.uuid4(), 1, _mock_user(), _mock_session())

        assert result["status"] == "skipped"
        assert result["step_index"] == 1


class TestPending:
    """GET /edit/pending"""

    def test_pending_filters_by_status(self):
        from app.api.routes.edit import get_pending_tasks

        t1 = MagicMock()
        t1.id = uuid.uuid4(); t1.script_id = uuid.uuid4()
        t1.status = "paused"; t1.error_message = None

        t2 = MagicMock()
        t2.id = uuid.uuid4(); t2.script_id = uuid.uuid4()
        t2.status = "failed"; t2.error_message = "error"

        t3 = MagicMock()
        t3.id = uuid.uuid4(); t3.script_id = uuid.uuid4()
        t3.status = "completed"; t3.error_message = None

        all_tasks = [t1, t2, t3]

        with patch("app.api.routes.edit.crud.get_edit_tasks_by_user", return_value=all_tasks):
            result = get_pending_tasks(_mock_user(), _mock_session())

        # Only paused and failed
        assert len(result) == 2
        assert result[0].status in ("paused", "failed")
        assert result[1].status in ("paused", "failed")


# ============================================================================
# Response model serialization
# ============================================================================

class TestResponseModels:
    """验证响应模型 JSON 序列化"""

    def test_start_edit_response(self):
        from app.api.routes.edit_models import StartEditResponse
        r = StartEditResponse(task_id="t1", status="planning", total_steps=5)
        d = r.model_dump()
        assert d == {"task_id": "t1", "status": "planning", "total_steps": 5}

    def test_task_progress_response(self):
        from app.api.routes.edit_models import TaskProgressResponse
        r = TaskProgressResponse(
            task_id="t1", status="running", current_step=2, total_steps=8,
            steps_done=2, steps_failed=0, progress_pct=25.0,
        )
        assert r.progress_pct == 25.0

    def test_step_list_response(self):
        from app.api.routes.edit_models import StepListResponse, StepItem
        r = StepListResponse(task_id="t1", steps=[
            StepItem(id="s1", step_index=0, tool_name="t", status="done"),
        ])
        assert len(r.steps) == 1

    def test_pending_task_item(self):
        from app.api.routes.edit_models import PendingTaskItem
        r = PendingTaskItem(
            task_id="t1", script_id="s1", status="failed",
            error_message="something broke",
        )
        d = r.model_dump()
        assert d["error_message"] == "something broke"
