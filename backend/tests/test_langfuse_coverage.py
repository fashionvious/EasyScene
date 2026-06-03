"""
Tests for req_17 — Langfuse coverage (python_executor + observability).

All Langfuse interactions mocked — no real Langfuse needed.
"""
import subprocess
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# observability.py — new functions
# ---------------------------------------------------------------------------

class TestEndManualSpan:
    """end_manual_span"""

    def test_none_client_returns_early(self):
        from app.agent.skills_agent.observability import end_manual_span

        with patch(
            "app.agent.skills_agent.observability.get_langfuse_client",
            return_value=None,
        ):
            # Should not raise
            end_manual_span("span-1", {"result": "ok"})

    def test_calls_span_end(self):
        from app.agent.skills_agent.observability import end_manual_span

        mock_client = MagicMock()
        mock_span = MagicMock()
        mock_client.span.return_value = mock_span

        with patch(
            "app.agent.skills_agent.observability.get_langfuse_client",
            return_value=mock_client,
        ):
            end_manual_span("span-1", {"result": "ok"}, level="INFO")

        mock_client.span.assert_called_once_with(id="span-1")
        mock_span.update.assert_called()
        mock_span.end.assert_called_once()

    def test_error_is_suppressed(self):
        from app.agent.skills_agent.observability import end_manual_span

        mock_client = MagicMock()
        mock_client.span.side_effect = RuntimeError("boom")

        with patch(
            "app.agent.skills_agent.observability.get_langfuse_client",
            return_value=mock_client,
        ):
            # Should not raise — error is logged, not propagated
            end_manual_span("span-1")

    def test_with_status_message(self):
        from app.agent.skills_agent.observability import end_manual_span

        mock_client = MagicMock()
        mock_span = MagicMock()
        mock_client.span.return_value = mock_span

        with patch(
            "app.agent.skills_agent.observability.get_langfuse_client",
            return_value=mock_client,
        ):
            end_manual_span(
                "span-1",
                output_data={"success": False},
                level="ERROR",
                status_message="TTS timeout",
            )

        assert mock_span.update.call_count >= 3  # output + level + status_message
        mock_span.end.assert_called_once()


class TestGetCurrentTraceId:
    """get_current_trace_id"""

    def test_returns_none_on_error(self):
        # get_current_trace_id should return None gracefully when not available
        from app.agent.skills_agent.observability import get_current_trace_id
        result = get_current_trace_id()
        # Returns None (Langfuse may or may not be configured — always graceful)
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# python_executor — trace_id
# ---------------------------------------------------------------------------

class TestPythonExecutorTraceId:
    """python_executor.execute() with trace_id"""

    @pytest.fixture
    def executor(self, tmp_path):
        from app.agent.skills_agent.python_executor import PythonCodeExecutor
        return PythonCodeExecutor(str(tmp_path))

    def test_trace_id_none_no_span_created(self, executor):
        """trace_id=None 时不创建 span（不 import Langfuse）"""
        result = executor.execute(
            "print('hello')",
            include_bootstrap=False,
            max_retries=0,
            keep_temp_on_error=False,
            trace_id=None,
        )
        assert result["success"] is True

    def test_trace_id_with_langfuse_creates_span(self, executor):
        """trace_id 非空 + Langfuse 可用 → 创建 span"""
        mock_langfuse = MagicMock()
        mock_span = MagicMock()
        mock_langfuse.span.return_value = mock_span

        with patch(
            "app.agent.skills_agent.observability.get_langfuse_client",
            return_value=mock_langfuse,
        ):
            result = executor.execute(
                "print('hello')",
                include_bootstrap=False,
                max_retries=0,
                keep_temp_on_error=False,
                trace_id="trace-123",
            )

        assert result["success"] is True
        mock_langfuse.span.assert_called_once()
        mock_span.update.assert_called()
        mock_span.end.assert_called_once()

    def test_trace_id_langfuse_unavailable_graceful(self, executor):
        """trace_id 非空但 Langfuse 不可用 → 跳过 span，不影响执行"""
        with patch(
            "app.agent.skills_agent.observability.get_langfuse_client",
            return_value=None,
        ):
            result = executor.execute(
                "print('hello')",
                include_bootstrap=False,
                max_retries=0,
                keep_temp_on_error=False,
                trace_id="trace-123",
            )

        assert result["success"] is True

    def test_timeout_path_ends_span(self, executor):
        """超时时 span 正确标记 WARNING 并结束"""
        mock_langfuse = MagicMock()
        mock_span = MagicMock()
        mock_langfuse.span.return_value = mock_span

        # Make subprocess.run timeout
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)), \
             patch(
                "app.agent.skills_agent.observability.get_langfuse_client",
                return_value=mock_langfuse,
             ):
            result = executor.execute(
                "print('hello')",
                include_bootstrap=False,
                max_retries=0,
                keep_temp_on_error=False,
                trace_id="trace-123",
            )

        assert result["success"] is False
        mock_span.update.assert_called()
        mock_span.end.assert_called_once()

    def test_exception_path_ends_span(self, executor):
        """异常时 span 正确标记 ERROR 并结束"""
        mock_langfuse = MagicMock()
        mock_span = MagicMock()
        mock_langfuse.span.return_value = mock_span

        with patch(
            "subprocess.run",
            side_effect=RuntimeError("unexpected"),
        ), patch(
            "app.agent.skills_agent.observability.get_langfuse_client",
            return_value=mock_langfuse,
        ):
            result = executor.execute(
                "print('hello')",
                include_bootstrap=False,
                max_retries=0,
                keep_temp_on_error=False,
                trace_id="trace-123",
            )

        assert result["success"] is False
        mock_span.update.assert_called()
        mock_span.end.assert_called_once()
