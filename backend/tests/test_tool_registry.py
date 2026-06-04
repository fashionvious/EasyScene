"""
Tests for tool_registry.py — ToolSpec + ToolRegistry.

Coverage: registration, query methods, frozen immutability,
integration with LangChain @tool functions, edge cases.
"""
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# ToolSpec — frozen dataclass
# ---------------------------------------------------------------------------

class TestToolSpec:
    """ToolSpec 数据类的创建和不可变性"""

    def test_create_minimal_spec(self):
        from app.agent.skills_agent.tool_registry import ToolSpec

        spec = ToolSpec(
            name="test_tool",
            handler="test_handler",
            category="read",
            func=MagicMock(),
        )

        assert spec.name == "test_tool"
        assert spec.handler == "test_handler"
        assert spec.category == "read"
        assert spec.exec_mode == "sync"           # default
        assert spec.concurrency_safe is True       # default
        assert spec.timeout == 30                  # default
        assert spec.max_retries == 0               # default
        assert spec.retry_strategy == "none"       # default

    def test_create_full_spec(self):
        from app.agent.skills_agent.tool_registry import ToolSpec

        mock_fn = MagicMock()
        spec = ToolSpec(
            name="ffmpeg",
            handler="media_normalizer",
            category="compute",
            func=mock_fn,
            exec_mode="async",
            concurrency_safe=False,
            timeout=600,
            max_retries=3,
            retry_strategy="recoverable",
            description="FFmpeg 视频标准化",
        )

        assert spec.name == "ffmpeg"
        assert spec.exec_mode == "async"
        assert spec.concurrency_safe is False
        assert spec.timeout == 600
        assert spec.max_retries == 3
        assert spec.retry_strategy == "recoverable"
        assert spec.func is mock_fn

    def test_frozen_prevents_mutation(self):
        from app.agent.skills_agent.tool_registry import ToolSpec

        spec = ToolSpec(name="t", handler="h", category="read", func=MagicMock())

        with pytest.raises(Exception):  # FrozenInstanceError or similar
            spec.name = "changed"  # type: ignore[misc]

    def test_spec_hashable_for_dict_key(self):
        from app.agent.skills_agent.tool_registry import ToolSpec

        spec = ToolSpec(name="t", handler="h", category="read", func=MagicMock())
        # Frozen dataclass should be hashable; test by using as dict key
        d = {spec: "value"}
        assert d[spec] == "value"


# ---------------------------------------------------------------------------
# ToolRegistry — registration & queries
# ---------------------------------------------------------------------------

class TestToolRegistry:
    """ToolRegistry 注册和查询功能"""

    @pytest.fixture
    def registry(self):
        from app.agent.skills_agent.tool_registry import ToolRegistry, ToolSpec

        reg = ToolRegistry()
        reg.register_many([
            ToolSpec(name="read_a", handler="h1", category="read",
                     func=MagicMock(), concurrency_safe=True, exec_mode="sync"),
            ToolSpec(name="read_b", handler="h2", category="read",
                     func=MagicMock(), concurrency_safe=True, exec_mode="sync"),
            ToolSpec(name="write_a", handler="h3", category="write",
                     func=MagicMock(), concurrency_safe=False, exec_mode="sync"),
            ToolSpec(name="compute_a", handler="h4", category="compute",
                     func=MagicMock(), concurrency_safe=False, exec_mode="async",
                     timeout=600),
        ])
        return reg

    def test_len(self, registry):
        assert len(registry) == 4

    def test_contains(self, registry):
        assert "read_a" in registry
        assert "nonexistent" not in registry

    def test_get_spec_existing(self, registry):
        spec = registry.get_spec("read_a")
        assert spec is not None
        assert spec.name == "read_a"
        assert spec.category == "read"

    def test_get_spec_nonexistent(self, registry):
        assert registry.get_spec("ghost_tool") is None

    def test_get_all_tools_returns_callables(self, registry):
        tools = registry.get_all_tools()
        assert len(tools) == 4
        assert all(callable(t) for t in tools)

    def test_get_tools_by_category(self, registry):
        readers = registry.get_tools_by_category("read")
        assert len(readers) == 2
        assert all(s.category == "read" for s in readers)

        writers = registry.get_tools_by_category("write")
        assert len(writers) == 1

        compute = registry.get_tools_by_category("compute")
        assert len(compute) == 1

    def test_get_tools_by_exec_mode(self, registry):
        sync = registry.get_tools_by_exec_mode("sync")
        assert len(sync) == 3  # read_a, read_b, write_a

        async_tools = registry.get_tools_by_exec_mode("async")
        assert len(async_tools) == 1
        assert async_tools[0].name == "compute_a"

    def test_get_concurrency_safe_tools(self, registry):
        safe = registry.get_concurrency_safe_tools()
        assert len(safe) == 2
        assert {s.name for s in safe} == {"read_a", "read_b"}

    def test_list_names(self, registry):
        names = registry.list_names()
        assert set(names) == {"read_a", "read_b", "write_a", "compute_a"}

    def test_register_duplicate_overwrites(self):
        from app.agent.skills_agent.tool_registry import ToolRegistry, ToolSpec

        reg = ToolRegistry()
        fn1 = MagicMock()
        fn2 = MagicMock()

        reg.register(ToolSpec(name="dup", handler="h1", category="read", func=fn1))
        reg.register(ToolSpec(name="dup", handler="h2", category="write", func=fn2))

        # Spec overwritten; tools list contains both fns
        assert reg.get_spec("dup").category == "write"
        assert len(reg) == 1          # specs deduped by name
        assert len(reg.get_all_tools()) == 2  # both fns in list

    def test_repr(self, registry):
        r = repr(registry)
        assert "ToolRegistry" in r
        assert "4" in r


