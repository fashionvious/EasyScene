"""
Tests for feature_flags.py — FeatureFlag + EditFeatureFlags.

Focus: default values, env var parsing, type coercion, frozen immutability,
__bool__ ergonomics, dump_all completeness, and env var isolation.
"""
import os
import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# FeatureFlag — basic construction & defaults
# ---------------------------------------------------------------------------

class TestFeatureFlagDefaults:
    """默认值返回（环境变量未设置时）"""

    def test_bool_default_false(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("TEST_FLAG", False)
        assert ff.get() is False
        assert bool(ff) is False

    def test_bool_default_true(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("TEST_FLAG", True)
        assert ff.get() is True
        assert bool(ff) is True

    def test_int_default(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("TEST_FLAG", 42)
        assert ff.get() == 42

    def test_str_default(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("TEST_FLAG", "sync")
        assert ff.get() == "sync"

    def test_float_default(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("TEST_FLAG", 0.75)
        assert ff.get() == 0.75


# ---------------------------------------------------------------------------
# FeatureFlag — env var parsing
# ---------------------------------------------------------------------------

class TestFeatureFlagEnvParsing:
    """环境变量解析和类型转换"""

    @pytest.fixture(autouse=True)
    def _clean_env(self):
        """每个测试前后清理 TEST_ 环境变量"""
        to_remove = [k for k in os.environ if k.startswith("TEST_")]
        saved = {k: os.environ[k] for k in to_remove}
        for k in to_remove:
            del os.environ[k]
        yield
        for k in to_remove:
            if k in os.environ:
                del os.environ[k]
        os.environ.update(saved)

    def test_bool_true_variants(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        for variant in ("true", "True", "TRUE", "1", "yes", "on"):
            os.environ["TEST_FLAG"] = variant
            ff = FeatureFlag("TEST_FLAG", False)
            assert ff.get() is True, f"variant={variant!r}"
            assert bool(ff) is True

    def test_bool_false_variants(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("TEST_FLAG", True)
        for variant in ("false", "False", "0", "no", "off"):
            os.environ["TEST_FLAG"] = variant
            assert ff.get() is False, f"variant={variant!r}"

    def test_bool_empty_env_uses_default(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        os.environ["TEST_FLAG"] = ""
        ff = FeatureFlag("TEST_FLAG", True)
        assert ff.get() is True

    def test_int_env_var(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        os.environ["TEST_FLAG"] = "8080"
        ff = FeatureFlag("TEST_FLAG", 3000)
        assert ff.get() == 8080

    def test_int_env_var_invalid_falls_back(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        os.environ["TEST_FLAG"] = "not_a_number"
        ff = FeatureFlag("TEST_FLAG", 3000)
        assert ff.get() == 3000  # falls back to default

    def test_float_env_var(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        os.environ["TEST_FLAG"] = "3.14"
        ff = FeatureFlag("TEST_FLAG", 1.0)
        assert ff.get() == 3.14

    def test_str_env_var(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        os.environ["TEST_FLAG"] = "celery"
        ff = FeatureFlag("TEST_FLAG", "sync")
        assert ff.get() == "celery"


# ---------------------------------------------------------------------------
# FeatureFlag — frozen immutability
# ---------------------------------------------------------------------------

class TestFeatureFlagFrozen:
    """frozen=True 禁止修改 Flag 定义"""

    def test_cannot_set_attribute(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("TEST", True)
        with pytest.raises(Exception):
            ff.default = False  # type: ignore[misc]

    def test_cannot_set_key(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("TEST", True)
        with pytest.raises(Exception):
            ff.key = "CHANGED"  # type: ignore[misc]

    def test_repr_includes_current_value(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("TEST_FLAG", False, "a test flag")
        r = repr(ff)
        assert "TEST_FLAG" in r
        assert "False" in r


# ---------------------------------------------------------------------------
# FeatureFlag — __bool__ ergonomics
# ---------------------------------------------------------------------------

class TestFeatureFlagBool:
    """布尔 Flag 的 if 判断便利性"""

    def test_bool_false_flag_is_falsy(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("TEST", False)
        assert not ff
        assert ff.get() is False

    def test_bool_true_flag_is_truthy(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("TEST", True)
        assert ff

    def test_int_flag_bool_follows_truthiness(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff_zero = FeatureFlag("TEST", 0)
        ff_nonzero = FeatureFlag("TEST", 1)

        assert not ff_zero       # 0 is falsy
        assert ff_nonzero        # 1 is truthy


# ---------------------------------------------------------------------------
# EditFeatureFlags — defaults
# ---------------------------------------------------------------------------

class TestEditFeatureFlagsDefaults:
    """所有 9 个 Flag 的默认值验证"""

    @pytest.fixture(autouse=True)
    def _clean_edit_env(self):
        to_remove = [k for k in os.environ if k.startswith(("CB_", "EDIT_", "CONCURRENT_", "CONTEXT_", "USE_", "LANGFUSE_", "DEBUG_"))]
        saved = {k: os.environ[k] for k in to_remove}
        for k in to_remove:
            del os.environ[k]
        yield
        for k in to_remove:
            if k in os.environ:
                del os.environ[k]
        os.environ.update(saved)

    def test_circuit_breaker_flags_default_off(self):
        from app.agent.skills_agent.feature_flags import flags

        assert flags.CB_FFMPEG_ENABLED.get() is False
        assert flags.CB_UI_ENABLED.get() is False

    def test_async_mode_default_sync(self):
        from app.agent.skills_agent.feature_flags import flags

        assert flags.EDIT_ASYNC_MODE.get() == "sync"

    def test_concurrent_exec_default_off(self):
        from app.agent.skills_agent.feature_flags import flags

        assert flags.CONCURRENT_EXEC_ENABLED.get() is False

    def test_context_compact_default_on(self):
        from app.agent.skills_agent.feature_flags import flags

        assert flags.CONTEXT_COMPACT_ENABLED.get() is True

    def test_storyboard_mode_default_off(self):
        from app.agent.skills_agent.feature_flags import flags

        assert flags.USE_STORYBOARD_MODE.get() is False

    def test_langfuse_span_default_on(self):
        from app.agent.skills_agent.feature_flags import flags

        assert flags.LANGFUSE_MANUAL_SPAN_ENABLED.get() is True

    def test_debug_flags_default_off(self):
        from app.agent.skills_agent.feature_flags import flags

        assert flags.DEBUG_KEEP_TEMP_FILES.get() is False
        assert flags.DEBUG_LOG_TOOL_ARGS.get() is False


# ---------------------------------------------------------------------------
# EditFeatureFlags — env override
# ---------------------------------------------------------------------------

class TestEditFeatureFlagsEnvOverride:
    """环境变量覆盖 Flag 默认值"""

    @pytest.fixture(autouse=True)
    def _clean_edit_env(self):
        to_remove = [k for k in os.environ if k.startswith(("CB_", "EDIT_", "CONCURRENT_", "CONTEXT_", "USE_", "LANGFUSE_", "DEBUG_"))]
        saved = {k: os.environ[k] for k in to_remove}
        for k in to_remove:
            del os.environ[k]
        yield
        for k in to_remove:
            if k in os.environ:
                del os.environ[k]
        os.environ.update(saved)

    def test_cb_ffmpeg_enabled_via_env(self):
        from app.agent.skills_agent.feature_flags import flags

        os.environ["CB_FFMPEG_ENABLED"] = "true"
        assert flags.CB_FFMPEG_ENABLED.get() is True
        assert bool(flags.CB_FFMPEG_ENABLED) is True

    def test_edit_async_mode_celery(self):
        from app.agent.skills_agent.feature_flags import flags

        os.environ["EDIT_ASYNC_MODE"] = "celery"
        assert flags.EDIT_ASYNC_MODE.get() == "celery"

    def test_concurrent_exec_enabled_by_1(self):
        from app.agent.skills_agent.feature_flags import flags

        os.environ["CONCURRENT_EXEC_ENABLED"] = "1"
        assert flags.CONCURRENT_EXEC_ENABLED.get() is True

    def test_use_storyboard_mode_enabled(self):
        from app.agent.skills_agent.feature_flags import flags

        os.environ["USE_STORYBOARD_MODE"] = "yes"
        assert flags.USE_STORYBOARD_MODE.get() is True


# ---------------------------------------------------------------------------
# EditFeatureFlags — dump_all
# ---------------------------------------------------------------------------

class TestDumpAll:
    """dump_all() 完整性"""

    def test_dump_all_has_nine_flags(self):
        from app.agent.skills_agent.feature_flags import flags

        data = flags.dump_all()
        assert len(data) == 9

    def test_dump_all_keys(self):
        from app.agent.skills_agent.feature_flags import flags

        data = flags.dump_all()
        expected = {
            "CB_FFMPEG_ENABLED", "CB_UI_ENABLED",
            "EDIT_ASYNC_MODE", "CONCURRENT_EXEC_ENABLED",
            "CONTEXT_COMPACT_ENABLED", "USE_STORYBOARD_MODE",
            "LANGFUSE_MANUAL_SPAN_ENABLED",
            "DEBUG_KEEP_TEMP_FILES", "DEBUG_LOG_TOOL_ARGS",
        }
        assert set(data.keys()) == expected

    def test_dump_all_each_has_value_default_description(self):
        from app.agent.skills_agent.feature_flags import flags

        data = flags.dump_all()
        for name, info in data.items():
            assert "value" in info, f"{name} missing 'value'"
            assert "default" in info, f"{name} missing 'default'"
            assert "description" in info, f"{name} missing 'description'"
            assert isinstance(info["description"], str)
            assert len(info["description"]) > 0

    def test_dump_all_values_match_individual_gets(self):
        from app.agent.skills_agent.feature_flags import flags

        data = flags.dump_all()
        for name, info in data.items():
            flag = getattr(flags, name)
            assert info["value"] == flag.get(), f"{name} mismatch"
            assert info["default"] == flag.default, f"{name} default mismatch"

    def test_dump_all_no_private_attrs(self):
        from app.agent.skills_agent.feature_flags import flags

        data = flags.dump_all()
        for name in data:
            assert not name.startswith("_"), f"Private attr leaked: {name}"


# ---------------------------------------------------------------------------
# EditFeatureFlags — singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    """全局单例一致性"""

    def test_flags_is_edit_feature_flags_instance(self):
        from app.agent.skills_agent.feature_flags import flags, EditFeatureFlags

        assert isinstance(flags, EditFeatureFlags)

    def test_multiple_instances_share_class_attrs(self):
        from app.agent.skills_agent.feature_flags import EditFeatureFlags

        a = EditFeatureFlags()
        b = EditFeatureFlags()
        # Class-level FeatureFlag objects are shared
        assert a.CB_FFMPEG_ENABLED is b.CB_FFMPEG_ENABLED


# ---------------------------------------------------------------------------
# FeatureFlag — edge cases
# ---------------------------------------------------------------------------

class TestFeatureFlagEdgeCases:
    """边界条件"""

    def test_empty_string_key(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("", "default_val")
        # Empty key reads empty env var — returns default
        assert ff.get() == "default_val"

    def test_none_default(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("NONEXISTENT_FLAG_12345", None)
        assert ff.get() is None

    def test_default_preserved_after_multiple_gets(self):
        from app.agent.skills_agent.feature_flags import FeatureFlag

        ff = FeatureFlag("TEST_FLAG", 100)
        assert ff.get() == 100
        assert ff.get() == 100  # idempotent
        assert ff.default == 100
