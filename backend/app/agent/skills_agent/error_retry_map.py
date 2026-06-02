"""
错误分类 ↔ 重试策略统一映射表（Error-Retry Integration）。

打通 errors.py（4 类 JyError）与 retry_config.py（tenacity 重试），
为 StepOrchestrator 提供统一的 should_retry / get_strategy / classify 接口。

原则：不需要超过 4 级的分类。当前 4 级 + TimeoutExpired 已全覆盖。
"""
from __future__ import annotations

import subprocess
from enum import Enum
from typing import Any


# ============================================================================
# Import JyError hierarchy (multi-path fallback)
# ============================================================================

JyError = UserInputError = InfraError = DataError = None  # type: ignore[assignment]

for _mod_path in (
    "scripts.utils.errors",
    "jianying_editor_skill.scripts.utils.errors",
):
    try:
        _mod = __import__(_mod_path, fromlist=[
            "JyError", "UserInputError", "InfraError", "DataError",
        ])
        JyError = getattr(_mod, "JyError", None)
        UserInputError = getattr(_mod, "UserInputError", None)
        InfraError = getattr(_mod, "InfraError", None)
        DataError = getattr(_mod, "DataError", None)
        break
    except ImportError:
        continue


# ============================================================================
# RetryAction enum
# ============================================================================

class RetryAction(str, Enum):
    RETRY = "retry"           # 自动重试（指数退避）
    NOTIFY_USER = "notify"    # 不重试，通知用户
    FATAL = "fatal"           # 不重试，终止任务


# ============================================================================
# ErrorRetryConfig
# ============================================================================

class ErrorRetryConfig:
    """错误 → 重试策略的统一配置。"""

    DEFAULT_STRATEGY: dict[str, Any] = {
        "max_retries": 1,
        "wait_strategy": "exponential(1s, 30s)",
        "action": RetryAction.RETRY,
    }

    RULES: list[dict[str, Any]] = []

    @classmethod
    def _ensure_rules(cls) -> None:
        """懒初始化规则列表（错误类在首次调用时可能尚未就绪）。"""
        if cls.RULES:
            return

        rules: list[dict[str, Any]] = []

        if UserInputError is not None:
            rules.append({
                "exception": UserInputError,
                "max_retries": 0,
                "action": RetryAction.NOTIFY_USER,
                "message_template": "用户输入错误: {error}",
            })
        if InfraError is not None:
            rules.append({
                "exception": InfraError,
                "max_retries": 3,
                "action": RetryAction.RETRY,
                "wait_strategy": "exponential(1s, 30s)",
                "message_template": "基础设施错误，自动重试中: {error}",
            })
        if DataError is not None:
            rules.append({
                "exception": DataError,
                "max_retries": 0,
                "action": RetryAction.NOTIFY_USER,
                "message_template": "数据格式错误: {error}",
            })

        # System errors (always available)
        rules.extend([
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
        ])

        cls.RULES = rules

    @classmethod
    def get_strategy(cls, exc: Exception) -> dict[str, Any]:
        """根据异常类型返回重试策略。"""
        cls._ensure_rules()
        for rule in cls.RULES:
            if isinstance(exc, rule["exception"]):
                return {
                    "max_retries": rule.get("max_retries", 1),
                    "action": rule.get("action", RetryAction.RETRY),
                    "wait_strategy": rule.get(
                        "wait_strategy", "exponential(1s, 30s)",
                    ),
                }
        return dict(cls.DEFAULT_STRATEGY)

    @classmethod
    def should_retry(cls, exc: Exception) -> bool:
        """快速判断：该异常是否应自动重试。"""
        strategy = cls.get_strategy(exc)
        return (
            strategy["action"] == RetryAction.RETRY
            and strategy["max_retries"] > 0
        )

    @classmethod
    def classify_retry_action(cls, exc: Exception) -> RetryAction:
        """返回异常对应的重试动作。"""
        return cls.get_strategy(exc).get("action", RetryAction.FATAL)


# ============================================================================
# ToolRegistry retry_strategy → retry count mapping
# ============================================================================

STRATEGY_TO_RETRY_COUNT: dict[str, int] = {
    "none": 0,
    "transient": 2,
    "recoverable": 3,
}
