"""
Tests for req_04 — new SQLModel tables (EditTask, EditStep, UserPreference).

Focus: defaults, validation, JSON round-trip, table metadata,
and consistency with existing model conventions.
No real database needed — all tests are in-memory.
"""
import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# EditTask — defaults & serialization
# ---------------------------------------------------------------------------

class TestEditTaskBase:
    """EditTaskBase + EditTaskCreate + EditTaskUpdate"""

    def test_base_defaults(self):
        from app.models import EditTaskBase

        obj = EditTaskBase()
        assert obj.status == "planning"
        assert obj.current_step == 0
        assert obj.total_steps == 0
        assert obj.project_state_json is None
        assert obj.error_message is None

    def test_create_minimal_fields(self):
        from app.models import EditTaskCreate

        uid = uuid.uuid4()
        sid = uuid.uuid4()
        obj = EditTaskCreate(user_id=uid, script_id=sid)

        assert obj.user_id == uid
        assert obj.script_id == sid
        assert obj.status == "planning"  # inherited default

    def test_create_accepts_all_fields(self):
        from app.models import EditTaskCreate

        uid, sid = uuid.uuid4(), uuid.uuid4()
        obj = EditTaskCreate(
            user_id=uid,
            script_id=sid,
            status="running",
            current_step=2,
            total_steps=10,
            project_state_json='{"key": "value"}',
            error_message="test error",
        )
        assert obj.status == "running"
        assert obj.current_step == 2
        assert obj.total_steps == 10
        assert obj.project_state_json == '{"key": "value"}'

    def test_update_partial_fields(self):
        from app.models import EditTaskUpdate

        obj = EditTaskUpdate(status="paused")
        assert obj.status == "paused"
        assert obj.current_step is None
        assert obj.total_steps is None

    def test_update_empty_allows_none(self):
        from app.models import EditTaskUpdate

        obj = EditTaskUpdate()
        assert obj.status is None

    def test_model_dump(self):
        from app.models import EditTaskBase

        obj = EditTaskBase(current_step=3, total_steps=8, status="running")
        d = obj.model_dump()
        assert d["status"] == "running"
        assert d["current_step"] == 3
        assert d["total_steps"] == 8

    def test_model_validate_json_roundtrip(self):
        from app.models import EditTaskBase

        original = EditTaskBase(
            status="running", current_step=1, total_steps=5,
            error_message="something went wrong",
        )
        json_str = original.model_dump_json()
        restored = EditTaskBase.model_validate_json(json_str)
        assert restored.status == original.status
        assert restored.current_step == original.current_step
        assert restored.error_message == original.error_message

    def test_status_max_length(self):
        from app.models import EditTaskBase

        obj = EditTaskBase(status="a" * 20)
        assert len(obj.status) == 20

        with pytest.raises(ValidationError):
            EditTaskBase(status="a" * 21)  # exceeds max_length=20


# ---------------------------------------------------------------------------
# EditStep — defaults & serialization
# ---------------------------------------------------------------------------

class TestEditStepBase:
    """EditStepBase + EditStepCreate + EditStepUpdate"""

    def test_base_defaults(self):
        from app.models import EditStepBase

        obj = EditStepBase(step_index=0, tool_name="load_skill")
        assert obj.step_index == 0
        assert obj.tool_name == "load_skill"
        assert obj.status == "pending"
        assert obj.args_json is None
        assert obj.result_json is None
        assert obj.error is None
        assert obj.started_at is None
        assert obj.finished_at is None
        assert obj.retry_count == 0

    def test_create_with_task_id(self):
        from app.models import EditStepCreate

        tid = uuid.uuid4()
        obj = EditStepCreate(task_id=tid, step_index=1, tool_name="resolve_media")

        assert obj.task_id == tid
        assert obj.step_index == 1
        assert obj.tool_name == "resolve_media"

    def test_create_requires_step_index_and_tool_name(self):
        from app.models import EditStepCreate

        with pytest.raises(ValidationError):
            EditStepCreate(task_id=uuid.uuid4())  # missing step_index + tool_name

    def test_update_partial(self):
        from app.models import EditStepUpdate

        obj = EditStepUpdate(status="done", retry_count=2)
        assert obj.status == "done"
        assert obj.retry_count == 2
        assert obj.error is None

    def test_status_max_length(self):
        from app.models import EditStepBase

        obj = EditStepBase(step_index=0, tool_name="t", status="a" * 20)
        assert len(obj.status) == 20

        with pytest.raises(ValidationError):
            EditStepBase(step_index=0, tool_name="t", status="a" * 21)

    def test_tool_name_max_length(self):
        from app.models import EditStepBase

        obj = EditStepBase(step_index=0, tool_name="a" * 100)
        assert len(obj.tool_name) == 100

        with pytest.raises(ValidationError):
            EditStepBase(step_index=0, tool_name="a" * 101)

    def test_json_roundtrip_with_timestamps(self):
        from app.models import EditStepBase

        now = datetime.utcnow()
        original = EditStepBase(
            step_index=2, tool_name="execute_jyproject_code",
            status="done", retry_count=1,
            started_at=now, finished_at=now,
        )
        json_str = original.model_dump_json()
        restored = EditStepBase.model_validate_json(json_str)
        assert restored.step_index == 2
        assert restored.tool_name == "execute_jyproject_code"
        assert restored.status == "done"
        assert restored.retry_count == 1


