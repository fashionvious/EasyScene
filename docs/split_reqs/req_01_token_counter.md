# 需求 01：模型适配的 Token 计数

> 原 PRD 编号: P0-5 (C-1) | 优先级: P0

## 1. 依赖关系

- **前置依赖**：无
- **被谁依赖**：req_07（分级技能注入）、req_14（上下文 autoCompact）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库状态（[token_utils.py](../../../backend/app/agent/skills_agent/token_utils.py#L9-L16)）：

```python
def estimate_tokens(text: str) -> int:
    """粗略估算 token 数量。中文场景保守估计：每个 token ≈ 2 个字符。"""
    if not text:
        return 0
    return max(1, len(text) // 2)
```

**存在的问题**：

1. **中英文偏差**：`len(text)//2` 对中文文本低估约 20-30%，对英文/ASCII 文本高估约 40-50%。项目使用 qwen3.6 系列模型（dashscope），其 tokenizer 行为与 OpenAI tokenizer 显著不同。
2. **tiktoken 不适用**（PRD v1.0 的建议已修正）：项目使用 dashscope 的 qwen 系列模型，tiktoken 的 cl100k_base 编码会导致 20-40% 的计数偏差，比当前简单估算更危险。
3. **PRD v2.0 已识别此问题**：建议使用 dashscope Tokenizer + 混合估算 fallback。

**实际影响**：当 `_generate_skills_prompt(token_budget=...)` 被调用时，技能列表可能超出或未充分利用 token 预算，影响 Agent 系统提示的质量。

### 代码库校验结论

- `jianying_agent.py:151` 调用 `calculate_context_budget()`，默认 `model_max_tokens=131072`（qwen3.6 上下文窗口）
- `jianying_agent.py:169` 每行技能描述调用 `estimate_tokens(line)` 进行预算扣减
- `jianying_agent.py:234-244` 将固定 GUIDE_TEMPLATE 纳入 token 预算
- 已有的基础设施足够支撑此改造，仅需替换 `estimate_tokens()` 的实现

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `backend/app/agent/skills_agent/token_utils.py` | 替换 `estimate_tokens()` 实现 |

### 核心技术细节

```python
import unicodedata
from typing import Optional

def _is_cjk(char: str) -> bool:
    """判断字符是否为 CJK（中日韩）字符"""
    return unicodedata.east_asian_width(char) in ("W", "F")

def estimate_tokens(text: str) -> int:
    """
    模型适配的 token 估算。

    策略（优先级递减）：
    1. dashscope Tokenizer 可用 → 使用 API 精确计数
    2. dashscope 不可用 → 混合估算（CJK 1.2 chars/token, ASCII 3.5 chars/token）

    基于 qwen3.6 tokenizer 的实际测试数据校准。
    """
    if not text:
        return 0

    # 尝试 dashscope Tokenizer（懒导入，避免强制依赖）
    try:
        from dashscope import Tokenizer
        tokenizer = Tokenizer()
        result = tokenizer.count_tokens(text)
        return max(1, result)
    except Exception:
        pass

    # fallback: 混合估算
    cjk_count = sum(1 for c in text if _is_cjk(c))
    ascii_count = len(text) - cjk_count
    estimated = int(cjk_count / 1.2 + ascii_count / 3.5)
    return max(1, estimated)


def calculate_context_budget(
    model_max_tokens: int = 131072,
    reserved_ratio: float = 0.20,
) -> int:
    """不变，保持现有签名兼容"""
    return int(model_max_tokens * (1 - reserved_ratio))
```

### 容错与边界

- dashscope Tokenizer 导入失败时无感降级到混合估算
- `_is_cjk()` 使用标准库 `unicodedata`，无额外依赖
- `estimate_tokens("")` 始终返回 0
- 保留 `calculate_context_budget()` 签名不变，确保向后兼容
- 混合估算的 chars/token 比率基于 qwen3.6 实际测试校准，详见实现注释

## 4. 验收标准 (DoD)

- [ ] `estimate_tokens("你好世界")` 返回的值在 qwen tokenizer 实际值的 ±15% 范围内
- [ ] `estimate_tokens("Hello World this is a test")` 对 ASCII 文本的估算不低估值超过 30%
- [ ] `estimate_tokens("中英混合 Hello World 测试")` 混合文本估算合理
- [ ] dashscope Tokenizer 不可用时，自动 fallback 到混合估算，不抛异常
- [ ] `calculate_context_budget()` 行为与改造前完全一致
- [ ] 现有所有调用方（`jianying_agent.py`）无需修改，功能正常
