"""
Tests for req_15 — SessionMemory user preference management.

Focus: extract_from_plan, build_preference_prompt, UserPreferenceSnapshot.
load/save use mocked PG session.
"""
import json
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# UserPreferenceSnapshot
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_defaults_all_none(self):
        from app.agent.skills_agent.session_memory import UserPreferenceSnapshot

        s = UserPreferenceSnapshot()
        assert s.preferred_resolution is None
        assert s.preferred_fps is None
        assert s.preferred_speaker is None
        assert s.frequent_media_paths == []
        assert s.last_project_name is None

    def test_full_fields(self):
        from app.agent.skills_agent.session_memory import UserPreferenceSnapshot

        s = UserPreferenceSnapshot(
            preferred_resolution="1080p", preferred_fps=30,
            preferred_speaker="zh_male", frequent_media_paths=["D:/videos"],
            last_project_name="test",
        )
        assert s.preferred_resolution == "1080p"
        assert s.frequent_media_paths == ["D:/videos"]


# ---------------------------------------------------------------------------
# extract_from_plan
# ---------------------------------------------------------------------------

class TestExtractFromPlan:
    """从分镜 JSON 提取用户偏好"""

    def test_extracts_resolution_from_config(self):
        from app.agent.skills_agent.session_memory import SessionMemory

        mem = SessionMemory("u1")
        storyboard = {
            "project_name": "My Video",
            "project_config": {"width": 1920, "height": 1080, "fps": 30},
            "steps": [],
        }

        snap = mem.extract_from_plan(storyboard)
        assert snap.preferred_resolution == "1080p"
        assert snap.preferred_fps == 30
        assert snap.last_project_name == "My Video"

    def test_extracts_4k_resolution(self):
        from app.agent.skills_agent.session_memory import SessionMemory

        mem = SessionMemory("u1")
        storyboard = {
            "project_config": {"width": 3840},
            "steps": [],
        }

        snap = mem.extract_from_plan(storyboard)
        assert snap.preferred_resolution == "4K"

    def test_extracts_720p(self):
        from app.agent.skills_agent.session_memory import SessionMemory

        mem = SessionMemory("u1")
        storyboard = {
            "project_config": {"width": 1280},
            "steps": [],
        }

        snap = mem.extract_from_plan(storyboard)
        assert snap.preferred_resolution == "720p"

    def test_extracts_speaker_from_steps(self):
        from app.agent.skills_agent.session_memory import SessionMemory

        mem = SessionMemory("u1")
        storyboard = {
            "steps": [
                {"action": "add_tts", "text": "hello", "speaker": "zh_male_huoli"},
                {"action": "import_media", "file": "v.mp4"},
            ],
        }

        snap = mem.extract_from_plan(storyboard)
        assert snap.preferred_speaker == "zh_male_huoli"

    def test_extracts_media_paths(self):
        from app.agent.skills_agent.session_memory import SessionMemory

        mem = SessionMemory("u1")
        storyboard = {
            "steps": [
                {"action": "import_media",
                 "file": "D:/videos/test.mp4", "start_time": "0s"},
            ],
        }

        snap = mem.extract_from_plan(storyboard)
        assert "D:/videos" in snap.frequent_media_paths

    def test_caps_media_paths_at_five(self):
        from app.agent.skills_agent.session_memory import SessionMemory

        mem = SessionMemory("u1")
        storyboard = {
            "steps": [
                {"action": "import_media", "file": f"D:/path{i}/file.mp4"}
                for i in range(10)
            ],
        }

        snap = mem.extract_from_plan(storyboard)
        assert len(snap.frequent_media_paths) == 5

    def test_empty_storyboard_does_not_crash(self):
        from app.agent.skills_agent.session_memory import SessionMemory

        mem = SessionMemory("u1")
        snap = mem.extract_from_plan({"steps": []})
        assert snap.preferred_resolution is None


# ---------------------------------------------------------------------------
# build_preference_prompt
# ---------------------------------------------------------------------------

class TestBuildPreferencePrompt:
    """偏好提示构建"""

    def test_no_session_returns_empty(self):
        from app.agent.skills_agent.session_memory import SessionMemory

        mem = SessionMemory("u1")
        result = mem.build_preference_prompt(session=None)
        assert result == ""

    def test_new_user_returns_empty(self):
        from app.agent.skills_agent.session_memory import SessionMemory, UserPreferenceSnapshot

        mem = SessionMemory("u1")
        # Mock load_preferences to return empty snapshot
        mem.load_preferences = MagicMock(return_value=UserPreferenceSnapshot())

        mock_session = MagicMock()
        result = mem.build_preference_prompt(session=mock_session)
        assert result == ""

    def test_full_preferences_includes_all(self):
        from app.agent.skills_agent.session_memory import SessionMemory, UserPreferenceSnapshot

        mem = SessionMemory("u1")
        mem.load_preferences = MagicMock(return_value=UserPreferenceSnapshot(
            preferred_resolution="1080p", preferred_fps=30,
            preferred_speaker="zh_male_huoli",
            last_project_name="My Project",
        ))

        result = mem.build_preference_prompt(session=MagicMock())
        assert "1080p" in result
        assert "30fps" in result
        assert "zh_male_huoli" in result
        assert "My Project" in result
        assert "用户编辑偏好" in result

    def test_token_count_under_200(self):
        from app.agent.skills_agent.session_memory import SessionMemory, UserPreferenceSnapshot
        from app.agent.skills_agent.token_utils import estimate_tokens

        mem = SessionMemory("u1")
        mem.load_preferences = MagicMock(return_value=UserPreferenceSnapshot(
            preferred_resolution="1080p", preferred_fps=30,
            preferred_speaker="zh_male_huoli",
            last_project_name="My Project",
        ))

        result = mem.build_preference_prompt(session=MagicMock())
        tokens = estimate_tokens(result)
        assert tokens < 200

    def test_partial_preferences_only_shows_set_fields(self):
        from app.agent.skills_agent.session_memory import SessionMemory, UserPreferenceSnapshot

        mem = SessionMemory("u1")
        mem.load_preferences = MagicMock(return_value=UserPreferenceSnapshot(
            preferred_fps=60,
        ))

        result = mem.build_preference_prompt(session=MagicMock())
        assert "60fps" in result
        assert "分辨率" not in result
        assert "发音人" not in result

    def test_pg_error_returns_empty(self):
        from app.agent.skills_agent.session_memory import SessionMemory

        mem = SessionMemory("u1")
        mem.load_preferences = MagicMock(side_effect=RuntimeError("DB down"))

        result = mem.build_preference_prompt(session=MagicMock())
        assert result == ""
