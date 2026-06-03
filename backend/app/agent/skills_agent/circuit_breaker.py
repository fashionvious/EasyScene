"""
轻量断路器（Circuit Breaker）。

仅用于 uiautomation 调用和 FFmpeg 调用——这些上游依赖可能完全不可用，
连续重试无意义。LLM 调用、文件操作不适用断路器。

状态机：CLOSED → (连续N次失败) → OPEN → (等待M秒) → HALF_OPEN → 探测 → CLOSED/OPEN
默认关闭（enabled=False），通过 Feature Flag 或 settings 控制启用。
"""
from __future__ import annotations

import logging
import threading
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"        # 正常通行
    OPEN = "open"            # 熔断，直接拒绝
    HALF_OPEN = "half_open"  # 允许 1 次探测调用


class CircuitBreakerOpenError(Exception):
    """断路器熔断时抛出的异常"""
    pass


class CircuitBreaker:
    """轻量断路器。

    用法:
        cb = CircuitBreaker("ffmpeg", failure_threshold=3, recovery_timeout=60)
        try:
            result = cb.call(ffmpeg_func, input_path, output_path)
        except CircuitBreakerOpenError:
            # 断路器熔断，等待恢复
            ...
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
        enabled: bool = False,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.enabled = enabled

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._lock = threading.Lock()

    # ---- public ----

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    logger.info(
                        "[CB:%s] OPEN → HALF_OPEN (等待 %.0fs 超时)",
                        self.name, self.recovery_timeout,
                    )
            return self._state

    def call(self, func, *args, **kwargs):
        """受断路器保护的函数调用。enabled=False 时直接透传。"""
        if not self.enabled:
            return func(*args, **kwargs)

        current_state = self.state
        if current_state == CircuitState.OPEN:
            raise CircuitBreakerOpenError(
                f"[CB:{self.name}] 断路器已熔断"
                f"（连续 {self.failure_threshold} 次失败），"
                f"请等待 {self.recovery_timeout}s 后重试"
            )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            raise

    def reset(self) -> None:
        """手动重置断路器（运维操作）。"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = 0.0
            logger.info("[CB:%s] 手动重置为 CLOSED", self.name)

    # ---- internal ----

    def _on_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                logger.info("[CB:%s] HALF_OPEN → CLOSED (探测成功)", self.name)

    def _on_failure(self, error: Exception) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._failure_count = self.failure_threshold
                logger.warning(
                    "[CB:%s] HALF_OPEN → OPEN (探测失败: %s)",
                    self.name, error,
                )
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.error(
                    "[CB:%s] CLOSED → OPEN (连续 %s 次失败, 最近: %s)",
                    self.name, self._failure_count, error,
                )


# ============================================================================
# 预定义断路器单例
# ============================================================================

_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """获取或创建断路器实例（按名称单例）。

    配置通过环境变量 / settings 读取（默认关闭）：
    - CB_UI_ENABLED / CB_FFMPEG_ENABLED: bool
    - CB_UI_FAILURE_THRESHOLD / CB_FFMPEG_FAILURE_THRESHOLD: int
    - CB_UI_RECOVERY_TIMEOUT / CB_FFMPEG_RECOVERY_TIMEOUT: float
    """
    if name in _breakers:
        return _breakers[name]

    import os
    env_prefix = f"CB_{name.upper()}"

    enabled = os.getenv(f"{env_prefix}_ENABLED", "false").lower() in (
        "true", "1", "yes",
    )
    failure_threshold = int(os.getenv(
        f"{env_prefix}_FAILURE_THRESHOLD", "3",
    ))
    recovery_timeout = float(os.getenv(
        f"{env_prefix}_RECOVERY_TIMEOUT", "60",
    ))

    cb = CircuitBreaker(
        name=name,
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout,
        enabled=enabled,
    )
    _breakers[name] = cb
    return cb
