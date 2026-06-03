"""Langfuse 可观测性集成

提供 LLM 调用和工具链的 trace/span 记录能力。
langfuse-langchain CallbackHandler 自动覆盖 LangChain 管理的调用，
手动 span API 覆盖 FFmpeg/TTS 等外部 subprocess 调用。
"""
import os
import logging
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

logger = logging.getLogger(__name__)

_langfuse_client: Langfuse | None = None


def get_langfuse_client() -> Langfuse | None:
    """
    获取全局 Langfuse 客户端（懒初始化）。

    Returns:
        Langfuse 客户端实例，若环境变量未配置则返回 None。
    """
    global _langfuse_client
    if _langfuse_client is None:
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        if not secret_key or not public_key:
            logger.info(
                "Langfuse 未配置（缺少 LANGFUSE_SECRET_KEY 或 "
                "LANGFUSE_PUBLIC_KEY），跳过初始化"
            )
            return None
        _langfuse_client = Langfuse(
            secret_key=secret_key,
            public_key=public_key,
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
    return _langfuse_client


def create_langchain_callback(
    trace_name: str = "jianying_agent",
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> CallbackHandler | None:
    """
    创建 LangChain 自动埋点回调处理器。

    Returns:
        CallbackHandler 实例，若 Langfuse 未配置则返回 None。
    """
    client = get_langfuse_client()
    if client is None:
        return None
    return CallbackHandler(
        client=client,
        trace_name=trace_name,
        user_id=user_id,
        session_id=session_id,
        tags=tags or [],
    )


def create_manual_span(trace_id: str, name: str, input_data: dict) -> str | None:
    """
    为 LangChain 无法自动覆盖的外部调用（FFmpeg, TTS）创建手动 span。

    Returns:
        span_id 供后续 update_span 使用，若 Langfuse 未配置则返回 None。
    """
    client = get_langfuse_client()
    if client is None:
        return None
    span = client.span(
        trace_id=trace_id,
        name=name,
        input=input_data,
    )
    return span.id


def end_manual_span(
    span_id: str,
    output_data: dict | None = None,
    level: str | None = None,
    status_message: str | None = None,
) -> None:
    """
    结束手动 span，提供输出更新 + 异常安全的 end()。

    Langfuse v3 中 span() 返回 Span 对象，直接调用 .end() 即可。
    """
    client = get_langfuse_client()
    if client is None:
        return
    try:
        span = client.span(id=span_id)
        if output_data:
            span.update(output=output_data)
        if level:
            span.update(level=level)
        if status_message:
            span.update(status_message=status_message)
        span.end()
    except Exception as e:
        logger.warning("end_manual_span failed: %s", e)


def get_current_trace_id() -> str | None:
    """
    尝试从当前 Langfuse 上下文中获取 trace_id。

    优先从 Langfuse SDK 的内置 context 中提取。
    若不可用（版本差异/未初始化），返回 None。
    """
    try:
        import langfuse
        return langfuse.get_current_trace_id()  # type: ignore[attr-defined]
    except (ImportError, AttributeError, Exception):
        return None
