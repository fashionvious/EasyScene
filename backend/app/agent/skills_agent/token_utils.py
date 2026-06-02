"""
Token 估算工具函数

用于上下文预算管理。主路径通过 dashscope Qwen tokenizer 精确计数，
fallback 基于 CJK/非CJK 字符混合估算（经 qwen-plus tokenizer 实测校准）。
"""
import unicodedata


# ---- fallback 常量（基于 qwen-plus tokenizer 实测校准，2026-06） ----
_CJK_CHARS_PER_TOKEN = 2.0      # 纯中文 ~2.0 chars/token
_ASCII_CHARS_PER_TOKEN = 5.5    # 纯英文 ~5.5 chars/token
_CODE_CHARS_PER_TOKEN = 3.5     # 代码/数字/符号混合 ~3.5 chars/token


def _is_cjk(char: str) -> bool:
    """判断字符是否为 CJK（中日韩）宽字符"""
    return unicodedata.east_asian_width(char) in ("W", "F")


def _is_ascii(char: str) -> bool:
    """判断字符是否为 ASCII 可打印字符"""
    return "\x20" <= char <= "\x7e"


def _count_tokens_fallback(text: str) -> int:
    """
    基于字符类型的混合估算（dashscope 不可用时的 fallback）。

    对三类字符分别使用不同的 chars/token 比率：
    - CJK 字符: 2.0 chars/token
    - ASCII 字符 (a-z, A-Z, 0-9, 标点): 5.5 chars/token（英文单词 tokenizer 效率高）
    - 其余字符 (空格、数字、符号、控制字符等): 3.5 chars/token（代码/混合场景居中）
    """
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
    """
    估算文本的 token 数量。

    主路径：dashscope Qwen tokenizer 精确计数。
    fallback：基于 CJK/ASCII/other 字符混合估算（经 qwen-plus 实测校准）。

    与 len(text)//2 相比：
    - 中文：准确度持平（都是 ~2 chars/token）
    - 英文：修正了 2.3x 的高估（5.5 vs 2 chars/token）
    """
    if not text:
        return 0

    # 主路径：dashscope Tokenizer（懒导入，避免强制依赖）
    try:
        from dashscope.tokenizers import get_tokenizer
        tokenizer = get_tokenizer("qwen-plus")
        result = tokenizer.encode(text)
        if result:
            return len(result)
    except Exception:
        pass

    # fallback
    return _count_tokens_fallback(text)


def calculate_context_budget(
    model_max_tokens: int = 131072,  # qwen3.6 上下文窗口
    reserved_ratio: float = 0.20,     # 保留 20% 给对话历史和响应
) -> int:
    """
    计算可用于技能注入的 token 预算。
    默认预留 20%（约 26K tokens）给对话历史和模型响应。
    """
    return int(model_max_tokens * (1 - reserved_ratio))
