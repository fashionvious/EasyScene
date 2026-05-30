# 需求 02：为任务执行增加重试机制与降级策略

## 1. 需求背景与目标

当前 CLI 执行器和 Python 执行器在工具调用失败时直接返回错误，没有任何重试机制。对于网络抖动（TTS WebSocket 连接）、临时性资源不足（FFmpeg 内存不足）等可恢复错误，直接失败导致用户体验差，需要手动重试。

**核心目标**：为所有关键工具调用增加指数退避重试机制，重试耗尽后触发降级策略（如切换 fallback 引擎或返回缓存结果）。

## 2. 现有代码分析

### 已有重试逻辑的模块

| 文件 | 当前行为 | 评估 |
|------|----------|------|
| [universal_tts.py](backend/jianying-editor-skill/scripts/universal_tts.py#L169-L181) | 手写 `for i in range(sami_retries)` 循环 + fallback 到 edge-tts | 逻辑正确，但重试方式不规范（固定延迟 `asyncio.sleep(0.35)`，非指数退避） |

### 缺失重试逻辑的模块

| 文件 | 当前行为 | 风险 |
|------|----------|------|
| [cli_executor.py](backend/app/agent/skills_agent/cli_executor.py#L238-L271) | 失败直接返回 error dict | 临时错误（如 FFmpeg 资源占用）导致任务失败 |
| [python_executor.py](backend/app/agent/skills_agent/python_executor.py#L135-L166) | 失败直接返回 error dict | 同上 |
| [media_normalizer.py](backend/jianying-editor-skill/scripts/utils/media_normalizer.py#L69-L81) | 失败直接 return None | 无重试 |

### 审查发现

1. **使用 tenacity 替代自定义装饰器**：原始需求文档建议实现自定义 `@with_retry` 装饰器，但项目已有 `tenacity==8.5.0` 依赖（[pyproject.toml:11](backend/pyproject.toml#L11)）。`tenacity` 是 Python 生态中最成熟的重试库，提供指数退避、重试条件判断、回调钩子等开箱即用的功能——**应直接使用 tenacity，避免重复造轮**。
2. **tenacity 与 async 函数的兼容性**：`universal_tts.py` 中的 `_run_sami_tts` 是 `async` 函数，tenacity 的 `@retry` 装饰器对 async 函数需要使用 `tenacity.AsyncRetrying` 或确保 tenacity 版本 ≥ 8.0（自动支持 async）。项目使用 `tenacity==8.5.0`，已原生支持 async，无需额外处理。
3. **cli_executor.py 的重试设计需区分可重试/不可重试错误**：原始文档的伪代码在循环中硬编码了 `returncode in (2, 22)` 作为不可重试错误码，但不同 CLI 工具的退出码含义不同，硬编码会误判。应改为基于错误类型/异常的重试策略。
4. **media_normalizer.py 的重试需注意返回类型**：该函数返回 `Optional[str]`，重试装饰器应包裹内部 subprocess 调用而非整个函数，以保持返回类型不变。

## 3. 技术实现方案

### 3.1 定义统一重试配置

在 `backend/app/agent/skills_agent/` 下新建 `retry_config.py`：

```python
"""统一重试配置 —— 基于项目已有的 tenacity 库"""
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import logging

logger = logging.getLogger(__name__)

DEFAULT_RETRY_CONFIG = {
    "max_attempts": 3,
    "min_wait": 1,       # 首次等待 1 秒
    "max_wait": 30,      # 最大等待 30 秒
}

RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    OSError,
    subprocess.TimeoutExpired,  # subprocess 超时也应重试
)

def make_retry_decorator(
    max_attempts: int = 3,
    retryable_exceptions: tuple = RETRYABLE_EXCEPTIONS,
):
    """创建标准重试装饰器"""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception_type(retryable_exceptions),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
```

> **修正说明**：
> 1. 原始文档的 `retryable_exceptions` 遗漏了 `subprocess.TimeoutExpired`，这是 CLI/Python 执行器最常遇到的超时异常，必须纳入可重试范围。
> 2. 补充了 `before_sleep_log` 钩子，在重试等待时输出 WARNING 日志，便于排查问题（遵循 AGENTS.md 中"严禁使用 print，使用 logging"的规范）。

### 3.2 改造 CLI 执行器

为 `CLIScriptExecutor.execute()` 增加可选 `max_retries` 参数。

**关键设计决策**：不使用 tenacity 的 `@retry` 装饰器包裹整个 `execute()` 方法，因为：
- `execute()` 返回 `dict`，不抛异常（所有错误都封装为 `{"success": False, ...}`），tenacity 无法判断何时该重试。
- 需要根据 `returncode` 和异常类型区分可重试/不可重试错误。

采用**内部循环 + 指数退避**方案：

```python
import time
import logging

logger = logging.getLogger(__name__)

RETRYABLE_RETURN_CODES = frozenset({
    # 通用可重试退出码（资源不足、临时故障等）
    # 注意：不包含 2(ENOENT) 和 22(EINVAL) 等参数错误码
    1,   # 一般错误（多数 CLI 工具的默认错误码）
    137, # SIGKILL（OOM killer）
    139, # SIGSEGV
})

def execute(self, script_name, args, timeout=300, max_retries=0):
    # ... 前置检查不变 ...

    last_error = None
    for attempt in range(max(1, max_retries + 1)):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, cwd=str(self.scripts_dir)
            )

            if result.returncode == 0:
                return {"success": True, "output": ..., "raw_output": ..., "error": None, "returncode": 0}

            # 不可重试的错误码 → 立即返回
            if result.returncode not in RETRYABLE_RETURN_CODES:
                return {"success": False, "error": result.stderr.strip(), "returncode": result.returncode}

            # 可重试的错误码 → 记录错误，继续循环
            last_error = f"returncode={result.returncode}, stderr={result.stderr.strip()}"
            logger.warning(f"[attempt {attempt+1}/{max_retries+1}] {script_name} failed: {last_error}")

        except subprocess.TimeoutExpired:
            last_error = f"timeout({timeout}s)"
            logger.warning(f"[attempt {attempt+1}/{max_retries+1}] {script_name} {last_error}")

        except Exception as e:
            # 非预期异常 → 不重试，直接返回
            return {"success": False, "error": f"执行失败: {str(e)}"}

        # 指数退避等待（最后一次失败不等待）
        if attempt < max_retries:
            wait = min(2 ** attempt, 30)
            logger.info(f"等待 {wait}s 后重试...")
            time.sleep(wait)

    # 重试耗尽
    return {
        "success": False,
        "error": f"重试 {max_retries} 次后仍失败: {last_error}",
    }
```

> **修正说明**：
> 1. 原始文档的伪代码在 `returncode != 0` 时全部进入重试逻辑，但参数错误（如 ENOENT=2, EINVAL=22）重试无意义且浪费时间。改为白名单机制：仅 `RETRYABLE_RETURN_CODES` 中的退出码触发重试。
> 2. 原始文档硬编码 `(2, 22)` 为不可重试错误码，但不同工具的退出码含义不同。改为白名单更安全——只有明确可重试的才重试。
> 3. 原始文档未处理非预期异常（如 `FileNotFoundError`），补充后直接返回不重试。
> 4. 默认 `max_retries=0`（不重试），保持向后兼容；调用方需显式启用重试。

### 3.3 改造 Python 执行器

与 CLI 执行器类似，为 `PythonCodeExecutor.execute()` 增加 `max_retries` 参数。由于 LLM 生成的代码执行失败通常是逻辑错误（重试无意义），**Python 执行器仅对 `subprocess.TimeoutExpired` 和 `OSError` 进行重试**，不对 `returncode != 0` 重试：

```python
def execute(self, code, include_bootstrap=True, capture_output=True, max_retries=0):
    last_error = None
    for attempt in range(max(1, max_retries + 1)):
        try:
            result = subprocess.run(cmd, capture_output=capture_output, text=True,
                                     timeout=self.timeout, cwd=str(self.work_dir))
            # 无论 returncode 如何，都不重试（代码逻辑错误重试无意义）
            return {"success": result.returncode == 0, ...}

        except subprocess.TimeoutExpired:
            last_error = f"代码执行超时（{self.timeout}秒）"
            if attempt < max_retries:
                wait = min(2 ** attempt, 30)
                time.sleep(wait)
                continue
            return {"success": False, "error": f"重试 {max_retries} 次后仍超时", ...}

        except OSError as e:
            last_error = str(e)
            if attempt < max_retries:
                wait = min(2 ** attempt, 30)
                time.sleep(wait)
                continue
            return {"success": False, "error": f"重试 {max_retries} 次后仍失败: {last_error}", ...}

        except Exception as e:
            # 非预期异常不重试
            return {"success": False, "error": f"执行失败: {str(e)}", ...}
```

> **修正说明**：原始文档未区分 CLI 执行器和 Python 执行器的重试策略差异。Python 执行器运行的是 LLM 生成的代码，`returncode != 0` 几乎总是代码逻辑错误，重试不会改变结果。

### 3.4 改造 universal_tts.py（规范化已有重试）

将其手写重试循环替换为 tenacity：

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
)
async def _run_sami_tts_with_retry(text, speaker, output_file, dev_id, iid):
    return await _run_sami_tts(text, speaker, output_file, dev_id, iid)
```

保持现有 fallback 到 edge-tts 的降级逻辑不变。

> **修正说明**：原始文档遗漏了 `OSError` 作为可重试异常。WebSocket 连接可能抛出 `OSError`（如网络接口不可用），应纳入重试范围。

### 3.5 改造 media_normalizer.py

对 `normalize_webm_for_jianying` 中的 subprocess 调用增加重试，**包裹 subprocess 调用而非整个函数**，以保持返回类型 `Optional[str]` 不变：

```python
from utils.retry_helper import make_sync_retry_decorator

_subprocess_retry = make_sync_retry_decorator(
    max_attempts=2,  # FFmpeg 转码重试 2 次即可
    retryable_exceptions=(subprocess.TimeoutExpired, OSError),
)

@_subprocess_retry
def _run_ffmpeg_normalize(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          timeout=SubprocessConfig.default_timeout)

def normalize_webm_for_jianying(input_path: str) -> Optional[str]:
    # ... 前置检查不变 ...

    try:
        proc = _run_ffmpeg_normalize(cmd)
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        return None
    except FileNotFoundError:
        print("❌ FFmpeg not found.")
        return None

    # ... 后续逻辑不变 ...
```

> **修正说明**：原始文档未提及 `media_normalizer.py` 的重试改造方式。若直接用 `@retry` 装饰整个函数，tenacity 会在函数返回 `None` 时也触发重试（因为 `None` 不是异常），导致逻辑错误。必须改为包裹 subprocess 调用。

### 3.6 降级策略设计

每种工具需定义其专属降级行为：

| 工具 | 重试耗尽后降级策略 | 降级返回值 |
|------|-------------------|-----------|
| `_run_sami_tts` | 已有 fallback → edge-tts（保持不变） | `(audio_path, "edge")` |
| `media_normalizer` | 返回原始文件路径（跳过标准化，由剪映自行处理） | `input_path`（原始路径，而非 `None`） |
| `smart_rough_cut` | 返回空 clips 列表 + 警告信息 | `[]` + 日志 WARNING |
| `execute_jyproject_code` | 返回完整错误日志，提示用户手动介入 | `{"success": False, "error": "...", "hint": "请检查代码逻辑或手动重试"}` |
| `cli_executor` | 返回结构化错误（含重试次数和最后错误信息） | `{"success": False, "error": "重试 N 次后仍失败: ...", "attempts": N+1}` |

> **修正说明**：
> 1. 原始文档的降级策略表中 `media_normalizer` 降级为"返回原始文件路径"，但代码中返回类型为 `Optional[str]`，返回 `input_path` 是合法的（`str` 是 `Optional[str]` 的子类型），且语义正确——跳过标准化让剪映自行处理。但需在调用方文档中说明：返回值可能是原始路径（未标准化），调用方不应假设返回值一定是 MP4。
> 2. 补充了 `cli_executor` 的降级返回值格式（含 `attempts` 字段），便于上层判断重试情况。

### 3.7 关于 LangGraph 重试子图

原始需求文档附录中的 LangGraph StateGraph 重试方案（第 4 节）更适合 **Agent 级别的推理重试**（如 LLM 生成的代码执行失败后让 LLM 修复代码再执行）。对于本需求的**工具级别的瞬时故障重试**，使用 tenacity 直接包裹 subprocess 调用是更轻量、更合适的选择。两者不冲突——可在后续迭代中叠加 LangGraph 方案处理 Agent 级重试。

## 4. 验收标准

1. **所有 key 工具调用具备重试机制**：`cli_executor.execute()`, `python_executor.execute()`, `media_normalizer.normalize_webm_for_jianying()` 在临时性失败时自动重试至少 2 次。
2. **使用 tenacity 实现指数退避**：`universal_tts.py` 的重试等待时间呈指数增长（1s → 2s → 4s，上限 10s），而非固定延迟 0.35s。
3. **重试耗尽后触发降级**：每种工具在超过最大重试次数后调用预定义的 fallback 函数，返回结构化降级结果而非直接抛异常。
4. **CLI 执行器区分可重试/不可重试错误**：`returncode` 在白名单 `RETRYABLE_RETURN_CODES` 中的才触发重试，参数错误等立即返回。
5. **Python 执行器仅对超时和 OSError 重试**：`returncode != 0` 不触发重试（LLM 代码逻辑错误重试无意义）。
6. **media_normalizer 的重试包裹 subprocess 调用而非整个函数**：避免 tenacity 在函数返回 `None` 时误触发重试。
7. **重试日志使用 logging 而非 print**：遵循 AGENTS.md 规范。

## 5. 原始文档问题汇总

| # | 问题 | 严重程度 | 修正措施 |
|---|------|----------|----------|
| 1 | `retryable_exceptions` 遗漏 `subprocess.TimeoutExpired`，这是最常遇到的可重试异常 | 高 | 补充到 `RETRYABLE_EXCEPTIONS` 元组 |
| 2 | CLI 执行器重试伪代码硬编码不可重试错误码 `(2, 22)`，不同工具退出码含义不同 | 高 | 改为白名单机制 `RETRYABLE_RETURN_CODES` |
| 3 | CLI 执行器在 `returncode != 0` 时全部进入重试，参数错误重试无意义 | 高 | 仅白名单中的退出码触发重试 |
| 4 | 未区分 CLI 和 Python 执行器的重试策略差异 | 高 | Python 执行器仅对 TimeoutExpired/OSError 重试 |
| 5 | `media_normalizer.py` 的重试方式未说明，直接用 `@retry` 装饰整个函数会导致返回 `None` 时也重试 | 高 | 包裹 subprocess 调用而非整个函数 |
| 6 | tenacity async 兼容性未说明 | 中 | 补充说明 tenacity 8.5.0 原生支持 async |
| 7 | 重试日志使用 `print` 而非 `logging`，违反 AGENTS.md 规范 | 中 | 改用 `logging.WARNING` + `before_sleep_log` |
| 8 | `universal_tts.py` 的可重试异常遗漏 `OSError` | 中 | 补充 `OSError` |
| 9 | 降级策略表中 `media_normalizer` 返回原始路径的语义未说明 | 低 | 补充说明调用方不应假设返回值一定是标准化后的 MP4 |
| 10 | 默认 `max_retries` 参数值未指定，可能导致所有调用默认重试 | 中 | 默认 `max_retries=0`（向后兼容），调用方显式启用 |
