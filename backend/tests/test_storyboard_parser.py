"""
Tests for storyboard_parser.py — StoryboardParser, ACTION_TO_TOOL, validate.

Pure logic tests — no external deps, no mocks needed.
"""
import json

import pytest

from app.agent.skills_agent.storyboard_parser import (
    StoryboardParser, ACTION_TO_TOOL, AVAILABLE_ACTIONS,
)


# ---------------------------------------------------------------------------
# ACTION_TO_TOOL mapping
# ---------------------------------------------------------------------------

class TestActionMapping:
    """验证映射表覆盖所有 7 个工具"""

    def test_all_seven_tools_covered(self):
        tools = set(ACTION_TO_TOOL.values())
        assert "resolve_media" in tools
        assert "list_media" in tools
        assert "execute_cli_script" in tools
        assert "execute_jyproject_code" in tools
        # All actions map to exactly these 4 tools
        assert tools == {
            "resolve_media", "list_media",
            "execute_cli_script", "execute_jyproject_code",
        }

    def test_jyproject_actions(self):
        """import_media, add_text, add_tts 等映射到 execute_jyproject_code"""
        jyproject_actions = [
            "import_media", "add_text", "add_tts", "add_audio",
            "add_effect", "add_transition", "add_subtitle",
        ]
        for action in jyproject_actions:
            assert ACTION_TO_TOOL[action] == "execute_jyproject_code", action

    def test_cli_script_actions(self):
        """smart_rough_cut, export 等映射到 execute_cli_script"""
        cli_actions = ["smart_rough_cut", "export", "asset_search", "web_record"]
        for action in cli_actions:
            assert ACTION_TO_TOOL[action] == "execute_cli_script", action

    def test_direct_tool_actions(self):
        """resolve_media 和 list_media 直接映射到自己"""
        assert ACTION_TO_TOOL["resolve_media"] == "resolve_media"
        assert ACTION_TO_TOOL["list_media"] == "list_media"


# ---------------------------------------------------------------------------
# StoryboardParser.parse()
# ---------------------------------------------------------------------------

class TestParse:
    """parse() — JSON → EditPlan 转换"""

    def test_parse_minimal_storyboard(self):
        parser = StoryboardParser()
        storyboard = {
            "project_name": "test",
            "steps": [
                {"action": "import_media", "file": "test.mp4", "start_time": "0s"},
            ],
        }

        plan = parser.parse(storyboard, user_id="u1", script_id="s1")

        assert plan.user_id == "u1"
        assert plan.script_id == "s1"
        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "execute_jyproject_code"
        assert plan.steps[0].args["action"] == "import_media"
        assert plan.steps[0].args["file"] == "test.mp4"
        assert plan.status.value == "planning"

    def test_parse_multiple_steps(self):
        parser = StoryboardParser()
        storyboard = {
            "steps": [
                {"action": "import_media", "file": "a.mp4", "start_time": "0s"},
                {"action": "add_text", "text": "Hello", "start_time": "0s", "duration": "3s"},
                {"action": "export", "draft_name": "my", "resolution": "1080p"},
            ],
        }

        plan = parser.parse(storyboard, user_id="u1", script_id="s1")

        assert len(plan.steps) == 3
        assert plan.steps[0].tool == "execute_jyproject_code"
        assert plan.steps[1].tool == "execute_jyproject_code"
        assert plan.steps[2].tool == "execute_cli_script"

    def test_parse_injects_project_context_into_each_step(self):
        parser = StoryboardParser()
        storyboard = {
            "project_name": "My Video",
            "project_config": {"width": 1920, "height": 1080, "fps": 30},
            "steps": [
                {"action": "import_media", "file": "v.mp4", "start_time": "0s"},
            ],
        }

        plan = parser.parse(storyboard, user_id="u1", script_id="s1")

        step = plan.steps[0]
        assert step.args["project_name"] == "My Video"
        assert step.args["project_config"] == {"width": 1920, "height": 1080, "fps": 30}

    def test_parse_stores_project_state(self):
        parser = StoryboardParser()
        storyboard = {
            "project_name": "Cool Video",
            "steps": [{"action": "list_media"}],
        }

        plan = parser.parse(storyboard, user_id="u1", script_id="s1")

        assert plan.project_state["project_name"] == "Cool Video"

    def test_parse_from_json_string(self):
        parser = StoryboardParser()
        json_str = json.dumps({
            "steps": [{"action": "resolve_media", "query": "test.mp4"}],
        })

        plan = parser.parse(json_str, user_id="u1", script_id="s1")

        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "resolve_media"

    def test_parse_empty_steps_creates_empty_plan(self):
        parser = StoryboardParser()
        plan = parser.parse({"steps": []}, user_id="u1", script_id="s1")

        assert len(plan.steps) == 0
        assert plan.status.value == "planning"

    def test_parse_unknown_action_raises_valueerror(self):
        parser = StoryboardParser()
        storyboard = {"steps": [{"action": "nonexistent_action"}]}

        with pytest.raises(ValueError, match="未知 action"):
            parser.parse(storyboard, user_id="u1", script_id="s1")