# ---------------------------------------------------------------------------
# UserPreference — defaults & constraints
# ---------------------------------------------------------------------------

class TestUserPreference:
    """UserPreferenceBase + UserPreferenceCreate + UserPreferenceUpdate"""

    def test_base_all_none_by_default(self):
        from app.models import UserPreferenceBase

        obj = UserPreferenceBase()
        assert obj.preferred_resolution is None
        assert obj.preferred_fps is None
        assert obj.preferred_speaker is None
        assert obj.frequent_media_paths_json is None
        assert obj.last_project_name is None

    def test_create_with_user_id(self):
        from app.models import UserPreferenceCreate

        uid = uuid.uuid4()
        obj = UserPreferenceCreate(user_id=uid, preferred_resolution="1080p", preferred_fps=30)
        assert obj.user_id == uid
        assert obj.preferred_resolution == "1080p"
        assert obj.preferred_fps == 30

    def test_update_partial(self):
        from app.models import UserPreferenceUpdate

        obj = UserPreferenceUpdate(preferred_speaker="zh_male_huoli")
        assert obj.preferred_speaker == "zh_male_huoli"
        assert obj.preferred_fps is None

    def test_resolution_max_length(self):
        from app.models import UserPreferenceBase

        obj = UserPreferenceBase(preferred_resolution="a" * 20)
        assert len(obj.preferred_resolution) == 20

        with pytest.raises(ValidationError):
            UserPreferenceBase(preferred_resolution="a" * 21)

    def test_speaker_max_length(self):
        from app.models import UserPreferenceBase

        obj = UserPreferenceBase(preferred_speaker="a" * 50)
        assert len(obj.preferred_speaker) == 50

        with pytest.raises(ValidationError):
            UserPreferenceBase(preferred_speaker="a" * 51)

    def test_project_name_max_length(self):
        from app.models import UserPreferenceBase

        obj = UserPreferenceBase(last_project_name="a" * 255)
        assert len(obj.last_project_name) == 255

        with pytest.raises(ValidationError):
            UserPreferenceBase(last_project_name="a" * 256)

    def test_json_roundtrip(self):
        from app.models import UserPreferenceBase

        original = UserPreferenceBase(
            preferred_resolution="4K",
            preferred_fps=60,
            preferred_speaker="zh_female_qingse",
            frequent_media_paths_json='["D:/videos"]',
            last_project_name="My Project",
        )
        json_str = original.model_dump_json()
        restored = UserPreferenceBase.model_validate_json(json_str)
        assert restored.preferred_resolution == "4K"
        assert restored.preferred_fps == 60
        assert restored.last_project_name == "My Project"


# ---------------------------------------------------------------------------
# Table metadata — table=True classes
# ---------------------------------------------------------------------------

class TestTableMetadata:
    """验证 table=True 类的元数据"""

    def test_edit_task_tablename(self):
        from app.models import EditTask
        assert EditTask.__tablename__ == "edit_task"

    def test_edit_step_tablename(self):
        from app.models import EditStep
        assert EditStep.__tablename__ == "edit_step"

    def test_user_preference_tablename(self):
        from app.models import UserPreference
        assert UserPreference.__tablename__ == "user_preference"

    def test_edit_task_has_table(self):
        from app.models import EditTask
        assert hasattr(EditTask, "__table__")

    def test_edit_step_has_table(self):
        from app.models import EditStep
        assert hasattr(EditStep, "__table__")

    def test_user_preference_has_table(self):
        from app.models import UserPreference
        assert hasattr(UserPreference, "__table__")


