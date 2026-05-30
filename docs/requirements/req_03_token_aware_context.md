# 需求 03：实现 Token 感知的上下文注入机制

## 1. 需求背景与目标

当前 `JianYingSkillMiddleware` 在每次模型调用时将所有技能描述和媒体文件列表完整注入系统消息。随着技能数量增长（rules + scripts + examples 可能达到数十个），单次注入的 token 量可能超过 10K，在长对话场景下容易触发模型上下文窗口限制。

**核心目标**：在注入技能描述和媒体文件列表前进行 token 预算估算，当总量超过模型上下文窗口的 80% 时触发渐进式截断，优先保留核心技能。

## 2. 现有代码分析

### 问题代码位置

[jianying_agent.py](backend/app/agent/skills_agent/jianying_agent.py#L128-L178) 中的 `wrap_model_call()` 和 `awrap_model_call()`：

```python
# 第 122-126 行：按分类遍历所有技能，全部注入
for category, label in categories.items():
    category_skills = self.parser.get_skills_by_category(category)
    if category_skills:
        skills_list.append(f"\n### {label}")
        for skill in category_skills:
            skills_list.append(f"- **{skill['name']}**: {skill['description']}")

# 第 140-146 行：媒体文件列表固定取前 10 个
for f in available_files[:10]:
    media_lines.append(f"- {f['name']} ({f['type']}, {f['size_mb']}MB)")
```

**具体问题**：
1. 技能描述无上限：`self.skills` 可能包含几十个 skill，每个 description 约 50-200 字符
2. 媒体文件列表硬编码 `[:10]`：当 token 预算紧张时 10 个也可能太多
3. 每次 `wrap_model_call` 触发都重新构建完整的 `skills_addendum`，没有缓存

### 审查发现：tiktoken 不适用于当前模型

原始需求文档建议引入 `tiktoken` 库进行 token 计数，但 `tiktoken` 是 OpenAI 专有 tokenizer，与当前项目使用的 qwen 系列模型（通过 DashScope）不兼容。替代方案：

- **方案 A（推荐）**：使用字符数估算（中文 ≈ 1.5 字符/token，英文 ≈ 4 字符/token），取保守估计 `len(text) / 2` 作为 token 估算
- **方案 B**：检查 `dashscope` SDK 是否提供 token 计数 API
- **方案 C**：引入 `transformers` 的通用 tokenizer（过重，不推荐）

推荐方案 A，因为上下文预算管理只需要近似估算，无需精确到个位数 token。

## 3. 技术实现方案

### 3.1 Token 估算工具函数

```python
def estimate_tokens(text: str) -> int:
    """
    粗略估算 token 数量。
    中文场景保守估计：每个 token ≈ 2 个字符。
    """
    if not text:
        return 0
    return max(1, len(text) // 2)


def calculate_context_budget(
    model_max_tokens: int = 131072,  # qwen3.6 上下文窗口
    reserved_ratio: float = 0.20,     # 保留 20% 给对话历史和响应
) -> int:
    """
    计算可用于技能注入的 token 预算。
    默认预留 20%（约 26K tokens）给对话历史和模型响应。
    """
    return int(model_max_tokens * (1 - reserved_ratio))
```

### 3.2 改造 `_generate_skills_prompt()` 方法

```python
def _generate_skills_prompt(self, token_budget: int | None = None) -> str:
    """
    生成技能列表提示，支持 token 预算控制。

    优先级：main > rule > script > example
    每类技能在预算内尽量保留，超出预算时 example 类首先被省略。
    """
    if token_budget is None:
        token_budget = calculate_context_budget()

    parts = []
    used = 0

    category_priority = [
        ("main", "主技能"),
        ("rule", "规则指南"),
        ("script", "CLI 脚本"),
        ("example", "示例代码"),
    ]

    for category, label in category_priority:
        cat_skills = self.parser.get_skills_by_category(category)
        if not cat_skills:
            continue

        cat_text = f"\n### {label}\n"
        cat_used = estimate_tokens(cat_text)

        skill_lines = []
        for skill in cat_skills:
            line = f"- **{skill['name']}**: {skill['description']}\n"
            line_tokens = estimate_tokens(line)
            if used + cat_used + line_tokens > token_budget:
                # 当前技能放不下，标记省略
                skill_lines.append(f"- ... 还有 {len(cat_skills) - len(skill_lines)} 个技能（使用 load_skill 查看）\n")
                break
            skill_lines.append(line)
            cat_used += line_tokens

        if skill_lines:
            parts.append(cat_text + "".join(skill_lines))
            used += cat_used + estimate_tokens(cat_text)

    return "\n".join(parts)
```

### 3.3 改造媒体文件列表注入

```python
def _build_media_summary(self, available_files, token_budget_remaining):
    """基于剩余 token 预算动态调整媒体文件列表数量"""
    if not available_files:
        return ""

    header = "\n### 可用媒体文件\n"
    used = estimate_tokens(header)

    media_lines = []
    max_show = min(len(available_files), max(1, token_budget_remaining // 50))
    # 每个文件行约 50 tokens

    for f in available_files[:max_show]:
        line = f"- {f['name']} ({f['type']}, {f['size_mb']}MB)\n"
        media_lines.append(line)

    if len(available_files) > max_show:
        media_lines.append(
            f"- ... 还有 {len(available_files) - max_show} 个文件（使用 list_media 查看）\n"
        )

    return header + "".join(media_lines)
```

### 3.4 缓存技能提示

因为技能列表不会在 Agent 生命周期中变化，`skills_prompt` 可在 `__init__` 时生成一次并缓存，`wrap_model_call` 直接使用缓存值而非每次重新构建。媒体文件摘要则仍需每次动态生成（文件可能增删）。

## 4. 验收标准

1. **Token 预算感知**：技能注入前进行 token 估算，当总预算超过模型上下文 80% 时触发截断，example 类技能优先被省略。
2. **动态媒体列表**：媒体文件显示数量不再硬编码为 10，而是根据当前 token 预算动态计算（每个文件行约 50 tokens 估算）。
3. **技能提示缓存**：`skills_prompt` 在 `__init__` 中生成并缓存，避免每次 `wrap_model_call` 重复构建。
