# 需求 11：错误分类与重试策略打通

> 原 PRD 编号: P1-4 (D-1) | 优先级: P1

## 1. 依赖关系

- **前置依赖**：req_02（ToolRegistry — `retry_strategy` 字段）、现有 `errors.py` + `retry_config.py`
- **被谁依赖**：req_13（断路器 — 依赖错误分类判断是否触发断路）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库有两套独立的错误处理体系：

**A. `errors.py`**（[backend/jianying-editor-skill/scripts/utils/errors.py](../../../backend/jianying-editor-skill/scripts/utils/errors.py)）：
```python
class JyError(Exception):       # 基础错误
class UserInputError(JyError):  # 用户输入错误
class InfraError(JyError):      # 基础设施错误
class DataError(JyError):       # 数据格式错误
```

**B. `retry_config.py`**（[backend/app/agent/skills_agent/retry_config.py](../../../backend/app/agent/skills_agent/retry_config.py)）：
```python
RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    OSError,
    subprocess.TimeoutExpired,
)
```

**问题**：两套体系互不感知。`retry_config.py` 不知道 `InfraError` 应该重试、`UserInputError` 不应该重试。

**PRD 修正**：不需要 5 级分类。当前 4 级 + `subprocess.TimeoutExpired` 已覆盖所有实际场景。TRANSIENT vs RECOVERABLE 在实践中边界模糊，合并为统一的 `retry_strategy` 字段。

### 代码库校验结论

- `cli_executor.py:22-26` 定义了 `RETRYABLE_RETURN_CODES`（进程退出码白名单），与 `errors.py` 完全独立
- `python_executor.py:276-323` 的 `execute()` 根据异常类型决定重试：`TimeoutExpired` 和 `OSError` 可重试，其他不重试
- 需要创建一个统一的映射表，将 `JyError` 子类 + 系统异常 → `retry_strategy`

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `backend/app/agent/skills_agent/error_retry_map.py` | 错误→重试策略统一映射表 |
| 修改 | `backend/app/agent/skills_agent/retry_config.py` | 引用统一映射表 |
| 修改 | `backend/jianying-editor-skill/scripts/utils/errors.py` | 无需修改（仅被引用） |

### 核心技术细节

```python
"""error_retry_map.py — 错误分类 ↔ 重试策略统一映射表"""
import subprocess
from enum import Enum
from typing import Any

# 引用现有错误类
from scripts.utils.errors import JyError, UserInputError, InfraError, DataError


class RetryAction(str, Enum):
    RETRY = "retry"           # 自动重试（指数退避）
    NOTIFY_USER = "notify"    # 不重试，通知用户
    FATAL = "fatal"           # 不重试，终止任务


class ErrorRetryConfig:
    """错误 → 重试策略的统一配置"""

    # 默认策略（未匹配到的异常）
    DEFAULT_STRATEGY = {
        "max_retries": 1,
        "wait_strategy": "exponential(1s, 30s)",
        "action": RetryAction.RETRY,
    }

    # (异常类型, action, max_retries, wait_strategy)
    RULES: list[dict[str, Any]] = [
        {
            "exception": UserInputError,
            "max_retries": 0,
            "action": RetryAction.NOTIFY_USER,
            "message_template": "用户输入错误: {error}",
        },
        {
            "exception": InfraError,
            "max_retries": 3,
            "action": RetryAction.RETRY,
            "wait_strategy": "exponential(1s, 30s)",
            "message_template": "基础设施错误，自动重试中: {error}",
        },
        {
            "exception": DataError,
            "max_retries": 0,
            "action": RetryAction.NOTIFY_USER,
            "message_template": "数据格式错误: {error}",
        },
        {
            "exception": subprocess.TimeoutExpired,
            "max_retries": 2,
            "action": RetryAction.RETRY,
            "wait_strategy": "exponential(1s, 15s)",
            "message_template": "执行超时，自动重试中: {error}",
        },
        {
            "exception": TimeoutError,
            "max_retries": 2,
            "action": RetryAction.RETRY,
            "wait_strategy": "exponential(1s, 15s)",
        },
        {
            "exception": ConnectionError,
            "max_retries": 3,
            "action": RetryAction.RETRY,
            "wait_strategy": "exponential(1s, 30s)",
        },
        {
            "exception": OSError,
            "max_retries": 2,
            "action": RetryAction.RETRY,
            "wait_strategy": "exponential(1s, 15s)",
        },
    ]

    @classmethod
    def get_strategy(cls, exc: Exception) -> dict:
        """根据异常类型返回重试策略"""
        for rule in cls.RULES:
            if isinstance(exc, rule["exception"]):
                return {
                    "max_retries": rule.get("max_retries", 1),
                    "action": rule.get("action", RetryAction.RETRY),
                    "wait_strategy": rule.get("wait_strategy", "exponential(1s, 30s)"),
                }
        return cls.DEFAULT_STRATEGY

    @classmethod
    def should_retry(cls, exc: Exception) -> bool:
        """快速判断：该异常是否应重试"""
        strategy = cls.get_strategy(exc)
        return strategy["action"] == RetryAction.RETRY and strategy["max_retries"] > 0

    @classmethod
    def classify_retry_action(cls, exc: Exception) -> RetryAction:
        """返回异常对应的重试动作"""
        return cls.get_strategy(exc).get("action", RetryAction.FATAL)


# 与 ToolRegistry 的 retry_strategy 字段集成
STRATEGY_TO_RETRY_COUNT = {
    "none": 0,
    "transient": 2,
    "recoverable": 3,
}
```