# ---------------------------------------------------------------------------
# ToolRegistry — default 22-tool integration smoke test (8 core + 14 Phase 1-2)
# ---------------------------------------------------------------------------

class TestDefaultToolRegistry:
    """验证 jianying_agent.py 中创建的实际 22 工具注册表。

    8 个 core tools + 14 个 Phase 1-2 剪辑工具 = 22 工具。
    sync: 21, async: 1 (execute_cli_script).
    """

    @pytest.fixture
    def middleware(self):
        from app.agent.skills_agent.jianying_agent import create_jianying_agent
        import os

        skill_root = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "jianying-editor-skill",
        )
        skill_root = os.path.abspath(skill_root)

        _, mw = create_jianying_agent(skill_root=skill_root)
        return mw

    def test_registry_has_22_tools(self, middleware):
        assert len(middleware.registry) == 22
        # tools list may be longer due to duplicate-registration func append
        assert len(middleware.tools) == 22

    def test_sync_tools_count(self, middleware):
        sync = middleware.registry.get_tools_by_exec_mode("sync")
        assert len(sync) == 21

    def test_async_tools_count(self, middleware):
        async_tools = middleware.registry.get_tools_by_exec_mode("async")
        assert len(async_tools) == 1
        assert async_tools[0].name == "execute_cli_script"

    def test_concurrency_safe_tools(self, middleware):
        safe = middleware.registry.get_concurrency_safe_tools()
        assert len(safe) == 5
        names = {s.name for s in safe}
        assert names == {
            "load_skill", "resolve_media", "list_media",
            "list_cli_scripts", "validate_jyproject_code",
        }

    def test_execute_cli_script_is_async_compute(self, middleware):
        spec = middleware.registry.get_spec("execute_cli_script")
        assert spec is not None
        assert spec.exec_mode == "async"
        assert spec.category == "compute"
        assert spec.concurrency_safe is False
        assert spec.timeout == 600
        assert spec.retry_strategy == "recoverable"
        assert spec.max_retries == 2

    def test_execute_jyproject_is_sync_write(self, middleware):
        spec = middleware.registry.get_spec("execute_jyproject_code")
        assert spec is not None
        assert spec.exec_mode == "sync"
        assert spec.category == "write"
        assert spec.concurrency_safe is False
        assert spec.timeout == 300

    def test_all_read_tools_concurrency_safe(self, middleware):
        readers = middleware.registry.get_tools_by_category("read")
        assert len(readers) == 5
        for spec in readers:
            assert spec.concurrency_safe is True, (
                f"read tool '{spec.name}' should be concurrency_safe"
            )

    # ── Phase 1 tools smoke tests ──

    _PHASE1_TOOLS = [
        ("apply_jcut",            "timeline_ops",    "write", 120),
        ("apply_lcut",            "timeline_ops",    "write", 120),
        ("reorder_segments",      "timeline_ops",    "write", 120),
        ("apply_split_screen",    "layout_ops",      "write", 120),
        ("add_dual_subtitles",    "subtitle_ops",    "write", 120),
        ("apply_zoom_transition", "transition_ops",  "write", 120),
        ("apply_push_transition", "transition_ops",  "write", 120),
    ]

    @pytest.mark.parametrize("name,handler,category,timeout", _PHASE1_TOOLS)
    def test_phase1_tool_registered(self, middleware, name, handler, category, timeout):
        spec = middleware.registry.get_spec(name)
        assert spec is not None, f"Phase 1 tool '{name}' not registered"
        assert spec.handler == handler
        assert spec.category == category
        assert spec.exec_mode == "sync"
        assert spec.concurrency_safe is False
        assert spec.timeout == timeout

    # ── Phase 2 tools smoke tests ──

    _PHASE2_TOOLS = [
        ("inject_mask_transition",  "draft_injector", "write", 60),
        ("inject_color_transition", "draft_injector", "write", 60),
        ("add_karaoke_subtitle",    "draft_injector", "write", 120),
        ("inject_subtitle_slide",   "draft_injector", "write", 60),
        ("apply_bgm_ducking",       "draft_injector", "write", 120),
        ("apply_audio_speed",       "draft_injector", "write", 60),
        ("apply_speed_ramp",        "draft_injector", "write", 60),
    ]

    @pytest.mark.parametrize("name,handler,category,timeout", _PHASE2_TOOLS)
    def test_phase2_tool_registered(self, middleware, name, handler, category, timeout):
        spec = middleware.registry.get_spec(name)
        assert spec is not None, f"Phase 2 tool '{name}' not registered"
        assert spec.handler == handler
        assert spec.category == category
        assert spec.exec_mode == "sync"
        assert spec.concurrency_safe is False
        assert spec.timeout == timeout

    def test_registry_get_all_tools_equals_middleware_tools(self, middleware):
        reg_tools = middleware.registry.get_all_tools()
        mw_tools = middleware.tools

        assert reg_tools == mw_tools
        # Same objects (not just equal)
        assert all(a is b for a, b in zip(reg_tools, mw_tools))

    def test_tools_list_is_copy_not_reference(self, middleware):
        tools1 = middleware.registry.get_all_tools()
        tools2 = middleware.registry.get_all_tools()
        assert tools1 == tools2
        assert tools1 is not tools2  # different list objects

    def test_tools_is_stable_after_repeated_calls(self, middleware):
        names1 = middleware.registry.list_names()
        names2 = middleware.registry.list_names()
        assert names1 == names2


