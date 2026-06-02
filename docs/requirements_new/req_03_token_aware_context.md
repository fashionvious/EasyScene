# 需求 03：实现 Token 感知的上下文注入机制

## 1. 需求背景与目标

当前 `JianYingSkillMiddleware` 在每次模型调用时将所有技能描述和媒体文件列表完整注入系统消息。随着技能数量增长（rules + scripts + examples 可能达到数十个），单次注入的 token 量可能超过 10K，在长对话场景下容易触发模型上下文窗口限制。

**核心目标**：在注入技能描述和媒体文件列表前进行 token 预算估算，当总量超过模型上下文窗口的 80% 时触发渐进式截断，优先保留核心技能。

## 2. 现有代码分析

### 问题代码位置

[jianying_agent.py](backend/app/agent/skills_agent/jianying_agent.py#L107-L126) 中的 `_generate_skills_prompt()` 和 `wrap_model_call()` / `awrap_model_call()`：

```python
# 第 107-126 行：_generate_skills_prompt() 按分类遍历所有技能，全部注入
def _generate_skills_prompt(self) -> None:
    skills_list = []
    categories = {
        "main": "主技能",
        "rule": "规则指南",
        "script": "CLI 脚本",
        "example": "示例代码"
    }
    for category, label in categories.items():
        category_skills = self.parser.get_skills_by_category(category)
        if category_skills:
            skills_list.append(f"\n### {label}")
            for skill in category_skills:
                skills_list.append(f"- **{skill['name']}**: {skill['description']}")
    self.skills_prompt = "\n".join(skills_list)

# 第 140-146 行：媒体文件列表固定取前 10 个
for f in available_files[:10]:
    media_lines.append(f"- {f['name']} ({f['type']}, {f['size_mb']}MB)")
```

**具体问题**：
1. 技能描述无上限：`self.skills` 可能包含几十个 skill，每个 description 约 50-200 字符
2. 媒体文件列表硬编码 `[:10]`：当 token 预算紧张时 10 个也可能太多
3. 每次 `wrap_model_call` 触发都重新构建完整的 `skills_addendum`，没有缓存
4. `wrap_model_call` 和 `awrap_model_call` 存在大量重复代码（第 128-188 行 vs 第 190-252 行），媒体摘要构建逻辑完全相同

### 审查发现

1. **tiktoken 不适用于当前模型**：原始需求文档建议引入 `tiktoken` 库进行 token 计数，但 `tiktoken` 是 OpenAI 专有 tokenizer，与当前项目使用的 qwen 系列模型（通过 DashScope）不兼容。替代方案：
   - **方案 A（推荐）**：使用字符数估算（中文 ≈ 1.5 字符/token，英文 ≈ 4 字符/token），取保守估计 `len(text) / 2` 作为 token 估算
   - **方案 B**：检查 `dashscope` SDK 是否提供 token 计数 API
   - **方案 C**：引入 `transformers` 的通用 tokenizer（过重，不推荐）
   
   推荐方案 A，因为上下文预算管理只需要近似估算，无需精确到个位数 token。

2. **`_generate_skills_prompt()` 当前签名不返回值**：现有代码中该方法将结果赋给 `self.skills_prompt`（无返回值），但原始文档的改造方案改为返回 `str`。需要同时更新 `__init__` 中的调用方式（第 55 行）。

3. **`skills_addendum` 中包含大量固定文本**：第 151-178 行的"工具使用指南"和"工作流程"部分约 1500+ 字符，这些固定文本也应纳入 token 预算计算，但原始文档仅关注了技能列表和媒体文件。

4. **`wrap_model_call` 和 `awrap_model_call` 重复**：两个方法的 `skills_addendum` 构建逻辑完全相同（约 60 行重复代码），应抽取为共享方法。

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

> **补充说明**：`reserved_ratio` 应考虑对话历史的动态增长。在长对话中，历史消息会持续占用上下文窗口，20% 可能不够。建议增加 `conversation_tokens` 参数，从 LangGraph checkpoint 中获取当前对话历史的 token 占用，动态调整预算：
>
> ```python
> def calculate_context_budget(
>     model_max_tokens: int = 131072,
>     conversation_tokens: int = 0,  # 当前对话历史已占用 token
>     response_reserve: int = 4096,  # 预留给模型响应
> ) -> int:
>     return max(0, model_max_tokens - conversation_tokens - response_reserve)
> ```
>
> 但这需要 LangGraph 提供 token 计数接口，可作为后续优化。初始实现仍使用固定比例。

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
                skill_lines.append(f"- ... 还有 {len(cat_skills) - len(skill_lines)} 个技能（使用 load_skill 查看）\n")
                break
            skill_lines.append(line)
            cat_used += line_tokens

        if skill_lines:
            parts.append(cat_text + "".join(skill_lines))
            used += cat_used + estimate_tokens(cat_text)

    return "\n".join(parts)
```

> **修正说明**：原始文档的截断逻辑存在 bug——`used` 变量在 `for skill in cat_skills` 循环中未累加每个技能的 `line_tokens`，导致预算检查始终基于初始值，截断永远不会触发。修正版在 `skill_lines.append(line)` 后增加了 `cat_used += line_tokens`，同时将 `used` 的更新移到分类循环末尾。

### 3.3 改造媒体文件列表注入

```python
def _build_media_summary(self, available_files, token_budget_remaining):
    """基于剩余 token 预算动态调整媒体文件列表数量"""
    if not available_files:
        return ""

    header = "\n### 可用媒体文件\n"
    used = estimate_tokens(header)

    # 每个文件行约 50 tokens（含文件名、类型、大小）
    per_file_tokens = 50
    max_show = min(len(available_files), max(1, (token_budget_remaining - used) // per_file_tokens))

    media_lines = []
    for f in available_files[:max_show]:
        line = f"- {f['name']} ({f['type']}, {f['size_mb']}MB)\n"
        media_lines.append(line)

    if len(available_files) > max_show:
        media_lines.append(
            f"- ... 还有 {len(available_files) - max_show} 个文件（使用 list_media 查看）\n"
        )

    return header + "".join(media_lines)
```

> **修正说明**：原始文档的 `max_show` 计算为 `token_budget_remaining // 50`，未扣除 header 本身占用的 token，可能导致实际超出预算。修正版先扣除 `used = estimate_tokens(header)`，再计算可用文件数。

### 3.4 将固定文本纳入预算并抽取共享方法

`skills_addendum` 中的"工具使用指南"和"工作流程"是固定文本，约 1500+ 字符（≈750 tokens），应纳入预算计算：

```python
# 固定文本模板（工具使用指南 + 工作流程），在 __init__ 中生成一次
GUIDE_TEMPLATE = """
## 工具使用指南

1. **resolve_media**: ...
2. **list_media**: ...
...

## 工作流程

1. **当用户提到视频/音频/图片文件时，先用 `resolve_media` 解析文件名获取完整路径**
...
"""

def _build_skills_addendum(self, token_budget: int | None = None) -> str:
    """构建完整的技能附录（替代 wrap_model_call 中的内联逻辑）"""
    if token_budget is None:
        token_budget = calculate_context_budget()

    # 固定文本占用
    guide_tokens = estimate_tokens(GUIDE_TEMPLATE)
    remaining = token_budget - guide_tokens

    # 技能列表（从缓存或动态生成）
    skills_prompt = self._skills_prompt_cache  # 缓存值
    skills_tokens = estimate_tokens(skills_prompt)

    # 如果技能列表超出剩余预算，重新生成截断版
    if skills_tokens > remaining:
        skills_prompt = self._generate_skills_prompt(token_budget=remaining)
        remaining -= estimate_tokens(skills_prompt)
    else:
        remaining -= skills_tokens

    # 媒体文件列表（动态，每次调用都重新生成）
    media_summary = ""
    try:
        available_files = self.media_resolver.list_available()
        if available_files:
            media_summary = self._build_media_summary(available_files, remaining)
    except Exception:
        pass

    return f"## 可用技能\n\n{skills_prompt}\n\n{media_summary}\n{GUIDE_TEMPLATE}"
```

> **修正说明**：原始文档未将固定文本纳入预算计算，可能导致实际注入量超出预期。同时，将 `wrap_model_call` 和 `awrap_model_call` 中重复的 `skills_addendum` 构建逻辑抽取为 `_build_skills_addendum()` 方法，消除约 60 行重复代码。

### 3.5 缓存技能提示

因为技能列表不会在 Agent 生命周期中变化，`skills_prompt` 可在 `__init__` 时生成一次并缓存，`wrap_model_call` 直接使用缓存值而非每次重新构建。媒体文件摘要则仍需每次动态生成（文件可能增删）。

```python
def __init__(self, skill_root, media_search_paths=None):
    # ... 现有初始化逻辑 ...
    self._skills_prompt_cache = self._generate_skills_prompt()  # 缓存
```

### 3.6 改造 wrap_model_call / awrap_model_call

```python
def wrap_model_call(self, request, handler):
    skills_addendum = self._build_skills_addendum()
    new_content = list(request.system_message.content_blocks) + [
        {"type": "text", "text": skills_addendum}
    ]
    new_system_message = SystemMessage(content=new_content)
    modified_request = request.override(system_message=new_system_message)
    return handler(modified_request)

async def awrap_model_call(self, request, handler):
    skills_addendum = self._build_skills_addendum()  # 共享同一构建逻辑
    new_content = list(request.system_message.content_blocks) + [
        {"type": "text", "text": skills_addendum}
    ]
    new_system_message = SystemMessage(content=new_content)
    modified_request = request.override(system_message=new_system_message)
    return await handler(modified_request)
```

## 4. 验收标准

1. **Token 预算感知**：技能注入前进行 token 估算，当总预算超过模型上下文 80% 时触发截断，example 类技能优先被省略。
2. **动态媒体列表**：媒体文件显示数量不再硬编码为 10，而是根据当前 token 预算动态计算（每个文件行约 50 tokens 估算）。
3. **技能提示缓存**：`skills_prompt` 在 `__init__` 中生成并缓存，避免每次 `wrap_model_call` 重复构建。
4. **固定文本纳入预算**："工具使用指南"和"工作流程"的 token 占用纳入预算计算，避免实际注入量超出预期。
5. **消除重复代码**：`wrap_model_call` 和 `awrap_model_call` 的 `skills_addendum` 构建逻辑抽取为共享方法 `_build_skills_addendum()`。
6. **截断逻辑正确性**：`used` 变量在每个技能行追加后正确累加，确保预算检查在长技能列表下能正确触发截断。

## 5. 原始文档问题汇总

| # | 问题 | 严重程度 | 修正措施 |
|---|------|----------|----------|
| 1 | `_generate_skills_prompt()` 截断逻辑中 `used` 未逐行累加，导致截断永远不触发 | 高 | 在 `skill_lines.append(line)` 后增加 `cat_used += line_tokens` |
| 2 | `_generate_skills_prompt()` 改为返回 `str`，但未说明如何更新 `__init__` 中的调用（原方法无返回值） | 中 | 补充 `__init__` 调用方式变更说明 |
| 3 | 未将"工具使用指南"和"工作流程"固定文本（≈750 tokens）纳入预算计算 | 高 | 在 `_build_skills_addendum()` 中先扣除固定文本占用 |
| 4 | `_build_media_summary()` 的 `max_show` 未扣除 header 占用的 token | 中 | 先扣除 `estimate_tokens(header)` 再计算可用文件数 |
| 5 | `wrap_model_call` 和 `awrap_model_call` 存在约 60 行重复代码，未提出重构方案 | 中 | 抽取为 `_build_skills_addendum()` 共享方法 |
| 6 | `reserved_ratio` 为固定值，未考虑长对话中历史消息动态增长 | 中 | 补充 `conversation_tokens` 动态预算方案说明 |
| 7 | 验收标准未覆盖截断逻辑的正确性验证 | 中 | 补充"截断在长列表下正确触发"的验收条目 |
