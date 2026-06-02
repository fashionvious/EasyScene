# 需求 13：断路器（Circuit Breaker）

> 原 PRD 编号: P1-5 (D-2) | 优先级: P1

## 1. 依赖关系

- **前置依赖**：req_11（错误分类与重试打通 — 判定哪些错误触发断路器计数）
- **被谁依赖**：无（独立于后续需求）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库无断路器机制。连续失败时，`tenacity` 重试仅作用于单个调用，不感知"上游依赖是否已不可用"。

**断路器适用场景**（PRD 限定）：
1. **uiautomation 调用**（控制剪映 App 的 GUI 操作）—— 如果剪映 App 崩溃，重试无意义
2. **FFmpeg 调用**（`media_normalizer.py` 的 Popen 调用）—— 如果 GPU 驱动异常，重试同样无意义
3. **不适用场景**：LLM 调用、文件操作（这些偶尔失败是正常的，不需要断路器）

**PRD 降级**：轻量版断路器。仅用于 uiautomation + FFmpeg。默认关闭，config 中可启用。

### 代码库校验结论

- `process_utils.py` 已有 `_kill_process_tree()` 用于 FFmpeg 进程清理，断路器可复用 PID 追踪
- `retry_config.py` 的指数退避已覆盖单次重试，断路器补充"连续 N 次失败后半开"语义
- 不需要复杂的半开状态机——轻量版：闭→连续N次失败→开→等待M秒→半开（允许1次探测）→闭/开

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `backend/app/agent/skills_agent/circuit_breaker.py` | 断路器核心类 |

### 核心技术细节

```python
"""circuit_breaker.py — 轻量断路器"""
import time
import threading
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"          # 正常通行
    OPEN = "open"              # 熔断，直接拒绝
    HALF_OPEN = "half_open"    # 允许 1 次探测调用


class CircuitBreaker:
    """
    轻量断路器。

    连续 failure_threshold 次失败后进入 OPEN 状态。
    等待 recovery_timeout 秒后进入 HALF_OPEN 状态，允许 1 次探测。
    探测成功 → CLOSED；探测失败 → 回到 OPEN。

    仅用于：
    - uiautomation 调用（控制剪映 App GUI）
    - FFmpeg 调用（GPU/内存资源异常）
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
        enabled: bool = True,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.enabled = enabled

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    logger.info(f"[CB:{self.name}] OPEN → HALF_OPEN（等待 {self.recovery_timeout}s 超时）")
            return self._state

    def call(self, func, *args, **kwargs):
        """
        受断路器保护的函数调用。

        用法：
            cb = CircuitBreaker("ffmpeg", failure_threshold=3, recovery_timeout=60)
            result = cb.call(normalize_webm, video_path, output_path)
        """
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

    def _on_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                logger.info(f"[CB:{self.name}] HALF_OPEN → CLOSED（探测成功）")

    def _on_failure(self, error: Exception) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._failure_count = self.failure_threshold  # 保持在 OPEN
                logger.warning(f"[CB:{self.name}] HALF_OPEN → OPEN（探测失败: {error}）")
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.error(
                    f"[CB:{self.name}] CLOSED → OPEN"
                    f"（连续 {self._failure_count} 次失败，最近错误: {error}）"
                )

    def reset(self) -> None:
        """手动重置断路器（如运维操作后）"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = 0.0
            logger.info(f"[CB:{self.name}] 手动重置为 CLOSED")


class CircuitBreakerOpenError(Exception):
    """断路器熔断时抛出的异常"""
    pass


# 预定义断路器实例
_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """获取或创建断路器实例（按名称单例）"""
    if name not in _breakers:
        from app.config import settings

        # 默认配置可通过 settings 覆盖
        if name == "uiautomation":
            cb = CircuitBreaker(
                name="uiautomation",
                failure_threshold=getattr(settings, "CB_UI_FAILURE_THRESHOLD", 3),
                recovery_timeout=getattr(settings, "CB_UI_RECOVERY_TIMEOUT", 120),
                enabled=getattr(settings, "CB_UI_ENABLED", False),  # 默认关闭
            )
        elif name == "ffmpeg":
            cb = CircuitBreaker(
                name="ffmpeg",
                failure_threshold=getattr(settings, "CB_FFMPEG_FAILURE_THRESHOLD", 3),
                recovery_timeout=getattr(settings, "CB_FFMPEG_RECOVERY_TIMEOUT", 60),
                enabled=getattr(settings, "CB_FFMPEG_ENABLED", False),  # 默认关闭
            )
        else:
            cb = CircuitBreaker(name=name)
        _breakers[name] = cb
    return _breakers[name]
```

### 容错与边界

- 线程安全：使用 `threading.Lock` 保护状态转换
- 默认关闭（`enabled=False`）：不破坏现有重试逻辑，通过 Feature Flag（req_22）控制启用
- OPEN 状态下直接抛 `CircuitBreakerOpenError`（不尝试执行），避免浪费资源
- HALF_OPEN 状态仅允许一次调用（线程安全：通过 lock 保护）
- `reset()` 方法供运维场景（如"已修复 FFmpeg 驱动，手动重置"）
- 断路器状态转换通过 logger 记录，可被 Langfuse（req_17）追踪

## 4. 验收标准 (DoD)

- [ ] 断路器 `enabled=False` 时，`call()` 直接透传（不计数、不熔断）
- [ ] 连续 3 次 `call()` 失败后，断路器状态变为 OPEN
- [ ] OPEN 状态下 `call()` 立即抛 `CircuitBreakerOpenError`（不执行原函数）
- [ ] OPEN 状态等待 recovery_timeout 后变为 HALF_OPEN
- [ ] HALF_OPEN 状态下 1 次成功调用回到 CLOSED
- [ ] HALF_OPEN 状态下 1 次失败调用回到 OPEN
- [ ] `reset()` 强制回到 CLOSED，清空计数
- [ ] `get_circuit_breaker("ffmpeg")` 和 `get_circuit_breaker("uiautomation")` 返回独立实例
