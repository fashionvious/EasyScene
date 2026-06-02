# 需求 09：Celery 重量步骤异步执行

> 原 PRD 编号: P1-1 (E-1) | 优先级: P1

## 1. 依赖关系

- **前置依赖**：req_02（ToolRegistry — 提供 `exec_mode="async"` 标记）、req_03（StepOrchestrator — 需要调度 Celery 任务的方法）、req_04（PG 模型 — 步骤结果写入 edit_step 表）
- **被谁依赖**：无后续需求硬依赖，但 req_10（素材并发）可能需要 Celery 的任务复用

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

项目已有两个 Celery 应用实例：
1. `backend/app/agent/core/celery_app.py` — 核心 Celery app（broker=settings.celery_broker_url）
2. `backend/app/agent/generateVideo/celery_config.py` — text2video 专用 Celery app（独立 broker URL）

```python
# celery_config.py
celery_app = Celery(
    main="text2video_tasks",
    broker="redis://:123456@localhost:6379/0",
    backend="redis://:123456@localhost:6379/0",
)
```

**重量/轻量步骤分类**（PRD Table E-1）：

| 轻量步骤（Agent 同步 ≤ 30s） | 重量步骤（Celery 异步 > 30s） |
|---------------------------|---------------------------|
| resolve_media (30s) | FFmpeg normalize (120-600s) |
| add_text_simple (<1s) | TTS synthesis (30-120s) |
| add_media_safe (<1s) | auto_exporter (60-900s) |
| load_skill (<1s) | smart_rough_cut (180-600s) |
| JyProject.save() (<1s) | 云端素材下载 (不定) |

### 代码库校验结论

- 复用 `celery_config.py` 的 `celery_app` 实例（避免创建第三个 Celery app）
- 新增 Celery Task 定义在独立文件 `backend/app/agent/skills_agent/celery_tasks.py`
- 在 `celery_config.py` 的 `include` 中注册新 Task 模块
- StepOrchestrator 的 `_dispatch_celery()`（在 req_03 中留白）在本需求实现

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `backend/app/agent/skills_agent/celery_tasks.py` | 编辑步骤 Celery Task 定义 |
| 修改 | `backend/app/agent/generateVideo/celery_config.py` | 注册新 Task 模块 |
| 修改 | `backend/app/agent/skills_agent/step_orchestrator.py` | 实现 `_dispatch_celery()` |

### 核心技术细节

```python
"""celery_tasks.py — 重量编辑步骤的 Celery Task 包装"""
import json
import logging
from datetime import datetime
from celery import shared_task
from celery_config import celery_app  # 复用现有 celery 实例

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def ffmpeg_normalize_task(self, video_path: str, output_path: str, **kwargs) -> dict:
    """FFmpeg 视频标准化（耗时 120-600s）"""
    from scripts.utils.media_normalizer import normalize_webm_for_jianying
    from scripts.utils.process_utils import run_with_timeout

    try:
        result = normalize_webm_for_jianying(video_path, output_path)
        return {"success": True, "output_path": output_path, "result": str(result)}
    except Exception as e:
        logger.error(f"FFmpeg normalize failed: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        return {"success": False, "error": str(e)}


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10)
def tts_synthesis_task(self, text: str, output_path: str, speaker: str = "zh_male_huoli") -> dict:
    """TTS 语音合成（耗时 30-120s）"""
    import subprocess
    import sys

    try:
        cmd = [
            sys.executable,
            "scripts/universal_tts.py",
            "--text", text,
            "--output", output_path,
            "--speaker", speaker,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return {"success": True, "output_path": output_path}
        else:
            raise RuntimeError(result.stderr)
    except Exception as e:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        return {"success": False, "error": str(e)}


@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def auto_export_task(self, draft_name: str, output_path: str,
                     resolution: str = "1080p", fps: int = 30) -> dict:
    """自动导出（耗时 60-900s）"""
    import subprocess
    import sys

    try:
        cmd = [
            sys.executable,
            "scripts/auto_exporter.py",
            draft_name, output_path,
            "--res", resolution,
            "--fps", str(fps),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if result.returncode == 0:
            return {"success": True, "output_path": output_path}
        else:
            raise RuntimeError(result.stderr)
    except Exception as e:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        return {"success": False, "error": str(e)}


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def smart_rough_cut_task(self, video_path: str, **kwargs) -> dict:
    """智能粗剪（耗时 180-600s）"""
    import subprocess
    import sys

    try:
        cmd = [sys.executable, "scripts/smart_rough_cut.py", "--video", video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            return {"success": True, "output": result.stdout}
        else:
            raise RuntimeError(result.stderr)
    except Exception as e:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        return {"success": False, "error": str(e)}


# 工具名 → Celery Task 映射
ASYNC_TOOL_TASK_MAP = {
    "execute_cli_script": {
        "smart_rough_cut": smart_rough_cut_task,
        "auto_exporter": auto_export_task,
        "universal_tts": tts_synthesis_task,
    },
}
```

**StepOrchestrator `_dispatch_celery` 实现**：

```python
# step_orchestrator.py 中替换 req_03 的占位实现

async def _dispatch_celery(self, step: StepSpec, spec: ToolSpec) -> Any:
    """将重量步骤提交为 Celery Task，轮询等待完成"""
    from celery_tasks import ASYNC_TOOL_TASK_MAP
    from celery.result import AsyncResult

    script_name = step.args.get("script_name", step.args.get("action", "unknown"))
    task_map = ASYNC_TOOL_TASK_MAP.get(step.tool, {})

    celery_task = task_map.get(script_name)
    if celery_task is None:
        # 未匹配到特定 Celery Task → 回退到同步 subprocess 执行
        logger.warning(f"无 Celery Task 匹配 {step.tool}:{script_name}，回退同步执行")
        return spec.func(**step.args)

    # 提取参数并提交
    task_kwargs = {k: v for k, v in step.args.items()
                   if k not in ("action", "step_index", "project_name", "project_config")}
    result: AsyncResult = celery_task.delay(**task_kwargs)

    # 写入 Redis 关联（celery_task_id → edit_step）
    if self.redis:
        await self.redis.set_celery_task_id(step.id, result.id)

    # 轮询等待 Celery 任务完成
    while not result.ready():
        await asyncio.sleep(2)

    if result.successful():
        return result.get()
    else:
        raise RuntimeError(f"Celery 任务失败: {result.traceback}")
```

### 容错与边界

- Celery Task 失败时自动重试（`max_retries` 配置在 Task 级别）
- 未匹配到 Celery Task 时回退到同步执行（兼容性）
- `celery_task_id` 写入 Redis 供前端轮询实时状态（req_14 的 Redis Manager）
- Celery Worker 不在运行时，异步步骤表现为超时错误（由 req_13 断路器保护）
- 任务超时通过 Celery `task_soft_time_limit` 控制

## 4. 验收标准 (DoD)

- [ ] `ffmpeg_normalize_task.delay("input.mp4", "output.mp4")` 提交成功，返回 AsyncResult
- [ ] Celery Worker 能正确执行 FFmpeg/TTS/Export/RoughCut 四种异步任务
- [ ] Task 失败时自动重试（`max_retries=2`）
- [ ] `_dispatch_celery()` 对未匹配的步骤回退同步执行
- [ ] `celery_task_id` 关联正确写入 Redis
- [ ] Celery 任务状态变更在 PG edit_step 表中正确反映
