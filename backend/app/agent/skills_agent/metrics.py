"""
编辑任务指标聚合与 CLI 命令。

用法:
    python -m app.agent.skills_agent.metrics --last 10
    python -m app.agent.skills_agent.metrics --format json --days 7
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.models import EditTask, EditStep
from app.core.db import engine


def get_recent_tasks(limit: int = 10) -> list[dict[str, Any]]:
    """获取最近 N 个编辑任务的聚合指标。"""
    with Session(engine) as session:
        tasks = session.exec(
            select(EditTask)
            .where(EditTask.is_deleted == 0)
            .order_by(EditTask.create_time.desc())
            .limit(limit)
        ).all()

        results: list[dict[str, Any]] = []
        for task in tasks:
            steps = session.exec(
                select(EditStep).where(EditStep.task_id == task.id)
            ).all()

            total = len(steps)
            done = sum(1 for s in steps if s.status == "done")
            failed = sum(1 for s in steps if s.status == "failed")
            total_retries = sum(s.retry_count for s in steps)

            durations = [
                (s.finished_at - s.started_at).total_seconds()
                for s in steps
                if s.started_at and s.finished_at
            ]
            avg_duration = sum(durations) / len(durations) if durations else 0

            results.append({
                "task_id": str(task.id),
                "script_id": str(task.script_id),
                "status": task.status,
                "total_steps": total,
                "done": done,
                "failed": failed,
                "total_retries": total_retries,
                "avg_step_duration_s": round(avg_duration, 1),
                "created_at": task.create_time.isoformat(),
            })

        return results


def get_overall_stats(days: int = 7) -> dict[str, Any]:
    """获取整体统计（最近 N 天）。"""
    cutoff = datetime.utcnow() - timedelta(days=days)

    with Session(engine) as session:
        tasks = session.exec(
            select(EditTask)
            .where(EditTask.create_time >= cutoff)
            .where(EditTask.is_deleted == 0)
        ).all()

        total_tasks = len(tasks)
        completed = sum(1 for t in tasks if t.status == "completed")
        failed_count = sum(1 for t in tasks if t.status == "failed")

        error_dist: dict[str, int] = {}
        for task in tasks:
            if task.error_message:
                msg = task.error_message
                if "超时" in msg or "Timeout" in msg:
                    kind = "超时"
                elif "UserInput" in msg:
                    kind = "用户输入"
                elif "InfraError" in msg:
                    kind = "基础设施"
                else:
                    kind = "其他"
                error_dist[kind] = error_dist.get(kind, 0) + 1

        return {
            "period_days": days,
            "total_tasks": total_tasks,
            "completed": completed,
            "failed": failed_count,
            "success_rate": round(
                completed / max(total_tasks, 1) * 100, 1,
            ),
            "error_distribution": error_dist,
        }


def format_metrics_table(metrics: list[dict[str, Any]]) -> str:
    """格式化指标为可读表格。"""
    if not metrics:
        return "暂无编辑任务记录"

    lines = [
        f"{'Task ID':<38} {'Status':<12} {'Steps':<8} {'Retries':<8} {'Avg Dur':<10}",
        "-" * 80,
    ]
    for m in metrics:
        task_id_short = m["task_id"][:8] + "..."
        if m["status"] == "completed":
            status_icon = "OK"
        elif m["status"] == "failed":
            status_icon = "FAIL"
        else:
            status_icon = m["status"]
        lines.append(
            f"{task_id_short:<38} {status_icon:<12} "
            f"{m['done']}/{m['total_steps']:<7} {m['total_retries']:<8} "
            f"{m['avg_step_duration_s']}s"
        )

    all_statuses = [m["status"] for m in metrics]
    success_count = sum(1 for s in all_statuses if s == "completed")
    lines.append("-" * 80)
    lines.append(
        f"成功率: {success_count}/{len(metrics)} "
        f"({round(success_count / max(len(metrics), 1) * 100, 1)}%)"
    )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.agent.skills_agent.metrics",
        description="编辑任务指标聚合查询",
    )
    parser.add_argument(
        "--last", type=int, default=10,
        help="查询最近 N 个任务（默认 10）",
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="整体统计的时间范围（天，默认 7）",
    )
    parser.add_argument(
        "--format", choices=["table", "json"], default="table",
        help="输出格式（默认 table）",
    )

    args = parser.parse_args()

    if args.format == "json":
        metrics = get_recent_tasks(limit=args.last)
        overall = get_overall_stats(days=args.days)
        print(json.dumps(
            {"recent_tasks": metrics, "overall": overall},
            ensure_ascii=False, indent=2,
        ))
    else:
        metrics = get_recent_tasks(limit=args.last)
        print(format_metrics_table(metrics))
        overall = get_overall_stats(days=args.days)
        print()
        print(f"过去 {overall['period_days']} 天整体统计:")
        print(f"  总任务: {overall['total_tasks']}")
        print(f"  成功: {overall['completed']} | 失败: {overall['failed']}")
        print(f"  成功率: {overall['success_rate']}%")
        if overall["error_distribution"]:
            print(f"  错误类型分布: {overall['error_distribution']}")


if __name__ == "__main__":
    main()
