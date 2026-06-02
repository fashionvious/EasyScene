"""
Tests for req_11 — ErrorRetryConfig unified error-retry mapping.

Focus: get_strategy, should_retry, classify_retry_action,
DEFAULT_STRATEGY fallback, and STRATEGY_TO_RETRY_COUNT mapping.
All system exceptions tested directly; JyError subclasses tested
when the import path resolves.
"""
import subprocess
import os, sys

import pytest


# Ensure jianying-editor-skill is on path for JyError imports
_skill_scripts = os.path.join(
    os.path.dirname(__file__), "..", "jianying-editor-skill",
)
sys.path.insert(0, os.path.abspath(_skill_scripts))


# ---------------------------------------------------------------------------
# ErrorRetryConfig — system exceptions
# ---------------------------------------------------------------------------

class TestErrorRetryConfigSystemErrors:
    """系统级异常的映射（不依赖 jianying-editor-skill 的 JyError）"""

    def test_timeout_expired_should_retry(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig

        exc = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30)
        assert ErrorRetryConfig.should_retry(exc) is True

    def test_timeout_error_should_retry(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig

        assert ErrorRetryConfig.should_retry(TimeoutError()) is True

    def test_connection_error_should_retry(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig

        assert ErrorRetryConfig.should_retry(ConnectionError()) is True

    def test_os_error_should_retry(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig

        assert ErrorRetryConfig.should_retry(OSError()) is True

    def test_value_error_uses_default_strategy(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig

        strategy = ErrorRetryConfig.get_strategy(ValueError("unknown"))
        assert strategy["max_retries"] == 1  # DEFAULT_STRATEGY
        assert strategy["action"].value == "retry"

    def test_default_strategy_retries_once(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig

        assert ErrorRetryConfig.should_retry(ValueError()) is True


# ---------------------------------------------------------------------------
# ErrorRetryConfig — JyError hierarchy (if importable)
# ---------------------------------------------------------------------------

class TestErrorRetryConfigJyErrors:
    """JyError 子类的映射"""

    @pytest.fixture(autouse=True)
    def _reload_rules(self):
        """Force rules reload after module import (lazy initialization)."""
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig
        ErrorRetryConfig.RULES = []

    def test_user_input_error_should_not_retry(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig
        from scripts.utils.errors import UserInputError

        assert ErrorRetryConfig.should_retry(UserInputError("bad")) is False

    def test_infra_error_should_retry(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig
        from scripts.utils.errors import InfraError

        assert ErrorRetryConfig.should_retry(InfraError("down")) is True

    def test_data_error_should_not_retry(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig
        from scripts.utils.errors import DataError

        assert ErrorRetryConfig.should_retry(DataError("bad json")) is False

    def test_user_input_error_classify(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig, RetryAction
        from scripts.utils.errors import UserInputError

        action = ErrorRetryConfig.classify_retry_action(UserInputError("bad"))
        assert action == RetryAction.NOTIFY_USER

    def test_data_error_classify(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig, RetryAction
        from scripts.utils.errors import DataError

        action = ErrorRetryConfig.classify_retry_action(DataError("bad json"))
        assert action == RetryAction.NOTIFY_USER

    def test_infra_error_classify(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig, RetryAction
        from scripts.utils.errors import InfraError

        action = ErrorRetryConfig.classify_retry_action(InfraError("redis down"))
        assert action == RetryAction.RETRY

    def test_get_strategy_infra_has_three_retries(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig
        from scripts.utils.errors import InfraError

        strategy = ErrorRetryConfig.get_strategy(InfraError("redis down"))
        assert strategy["max_retries"] == 3
        assert strategy["action"].value == "retry"

    def test_jyerror_base_class_is_default(self):
        from app.agent.skills_agent.error_retry_map import ErrorRetryConfig
        from scripts.utils.errors import JyError

        # JyError base class is NOT in the rules → falls to DEFAULT (1 retry)
        strategy = ErrorRetryConfig.get_strategy(JyError("generic"))
        assert strategy["max_retries"] == 1


# ---------------------------------------------------------------------------
# RetryAction enum
# ---------------------------------------------------------------------------

class TestRetryAction:
    def test_enum_values(self):
        from app.agent.skills_agent.error_retry_map import RetryAction

        assert RetryAction.RETRY == "retry"
        assert RetryAction.NOTIFY_USER == "notify"
        assert RetryAction.FATAL == "fatal"


# ---------------------------------------------------------------------------
# STRATEGY_TO_RETRY_COUNT
# ---------------------------------------------------------------------------

class TestStrategyToRetryCount:
    def test_mapping(self):
        from app.agent.skills_agent.error_retry_map import STRATEGY_TO_RETRY_COUNT

        assert STRATEGY_TO_RETRY_COUNT["none"] == 0
        assert STRATEGY_TO_RETRY_COUNT["transient"] == 2
        assert STRATEGY_TO_RETRY_COUNT["recoverable"] == 3


# ---------------------------------------------------------------------------
# StepOrchestrator _classify_error_action (delegates to ErrorRetryConfig)
# ---------------------------------------------------------------------------

class TestOrchestratorErrorAction:
    """_classify_error_action 委托到 ErrorRetryConfig"""

    def test_user_input_error_returns_notify_user(self):
        from app.agent.skills_agent.step_orchestrator import StepOrchestrator
        from scripts.utils.errors import UserInputError

        result = StepOrchestrator._classify_error_action(UserInputError("bad"))
        assert result == "notify"

    def test_value_error_returns_retry(self):
        from app.agent.skills_agent.step_orchestrator import StepOrchestrator

        # Unknown exception → DEFAULT_STRATEGY → RETRY
        result = StepOrchestrator._classify_error_action(ValueError("generic"))
        assert result == "retry"
