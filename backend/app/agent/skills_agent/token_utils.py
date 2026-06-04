"""
Token 估算工具函数

用于上下文预算管理。主路径通过 dashscope Qwen tokenizer 精确计数，
fallback 基于 CJK/非CJK 字符混合估算（经 qwen-plus tokenizer 实测校准）。
"""

import unicodedata

_CJK_CHARS_PER_TOKEN = 2.0
_ASCII_CHARS_PER_TOKEN = 5.5
_CODE_CHARS_PER_TOKEN = 3.5


def _is_cjk(char: str) -> bool:
    return unicodedata.east_asian_width(char) in ("W", "F")


def _is_ascii(char: str) -> bool:
    return " " <= char <= "~"


def _count_tokens_fallback(text: str) -> int:
    cjk_count = 0
    ascii_count = 0
    other_count = 0

    for c in text:
        if _is_cjk(c):
            cjk_count += 1
        elif _is_ascii(c):
            ascii_count += 1
        else:
            other_count += 1

    estimated = (
        cjk_count / _CJK_CHARS_PER_TOKEN
        + ascii_count / _ASCII_CHARS_PER_TOKEN
        + other_count / _CODE_CHARS_PER_TOKEN
    )

    return max(1, int(estimated))


def estimate_tokens(text: str) -> int:
    if not text:
        return 0

    try:
        from dashscope.tokenizers import get_tokenizer

        tokenizer = get_tokenizer("qwen-plus")
        result = tokenizer.encode(text)
        if result:
            return len(result)
    except Exception:
        pass

    return _count_tokens_fallback(text)


def calculate_context_budget(
    model_max_tokens: int = 131072,
    reserved_ratio: float = 0.2,
) -> int:
    return int(model_max_tokens * (1 - reserved_ratio))
