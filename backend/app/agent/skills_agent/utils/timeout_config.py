"""
统一超时配置

为所有 subprocess.run 调用提供统一的超时控制和输出截断工具。
此文件与 jianying-editor-skill/scripts/utils/timeout_config.py 保持同步。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SubprocessConfig:
    default_timeout: int = 120          # 默认 2 分钟（适用于 FFmpeg 转码等）
    max_timeout: int = 600              # 最大 10 分钟
    output_truncate_bytes: int = 10240  # 10KB 截断阈值
    head_keep_bytes: int = 2048         # 截断时保留头部字节数
    tail_keep_bytes: int = 2048         # 截断时保留尾部字节数


def truncate_output(text: str, max_bytes: int = 10240) -> str:
    """截断输出，保留首尾各 2KB，中间用标记省略

    注意：此处 max_bytes 按 UTF-8 编码后的字节数计算，
    而非 Python str 的字符数。对于纯 ASCII 文本两者一致，
    对于含中文的文本，len(text.encode('utf-8')) 才是真实字节数。
    """
    raw_bytes = text.encode("utf-8")
    if len(raw_bytes) <= max_bytes:
        return text

    head_size = SubprocessConfig.head_keep_bytes
    tail_size = SubprocessConfig.tail_keep_bytes
    omitted = len(raw_bytes) - head_size - tail_size

    head_text = raw_bytes[:head_size].decode("utf-8", errors="replace")
    tail_text = raw_bytes[-tail_size:].decode("utf-8", errors="replace")

    return (
        head_text
        + f"\n\n...[截断 {omitted} 字节]...\n\n"
        + tail_text
    )
