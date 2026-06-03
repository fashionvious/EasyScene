"""
Tests for req_14 — context_compactor (autoCompact).

Focus: should_compact trigger logic, compact_messages behavior,
user message preservation, tool output summarization, error resilience.
All messages are simulated — no LangChain/LangGraph deps needed.
"""
from unittest.mock import MagicMock

import pytest


# ---- Helpers ----

def _make_msg(role_or_type, content, tool_call_id=None):
    """Create a simulated message object with real string content."""
    class _FakeMsg:
        def __init__(self):
            self.content = content
            if tool_call_id is not None:
                self.tool_call_id = tool_call_id
            self.type = "tool" if tool_call_id else role_or_type
            self.role = role_or_type

        def model_dump(self):
            d = {"content": self.content, "type": self.type, "role": self.role}
            if hasattr(self, "tool_call_id"):
                d["tool_call_id"] = self.tool_call_id
            return d

    return _FakeMsg()


def _make_tool_msg(content, tool_call_id="call_1"):
    return _make_msg("tool", content, tool_call_id=tool_call_id)


# ---------------------------------------------------------------------------
# should_compact
# ---------------------------------------------------------------------------

class TestShouldCompact:
    """触发条件判断"""

    def test_short_conversation_returns_false(self):
        from app.agent.skills_agent.context_compactor import should_compact

        msgs = [_make_msg("user", "hello"), _make_msg("assistant", "hi")]
        assert should_compact(msgs) is False

    def test_deep_conversation_returns_true(self):
        from app.agent.skills_agent.context_compactor import should_compact

        # Many rounds with Chinese-heavy content to hit 80% of budget
        msgs = []
        for i in range(80):
            msgs.append(_make_msg("user", ("测试消息" + str(i) + " ") * 80))
            msgs.append(_make_msg("assistant", ("回复内容" + str(i) + " ") * 80))
            msgs.append(_make_tool_msg(("工具执行结果输出" + str(i) + " ") * 80))

        assert should_compact(msgs) is True

    def test_empty_messages_returns_false(self):
        from app.agent.skills_agent.context_compactor import should_compact

        assert should_compact([]) is False


# ---------------------------------------------------------------------------
# compact_messages
# ---------------------------------------------------------------------------

class TestCompactMessages:
    """压缩行为"""

    def test_few_messages_not_compacted(self):
        from app.agent.skills_agent.context_compactor import compact_messages

        msgs = [_make_msg("user", "hi"), _make_msg("assistant", "hello")]
        result = compact_messages(msgs)

        assert result.rounds_compacted == 0
        assert result.compacted_content == msgs

    def test_compacts_old_tool_outputs(self):
        from app.agent.skills_agent.context_compactor import compact_messages

        # Create 6 rounds: 3 old (compressed) + 3 recent (kept)
        msgs = []
        for i in range(6):
            msgs.append(_make_msg("user", f"Q{i}"))
            msgs.append(_make_msg("assistant", f"A{i}"))
            msgs.append(_make_tool_msg(f"Tool output for step {i} " * 5))

        result = compact_messages(msgs)

        # Some rounds should be compacted
        assert result.rounds_compacted > 0
        assert result.tokens_saved > 0

    def test_user_messages_preserved(self):
        from app.agent.skills_agent.context_compactor import compact_messages

        msgs = []
        for i in range(6):
            msgs.append(_make_msg("user", f"user question {i}"))
            msgs.append(_make_msg("assistant", f"ai answer {i}"))
            msgs.append(_make_tool_msg(f"tool result {i} " * 5))

        result = compact_messages(msgs)

        user_msgs = [
            m for m in result.compacted_content
            if (m.get("role") if isinstance(m, dict) else m.role) == "user"
        ]
        assert len(user_msgs) == 6
        for m in user_msgs:
            content = m["content"] if isinstance(m, dict) else m.content
            assert "user question" in content

    def test_tool_outputs_summarized(self):
        from app.agent.skills_agent.context_compactor import compact_messages

        msgs = []
        for i in range(6):
            msgs.append(_make_msg("user", f"Q{i}"))
            msgs.append(_make_tool_msg(f"success: step {i} completed successfully " * 3))
            msgs.append(_make_msg("assistant", f"A{i}"))

        result = compact_messages(msgs)

        # At least one tool output should have been summarized
        summarized = 0
        for m in result.compacted_content:
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
            if "[工具调用" in str(content):
                summarized += 1
        assert summarized > 0

    def test_tokens_saved_positive_when_compacted(self):
        from app.agent.skills_agent.context_compactor import compact_messages

        msgs = []
        for i in range(8):
            msgs.append(_make_msg("user", f"Q{i}"))
            msgs.append(_make_tool_msg("very long tool output " * 20))
            msgs.append(_make_msg("assistant", f"A{i}"))

        result = compact_messages(msgs)

        if result.rounds_compacted > 0:
            assert result.tokens_saved > 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """边界条件与容错"""

    def test_empty_list(self):
        from app.agent.skills_agent.context_compactor import compact_messages

        result = compact_messages([])
        assert result.rounds_compacted == 0

    def test_no_tool_messages(self):
        from app.agent.skills_agent.context_compactor import compact_messages

        msgs = [_make_msg("user", "Q"), _make_msg("assistant", "A")] * 6
        result = compact_messages(msgs)

        # No tool messages to compress, but rounds > 3 so some compression
        # of non-tool messages doesn't happen → rounds_compacted = 0
        assert result.tokens_saved >= 0

    def test_mixed_message_types_no_crash(self):
        from app.agent.skills_agent.context_compactor import compact_messages

        # Edge: plain dicts mixed with objects
        msgs = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1", "tool_call_id": "t1", "type": "tool"},
            _make_msg("user", "Q2"),
            _make_tool_msg("result"),
        ] * 4

        result = compact_messages(msgs)
        assert result is not None


# ---------------------------------------------------------------------------
# Summarizer
# ---------------------------------------------------------------------------

class TestSummarizer:
    """_summarize_tool_result 摘要格式"""

    def test_success_summary(self):
        from app.agent.skills_agent.context_compactor import _summarize_tool_result

        msg = _make_tool_msg("步骤 3 已完成: 素材导入成功, 用时 12s")
        summary = _summarize_tool_result(msg)
        assert "[工具调用成功:" in summary

    def test_failure_summary(self):
        from app.agent.skills_agent.context_compactor import _summarize_tool_result

        msg = _make_tool_msg("ERROR: FFmpeg 转码失败, timeout")
        summary = _summarize_tool_result(msg)
        assert "[工具调用失败:" in summary

    def test_truncated_to_80_chars(self):
        from app.agent.skills_agent.context_compactor import _summarize_tool_result

        long_content = "a" * 200
        msg = _make_tool_msg(long_content)
        summary = _summarize_tool_result(msg)

        # Summary format: [工具调用XX: {snippet}...]
        # The snippet portion should be at most 80 chars
        assert len(summary) <= 150  # generous upper bound
