"""
Tests for req_20 — metrics CLI (get_recent_tasks, get_overall_stats, format_metrics_table).

All PG queries mocked — no real database needed.
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


def _mock_task(task_id, status="completed", is_deleted=0, error_message=None,
               create_time=None, script_id=None):
    t = MagicMock()
    t.id = task_id or uuid.uuid4()
    t.script_id = script_id or uuid.uuid4()
    t.status = status
    t.is_deleted = is_deleted
    t.error_message = error_message
    t.create_time = create_time or datetime.utcnow()
    return t


def _mock_step(step_index, status="done", retry_count=0, started_at=None, finished_at=None):
    s = MagicMock()
    s.step_index = step_index
    s.status = status
    s.retry_count = retry_count
    s.started_at = started_at
    s.finished_at = finished_at
    return s


# ---------------------------------------------------------------------------
# get_recent_tasks
# ---------------------------------------------------------------------------

class TestGetRecentTasks:
    def test_returns_empty_list_when_no_tasks(self):
        with patch("app.agent.skills_agent.metrics.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.exec.return_value.all.return_value = []
            mock_session_cls.return_value.__enter__.return_value = mock_session

            from app.agent.skills_agent.metrics import get_recent_tasks
            result = get_recent_tasks(limit=5)

            assert result == []

    def test_aggregates_task_stats(self):
        with patch("app.agent.skills_agent.metrics.Session") as mock_session_cls:
            mock_session = MagicMock()
            tid = uuid.uuid4()
            now = datetime.utcnow()

            mock_session.exec.side_effect = [
                # First call: get_recent_tasks → list of EditTask
                MagicMock(all=MagicMock(return_value=[
                    _mock_task(tid, status="completed", create_time=now),
                ])),
                # Second call: get_steps for this task
                MagicMock(all=MagicMock(return_value=[
                    _mock_step(0, "done", 0, now, now + timedelta(seconds=10)),
                    _mock_step(1, "done", 1, now, now + timedelta(seconds=20)),
                    _mock_step(2, "failed", 2, None, None),
                ])),
            ]
            mock_session_cls.return_value.__enter__.return_value = mock_session

            from app.agent.skills_agent.metrics import get_recent_tasks
            result = get_recent_tasks(limit=5)

            assert len(result) == 1
            assert result[0]["status"] == "completed"
            assert result[0]["total_steps"] == 3
            assert result[0]["done"] == 2
            assert result[0]["failed"] == 1
            assert result[0]["total_retries"] == 3  # 0+1+2
            assert result[0]["avg_step_duration_s"] == 15.0


# ---------------------------------------------------------------------------
# get_overall_stats
# ---------------------------------------------------------------------------

class TestGetOverallStats:
    def test_empty_returns_zero_stats(self):
        with patch("app.agent.skills_agent.metrics.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.exec.return_value.all.return_value = []
            mock_session_cls.return_value.__enter__.return_value = mock_session

            from app.agent.skills_agent.metrics import get_overall_stats
            result = get_overall_stats(days=7)

            assert result["total_tasks"] == 0
            assert result["success_rate"] == 0.0

    def test_mixed_statuses(self):
        with patch("app.agent.skills_agent.metrics.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.exec.return_value.all.return_value = [
                _mock_task(uuid.uuid4(), "completed"),
                _mock_task(uuid.uuid4(), "completed"),
                _mock_task(uuid.uuid4(), "failed", error_message="FFmpeg 超时"),
            ]
            mock_session_cls.return_value.__enter__.return_value = mock_session

            from app.agent.skills_agent.metrics import get_overall_stats
            result = get_overall_stats(days=7)

            assert result["total_tasks"] == 3
            assert result["completed"] == 2
            assert result["failed"] == 1
            assert result["success_rate"] == round(2 / 3 * 100, 1)
            assert result["error_distribution"]["超时"] == 1

    def test_error_distribution(self):
        with patch("app.agent.skills_agent.metrics.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.exec.return_value.all.return_value = [
                _mock_task(uuid.uuid4(), "failed", error_message="UserInput: bad"),
                _mock_task(uuid.uuid4(), "failed", error_message="InfraError: down"),
                _mock_task(uuid.uuid4(), "failed", error_message="未知错误"),
            ]
            mock_session_cls.return_value.__enter__.return_value = mock_session

            from app.agent.skills_agent.metrics import get_overall_stats
            result = get_overall_stats(days=7)

            dist = result["error_distribution"]
            assert dist["用户输入"] == 1
            assert dist["基础设施"] == 1
            assert dist["其他"] == 1


# ---------------------------------------------------------------------------
# format_metrics_table
# ---------------------------------------------------------------------------

class TestFormatMetricsTable:
    def test_empty_returns_placeholder(self):
        from app.agent.skills_agent.metrics import format_metrics_table

        result = format_metrics_table([])
        assert "暂无编辑任务记录" in result

    def test_formats_single_task(self):
        from app.agent.skills_agent.metrics import format_metrics_table

        metrics = [{
            "task_id": "12345678-1234-1234-1234-123456789abc",
            "status": "completed",
            "total_steps": 5,
            "done": 5,
            "failed": 0,
            "total_retries": 1,
            "avg_step_duration_s": 12.5,
        }]

        result = format_metrics_table(metrics)
        assert "12345678..." in result
        assert "OK" in result
        assert "5/5" in result
        assert "1" in result
        assert "12.5s" in result

    def test_includes_success_rate(self):
        from app.agent.skills_agent.metrics import format_metrics_table

        metrics = [
            {"task_id": "a", "status": "completed", "total_steps": 2, "done": 2,
             "failed": 0, "total_retries": 0, "avg_step_duration_s": 1.0},
            {"task_id": "b", "status": "failed", "total_steps": 2, "done": 1,
             "failed": 1, "total_retries": 3, "avg_step_duration_s": 5.0},
        ]

        result = format_metrics_table(metrics)
        assert "成功率: 1/2 (50.0%)" in result

    def test_100_percent_success_rate(self):
        from app.agent.skills_agent.metrics import format_metrics_table

        metrics = [
            {"task_id": "x", "status": "completed", "total_steps": 1, "done": 1,
             "failed": 0, "total_retries": 0, "avg_step_duration_s": 1.0},
        ]

        result = format_metrics_table(metrics)
        assert "100.0%" in result
