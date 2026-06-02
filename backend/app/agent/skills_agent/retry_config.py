"""统一重试配置 —— 基于项目已有的 tenacity 库。

向后兼容：ErrorRetryConfig（error_retry_map.py）统一了 errors.py 和
retry_config.py 的错误分类体系。新代码推荐使用 ErrorRetryConfig.should_retry()。
"""
import subprocess
import logging
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

DEFAULT_RETRY_CONFIG = {
    "max_attempts": 3,
    "min_wait": 1,       # 首次等待 1 秒
    "max_wait": 30,      # 最大等待 30 秒
}

# 原有可重试异常元组 — 保留向后兼容。
# 新代码推荐 error_retry_map.ErrorRetryConfig.should_retry(exc)
RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    OSError,
    subprocess.TimeoutExpired,
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