**集成到现有 retry_config.py**（无需破坏现有接口）：

```python
# retry_config.py 修改

from error_retry_map import ErrorRetryConfig

# 原有 RETRYABLE_EXCEPTIONS 保留向后兼容，但标记为 deprecated
RETRYABLE_EXCEPTIONS = (
    TimeoutError, ConnectionError, OSError, subprocess.TimeoutExpired,
)

def make_retry_decorator(
    max_attempts: int = 3,
    retryable_exceptions: tuple = RETRYABLE_EXCEPTIONS,
):
    """不变，向后兼容。新代码推荐使用 ErrorRetryConfig.should_retry()"""
    # ... 现有实现保持不变 ...
```

**StepOrchestrator 集成**（在 `_execute_single_step` 中使用）：

```python
# step_orchestrator.py

from error_retry_map import ErrorRetryConfig, RetryAction

async def _execute_single_step(self, plan, step) -> Any:
    spec = self.registry.get_spec(step.tool)
    max_retries = spec.max_retries if spec else 0

    for attempt in range(max(1, max_retries + 1)):
        try:
            # ... 执行步骤 ...
            return result
        except Exception as e:
            strategy = ErrorRetryConfig.get_strategy(e)

            if strategy["action"] == RetryAction.NOTIFY_USER:
                step.error = f"[需用户处理] {e}"
                raise

            if strategy["action"] == RetryAction.FATAL:
                step.error = f"[致命错误] {e}"
                raise

            # RetryAction.RETRY
            if attempt >= strategy["max_retries"]:
                step.error = f"重试 {strategy['max_retries']} 次后仍失败: {e}"
                raise

            wait = min(2 ** attempt, 30)
            logger.warning(f"步骤 {step.tool} 失败，{wait}s 后重试 (attempt {attempt+1}): {e}")
            await asyncio.sleep(wait)
            step.retry_count = attempt + 1
```

### 容错与边界

- `ErrorRetryConfig.get_strategy()` 对未知异常类型返回 DEFAULT_STRATEGY（重试 1 次）
- `should_retry()` 为快速路径，避免在重试循环中每次都解析完整策略
- 不删除 `retry_config.py` 中的现有代码（向后兼容），仅新增统一映射表
- `errors.py` 无需修改（保持独立定义，由 mapper 引用）
- 新增错误类型时，只需在 `ErrorRetryConfig.RULES` 中添加一行

## 4. 验收标准 (DoD)

- [ ] `ErrorRetryConfig.should_retry(UserInputError("bad arg"))` 返回 `False`
- [ ] `ErrorRetryConfig.should_retry(InfraError("redis down"))` 返回 `True`
- [ ] `ErrorRetryConfig.should_retry(subprocess.TimeoutExpired("cmd", 30))` 返回 `True`
- [ ] `ErrorRetryConfig.classify_retry_action(DataError("bad json"))` 返回 `RetryAction.NOTIFY_USER`
- [ ] 对未知异常类型（如 `ValueError`），返回 DEFAULT_STRATEGY（retry=1）
- [ ] 现有的 `retry_config.make_retry_decorator()` 函数行为不变
- [ ] StepOrchestrator 执行失败步骤时，根据异常类型决定重试/通知/终止