# ---------------------------------------------------------------------------
# Public API models
# ---------------------------------------------------------------------------

class TestPublicModels:
    """响应模型字段完整性"""

    def test_edit_task_public_includes_ids(self):
        from app.models import EditTaskPublic
        uid, sid = uuid.uuid4(), uuid.uuid4()
        now = datetime.utcnow()

        obj = EditTaskPublic(
            id=uid, user_id=uid, script_id=sid,
            create_time=now, update_time=now,
            status="completed", current_step=5, total_steps=5,
        )
        assert obj.id == uid
        assert obj.user_id == uid
        assert obj.script_id == sid
        assert obj.status == "completed"

    def test_edit_step_public_includes_ids(self):
        from app.models import EditStepPublic
        sid = uuid.uuid4()
        now = datetime.utcnow()

        obj = EditStepPublic(
            id=sid, task_id=sid, step_index=3, tool_name="export",
            create_time=now, update_time=now,
        )
        assert obj.id == sid
        assert obj.task_id == sid
        assert obj.step_index == 3

    def test_user_preference_public_includes_ids(self):
        from app.models import UserPreferencePublic
        pid, uid = uuid.uuid4(), uuid.uuid4()
        now = datetime.utcnow()

        obj = UserPreferencePublic(
            id=pid, user_id=uid,
            create_time=now, update_time=now,
            preferred_resolution="1080p",
        )
        assert obj.id == pid
        assert obj.user_id == uid
        assert obj.preferred_resolution == "1080p"


# ---------------------------------------------------------------------------
# EditScenePublic list wrapper
# ---------------------------------------------------------------------------

class TestListWrappers:
    def test_edit_steps_public_list_wrapper(self):
        from app.models import EditStepsPublic, EditStepPublic
        import uuid as _uuid
        now = datetime.utcnow()
        tid = _uuid.uuid4()

        items = [
            EditStepPublic(
                id=_uuid.uuid4(), task_id=tid,
                step_index=i, tool_name="test",
                create_time=now, update_time=now,
            )
            for i in range(3)
        ]
        wrapper = EditStepsPublic(data=items, count=len(items))
        assert wrapper.count == 3
        assert len(wrapper.data) == 3


# ---------------------------------------------------------------------------
# Cross-model consistency
# ---------------------------------------------------------------------------

class TestCrossModelConsistency:
    """确保新模型遵循现有代码库约定"""

    def test_create_classes_inherit_base(self):
        from app.models import EditTaskCreate, EditTaskBase, EditStepCreate, EditStepBase
        from app.models import UserPreferenceCreate, UserPreferenceBase

        assert issubclass(EditTaskCreate, EditTaskBase)
        assert issubclass(EditStepCreate, EditStepBase)
        assert issubclass(UserPreferenceCreate, UserPreferenceBase)

    def test_update_classes_are_independent(self):
        from app.models import EditTaskUpdate, EditStepUpdate, UserPreferenceUpdate

        # Update models are standalone SQLModel (not inherit from base)
        # so they can have all fields Optional
        assert EditTaskUpdate.__bases__[0].__name__ == "SQLModel"
        assert EditStepUpdate.__bases__[0].__name__ == "SQLModel"
        assert UserPreferenceUpdate.__bases__[0].__name__ == "SQLModel"

    def test_public_models_inherit_base(self):
        from app.models import EditTaskPublic, EditTaskBase
        from app.models import EditStepPublic, EditStepBase
        from app.models import UserPreferencePublic, UserPreferenceBase

        assert issubclass(EditTaskPublic, EditTaskBase)
        assert issubclass(EditStepPublic, EditStepBase)
        assert issubclass(UserPreferencePublic, UserPreferenceBase)

    def test_is_deleted_has_default_zero(self):
        from app.models import EditTask

        assert EditTask.model_fields["is_deleted"].default == 0

    def test_edit_step_no_is_deleted(self):
        """EditStep 是子记录，通过 CASCADE 随父删除，不需要独立软删除"""
        from app.models import EditStep
        assert "is_deleted" not in EditStep.model_fields
