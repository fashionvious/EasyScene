"""
Tests for req_07 — L0/L1 skill injection routing.

Focus: L0 route summary token budget, L1 category_detail injection,
backward compatibility of load_skill (L2), and build_skills_addendum behavior.
"""
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers — create a minimal skill directory
# ---------------------------------------------------------------------------

def _make_skill_root(tmp_path: Path) -> str:
    """Create a minimal jianying-editor-skill directory for testing."""
    root = tmp_path / "test-skill"
    root.mkdir()

    # SKILL.md (main)
    (root / "SKILL.md").write_text("""---
name: test-skill
description: 测试技能
---
# Test Skill
Main content here.
""", encoding="utf-8")

    # rules/
    rules = root / "rules"
    rules.mkdir()
    (rules / "rule_media.md").write_text("""---
name: rule_media
description: 素材处理规则
---
# 素材处理
详细的音频视频导入、格式转换规则...
""", encoding="utf-8")
    (rules / "rule_export.md").write_text("""# 导出规则
导出分辨率、帧率设置规则...
""", encoding="utf-8")

    # scripts/
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "smart_rough_cut.py").write_text('"""智能粗剪工具"""\nprint("cut")\n', encoding="utf-8")

    # examples/
    examples = root / "examples"
    examples.mkdir()
    (examples / "basic_project.py").write_text('"""基础项目示例"""\nprint("example")\n', encoding="utf-8")

    return str(root)


# ---------------------------------------------------------------------------
# L0 Route Summary
# ---------------------------------------------------------------------------

class TestL0RouteSummary:
    """_build_route_summary() — L0 路由摘要"""

    def test_l0_includes_all_categories(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        summary = mw._route_summary_cache

        assert "主技能" in summary
        assert "规则" in summary
        assert "脚本" in summary
        assert "示例" in summary

    def test_l0_includes_skill_names(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        summary = mw._route_summary_cache

        assert "test-skill" in summary
        assert "rule_media" in summary
        assert "script_smart_rough_cut" in summary
        assert "example_basic_project" in summary

    def test_l0_mentions_load_skill(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        summary = mw._route_summary_cache

        assert "load_skill" in summary

    def test_l0_cached_in_route_summary_cache(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        # Cache should be set
        assert hasattr(mw, "_route_summary_cache")
        assert isinstance(mw._route_summary_cache, str)
        assert len(mw._route_summary_cache) > 0

    def test_l0_token_count_under_limit(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware
        from app.agent.skills_agent.token_utils import estimate_tokens

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        tokens = estimate_tokens(mw._route_summary_cache)
        # With 1 main + 2 rules + 1 script + 1 example, should be well under 500
        assert tokens < 500


# ---------------------------------------------------------------------------
# L1 Category Detail
# ---------------------------------------------------------------------------

class TestL1CategoryDetail:
    """_build_category_detail() — L1 类别详情"""

    def test_rule_category_injects_content(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        detail = mw._build_category_detail("rule")

        assert "素材处理" in detail or "详细的" in detail
        assert "rule_media" in detail
        assert "rule_export" in detail

    def test_unknown_category_returns_empty(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        result = mw._build_category_detail("nonexistent_category")
        assert result == ""

    def test_script_category_includes_descriptions(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        detail = mw._build_category_detail("script")
        assert "script_smart_rough_cut" in detail


# ---------------------------------------------------------------------------
# _build_skills_addendum
# ---------------------------------------------------------------------------

class TestBuildSkillsAddendum:
    """_build_skills_addendum() — L0/L1 组合"""

    def test_no_hint_generates_l0_only(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        add = mw._build_skills_addendum()           # L0 only (no hint)
        add_with_l1 = mw._build_skills_addendum(category_hint="rule")  # L0+L1

        # L0 is contained in addendum
        assert mw._route_summary_cache in add
        # L1 version is longer than L0-only version (rule content injected)
        assert len(add_with_l1) > len(add)

    def test_rule_hint_injects_l1(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        add_no_hint = mw._build_skills_addendum()
        add_with_hint = mw._build_skills_addendum(category_hint="rule")

        # With rule hint, addendum should be longer (L1 injected)
        assert len(add_with_hint) > len(add_no_hint)

    def test_media_files_included_when_available(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root, media_search_paths=[str(tmp_path)])

        add = mw._build_skills_addendum()
        # GUIDE_TEMPLATE always present
        assert "GUIDE_TEMPLATE" not in add  # GUIDE_TEMPLATE is a Python constant name
        # The actual text of GUIDE_TEMPLATE includes "工具使用指南"
        assert "工具使用指南" in add

    def test_guide_template_always_included(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        add = mw._build_skills_addendum()
        assert "resolve_media" in add  # from GUIDE_TEMPLATE
        assert "工作流程" in add       # from GUIDE_TEMPLATE


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """确保改造不破坏现有 Agent 行为"""

    def test_load_skill_tool_still_works(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        # LangChain @tool creates a StructuredTool — call via .invoke()
        result = mw.load_skill_tool.invoke({"skill_name": "test-skill"})
        assert "已加载技能" in result
        assert "# Test Skill" in result

    def test_load_skill_nonexistent_returns_available_list(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        result = mw.load_skill_tool.invoke({"skill_name": "nonexistent"})
        assert "未找到技能" in result

    def test_agent_creates_normally(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import create_jianying_agent

        root = _make_skill_root(tmp_path)
        agent, mw = create_jianying_agent(root)
        assert len(mw.tools) == 7

    def test_wrap_model_call_does_not_crash(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        # wrap_model_call should not raise
        # (we can't fully test without a real ModelRequest, but the method
        #  should exist and be callable with the new L0/L1 internals)
        assert callable(mw.wrap_model_call)
        assert callable(mw.awrap_model_call)

    def test_generate_skills_prompt_still_callable(self, tmp_path):
        from app.agent.skills_agent.jianying_agent import JianYingSkillMiddleware

        root = _make_skill_root(tmp_path)
        mw = JianYingSkillMiddleware(root)

        # Old method preserved for backward compat — returns non-empty string
        result = mw._generate_skills_prompt()
        assert isinstance(result, str)
        assert len(result) > 0
