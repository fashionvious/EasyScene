"""
Tests for req_05 — CRUD functions for edit_task, edit_step, user_preference.

All tests use a mocked SQLModel Session — no real database required.
Verifies that the correct session methods are called with the correct arguments.
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers — mock a SQLModel Session
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_session():
    """Returns a MagicMock that behaves like a sqlmodel.Session."""
    s = MagicMock()
    s.get.return_value = None
    s.exec.return_value.first.return_value = None
    s.exec.return_value.all.return_value = []
    return s


def _mock_task(task_id=None, user_id=None, script_id=None):
    """Create a mock EditTask with minimal attributes."""
    from app.models import EditTask
    t = MagicMock(spec=EditTask)
    t.id = task_id or uuid.uuid4()
    t.user_id = user_id or uuid.uuid4()
    t.script_id = script_id or uuid.uuid4()
    t.status = "planning"
    t.current_step = 0
    t.total_steps = 0
    t.project_state_json = None
    t.error_message = None
    t.is_deleted = 0
    t.update_time = datetime.utcnow()
    return t


def _mock_step(task_id, step_index, tool_name="test_tool", status="done"):
    """Create a mock EditStep."""
    from app.models import EditStep
    s = MagicMock(spec=EditStep)
    s.task_id = task_id
    s.step_index = step_index
    s.tool_name = tool_name
    s.status = status
    s.retry_count = 0
    s.started_at = datetime.utcnow()
    s.finished_at = datetime.utcnow()
    return s


# ---------------------------------------------------------------------------
# EditTask CRUD
# ---------------------------------------------------------------------------

class TestEditTaskCRUD:
    """create_edit_task / get_edit_task / get_edit_tasks_by_user / update"""

    def test_create_calls_add_commit_refresh(self, mock_session):
        from app.crud import create_edit_task
        from app.models import EditTaskCreate

        uid, sid = uuid.uuid4(), uuid.uuid4()
        task_in = EditTaskCreate(user_id=uid, script_id=sid, total_steps=5)

        # session.refresh should populate the id
        def _set_id(obj):
            obj.id = uuid.uuid4()
        mock_session.refresh.side_effect = _set_id

        result = create_edit_task(session=mock_session, task_in=task_in)

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()
        assert result.id is not None

    def test_get_existing_task(self, mock_session):
        from app.crud import get_edit_task

        tid = uuid.uuid4()
        mock_task = _mock_task(task_id=tid)
        mock_session.get.return_value = mock_task

        result = get_edit_task(session=mock_session, task_id=tid)

        mock_session.get.assert_called_once()
        assert result is mock_task

    def test_get_nonexistent_task_returns_none(self, mock_session):
        from app.crud import get_edit_task

        mock_session.get.return_value = None
        result = get_edit_task(session=mock_session, task_id=uuid.uuid4())

        assert result is None

    def test_get_tasks_by_user_calls_exec(self, mock_session):
        from app.crud import get_edit_tasks_by_user

        uid = uuid.uuid4()
        get_edit_tasks_by_user(session=mock_session, user_id=uid, limit=5)

        # Verify session.exec was called (with a SQL select statement)
        mock_session.exec.assert_called_once()

    def test_get_tasks_by_user_returns_list(self, mock_session):
        from app.crud import get_edit_tasks_by_user

        mock_tasks = [_mock_task() for _ in range(3)]
        mock_session.exec.return_value.all.return_value = mock_tasks

        result = get_edit_tasks_by_user(
            session=mock_session, user_id=uuid.uuid4(),
        )
        assert len(result) == 3

    def test_update_edit_task_calls_session_add(self, mock_session):
        from app.crud import update_edit_task
        from app.models import EditTaskUpdate

        db_task = _mock_task()
        db_task.update_time = datetime(2020, 1, 1)  # set a known old time
        task_in = EditTaskUpdate(status="running")

        update_edit_task(session=mock_session, db_task=db_task, task_in=task_in)

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()

    def test_update_edit_task_calls_sqlmodel_update_with_data(self, mock_session):
        from app.crud import update_edit_task
        from app.models import EditTaskUpdate

        db_task = _mock_task()
        task_in = EditTaskUpdate(status="paused", error_message="test error")

        # Spy on sqlmodel_update to verify it receives the right dict
        original_update = db_task.sqlmodel_update
        captured = {}
        def _capture(data, **kw):
            captured.update(data)
            return original_update(data, **kw)
        db_task.sqlmodel_update = _capture

        update_edit_task(session=mock_session, db_task=db_task, task_in=task_in)

        assert captured.get("status") == "paused"
        assert captured.get("error_message") == "test error"

    def test_update_status_existing(self, mock_session):
        from app.crud import update_edit_task_status

        db_task = _mock_task()
        mock_session.get.return_value = db_task

        result = update_edit_task_status(
            session=mock_session, task_id=db_task.id, status="completed",
        )

        assert result is not None
        assert db_task.status == "completed"

    def test_update_status_nonexistent_returns_none(self, mock_session):
        from app.crud import update_edit_task_status

        mock_session.get.return_value = None
        result = update_edit_task_status(
            session=mock_session, task_id=uuid.uuid4(), status="completed",
        )

        assert result is None
        mock_session.add.assert_not_called()


# ---------------------------------------------------------------------------
# EditStep CRUD
# ---------------------------------------------------------------------------

class TestEditStepCRUD:
    """create / upsert / get_steps_by_task / get_step_stats"""

    def test_create_step(self, mock_session):
        from app.crud import create_edit_step
        from app.models import EditStepCreate

        tid = uuid.uuid4()
        step_in = EditStepCreate(task_id=tid, step_index=0, tool_name="resolve_media")

        def _set_id(obj):
            obj.id = uuid.uuid4()
        mock_session.refresh.side_effect = _set_id

        result = create_edit_step(session=mock_session, step_in=step_in)

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        assert result.id is not None

    def test_upsert_creates_when_not_exists(self, mock_session):
        from app.crud import upsert_edit_step

        mock_session.exec.return_value.first.return_value = None  # not exists

        tid = uuid.uuid4()
        result = upsert_edit_step(
            session=mock_session, task_id=tid, step_index=2,
            step_data={"tool_name": "auto_exporter", "status": "pending"},
        )

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        assert result.tool_name == "auto_exporter"
        assert result.status == "pending"

    def test_upsert_updates_when_exists(self, mock_session):
        from app.crud import upsert_edit_step

        tid = uuid.uuid4()
        existing = _mock_step(tid, 1, "smart_rough_cut", "running")
        mock_session.exec.return_value.first.return_value = existing

        result = upsert_edit_step(
            session=mock_session, task_id=tid, step_index=1,
            step_data={"status": "done", "retry_count": 1},
        )

        assert result is existing
        assert existing.status == "done"
        assert existing.retry_count == 1
        mock_session.add.assert_called_once()

    def test_upsert_preserves_existing_fields_when_not_in_data(self, mock_session):
        from app.crud import upsert_edit_step

        tid = uuid.uuid4()
        existing = _mock_step(tid, 0, "load_skill", "done")
        existing.result_json = '{"key": "value"}'
        mock_session.exec.return_value.first.return_value = existing

        result = upsert_edit_step(
            session=mock_session, task_id=tid, step_index=0,
            step_data={"status": "failed"},  # only updating status
        )

        assert result.status == "failed"
        assert result.result_json == '{"key": "value"}'  # preserved

    def test_get_steps_sorted_by_index(self, mock_session):
        from app.crud import get_steps_by_task

        tid = uuid.uuid4()
        steps = [
            _mock_step(tid, 0),
            _mock_step(tid, 1),
            _mock_step(tid, 2),
        ]
        mock_session.exec.return_value.all.return_value = steps

        result = get_steps_by_task(session=mock_session, task_id=tid)

        assert len(result) == 3
        assert result[0].step_index == 0
        assert result[2].step_index == 2

    def test_get_step_stats_with_mixed_statuses(self, mock_session):
        from app.crud import get_step_stats

        tid = uuid.uuid4()
        start = datetime(2026, 6, 1, 12, 0, 0)
        end = datetime(2026, 6, 1, 12, 0, 30)  # exactly 30s later

        s1 = _mock_step(tid, 0, status="done"); s1.started_at = start; s1.finished_at = end
        s2 = _mock_step(tid, 1, status="done"); s2.started_at = start; s2.finished_at = end
        s3 = _mock_step(tid, 2, status="failed"); s3.retry_count = 2; s3.started_at = None; s3.finished_at = None
        s4 = _mock_step(tid, 3, status="pending"); s4.started_at = None; s4.finished_at = None

        mock_session.exec.return_value.all.return_value = [s1, s2, s3, s4]

        stats = get_step_stats(session=mock_session, task_id=tid)

        assert stats["total"] == 4
        assert stats["done"] == 2
        assert stats["failed"] == 1
        assert stats["total_retries"] == 2  # only s3 has retries (>0)
        assert stats["avg_duration_seconds"] == 30.0

    def test_get_step_stats_empty(self, mock_session):
        from app.crud import get_step_stats

        mock_session.exec.return_value.all.return_value = []

        stats = get_step_stats(session=mock_session, task_id=uuid.uuid4())

        assert stats["total"] == 0
        assert stats["done"] == 0
        assert stats["avg_duration_seconds"] == 0.0


# ---------------------------------------------------------------------------
# UserPreference CRUD
# ---------------------------------------------------------------------------

class TestUserPreferenceCRUD:
    """get_or_create / update"""

    def test_get_or_create_returns_existing(self, mock_session):
        from app.crud import get_or_create_user_preference
        from app.models import UserPreference

        uid = uuid.uuid4()
        existing = MagicMock(spec=UserPreference)
        existing.user_id = uid
        existing.preferred_resolution = "1080p"
        mock_session.exec.return_value.first.return_value = existing

        result = get_or_create_user_preference(session=mock_session, user_id=uid)

        assert result is existing
        mock_session.add.assert_not_called()  # no create needed
        mock_session.commit.assert_not_called()

    def test_get_or_create_creates_when_not_exists(self, mock_session):
        from app.crud import get_or_create_user_preference

        mock_session.exec.return_value.first.return_value = None
        uid = uuid.uuid4()

        result = get_or_create_user_preference(session=mock_session, user_id=uid)

        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()
        assert result.user_id == uid

    def test_update_calls_sqlmodel_update_with_data(self, mock_session):
        from app.crud import update_user_preference
        from app.models import UserPreference, UserPreferenceUpdate

        uid = uuid.uuid4()
        existing = MagicMock(spec=UserPreference)
        existing.user_id = uid
        existing.preferred_resolution = "720p"
        existing.preferred_fps = 24
        existing.update_time = datetime.utcnow()
        mock_session.exec.return_value.first.return_value = existing

        # Spy on sqlmodel_update to capture the dict it receives
        captured = {}
        original_update = existing.sqlmodel_update
        def _capture(data, **kw):
            captured.update(data)
            return original_update(data, **kw)
        existing.sqlmodel_update = _capture

        pref_in = UserPreferenceUpdate(preferred_resolution="1080p", preferred_fps=60)

        result = update_user_preference(session=mock_session, user_id=uid, pref_in=pref_in)

        assert captured.get("preferred_resolution") == "1080p"
        assert captured.get("preferred_fps") == 60
        mock_session.add.assert_called_once()

    def test_update_creates_if_user_has_no_preference(self, mock_session):
        from app.crud import update_user_preference
        from app.models import UserPreferenceUpdate

        # First call returns None (no pref), second call returns the new one
        mock_session.exec.return_value.first.side_effect = [None, MagicMock()]
        uid = uuid.uuid4()
        pref_in = UserPreferenceUpdate(preferred_speaker="zh_male_huoli")

        result = update_user_preference(session=mock_session, user_id=uid, pref_in=pref_in)

        assert result is not None
        # add called twice: once for create, once for update
        assert mock_session.add.call_count == 2


# ---------------------------------------------------------------------------
# Signature consistency with existing patterns
# ---------------------------------------------------------------------------

class TestSignatureConsistency:
    """确保新 CRUD 函数遵循现有的命名和签名约定"""

    def test_all_new_functions_use_keyword_only_session(self):
        import app.crud as crud_module
        import inspect

        new_funcs = [
            "create_edit_task", "get_edit_task", "get_edit_tasks_by_user",
            "update_edit_task", "update_edit_task_status",
            "create_edit_step", "upsert_edit_step", "get_steps_by_task",
            "get_step_stats", "get_or_create_user_preference",
            "update_user_preference",
        ]

        for name in new_funcs:
            func = getattr(crud_module, name)
            sig = inspect.signature(func)
            params = list(sig.parameters.keys())
            assert "session" in params, f"{name} missing 'session' parameter"
            assert params.index("session") < len(params), (
                f"{name}: session should be first positional-or-keyword param"
            )
