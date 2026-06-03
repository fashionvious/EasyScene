"""
上下文压缩器（单层 autoCompact）。

当对话 token 使用率超过 80% 时自动触发：
- 保留最近 3 轮完整内容
- 旧轮次的工具输出替换为单行摘要
- 用户消息始终完整保留
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .token_utils import estimate_tokens, calculate_context_budget

logger = logging.getLogger(__name__)

COMPACT_TRIGGER_RATIO = 0.80
KEEP_RECENT_ROUNDS = 3


@dataclass
class CompactResult:
    compacted_content: list
    rounds_compacted: int
    tokens_saved: int


def should_compact(messages: list, model_max_tokens: int = 131072) -> bool:
    """判断是否需要触发上下文压缩（token 使用率 > 80%）。"""
    total_text = ""
    for msg in messages:
        content = getattr(msg, "content", None)
        if content is not None:
            total_text += str(content)
        elif isinstance(msg, dict):
            total_text += str(msg.get("content", ""))

    usage = estimate_tokens(total_text)
    budget = calculate_context_budget(model_max_tokens, reserved_ratio=0.20)
    return usage > budget * COMPACT_TRIGGER_RATIO


def compact_messages(messages: list) -> CompactResult:
    """压缩消息历史中的工具输出（保留最近 KEEP_RECENT_ROUNDS 轮完整）。"""
    try:
        return _compact_impl(messages)
    except Exception:
        logger.warning("autoCompact 失败，返回原始消息", exc_info=True)
        return CompactResult(
            compacted_content=list(messages),
            rounds_compacted=0,
            tokens_saved=0,
        )


def _compact_impl(messages: list) -> CompactResult:
    if len(messages) <= KEEP_RECENT_ROUNDS * 2:
        return CompactResult(list(messages), 0, 0)

    rounds = _group_into_rounds(messages)
    if len(rounds) <= KEEP_RECENT_ROUNDS:
        return CompactResult(list(messages), 0, 0)

    total_before = estimate_tokens(_flatten_messages(messages))
    compacted: list = []

    old_rounds = rounds[:-KEEP_RECENT_ROUNDS]
    recent_rounds = rounds[-KEEP_RECENT_ROUNDS:]
    compacted_count = 0

    for round_msgs in old_rounds:
        for msg in round_msgs:
            if _is_tool_result(msg):
                summary = _summarize_tool_result(msg)
                compacted.append(_make_summary_message(summary, msg))
                compacted_count += 1
            else:
                compacted.append(msg)

    for round_msgs in recent_rounds:
        compacted.extend(round_msgs)

    total_after = estimate_tokens(_flatten_messages(compacted))
    tokens_saved = max(0, total_before - total_after)

    logger.info(
        "autoCompact: 压缩 %s 个工具输出, 节省约 %s tokens",
        compacted_count, tokens_saved,
    )

    return CompactResult(compacted, compacted_count, tokens_saved)


# ---- helpers ----

def _summarize_tool_result(msg) -> str:
    content = str(getattr(msg, "content", msg)) if not isinstance(msg, dict) else str(msg.get("content", ""))

    if "成功" in content or "success" in content.lower():
        status = "成功"
    elif "失败" in content or "error" in content.lower():
        status = "失败"
    else:
        status = "完成"

    snippet = content[:80].replace("\n", " ")
    return f"[工具调用{status}: {snippet}...]"


def _is_tool_result(msg) -> bool:
    if getattr(msg, "tool_call_id", None) is not None:
        return True
    msg_type = getattr(msg, "type", "")
    if msg_type == "tool":
        return True
    return False


def _group_into_rounds(messages: list) -> list[list]:
    rounds: list[list] = []
    current: list = []

    for msg in messages:
        role = getattr(msg, "role", "") or getattr(msg, "type", "")
        if role in ("user", "human") and current:
            rounds.append(current)
            current = []
        current.append(msg)

    if current:
        rounds.append(current)

    return rounds


def _flatten_messages(messages: list) -> str:
    return "".join(str(getattr(m, "content", "")) for m in messages)


def _make_summary_message(text: str, original_msg):
    if hasattr(original_msg, "model_dump"):
        try:
            data = original_msg.model_dump()
            data["content"] = text
            return type(original_msg)(**data)
        except Exception:
            pass
    return {"role": "tool", "content": text}
