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

### 审查发现：使用 tenacity 替代自定义装饰器

原始需求文档建议实现自定义 `@with_retry` 装饰器，但项目已有 `tenacity==8.5.0` 依赖（[pyproject.toml:11](backend/pyproject.toml#L11)）。`tenacity` 是 Python 生态中最成熟的重试库，提供指数退避、重试条件判断、回调钩子等开箱即用的功能——**应直接使用 tenacity，避免重复造轮**。

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
)

DEFAULT_RETRY = {
    "max_attempts": 3,
    "min_wait": 1,       # 首次等待 1 秒
    "max_wait": 30,      # 最大等待 30 秒
}

def make_retry_decorator(max_attempts: int = 3, retryable_exceptions: tuple = (
    TimeoutError, ConnectionError, OSError,
)):
    """创建标准重试装饰器"""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception_type(retryable_exceptions),
        reraise=True,
    )
```

### 3.2 改造 CLI 执行器

为 `CLIScriptExecutor.execute()` 增加可选 `max_retries` 参数，在 `subprocess.TimeoutExpired` 和 `returncode != 0`（可重试错误码）时自动重试：

```python
# 在 execute() 中追加重试逻辑
from tenacity import retry, stop_after_attempt, wait_exponential

def execute(self, script_name, args, timeout=300, max_retries=3):
    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(...)
            if result.returncode == 0:
                return {"success": True, ...}
            # 某些错误码不可重试（如参数错误）
            if result.returncode in (2, 22):  # ENOENT, EINVAL
                return {"success": False, ...}
        except subprocess.TimeoutExpired:
            if attempt == max_retries:
                return {"success": False, "error": f"重试 {max_retries} 次后仍超时"}
            wait = min(2 ** attempt, 30)
            time.sleep(wait)
```

### 3.3 改造 universal_tts.py（规范化已有重试）

将其手写重试循环替换为 tenacity：

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError)),
)
async def _run_sami_tts_with_retry(text, speaker, output_file, dev_id, iid):
    return await _run_sami_tts(text, speaker, output_file, dev_id, iid)
```

保持现有 fallback 到 edge-tts 的降级逻辑不变。

### 3.4 降级策略设计

每种工具需定义其专属降级行为：

| 工具 | 重试耗尽后降级策略 |
|------|-------------------|
| `_run_sami_tts` | 已有 fallback → edge-tts（保持不变） |
| `media_normalizer` | 返回原始文件路径（跳过标准化，由剪映自行处理） |
| `smart_rough_cut` | 返回空 clips 列表 + 警告信息 |
| `execute_jyproject_code` | 返回完整错误日志，提示用户手动介入 |

### 3.5 关于 LangGraph 重试子图

原始需求文档附录中的 LangGraph StateGraph 重试方案（第 4 节）更适合 **Agent 级别的推理重试**（如 LLM 生成的代码执行失败后让 LLM 修复代码再执行）。对于本需求的**工具级别的瞬时故障重试**，使用 tenacity 直接包裹 subprocess 调用是更轻量、更合适的选择。两者不冲突——可在后续迭代中叠加 LangGraph 方案处理 Agent 级重试。

## 4. 验收标准

1. **所有 key 工具调用具备重试机制**：`cli_executor.execute()`, `python_executor.execute()`, `media_normalizer.normalize_webm_for_jianying()` 在临时性失败时自动重试至少 2 次。
2. **使用 tenacity 实现指数退避**：重试等待时间呈指数增长（1s → 2s → 4s，上限 30s），而非固定延迟。
3. **重试耗尽后触发降级**：每种工具在超过最大重试次数后调用预定义的 fallback 函数，返回结构化降级结果而非直接抛异常。
