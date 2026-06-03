"""
StepOrchestrator — 线性分步编排器。

职责：接收 EditPlan，按序执行步骤，每步自动保存 PG checkpoint。
支持 pause / resume，异步步骤委托给 Celery（req_09）。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# 并发控制常量
MAX_CONCURRENT = 3        # 最大并发步骤数
MAX_FFMPEG_CONCURRENT = 2  # FFmpeg 特殊限制


# ============================================================================
# Enums
# ============================================================================

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


# ============================================================================
# Dataclasses
# ============================================================================

@dataclass
class StepSpec:
    """单个编辑步骤的声明式描述"""
    tool: str                                   # 工具名（匹配 ToolRegistry）
    args: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
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


# ============================================================================
# Orchestrator
# ============================================================================

class StepOrchestrator:
    """线性分步编排器。

    执行模型：
      PLANNING → RUNNING(step_0) → RUNNING(step_1) → ... → COMPLETED
                     ↑ 失败从 checkpoint 恢复          ↓
                   PAUSED (步骤间暂停)

    每个 step 完成后自动保存 checkpoint 到 PG（通过 CRUD）。
    恢复时读取 checkpoint → 跳过 done 步骤 → 从 current_index 继续。
    """

    def __init__(
        self,
        tool_registry: Any = None,            # ToolRegistry (req_02)
        task_crud: Any = None,                # crud module with edit_task functions
        step_crud: Any = None,                # crud module with edit_step functions
        project_context_factory: Any = None,   # ProjectContext factory (future req)
        redis_manager: Any = None,             # EditTaskRedisManager (req_16)
    ):
        self.registry = tool_registry
        self.task_crud = task_crud
        self.step_crud = step_crud
        self.project_ctx_factory = project_context_factory
        self.redis = redis_manager

    # ---- public API ----

    async def execute(self, plan: EditPlan) -> EditPlan:
        """执行完整计划，相邻并发安全步骤自动并行化（Phase 1）。"""
        plan.status = TaskStatus.RUNNING
        await self._save_task_status(plan)

        while plan.current_index < len(plan.steps):
            # 收集连续的并发安全步骤
            batch = self._collect_concurrent_batch(plan)

            if len(batch) > 1:
                # 并发执行
                results = await asyncio.gather(
                    *[self._execute_single_step(plan, step) for step in batch],
                    return_exceptions=True,
                )
                for step, result in zip(batch, results):
                    if isinstance(result, StepPausedError):
                        step.status = StepStatus.FAILED
                        step.error = str(result)
                        plan.status = TaskStatus.PAUSED
                    elif isinstance(result, Exception):
                        step.status = StepStatus.FAILED
                        step.error = str(result)
                        if plan.status != TaskStatus.FAILED:
                            plan.status = TaskStatus.FAILED
                    else:
                        step.status = StepStatus.DONE
                        step.result = result
                    step.finished_at = time.time()
                    await self._save_step_checkpoint(plan.task_id, step)
            else:
                # 单个步骤串行执行
                step = batch[0]
                if step.status == StepStatus.DONE:
                    plan.current_index += 1
                    continue
                try:
                    await self._execute_single_step(plan, step)
                except StepPausedError as e:
                    # NOTIFY_USER: 步骤已标记 FAILED，plan 设为 PAUSED
                    plan.status = TaskStatus.PAUSED
                    await self._save_task_status(plan)
                    break
                except Exception as e:
                    step.error = str(e)
                    step.status = StepStatus.FAILED
                    plan.status = TaskStatus.FAILED
                    await self._save_step_checkpoint(plan.task_id, step)
                    await self._save_task_status(plan)
                    break
                step.finished_at = time.time()
                await self._save_step_checkpoint(plan.task_id, step)

            plan.current_index += len(batch)

            # 更新 Redis 实时进度
            if self.redis:
                try:
                    await self.redis.update_step_progress(
                        plan.task_id,
                        step_index=plan.current_index - 1,
                        step_name=batch[-1].tool if batch else "",
                        step_status=plan.status.value,
                        progress_pct=round(
                            plan.current_index / len(plan.steps) * 100, 1,
                        ),
                    )
                except Exception:
                    logger.warning("Redis 进度更新失败，继续执行", exc_info=True)

            if plan.status == TaskStatus.FAILED:
                await self._save_task_status(plan)
                break

        if plan.current_index >= len(plan.steps) and plan.status != TaskStatus.FAILED:
            plan.status = TaskStatus.COMPLETED

        await self._save_task_status(plan)
        return plan

    def _collect_concurrent_batch(self, plan: EditPlan) -> list[StepSpec]:
        """
        从 current_index 开始收集可并发的连续步骤。

        条件：
        1. 步骤的 tool 标记为 concurrency_safe=True
        2. 不是 Celery 异步步骤（async 步骤单独执行）
        3. 连续收集，遇到不安全步骤停止
        4. 最多 MAX_CONCURRENT 个，FFmpeg ≤ MAX_FFMPEG_CONCURRENT
        """
        batch: list[StepSpec] = []
        ffmpeg_count = 0

        for i in range(plan.current_index, len(plan.steps)):
            step = plan.steps[i]

            if step.status == StepStatus.DONE:
                if not batch:
                    plan.current_index = i + 1
                    continue
                break

            if step.status not in (StepStatus.PENDING, StepStatus.FAILED):
                break

            spec = self.registry.get_spec(step.tool) if self.registry else None

            if spec is None or not spec.concurrency_safe:
                if batch:
                    break
                batch.append(step)
                break

            if spec.exec_mode == "async":
                if batch:
                    break
                batch.append(step)
                break

            # FFmpeg 步骤限制
            is_ffmpeg = (
                step.tool == "execute_cli_script"
                and step.args.get("action") in ("smart_rough_cut",)
            )
            if is_ffmpeg and ffmpeg_count >= MAX_FFMPEG_CONCURRENT:
                break
            if is_ffmpeg:
                ffmpeg_count += 1

            batch.append(step)

            if len(batch) >= MAX_CONCURRENT:
                break

        return batch if batch else [plan.steps[plan.current_index]]

    async def resume(self, task_id: str) -> EditPlan | None:
        """从 PG checkpoint 恢复执行。"""
        plan = await self._load_plan_from_checkpoint(task_id)
        if plan is None:
            return None
        return await self.execute(plan)

    async def pause(self, task_id: str) -> bool:
        """暂停：在当前步骤完成后停止。"""
        plan = await self._load_plan_from_checkpoint(task_id)
        if plan is None:
            return False
        plan.status = TaskStatus.PAUSED
        await self._save_task_status(plan)
        return True

    async def retry_step(self, task_id: str, step_index: int) -> bool:
        """重置失败步骤为 pending 并恢复执行。"""
        plan = await self._load_plan_from_checkpoint(task_id)
        if plan is None:
            return False
        if step_index < 0 or step_index >= len(plan.steps):
            return False

        step = plan.steps[step_index]
        step.status = StepStatus.PENDING
        step.error = None
        step.retry_count += 1
        plan.current_index = step_index
        plan.status = TaskStatus.RUNNING

        await self._save_step_checkpoint(plan.task_id, step)
        await self._save_task_status(plan)

        # Resume execution
        await self.execute(plan)
        return True

    # ---- internal ----

    async def _execute_single_step(self, plan: EditPlan, step: StepSpec) -> Any:
        """执行单个步骤。storyboard action → JyProject code → 执行。"""
        step.status = StepStatus.RUNNING
        step.started_at = time.time()
        await self._save_step_checkpoint(plan.task_id, step)

        spec = None
        if self.registry:
            spec = self.registry.get_spec(step.tool)

        try:
            # storyboard action → 生成 JyProject 代码
            actual_args = dict(step.args)
            if step.tool == "execute_jyproject_code" and "action" in actual_args:
                code = _build_jyproject_code(step.args)
                actual_args = {"code": code}

            if spec is not None and spec.exec_mode == "async":
                result = await self._dispatch_celery(step, spec, actual_args)
            elif spec is not None:
                result = await asyncio.to_thread(spec.func, **actual_args)
            else:
                raise ValueError(f"未知工具 '{step.tool}' 且 ToolRegistry 不可用")

            step.result = result
            step.status = StepStatus.DONE
            return result

        except Exception as e:
            action = self._classify_error_action(e)
            if action == "notify":
                step.status = StepStatus.FAILED
                step.error = str(e)
                plan.status = TaskStatus.PAUSED
                await self._save_step_checkpoint(plan.task_id, step)

                # 写入用户待决策队列
                if self.redis:
                    try:
                        await self.redis.add_user_pending(
                            user_id=plan.user_id,
                            task_id=plan.task_id,
                            step_index=plan.current_index,
                            error=str(e),
                        )
                    except Exception:
                        logger.warning("写 pending 队列失败", exc_info=True)

                raise StepPausedError(
                    f"步骤需用户决策: {e}"
                ) from e

            # fatal / retry: re-raise for caller to handle
            raise

    @staticmethod
    def _classify_error_action(exc: Exception) -> str:
        """委托给 ErrorRetryConfig 统一分类。

        返回: "notify" | "retry" | "fatal"
        """
        from .error_retry_map import ErrorRetryConfig
        action = ErrorRetryConfig.classify_retry_action(exc)
        return action.value


    async def _dispatch_celery(self, step: StepSpec, spec: Any, actual_args: dict | None = None) -> Any:
        """将重量步骤提交为 Celery Task，异步轮询等待完成。"""
        try:
            from .celery_tasks import ASYNC_TOOL_TASK_MAP
        except ImportError:
            from celery_tasks import ASYNC_TOOL_TASK_MAP

        args = actual_args or step.args
        action = step.args.get("action", "unknown")
        tool_map = ASYNC_TOOL_TASK_MAP.get(step.tool, {})
        celery_task = tool_map.get(action)

        if celery_task is None:
            logger.warning(
                "无 Celery Task 匹配 %s:%s，回退同步执行", step.tool, action,
            )
            return await asyncio.to_thread(spec.func, **args)

        # 提取 Celery task 所需的参数（过滤元数据字段）
        task_kwargs = {
            k: v for k, v in args.items()
            if k not in ("action", "step_index", "project_name", "project_config")
        }
        result = celery_task.delay(**task_kwargs)

        # 写入 Redis 关联
        if self.redis:
            try:
                await self.redis.set_celery_task_id(step.id, result.id)
            except Exception:
                logger.warning("Redis celery_task_id 关联失败", exc_info=True)

        # 异步轮询等待完成
        while True:
            ready = await asyncio.to_thread(result.ready)
            if ready:
                break
            await asyncio.sleep(2)

        success = await asyncio.to_thread(result.successful)
        if success:
            return await asyncio.to_thread(result.get)
        else:
            tb = await asyncio.to_thread(lambda: str(result.traceback) if result.traceback else "未知错误")
            raise RuntimeError(f"Celery 任务失败 ({action}): {tb}")

    # ---- checkpoint helpers ----

    async def _save_step_checkpoint(self, task_id: str, step: StepSpec) -> None:
        """持久化步骤状态到 PG edit_step 表。"""
        if self.step_crud is None:
            return

        try:
            step_data = {
                "tool_name": step.tool,
                "args_json": None,
                "status": step.status.value,
                "result_json": None,
                "error": step.error,
                "retry_count": step.retry_count,
                "started_at": None,
                "finished_at": None,
            }
            await asyncio.to_thread(
                self.step_crud.upsert_edit_step,
                session=_get_sync_session(),
                task_id=uuid.UUID(task_id),
                step_index=0,  # caller should track step_index
                step_data=step_data,
            )
        except Exception:
            logger.warning("保存 step checkpoint 失败: %s", task_id, exc_info=True)

    async def _save_task_status(self, plan: EditPlan) -> None:
        """持久化任务状态到 PG edit_task 表。"""
        if self.task_crud is None:
            return

        try:
            await asyncio.to_thread(
                self.task_crud.update_edit_task_status,
                session=_get_sync_session(),
                task_id=uuid.UUID(plan.task_id),
                status=plan.status.value,
            )
        except Exception:
            logger.warning("保存 task 状态失败: %s", plan.task_id, exc_info=True)

    async def _load_plan_from_checkpoint(self, task_id: str) -> EditPlan | None:
        """从 PG 读取 edit_task + edit_step 重建 EditPlan。"""
        if self.task_crud is None or self.step_crud is None:
            return None

        try:
            task = await asyncio.to_thread(
                self.task_crud.get_edit_task,
                session=_get_sync_session(),
                task_id=uuid.UUID(task_id),
            )
            if task is None:
                return None

            steps_data = await asyncio.to_thread(
                self.step_crud.get_steps_by_task,
                session=_get_sync_session(),
                task_id=uuid.UUID(task_id),
            )

            import json

            steps = [
                StepSpec(
                    id=str(s.id),
                    tool=s.tool_name,
                    args=json.loads(s.args_json) if s.args_json else {},
                    status=StepStatus(s.status),
                    result=json.loads(s.result_json) if s.result_json else None,
                    error=s.error,
                    started_at=(
                        s.started_at.timestamp() if s.started_at else None
                    ),
                    finished_at=(
                        s.finished_at.timestamp() if s.finished_at else None
                    ),
                    retry_count=s.retry_count,
                )
                for s in steps_data
            ]

            return EditPlan(
                task_id=str(task.id),
                user_id=str(task.user_id),
                script_id=str(task.script_id),
                steps=steps,
                current_index=task.current_step,
                status=TaskStatus(task.status),
                project_state=(
                    json.loads(task.project_state_json)
                    if task.project_state_json else {}
                ),
            )
        except Exception:
            logger.warning("加载 checkpoint 失败: %s", task_id, exc_info=True)
            return None


# ============================================================================
# Custom exceptions
# ============================================================================


class StepPausedError(Exception):
    """步骤暂停异常：由 NOTIFY_USER 错误触发，需用户决策后恢复。"""
    pass


# ============================================================================
# Storyboard → JyProject code 转换
# ============================================================================

def _build_jyproject_code(args: dict) -> str:
    """从单个 storyboard action 生成 JyProject Python 代码。

    每个 action 生成独立的 project → add → save 脚本。
    所有 import 由 bootstrap 自动注入。
    """
    action = args.get("action", "")
    project_name = args.get("project_name", "未命名项目")
    step_index = args.get("step_index", 0)

    # 每个步骤创建独立 project（后续步骤通过 draft_name 复用草稿）
    project_var = f"project_{step_index}"
    lines = [
        f'{project_var} = JyProject("{project_name}")',
    ]

    if action == "import_media":
        file_path = args.get("file", "")
        start = args.get("start_time", "0s")
        track = args.get("track", "main")
        lines.append(
            f'{project_var}.add_media_safe(r"{file_path}", "{start}", track_name="{track}")'
        )

    elif action == "add_text":
        text = args.get("text", "")
        start = args.get("start_time", "0s")
        duration = args.get("duration", "3s")
        lines.append(
            f'{project_var}.add_text_simple("{text}", start_time="{start}", duration="{duration}")'
        )

    elif action == "add_tts":
        text = args.get("text", "")
        speaker = args.get("speaker", "zh_male_huoli")
        start = args.get("start_time", "0s")
        lines.append(
            f'{project_var}.add_tts_intelligent("{text}", speaker="{speaker}", start_time="{start}")'
        )

    elif action == "add_audio":
        file_path = args.get("file", args.get("audio_path", ""))
        start = args.get("start_time", "0s")
        track = args.get("track", "audio")
        lines.append(
            f'{project_var}.add_audio_safe(r"{file_path}", "{start}", track_name="{track}")'
        )

    elif action == "add_effect":
        effect_name = args.get("effect_name", "")
        start = args.get("start_time", "0s")
        duration = args.get("duration", "3s")
        lines.append(
            f'{project_var}.add_effect_simple("{effect_name}", start_time="{start}", duration="{duration}")'
        )

    elif action == "add_transition":
        trans_name = args.get("transition_name", "模糊")
        duration = args.get("duration", "0.5s")
        lines.append(
            f'{project_var}.add_transition_simple("{trans_name}", duration="{duration}")'
        )

    elif action == "add_subtitle":
        text = args.get("text", "")
        speaker = args.get("speaker", "zh_male_huoli")
        start = args.get("start_time", "0s")
        lines.append(
            f'{project_var}.add_narrated_subtitles("{text}", speaker="{speaker}", start_time="{start}")'
        )

    elif action == "export":
        draft_name = args.get("draft_name", project_name)
        resolution = args.get("resolution", "1080p")
        output = args.get("output_path", f"{draft_name}.mp4")
        lines = [
            f'# Export: {draft_name}',
            f'import subprocess, sys',
            f'cmd = [sys.executable, "scripts/auto_exporter.py", "{draft_name}", "{output}", "--res", "{resolution}"]',
            f'result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=900)',
            f'print("Export:", "OK" if result.returncode == 0 else result.stderr)',
        ]

    else:
        # 未知 action → 作为通用代码执行
        lines.append(f'# Unknown action: {action}')
        for k, v in args.items():
            if k not in ("action", "step_index", "project_name", "project_config"):
                lines.append(f'# {k} = {v!r}')

    # 公共尾部
    lines.append(f'_trim_project_duration({project_var})')
    lines.append(f'{project_var}.save()')
    lines.append(f'print("Step {step_index} ({action}) done")')

    return "\n".join(lines)


# ============================================================================
# Session helper
# ============================================================================

_session_factory = None


def set_session_factory(factory):
    """注入 SQLModel Session 工厂，供 orchestrator 内部使用。"""
    global _session_factory
    _session_factory = factory


def _get_sync_session():
    """获取同步 SQLModel Session。"""
    if _session_factory is None:
        raise RuntimeError(
            "Session 工厂未设置，请调用 set_session_factory() "
            "注入 SQLModel Session 创建函数"
        )
    return _session_factory()
