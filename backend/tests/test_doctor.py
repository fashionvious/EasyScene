"""
Tests for req_22 — doctor health check.

Focus: run_all_checks, format_doctor_output, CheckResult,
and pure checks (Python, FFmpeg, Langfuse, disk).
Infrastructure checks (Redis, PG, Jianying) are tested for error resilience.
"""
import json
import os
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------

class TestCheckResult:
    def test_defaults(self):
        from app.agent.skills_agent.doctor import CheckResult

        r = CheckResult(name="test", status="PASS", message="ok")
        assert r.name == "test"
        assert r.detail == ""
        assert r.duration_ms == 0.0

    def test_full_fields(self):
        from app.agent.skills_agent.doctor import CheckResult

        r = CheckResult(
            name="Test", status="FAIL", message="error",
            detail="details here", duration_ms=12.5,
        )
        assert r.status == "FAIL"
        assert r.duration_ms == 12.5


# ---------------------------------------------------------------------------
# Pure checks (no infrastructure needed)
# ---------------------------------------------------------------------------

class TestPureChecks:
    """不依赖 Redis/PG/Jianying 的检查"""

    def test_python_version_passes(self):
        # Python 3.12 >= 3.11 → PASS
        from app.agent.skills_agent.doctor import check_python
        r = check_python()
        assert r.status == "PASS"
        assert "3." in r.message

    def test_ffmpeg_check(self):
        from app.agent.skills_agent.doctor import check_ffmpeg

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            r = check_ffmpeg()
            assert r.status == "PASS"

    def test_ffmpeg_missing(self):
        from app.agent.skills_agent.doctor import check_ffmpeg

        with patch("shutil.which", return_value=None):
            r = check_ffmpeg()
            assert r.status == "FAIL"

    def test_langfuse_configured(self):
        from app.agent.skills_agent.doctor import check_langfuse

        with patch.dict(os.environ, {
            "LANGFUSE_SECRET_KEY": "sk-xxx",
            "LANGFUSE_PUBLIC_KEY": "pk-xxx",
        }):
            r = check_langfuse()
            assert r.status == "PASS"

    def test_langfuse_not_configured(self):
        from app.agent.skills_agent.doctor import check_langfuse

        with patch.dict(os.environ, {}, clear=True):
            r = check_langfuse()
            assert r.status == "SKIP"

    def test_disk_space(self):
        from app.agent.skills_agent.doctor import check_disk_space

        r = check_disk_space()
        assert r.status in ("PASS", "WARN")  # depends on actual disk

    def test_skill_root_check(self):
        from app.agent.skills_agent.doctor import check_skill_root

        r = check_skill_root()
        # Should have found the skill root or reported it missing
        assert r.status in ("PASS", "FAIL")

    def test_feature_flags_check(self):
        from app.agent.skills_agent.doctor import check_feature_flags

        r = check_feature_flags()
        assert r.status in ("PASS", "SKIP")


# ---------------------------------------------------------------------------
# run_all_checks
# ---------------------------------------------------------------------------

class TestRunAllChecks:
    def test_returns_list_of_check_results(self):
        from app.agent.skills_agent.doctor import run_all_checks

        results = run_all_checks()
        assert len(results) >= 8  # at least 8 checks registered
        for r in results:
            assert hasattr(r, "name")
            assert hasattr(r, "status")

    def test_individual_failure_does_not_stop_others(self):
        from app.agent.skills_agent.doctor import CHECKS, run_all_checks

        # Temporarily add a check that always crashes
        original_len = len(CHECKS)
        from app.agent.skills_agent.doctor import register_check

        @register_check("Always Crash", "测试")
        def _crash():
            raise RuntimeError("boom")

        try:
            results = run_all_checks()
            assert len(results) == original_len + 1
            # The crash check should be FAIL
            crash_result = results[-1]
            assert crash_result.status == "FAIL"
            assert "boom" in crash_result.message
        finally:
            CHECKS.pop()  # clean up


# ---------------------------------------------------------------------------
# format_doctor_output
# ---------------------------------------------------------------------------

class TestFormatDoctorOutput:
    def test_empty_results(self):
        from app.agent.skills_agent.doctor import format_doctor_output

        output = format_doctor_output([])
        assert "Total: 0" in output

    def test_verbose_shows_detail(self):
        from app.agent.skills_agent.doctor import format_doctor_output, CheckResult

        results = [
            CheckResult(name="Test", status="PASS", message="ok",
                       detail="extra info", duration_ms=5.0),
        ]
        output = format_doctor_output(results, verbose=True)
        assert "extra info" in output

    def test_json_output(self):
        from app.agent.skills_agent.doctor import CheckResult

        r = CheckResult(name="T", status="PASS", message="ok",
                       detail="d", duration_ms=1.0)
        data = {
            "name": r.name, "status": r.status,
            "message": r.message, "detail": r.detail,
            "duration_ms": r.duration_ms,
        }
        json_str = json.dumps(data)
        parsed = json.loads(json_str)
        assert parsed["name"] == "T"
        assert parsed["status"] == "PASS"


# ---------------------------------------------------------------------------
# Infrastructure checks (error resilience)
# ---------------------------------------------------------------------------

class TestInfrastructureChecks:
    """Redis/PG 不可用时应优雅返回 FAIL，不抛异常"""

    def test_redis_unavailable_returns_fail(self):
        with patch(
            "app.agent.utils.redis.get_video_project_manager",
            side_effect=ConnectionError("redis down"),
        ):
            from app.agent.skills_agent.doctor import check_redis
            r = check_redis()
            assert r.status == "FAIL"

    def test_postgresql_unavailable_returns_fail(self):
        with patch(
            "app.core.db.engine.connect",
            side_effect=Exception("pg down"),
        ):
            from app.agent.skills_agent.doctor import check_postgresql
            r = check_postgresql()
            assert r.status == "FAIL"

    def test_jianying_on_non_windows(self):
        # Simulate non-Windows by making winreg import fail
        import builtins
        real_import = builtins.__import__

        def _block_winreg(name, *args, **kwargs):
            if name == "winreg":
                raise ImportError("No module named 'winreg'")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_block_winreg):
            from app.agent.skills_agent.doctor import check_jianying_app
            r = check_jianying_app()
            assert r.status == "SKIP"
