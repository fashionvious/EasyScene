# 需求 03：StepOrchestrator + PostgreSQL 持久化 + PostgresSaver

> 原 PRD 编号: P0-1 (A-1) | 优先级: P0

## 1. 依赖关系

- **前置依赖**：req_02（ToolRegistry — 提供 ToolSpec 元数据）、req_04（PG 表定义 — 提供 EditTask/EditStep 表）
- **被谁依赖**：req_06（StoryboardToSteps — 使用 StepSpec 格式）、req_08（API 路由 — 调用 Orchestrator）、req_09（Celery 分发 — 由 Orchestrator 触发）、req_10（素材并发 — Orchestrator 内的并行逻辑）、req_12（用户交互队列 — 由 Orchestrator 暂停时触发）、req_15（会话记忆 — EditTask 关联 user_id）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库状态（[jianying_agent.py:346](../../../backend/app/agent/skills_agent/jianying_agent.py#L346) 和 [jianying_agent.py:377-382](../../../backend/app/agent/skills_agent/jianying_agent.py#L377-L382)）：

```python
agent = create_agent(
    model,
    system_prompt=system_prompt,
    middleware=[middleware],
    checkpointer=InMemorySaver()  # ← 进程重启后对话历史丢失
)
```

**存在的问题**：

1. **无显式编排层**：Agent 主循环依赖 LangChain `create_agent` 黑盒，无分步执行能力。LLM 一次性生成完整 Python 脚本，单步出错全盘重来。
2. **InMemorySaver**：进程重启/崩溃后对话历史丢失，用户需重新开始。
3. **无 Checkpoint 机制**：无法从失败步骤恢复，无法展示步骤级进度。

**PRD v2.0 关键洞察**：项目已有 `langgraph-checkpoint-postgres==3.0.5` 在依赖中（pyproject.toml），PostgresSaver 实际上是一行替换。同时项目已有成熟的 SQLModel + Session 模式（参见 [models.py](../../../backend/app/models.py) 和 [crud.py](../../../backend/app/crud.py)）。

**注意**：本需求中 PG 表定义部分被拆分至 req_04（数据模型），因为 CRUD 和后续所有业务逻辑都依赖它。StepOrchestrator 本身依赖 req_04。

### 代码库校验结论

- `InMemorySaver()` 在 `jianying_agent.py:381`，替换为 `PostgresSaver` 需提供 `conn` 参数（asyncpg connection pool），需要 db engine 配置
- 现有的 `crud.py` 提供了 Session 管理模式（`session.add(db_obj); session.commit(); session.refresh(db_obj)`），可直接复用
- `python_executor.py:149-161` 中的 `_trim_project_duration()` 是 JyProject 执行的固定步骤，Orchestrator 需与之交互
- 本项目已有 `langgraph-checkpoint-postgres` 依赖，无需额外安装

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `backend/app/agent/skills_agent/step_orchestrator.py` | StepOrchestrator 核心类 |
| 修改 | `backend/app/agent/skills_agent/jianying_agent.py` | InMemorySaver → PostgresSaver；run_jianying_agent → 支持 Orchestrator 驱动 |
| 依赖 | `backend/app/models.py` | 复用 req_04 定义的 EditTask/EditStep 表 |
| 依赖 | `backend/app/crud.py` | 复用 req_05 定义的 CRUD 函数 |

### 核心技术细节

```python
"""step_orchestrator.py — 线性分步执行器"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional
import uuid
import time
import logging

logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStatus(str, Enum):
    PLANNING = "planning"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StepSpec:
    """单个编辑步骤的声明式描述"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool: str                          # 工具名（匹配 ToolRegistry）
    args: dict[str, Any] = field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    retry_count: int = 0


@dataclass
class EditPlan:
    """完整的编辑计划"""
    task_id: str
    user_id: str
    script_id: str
    steps: list[StepSpec]
    current_index: int = 0
    status: TaskStatus = TaskStatus.PLANNING
    project_state: dict[str, Any] = field(default_factory=dict)


class StepOrchestrator:
    """
    线性分步编排器。

    执行模型：
      PLANNING → RUNNING(step_0) → RUNNING(step_1) → ... → COMPLETED
                     ↑ 失败从 checkpoint 恢复          ↓
                   PAUSED (步骤间暂停)

    每个 step 完成后 → 自动保存 checkpoint 到 PG (edit_step 表)
    恢复时 → 读取 checkpoint → 跳过 done 步骤 → 从 current_index 继续
    """

    def __init__(
        self,
        tool_registry,         # ToolRegistry (req_02)
        edit_task_crud,        # CRUD 函数集 (req_05)
        edit_step_crud,        # CRUD 函数集 (req_05)
        project_context_factory,  # ProjectContext 工厂 (req_16)
        redis_manager=None,    # EditTaskRedisManager (req_14)
    ):
        self.registry = tool_registry
        self.task_crud = edit_task_crud
        self.step_crud = edit_step_crud
        self.project_ctx_factory = project_context_factory
        self.redis = redis_manager

    async def execute(self, plan: EditPlan) -> EditPlan:
        """执行完整计划，每个步骤自动 checkpoint"""
        plan.status = TaskStatus.RUNNING

        while plan.current_index < len(plan.steps):
            step = plan.steps[plan.current_index]

            if step.status == StepStatus.DONE:
                plan.current_index += 1
                continue

            step.status = StepStatus.RUNNING
            step.started_at = time.time()
            await self._save_step_checkpoint(plan, step)

            try:
                spec = self.registry.get_spec(step.tool)
                if spec is None:
                    raise ValueError(f"未知工具: {step.tool}")

                # 同步步骤 → 直接调用 tool function
                if spec.exec_mode == "sync":
                    result = spec.func(**step.args)  # 简化，实际需处理 LangChain tool 调用
                else:
                    # 异步步骤 → 提交 Celery Task（req_09）
                    result = await self._dispatch_celery(step, spec)

                step.result = result
                step.status = StepStatus.DONE

            except Exception as e:
                step.error = str(e)
                step.status = StepStatus.FAILED
                plan.status = TaskStatus.FAILED
                await self._save_step_checkpoint(plan, step)
                break

            step.finished_at = time.time()
            plan.current_index += 1
            await self._save_step_checkpoint(plan, step)

            # 更新 Redis 实时进度（若已配置）
            if self.redis:
                await self.redis.update_step(plan)

        if plan.current_index >= len(plan.steps):
            plan.status = TaskStatus.COMPLETED

        await self._save_task_completion(plan)
        return plan

    async def resume(self, task_id: str) -> EditPlan:
        """从 PG checkpoint 恢复执行"""
        plan = await self._load_plan_from_checkpoint(task_id)
        return await self.execute(plan)

    async def pause(self, task_id: str) -> bool:
        """暂停：当前步骤完成后停止"""
        plan = await self._load_plan_from_checkpoint(task_id)
        plan.status = TaskStatus.PAUSED
        await self._save_task(plan)
        return True

    async def _save_step_checkpoint(self, plan: EditPlan, step: StepSpec) -> None:
        """将步骤状态持久化到 PG edit_step 表（通过 req_05 CRUD）"""
        await self.step_crud.upsert_step(plan.task_id, step)

    async def _save_task_completion(self, plan: EditPlan) -> None:
        """将任务最终状态持久化到 PG edit_task 表"""
        await self.task_crud.update_task_status(plan.task_id, plan.status)

    async def _load_plan_from_checkpoint(self, task_id: str) -> EditPlan:
        """从 PG 读取 edit_task + edit_step 重建 EditPlan"""
        task = await self.task_crud.get_task(task_id)
        steps = await self.step_crud.get_steps_by_task(task_id)
        return EditPlan(
            task_id=task_id,
            user_id=task.user_id,
            script_id=task.script_id,
            steps=[self._dict_to_stepspec(s) for s in steps],
            current_index=task.current_step,
            project_state=task.project_state_json or {},
        )

    async def _dispatch_celery(self, step: StepSpec, spec) -> Any:
        """提交 Celery 异步任务（在 req_09 中完善）"""
        # 当前阶段：抛出 NotImplementedError，在 req_09 完成
        raise NotImplementedError("Celery 异步分发待 req_09 实现")

    # ... 其他辅助方法 ...
```

**PostgresSaver 替换**（[jianying_agent.py:381](../../../backend/app/agent/skills_agent/jianying_agent.py#L381)）：

```python
# 改造前:
checkpointer=InMemorySaver()

# 改造后:
from langgraph.checkpoint.postgres import PostgresSaver
checkpointer = PostgresSaver(conn)  # conn 来自项目已有的 asyncpg/db engine
await checkpointer.setup()  # 首次使用需要建表
```

### 容错与边界

- **不保证跨 app 崩溃的恢复**：如果剪映 App 崩溃，需要重启 app + 重建草稿（uiautomation 的物理限制）
- Checkpoint 保存失败不应阻塞步骤执行（catch + log warning）
- `resume()` 需要验证 task_id 存在且状态为 PAUSED 或 FAILED
- PG 连接池的 `conn` 参数复用项目已有的 `app.core.db` 模块
- PostgresSaver 的 `setup()` 在应用启动时调用一次（放在 FastAPI lifespan 中）

## 4. 验收标准 (DoD)

- [ ] `StepOrchestrator.execute(plan)` 按序执行所有步骤，每个步骤完成后自动保存 PG checkpoint
- [ ] 步骤执行失败时，同一步骤的状态为 FAILED，任务状态也为 FAILED
- [ ] `resume(task_id)` 能正确跳过已完成步骤，从失败步骤继续
- [ ] `pause(task_id)` 在当前步骤完成后暂停
- [ ] InMemorySaver 替换为 PostgresSaver 后，进程重启对话历史可恢复
- [ ] PostgresSaver 的 `setup()` 在应用启动时成功建表
- [ ] 线性执行 5 个模拟步骤端到端通过，checkpoint 可查询