# ---------------------------------------------------------------------------
# StoryboardParser.validate()
# ---------------------------------------------------------------------------

class TestValidate:
    """validate() — 预校验逻辑"""

    def test_valid_storyboard_returns_empty_errors(self):
        parser = StoryboardParser()
        storyboard = {
            "steps": [
                {"action": "import_media", "file": "test.mp4", "start_time": "0s"},
                {"action": "export", "draft_name": "my"},
            ],
        }

        errors = parser.validate(storyboard)
        assert errors == []

    def test_missing_steps(self):
        parser = StoryboardParser()
        errors = parser.validate({"steps": []})
        assert len(errors) == 1
        assert "为空" in errors[0]

    def test_missing_action_field(self):
        parser = StoryboardParser()
        storyboard = {"steps": [{"file": "test.mp4"}]}  # no action

        errors = parser.validate(storyboard)
        assert len(errors) == 1
        assert "缺少 action" in errors[0]

    def test_unknown_action(self):
        parser = StoryboardParser()
        storyboard = {"steps": [{"action": "fly_to_moon"}]}

        errors = parser.validate(storyboard)
        assert len(errors) == 1
        assert "未知 action" in errors[0]
        assert "fly_to_moon" in errors[0]

    def test_import_media_missing_file(self):
        parser = StoryboardParser()
        storyboard = {"steps": [{"action": "import_media", "start_time": "0s"}]}

        errors = parser.validate(storyboard)
        assert len(errors) == 1
        assert "缺少 file" in errors[0]

    def test_add_tts_missing_text(self):
        parser = StoryboardParser()
        storyboard = {"steps": [{"action": "add_tts", "speaker": "zh"}]}

        errors = parser.validate(storyboard)
        assert len(errors) == 1
        assert "缺少 text" in errors[0]

    def test_add_text_missing_text(self):
        parser = StoryboardParser()
        storyboard = {"steps": [{"action": "add_text", "duration": "3s"}]}

        errors = parser.validate(storyboard)
        assert len(errors) == 1
        assert "缺少 text" in errors[0]

    def test_export_missing_draft_name(self):
        parser = StoryboardParser()
        storyboard = {"steps": [{"action": "export", "resolution": "1080p"}]}

        errors = parser.validate(storyboard)
        assert len(errors) == 1
        assert "缺少 draft_name" in errors[0]

    def test_multiple_errors_accumulated(self):
        parser = StoryboardParser()
        storyboard = {
            "steps": [
                {"action": "unknown_1"},
                {"action": "unknown_2"},
                {"action": "import_media", "start_time": "0s"},  # missing file
            ],
        }

        errors = parser.validate(storyboard)
        assert len(errors) == 3

    def test_steps_not_a_list(self):
        parser = StoryboardParser()
        errors = parser.validate({"steps": "not_a_list"})
        assert len(errors) == 1
        assert "必须是数组" in errors[0]

    def test_step_not_a_dict(self):
        parser = StoryboardParser()
        errors = parser.validate({"steps": ["not_a_dict"]})
        assert len(errors) == 1
        assert "必须是 JSON 对象" in errors[0]

    def test_invalid_json_string(self):
        parser = StoryboardParser()
        errors = parser.validate("not valid json {{{")
        assert len(errors) == 1
        assert "JSON 解析失败" in errors[0]

    def test_validate_from_json_string(self):
        parser = StoryboardParser()
        valid_json = json.dumps({
            "steps": [{"action": "list_media"}],
        })
        errors = parser.validate(valid_json)
        assert errors == []
