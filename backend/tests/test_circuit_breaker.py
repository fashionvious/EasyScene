"""
Tests for req_13 — CircuitBreaker.

Coverage: CLOSED/OPEN/HALF_OPEN state machine, disabled mode,
thread safety, reset, singleton factory, CircuitBreakerOpenError.
All tests self-contained — no external deps needed.
"""
import time
import pytest

from app.agent.skills_agent.circuit_breaker import (
    CircuitBreaker, CircuitBreakerOpenError, CircuitState,
    get_circuit_breaker,
)


# ---------------------------------------------------------------------------
# Disabled mode
# ---------------------------------------------------------------------------

class TestDisabledMode:
    """enabled=False → 直接透传，不计数、不熔断"""

    def test_call_passes_through_when_disabled(self):
        cb = CircuitBreaker("test", enabled=False)

        result = cb.call(lambda x: x * 2, 21)

        assert result == 42
        assert cb.state == CircuitState.CLOSED

    def test_failures_dont_count_when_disabled(self):
        cb = CircuitBreaker("test", failure_threshold=2, enabled=False)

        for _ in range(5):
            try:
                cb.call(lambda: (_ for _ in ()).throw(ValueError("boom")))
            except ValueError:
                pass

        assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# State machine: CLOSED → OPEN
# ---------------------------------------------------------------------------

class TestClosedToOpen:
    """连续 failure_threshold 次失败 → OPEN"""

    def test_three_failures_open_circuit(self):
        cb = CircuitBreaker("test", failure_threshold=3, enabled=True)

        for i in range(3):
            with pytest.raises(ValueError):
                cb.call(lambda: (_ for _ in ()).throw(ValueError(f"fail {i}")))

        assert cb.state == CircuitState.OPEN

    def test_one_success_resets_count(self):
        """成功调用后计数器清零"""
        cb = CircuitBreaker("test", failure_threshold=3, enabled=True)

        cb.call(lambda: "ok1")
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("fail1")))
        cb.call(lambda: "ok2")  # resets counter
        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("fail2")))

        # Only 2 consecutive failures (counter reset by ok2)
        assert cb.state == CircuitState.CLOSED

    def test_two_failures_dont_open_when_threshold_is_three(self):
        cb = CircuitBreaker("test", failure_threshold=3, enabled=True)

        for _ in range(2):
            with pytest.raises(ValueError):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# OPEN state behavior
# ---------------------------------------------------------------------------

class TestOpenState:
    """OPEN 状态下直接拒绝"""

    def test_open_refuses_immediately(self):
        cb = CircuitBreaker("test", failure_threshold=1, enabled=True)

        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        assert cb.state == CircuitState.OPEN

        with pytest.raises(CircuitBreakerOpenError):
            cb.call(lambda: "should not be called")


# ---------------------------------------------------------------------------
# HALF_OPEN → CLOSED / OPEN
# ---------------------------------------------------------------------------

class TestHalfOpen:
    """HALF_OPEN 探测逻辑"""

    def test_half_open_success_closes(self):
        import os

        # Use a very short recovery timeout
        cb = CircuitBreaker("test", failure_threshold=1,
                           recovery_timeout=0.01, enabled=True)

        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        assert cb.state == CircuitState.OPEN
        time.sleep(0.02)  # Wait for recovery timeout

        # State transition: OPEN → HALF_OPEN (on state property read)
        assert cb.state == CircuitState.HALF_OPEN

        # Successful probe → CLOSED
        result = cb.call(lambda x: x, "success")
        assert result == "success"
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker("test", failure_threshold=1,
                           recovery_timeout=0.01, enabled=True)

        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("fail1")))

        time.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN

        with pytest.raises(ValueError):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("fail2")))

        assert cb.state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

class TestReset:
    """手动重置"""

    def test_reset_to_closed_clears_count(self):
        cb = CircuitBreaker("test", failure_threshold=2, enabled=True)

        for _ in range(2):
            with pytest.raises(ValueError):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        assert cb.state == CircuitState.OPEN

        cb.reset()

        assert cb.state == CircuitState.CLOSED
        # Immediate success after reset
        assert cb.call(lambda: "recovered") == "recovered"


# ---------------------------------------------------------------------------
# CircuitBreakerOpenError
# ---------------------------------------------------------------------------

class TestOpenError:
    def test_is_exception(self):
        with pytest.raises(CircuitBreakerOpenError):
            raise CircuitBreakerOpenError("breaker open")

    def test_message_contains_details(self):
        err = CircuitBreakerOpenError("[CB:ffmpeg] 断路器已熔断（连续 3 次失败）")
        assert "ffmpeg" in str(err)
        assert "熔断" in str(err)


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

class TestGetCircuitBreaker:
    """get_circuit_breaker 单例工厂"""

    def test_returns_same_instance_for_same_name(self):
        cb1 = get_circuit_breaker("ffmpeg")
        cb2 = get_circuit_breaker("ffmpeg")

        assert cb1 is cb2

    def test_different_names_return_different_instances(self):
        cb_ui = get_circuit_breaker("uiautomation")
        cb_ff = get_circuit_breaker("ffmpeg")

        assert cb_ui is not cb_ff
        assert cb_ui.name == "uiautomation"
        assert cb_ff.name == "ffmpeg"

    def test_default_disabled(self):
        """默认 enabled=False（不破坏现有行为）"""
        import os
        # Clear env vars that might enable it
        saved = {}
        for key in ("CB_FFMPEG_ENABLED", "CB_UI_ENABLED"):
            saved[key] = os.environ.pop(key, None)

        try:
            # Force a new instance by clearing the singleton
            from app.agent.skills_agent.circuit_breaker import _breakers
            _breakers.pop("ffmpeg", None)
            cb = get_circuit_breaker("ffmpeg")
            assert cb.enabled is False
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_env_var_enables_breaker(self):
        import os
        os.environ["CB_FFMPEG_ENABLED"] = "true"

        from app.agent.skills_agent.circuit_breaker import _breakers
        _breakers.pop("ffmpeg", None)

        cb = get_circuit_breaker("ffmpeg")
        assert cb.enabled is True

        del os.environ["CB_FFMPEG_ENABLED"]
