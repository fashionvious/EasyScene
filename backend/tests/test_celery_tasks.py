"""
Tests for req_09 — Celery heavy step tasks + _dispatch_celery.

All Celery + subprocess operations mocked — no Redis/FFmpeg/TTS needed.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.skills_agent.step_orchestrator import (
    StepSpec, StepOrchestrator,
)


# ============================================================================
# ASYNC_TOOL_TASK_MAP
# ============================================================================

class TestAsyncToolTaskMap:
    """映射表覆盖所有异步 action"""

    def test_all_three_actions_mapped(self):
        from app.agent.skills_agent.celery_tasks import ASYNC_TOOL_TASK_MAP

        cli_map = ASYNC_TOOL_TASK_MAP["execute_cli_script"]
        assert "smart_rough_cut" in cli_map
        assert "export" in cli_map
        assert "universal_tts" in cli_map

    def test_tasks_are_callable(self):
        from app.agent.skills_agent.celery_tasks import ASYNC_TOOL_TASK_MAP

        for task in ASYNC_TOOL_TASK_MAP["execute_cli_script"].values():
            assert callable(task)


# ============================================================================
# _dispatch_celery
# ============================================================================

class TestDispatchCelery:
    """StepOrchestrator._dispatch_celery — Celery 分发"""

    def _mock_registry(self):
        reg = MagicMock()
        spec = MagicMock()
        spec.exec_mode = "async"
        spec.func = MagicMock(return_value="sync_fallback")
        reg.get_spec.return_value = spec
        return reg

    def test_matched_action_submits_celery_task(self):
        """匹配到 Celery task → 提交延迟任务并轮询"""
        mock_task = MagicMock()
        mock_result = MagicMock()
        mock_result.id = "celery-task-abc"
        mock_result.ready.side_effect = [False, False, True]
        mock_result.successful.return_value = True
        mock_result.get.return_value = {"success": True}
        mock_task.delay.return_value = mock_result

        with patch.dict(
            "app.agent.skills_agent.celery_tasks.ASYNC_TOOL_TASK_MAP",
            {"execute_cli_script": {"smart_rough_cut": mock_task}},
            clear=True,
        ):
            orch = StepOrchestrator(tool_registry=self._mock_registry())
            step = StepSpec(tool="execute_cli_script", args={
                "action": "smart_rough_cut", "video_path": "/tmp/v.mp4",
                "step_index": 0,
            })

            result = asyncio.run(orch._dispatch_celery(step, MagicMock()))

        mock_task.delay.assert_called_once()
        assert result == {"success": True}
        assert mock_result.ready.call_count >= 3

    def test_unmatched_action_falls_back_to_sync(self):
        """未匹配到 Celery task → 回退同步执行"""
        with patch.dict(
            "app.agent.skills_agent.celery_tasks.ASYNC_TOOL_TASK_MAP",
            {"execute_cli_script": {}},   # empty inner map
            clear=True,
        ):
            mock_spec = MagicMock()
            mock_spec.func = MagicMock(return_value="sync_done")

            orch = StepOrchestrator(tool_registry=self._mock_registry())
            step = StepSpec(tool="execute_cli_script", args={
                "action": "unknown_action",
            })

            result = asyncio.run(orch._dispatch_celery(step, mock_spec))

        assert result == "sync_done"
        mock_spec.func.assert_called_once()

    def test_unknown_tool_falls_back_to_sync(self):
        """工具名不在 ASYNC_TOOL_TASK_MAP 中 → 回退同步"""
        with patch.dict(
            "app.agent.skills_agent.celery_tasks.ASYNC_TOOL_TASK_MAP",
            {},  # empty outer map
            clear=True,
        ):
            mock_spec = MagicMock()
            mock_spec.func = MagicMock(return_value=42)

            orch = StepOrchestrator()
            step = StepSpec(tool="unknown_tool", args={"action": "x"})

            result = asyncio.run(orch._dispatch_celery(step, mock_spec))

        assert result == 42

    def test_celery_failure_raises_runtime_error(self):
        """Celery 任务失败 → 抛 RuntimeError"""
        mock_task = MagicMock()
        mock_result = MagicMock()
        mock_result.id = "celery-fail"
        mock_result.ready.return_value = True
        mock_result.successful.return_value = False
        mock_result.traceback = "Traceback: ..."
        mock_task.delay.return_value = mock_result

        with patch.dict(
            "app.agent.skills_agent.celery_tasks.ASYNC_TOOL_TASK_MAP",
            {"execute_cli_script": {"export": mock_task}},
            clear=True,
        ):
            orch = StepOrchestrator(tool_registry=self._mock_registry())
            step = StepSpec(tool="execute_cli_script", args={
                "action": "export", "draft_name": "test",
            })

            with pytest.raises(RuntimeError, match="Celery 任务失败"):
                asyncio.run(orch._dispatch_celery(step, MagicMock()))


# ============================================================================
# Celery Task definitions
# ============================================================================

class TestCeleryTaskDefinitions:
    """验证 4 个 Celery Task 的签名和元数据"""

    def test_ffmpeg_task_has_retry_config(self):
        from app.agent.skills_agent.celery_tasks import ffmpeg_normalize_task

        assert ffmpeg_normalize_task.max_retries == 2
        assert ffmpeg_normalize_task.default_retry_delay == 30

    def test_tts_task_has_retry_config(self):
        from app.agent.skills_agent.celery_tasks import tts_synthesis_task

        assert tts_synthesis_task.max_retries == 2
        assert tts_synthesis_task.default_retry_delay == 10

    def test_export_task_has_retry_config(self):
        from app.agent.skills_agent.celery_tasks import auto_export_task

        assert auto_export_task.max_retries == 1
        assert auto_export_task.default_retry_delay == 60

    def test_rough_cut_task_has_retry_config(self):
        from app.agent.skills_agent.celery_tasks import smart_rough_cut_task

        assert smart_rough_cut_task.max_retries == 2
        assert smart_rough_cut_task.default_retry_delay == 30

    def test_all_tasks_are_celery_tasks(self):
        from celery import Task
        from app.agent.skills_agent.celery_tasks import (
            ffmpeg_normalize_task, tts_synthesis_task,
            auto_export_task, smart_rough_cut_task,
        )

        for t in [ffmpeg_normalize_task, tts_synthesis_task,
                  auto_export_task, smart_rough_cut_task]:
            assert hasattr(t, "delay"), f"{t.name} missing delay()"
            assert hasattr(t, "max_retries"), f"{t.name} missing max_retries"
