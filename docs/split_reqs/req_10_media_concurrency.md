# 需求 10：素材导入并发执行（Phase 1）

> 原 PRD 编号: P1-2 (B-2) | 优先级: P1

## 1. 依赖关系

- **前置依赖**：req_02（ToolRegistry — `concurrency_safe` 标记）、req_03（StepOrchestrator — 需要支持步骤级并发的执行循环）
- **被谁依赖**：无（Phase 2 的完整并发编排依赖此阶段的结果）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前执行模式为完全串行。StepOrchestrator 按 `EditPlan.steps` 顺序单步执行。

**FFmpeg 资源竞争问题**（PRD 2.2）：
- `media_normalizer.py` 和 `smart_rough_cut.py` 调用 FFmpeg（CPU 编码 libx264）
- 并发 FFmpeg 进程会耗尽 CPU/内存
- **硬性限制**：FFmpeg 并发 ≤ 2

**Phase 1 范围**（PRD 降级策略）：
- 仅素材导入步骤（resolve_media + FFmpeg normalize）并发执行
- 仅 `category="read"` + `concurrency_safe=True` 的工具启用 `asyncio.gather`
- 最大并发 = 3
- 其他步骤保持串行

### 代码库校验结论

- `media_resolver.py` 的 `resolve()` 方法是纯 I/O + 文件系统操作，天然线程安全
- FFmpeg normalize 归类为 weight/compute 步骤，在 Phase 1 中通过 Celery（req_09）异步执行，天然支持并行（但需限制并发数 2）
- ToolRegistry（req_02）的 `concurrency_safe` 字段直接用于并发决策
- 当前架构无 `asyncio` 集成点——`cli_executor.execute()` 和 `python_executor.execute()` 均为同步调用
- StepOrchestrator 使用 `asyncio`（已在 req_03 设计为 `async def execute()`）

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `backend/app/agent/skills_agent/step_orchestrator.py` | 在 `execute()` 中增加并发执行逻辑 |

### 核心技术细节

**StepOrchestrator 并发执行扩展**：

```python
# step_orchestrator.py — execute() 方法的并发改造

import asyncio

# 并发安全工具类别
CONCURRENT_SAFE_CATEGORIES = {"read"}
MAX_CONCURRENT = 3
MAX_FFMPEG_CONCURRENT = 2  # FFmpeg 特殊限制


class StepOrchestrator:
    # ... 现有方法保持不变 ...

    async def execute(self, plan: EditPlan) -> EditPlan:
        """执行完整计划，相邻的并发安全步骤自动并行化"""
        plan.status = TaskStatus.RUNNING

        while plan.current_index < len(plan.steps):
            # 收集连续的并发安全步骤
            concurrent_batch = self._collect_concurrent_batch(plan)
            
            if len(concurrent_batch) > 1:
                # 并发执行这批步骤
                results = await asyncio.gather(
                    *[self._execute_single_step(plan, step)
                      for step in concurrent_batch],
                    return_exceptions=True,
                )
                for step, result in zip(concurrent_batch, results):
                    if isinstance(result, Exception):
                        step.status = StepStatus.FAILED
                        step.error = str(result)
                    else:
                        step.status = StepStatus.DONE
                        step.result = result
                    step.finished_at = time.time()
                    await self._save_step_checkpoint(plan, step)
            else:
                # 单个步骤串行执行
                step = concurrent_batch[0]
                await self._execute_single_step(plan, step)
                await self._save_step_checkpoint(plan, step)

            plan.current_index += len(concurrent_batch)

            # 更新 Redis 进度
            if self.redis:
                await self.redis.update_step(plan)

        plan.status = TaskStatus.COMPLETED
        await self._save_task_completion(plan)
        return plan

    def _collect_concurrent_batch(self, plan: EditPlan) -> list[StepSpec]:
        """
        从 current_index 开始收集可并发的连续步骤。

        条件：
        1. 步骤的 tool 标记为 concurrency_safe=True
        2. 步骤不是 Celery 异步步骤（async 步骤已在 req_09 中并行化）
        3. 连续收集，遇到不可并发的步骤停止
        4. 最大收集数 ≤ MAX_CONCURRENT
        
        如果只有 1 个可并发步骤，返回单元素列表（串行执行）。
        """
        batch: list[StepSpec] = []
        ffmpeg_count = 0

        for i in range(plan.current_index, len(plan.steps)):
            step = plan.steps[i]

            if step.status == StepStatus.DONE:
                if not batch:
                    # 跳过已完成步骤时，不收集并发批
                    plan.current_index = i + 1
                    continue
                break

            if step.status in (StepStatus.PENDING, StepStatus.FAILED):
                spec = self.registry.get_spec(step.tool)
                if spec is None or not spec.concurrency_safe:
                    if batch:
                        break  # 已有并发批，遇到不安全步骤停止
                    else:
                        batch.append(step)  # 单个步骤，串行
                        break

                if spec.exec_mode == "async":
                    # Celery 步骤：单独执行，不参与并发批
                    if batch:
                        break
                    batch.append(step)
                    break

                # FFmpeg 特殊限制
                if step.tool == "execute_cli_script" and \
                   step.args.get("action") in ("smart_rough_cut",):
                    if ffmpeg_count >= MAX_FFMPEG_CONCURRENT:
                        break
                    ffmpeg_count += 1

                batch.append(step)

                if len(batch) >= MAX_CONCURRENT:
                    break

        return batch if batch else [plan.steps[plan.current_index]]

    async def _execute_single_step(
        self, plan: EditPlan, step: StepSpec,
    ) -> Any:
        """执行单个步骤（内部方法）"""
        step.status = StepStatus.RUNNING
        step.started_at = time.time()

        spec = self.registry.get_spec(step.tool)
        if spec is None:
            raise ValueError(f"未知工具: {step.tool}")

        if spec.exec_mode == "async":
            return await self._dispatch_celery(step, spec)
        else:
            # 同步工具调用（通过 asyncio.to_thread 避免阻塞事件循环）
            return await asyncio.to_thread(spec.func, **step.args)
```

### 容错与边界

- `asyncio.gather(return_exceptions=True)` 确保一个步骤失败不影响其他并发步骤
- `MAX_CONCURRENT=3` 和 `MAX_FFMPEG_CONCURRENT=2` 为硬编码常量，后续可移到 req_02 的 ToolRegistry 配置
- `_collect_concurrent_batch()` 对已完成步骤的处理：跳过并推进 current_index
- 使用 `asyncio.to_thread` 封装同步 tool function，避免阻塞事件循环
- 并发步骤中任一失败后，其他步骤继续执行完毕（与串行的"一步失败全盘终止"行为不同——这是并发设计的预期行为）

## 4. 验收标准 (DoD)

- [ ] 3 个 `resolve_media` 步骤在 EditPlan 中连续排列时，并发执行（总耗时 ≈ max(单个耗时) 而非 sum(耗时)）
- [ ] `category="write"` 的步骤（如 `execute_jyproject_code`）不参与并发批
- [ ] FFmpeg 步骤并发数不超过 2
- [ ] 并发批中某个步骤失败时，其他步骤正常完成（不被中断）
- [ ] `asyncio.to_thread` 封装的同步工具不阻塞事件循环
- [ ] 单步执行模式（无并发安全步骤相邻时）行为不变
- [ ] 并发执行后的 checkpoint 保存完整（每个 step 独立保存）