# ---------------------------------------------------------------------------
# ToolRegistry — edge cases
# ---------------------------------------------------------------------------

class TestToolRegistryEdgeCases:
    """边界条件"""

    def test_empty_registry(self):
        from app.agent.skills_agent.tool_registry import ToolRegistry

        reg = ToolRegistry()
        assert len(reg) == 0
        assert reg.get_all_tools() == []
        assert reg.get_tools_by_category("read") == []
        assert reg.get_tools_by_exec_mode("sync") == []
        assert reg.get_concurrency_safe_tools() == []
        assert reg.list_names() == []
        assert reg.get_spec("nonexistent") is None

    def test_single_tool_registry(self):
        from app.agent.skills_agent.tool_registry import ToolRegistry, ToolSpec

        reg = ToolRegistry()
        fn = MagicMock()
        reg.register(ToolSpec(name="only", handler="h", category="read", func=fn))

        assert len(reg) == 1
        assert reg.get_all_tools() == [fn]
        assert reg.get_spec("only").func is fn

    def test_category_filter_empty_result(self):
        from app.agent.skills_agent.tool_registry import ToolRegistry, ToolSpec

        reg = ToolRegistry()
        reg.register(ToolSpec(name="r", handler="h", category="read", func=MagicMock()))

        assert reg.get_tools_by_category("write") == []
        assert reg.get_tools_by_category("compute") == []

    def test_exec_mode_filter_empty_result(self):
        from app.agent.skills_agent.tool_registry import ToolRegistry, ToolSpec

        reg = ToolRegistry()
        reg.register(ToolSpec(name="r", handler="h", category="read",
                              func=MagicMock(), exec_mode="sync"))

        assert reg.get_tools_by_exec_mode("async") == []
