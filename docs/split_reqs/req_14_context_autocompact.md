# 需求 14：上下文压缩 autoCompact（单层）

> 原 PRD 编号: P1-3 (C-3) | 优先级: P1

## 1. 依赖关系

- **前置依赖**：req_01（Token 计数 — 判断何时触发压缩）、req_07（分级技能注入 — 理解哪些内容可被压缩）
- **被谁依赖**：无（独立于后续需求）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库的 token 管理（[jianying_agent.py:222-259](../../../backend/app/agent/skills_agent/jianying_agent.py#L222-L259)）仅在 system prompt 构建阶段做预算控制：

```python
def _build_skills_addendum(self, token_budget=None) -> str:
    guide_tokens = estimate_tokens(GUIDE_TEMPLATE)
    remaining = token_budget - guide_tokens
    skills_prompt = self._skills_prompt_cache
    if skills_tokens > remaining:
        skills_prompt = self._generate_skills_prompt(token_budget=remaining)
```

**问题**：token 预算仅在注入技能时检查，不监控对话运行时的 token 消耗。当用户进行 15+ 轮交互后，对话历史可能膨胀到超出上下文窗口。

**PRD v2.0 降级**：v1.0 提出 3 层压缩（reactive/auto/collapse）。对于剪辑场景（80% 的对话 ≤ 10 轮），**单层 autoCompact 即可**。

**触发策略**：
- 当 `token_usage > 80%` 时自动触发
- 将旧轮次的工具输出替换为摘要（如 `[步骤 3 已完成: 素材导入成功, 用时 12s]`）
- 保留最近 3 轮的完整内容
- 基于现有的 `estimate_tokens()` + `calculate_context_budget()`

### 代码库校验结论

- `jianying_agent.py:308-382` 的 `create_jianying_agent` 函数创建 Agent 使用 `create_agent`，LangChain 的 agent 框架中消息列表由 LangGraph 管理
- autoCompact 需要 hook 到 LangGraph 的消息流中——可在 `JianYingSkillMiddleware.wrap_model_call` 中截获 `request.system_message` + 消息历史
- 或者作为独立的压缩器，在 `_build_skills_addendum` 之后、构建最终 prompt 之前执行
- 现有基础设施足够，不需要新库

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `backend/app/agent/skills_agent/context_compactor.py` | 上下文压缩器 |
| 修改 | `backend/app/agent/skills_agent/jianying_agent.py` | 在 `wrap_model_call` 中集成压缩 |

### 核心技术细节

```python
"""context_compactor.py — 单层 autoCompact"""
import re
from dataclasses import dataclass
from token_utils import estimate_tokens, calculate_context_budget


# 压缩触发阈值（token 使用率）
COMPACT_TRIGGER_RATIO = 0.80  # 80% 时触发
# 保留最近完整轮次数
KEEP_RECENT_ROUNDS = 3


@dataclass
class CompactResult:
    compacted_content: list
    rounds_compacted: int
    tokens_saved: int


def should_compact(messages: list, model_max_tokens: int = 131072) -> bool:
    """
    判断是否需要触发上下文压缩。

    计算所有消息的 token 总数，若超过 model_max_tokens * 80%，返回 True。
    """
    total_text = ""
    for msg in messages:
        if hasattr(msg, "content"):
            total_text += str(msg.content)
        elif isinstance(msg, dict):
            total_text += str(msg.get("content", ""))

    usage = estimate_tokens(total_text)
    budget = calculate_context_budget(model_max_tokens, reserved_ratio=0.20)
    return usage > budget * COMPACT_TRIGGER_RATIO


def compact_messages(messages: list) -> CompactResult:
    """
    压缩消息历史。

    对旧轮次的工具输出进行摘要化处理：
    - 保留最近 KEEP_RECENT_ROUNDS 轮完整内容（用户消息 + AI 回复 + 工具调用结果）
    - 超过 KEEP_RECENT_ROUNDS 的轮次的工具输出替换为单行摘要
    - 用户消息始终保留（压缩的是 AI 侧的工具输出）

    压缩前后 token 数对比记录到 logger。
    """
    if len(messages) <= KEEP_RECENT_ROUNDS * 2:
        return CompactResult(
            compacted_content=list(messages),
            rounds_compacted=0,
            tokens_saved=0,
        )

    # 按轮次分组（每轮：user message + AI message with tool calls）
    rounds = _group_into_rounds(messages)
    if len(rounds) <= KEEP_RECENT_ROUNDS:
        return CompactResult(
            compacted_content=list(messages),
            rounds_compacted=0,
            tokens_saved=0,
        )

    total_before = estimate_tokens(_flatten_messages(messages))
    compacted_messages: list = []

    # 旧轮次 → 压缩
    old_rounds = rounds[:-KEEP_RECENT_ROUNDS]
    recent_rounds = rounds[-KEEP_RECENT_ROUNDS:]
    compacted_count = 0

    for round_msgs in old_rounds:
        for msg in round_msgs:
            if _is_tool_result(msg):
                # 替换为单行摘要
                summary = _summarize_tool_result(msg)
                compacted_messages.append(_make_summary_message(summary, msg))
                compacted_count += 1
            else:
                compacted_messages.append(msg)

    # 最近 N 轮 → 保持完整
    for round_msgs in recent_rounds:
        compacted_messages.extend(round_msgs)

    total_after = estimate_tokens(_flatten_messages(compacted_messages))
    tokens_saved = max(0, total_before - total_after)

    return CompactResult(
        compacted_content=compacted_messages,
        rounds_compacted=compacted_count,
        tokens_saved=tokens_saved,
    )


def _summarize_tool_result(msg) -> str:
    """将工具输出压缩为单行摘要"""
    content = str(msg.content) if hasattr(msg, "content") else str(msg)

    # 提取关键信息：成功/失败、耗时、输出关键词
    if "成功" in content or "success" in content.lower():
        status = "成功"
    elif "失败" in content or "error" in content.lower():
        status = "失败"
    else:
        status = "完成"

    # 截断到 80 字符
    snippet = content[:80].replace("\n", " ")
    return f"[工具调用{status}: {snippet}...]"


def _is_tool_result(msg) -> bool:
    """判断消息是否为工具调用结果"""
    msg_type = getattr(msg, "type", "")
    if msg_type == "tool":
        return True
    # LangChain ToolMessage 的 type 可能是 "tool"
    if hasattr(msg, "tool_call_id"):
        return True
    return False


def _group_into_rounds(messages: list) -> list[list]:
    """将消息按 user/assistant 交替分轮次"""
    rounds: list[list] = []
    current_round: list = []

    for msg in messages:
        role = getattr(msg, "role", "") or getattr(msg, "type", "")
        if role in ("user", "human") and current_round:
            rounds.append(current_round)
            current_round = []
        current_round.append(msg)

    if current_round:
        rounds.append(current_round)

    return rounds


def _flatten_messages(messages: list) -> str:
    return "".join(
        str(getattr(m, "content", "")) for m in messages
    )


def _make_summary_message(text: str, original_msg) -> dict:
    """构造压缩后的摘要消息"""
    if hasattr(original_msg, "model_dump"):
        data = original_msg.model_dump()
        data["content"] = text
        return type(original_msg)(**data)
    return {"role": "tool", "content": text}
```

**集成到 jianying_agent.py**：

```python
# JianYingSkillMiddleware.wrap_model_call 中集成

from context_compactor import should_compact, compact_messages

def wrap_model_call(self, request, handler):
    # 检查是否需要压缩
    if hasattr(request, "messages") and should_compact(request.messages):
        result = compact_messages(request.messages)
        logger.info(
            f"autoCompact: 压缩了 {result.rounds_compacted} 个工具输出，"
            f"节省约 {result.tokens_saved} tokens"
        )
        modified_request = request.override(messages=result.compacted_content)
        # 继续注入技能附录
        skills_addendum = self._build_skills_addendum()
        # ...
        return handler(modified_request)

    # 原有逻辑不变
    skills_addendum = self._build_skills_addendum()
    modified_request = request.override(system_message=new_system_message)
    return handler(modified_request)
```

### 容错与边界

- `should_compact()` 返回 True 但实际不需压缩时，`compact_messages()` 不修改消息（返回原列表 + `rounds_compacted=0`）
- 压缩仅针对工具输出（ToolMessage），用户消息和 AI 文本回复始终保留
- `_summarize_tool_result()` 截断到 80 字符，确保压缩后的摘要远小于原始输出
- 压缩失败（如消息格式异常）时静默降级：返回原消息列表，不抛异常
- 压缩事件记录到 logger（供 Langfuse 追踪）
- 不修改 `model_dump` 后的原始消息（通过 copy 创建新消息对象）

## 4. 验收标准 (DoD)

- [ ] `should_compact(messages)` 对 15 轮对话返回 `True`（token 使用率 > 80%）
- [ ] `should_compact(messages)` 对 3 轮对话返回 `False`
- [ ] `compact_messages(messages)` 返回的 `compacted_content` 中最近 3 轮完整保留
- [ ] 旧轮次的工具输出（ToolMessage）被替换为单行摘要（`[工具调用成功: ...]`）
- [ ] 压缩后 token 数存在可量化的减少（`tokens_saved > 0`）
- [ ] 用户消息在压缩后完全保留
- [ ] 压缩失败时静默降级（不抛异常，返回原消息列表）
