# 需求 20：指标聚合 + metrics 命令

> 原 PRD 编号: P2-4 (D-4) | 优先级: P2

## 1. 依赖关系

- **前置依赖**：req_04（PG 数据模型 — `edit_step` 表提供统计数据源）、req_05（CRUD 函数 — `get_step_stats()` 提供聚合查询）
- **被谁依赖**：无

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库无指标聚合。所有 Agent 执行结果是零散的日志文件（`jy_exec_log_*.txt`）和临时 Python 文件（`tmp*.py`）。

**PRD 降级决策**：
- 存储：从 PG `edit_step` 表直接查询统计（不需要额外的 SQLite/JSON 指标存储）
- 命令：`python -m skills_agent.metrics --last 10`
- 指标：任务成功率、各步骤平均耗时、错误类型分布、Token 消耗趋势

### 代码库校验结论

- `crud.get_step_stats(task_id)`（在 req_05 中实现）已经能返回 `total/done/failed/total_retries/avg_duration_seconds`
- 需要新增的是跨任务聚合查询（如最近 N 个任务的整体成功率）
- CLI 命令入口使用 Python `argparse` 模块
- 指标输出使用简单的表格格式（`tabulate` 可选依赖，也可手动格式化）

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `backend/app/agent/skills_agent/metrics.py` | 指标查询逻辑 + CLI 命令入口 |

### 核心技术细节

```python
"""metrics.py — 编辑任务指标聚合与 CLI 命令"""
import argparse
from datetime import datetime, timedelta
from typing import Any
from sqlmodel import Session, select, func
from app.models import EditTask, EditStep
from app.core.db import engine


def get_recent_tasks(limit: int = 10) -> list[dict[str, Any]]:
    """获取最近 N 个编辑任务的聚合指标"""
    with Session(engine) as session:
        tasks = session.exec(
            select(EditTask)
            .where(EditTask.is_deleted == 0)
            .order_by(EditTask.create_time.desc())
            .limit(limit)
        ).all()

        results = []
        for task in tasks:
            steps = session.exec(
                select(EditStep).where(EditStep.task_id == task.id)
            ).all()

            total = len(steps)
            done = sum(1 for s in steps if s.status == "done")
            failed = sum(1 for s in steps if s.status == "failed")
            total_retries = sum(s.retry_count for s in steps)

            # 计算平均耗时
            durations = []
            for s in steps:
                if s.started_at and s.finished_at:
                    durations.append(
                        (s.finished_at - s.started_at).total_seconds()
                    )
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
    """获取整体统计（最近 N 天）"""
    cutoff = datetime.utcnow() - timedelta(days=days)

    with Session(engine) as session:
        tasks = session.exec(
            select(EditTask)
            .where(EditTask.create_time >= cutoff)
            .where(EditTask.is_deleted == 0)
        ).all()

        total_tasks = len(tasks)
        completed = sum(1 for t in tasks if t.status == "completed")
        failed = sum(1 for t in tasks if t.status == "failed")

        # 错误类型分布
        error_dist: dict[str, int] = {}
        for task in tasks:
            if task.error_message:
                # 简化分类
                if "超时" in task.error_message or "Timeout" in task.error_message:
                    kind = "超时"
                elif "UserInput" in task.error_message:
                    kind = "用户输入"
                elif "InfraError" in task.error_message:
                    kind = "基础设施"
                else:
                    kind = "其他"
                error_dist[kind] = error_dist.get(kind, 0) + 1

        return {
            "period_days": days,
            "total_tasks": total_tasks,
            "completed": completed,
            "failed": failed,
            "success_rate": round(completed / max(total_tasks, 1) * 100, 1),
            "error_distribution": error_dist,
        }


def format_metrics_table(metrics: list[dict]) -> str:
    """格式化指标为可读表格"""
    if not metrics:
        return "暂无编辑任务记录"

    lines = [
        f"{'Task ID':<38} {'Status':<12} {'Steps':<8} {'Retries':<8} {'Avg Dur':<10}",
        "-" * 80,
    ]
    for m in metrics:
        task_id_short = m["task_id"][:8] + "..."
        status_icon = " OK" if m["status"] == "completed" else (
            "FAIL" if m["status"] == "failed" else m["status"]
        )
        lines.append(
            f"{task_id_short:<38} {status_icon:<12} "
            f"{m['done']}/{m['total_steps']:<7} {m['total_retries']:<8} "
            f"{m['avg_step_duration_s']}s"
        )

    # 汇总行
    all_statuses = [m["status"] for m in metrics]
    success_count = sum(1 for s in all_statuses if s == "completed")
    lines.append("-" * 80)
    lines.append(
        f"成功率: {success_count}/{len(metrics)} "
        f"({round(success_count / len(metrics) * 100, 1)}%)"
    )

    return "\n".join(lines)


# ---- CLI 入口 ----

def main():
    parser = argparse.ArgumentParser(
        prog="python -m skills_agent.metrics",
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
        import json
        metrics = get_recent_tasks(limit=args.last)
        overall = get_overall_stats(days=args.days)
        print(json.dumps({"recent_tasks": metrics, "overall": overall},
                         ensure_ascii=False, indent=2))
    else:
        metrics = get_recent_tasks(limit=args.last)
        print(format_metrics_table(metrics))
        print()
        overall = get_overall_stats(days=args.days)
        print(f"过去 {overall['period_days']} 天整体统计:")
        print(f"  总任务: {overall['total_tasks']}")
        print(f"  成功: {overall['completed']} | 失败: {overall['failed']}")
        print(f"  成功率: {overall['success_rate']}%")
        if overall["error_distribution"]:
            print(f"  错误类型分布: {overall['error_distribution']}")


if __name__ == "__main__":
    main()
```

### 容错与边界

- `get_recent_tasks()` 无任何编辑任务时返回空列表（不抛异常）
- `get_overall_stats()` 处理除数为 0 的情况（`max(total_tasks, 1)`）
- 指标查询不依赖 Redis（纯 PG 查询），避免 Redis 不可用导致指标命令失败
- CLI 命令的 `--format json` 用于脚本化/CI 集成，`--format table` 用于人工查看
- Token 消耗趋势基于 LLM 调用日志（`langfuse` trace），当前阶段不实现（标记为 TODO）

## 4. 验收标准 (DoD)

- [ ] `python -m skills_agent.metrics --last 5` 输出最近 5 个任务的指标表格
- [ ] 表格包含：Task ID、Status、Steps（done/total）、Retries、Avg Duration
- [ ] `python -m skills_agent.metrics --format json` 输出合法 JSON
- [ ] `get_overall_stats(days=7)` 返回正确的时间范围统计
- [ ] 无任何任务时输出"暂无编辑任务记录"而非崩溃
- [ ] 成功率达到 100% 时正确显示 100.0%
- [ ] 跨任务聚合查询性能正常（10 个任务 < 200ms）
