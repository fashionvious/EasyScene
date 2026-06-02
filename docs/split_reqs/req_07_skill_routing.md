# 需求 07：分级技能注入 L0/L1（路由摘要 + 类别详情）

> 原 PRD 编号: P0-6 (C-2) | 优先级: P0

## 1. 依赖关系

- **前置依赖**：req_01（Token 计数模型适配 — 分级注入需要精确的 token 预算计算）
- **被谁依赖**：req_14（上下文 autoCompact — 依赖 token 预算感知的技能注入系统）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库已通过 req_03（旧版需求号，已实现）实现了 token-budget-aware 截断（[jianying_agent.py:137-190](../../../backend/app/agent/skills_agent/jianying_agent.py#L137-L190)）：

```python
def _generate_skills_prompt(self, token_budget: int | None = None) -> str:
    """优先级：main > rule > script > example"""
    category_priority = [
        ("main", "主技能"),
        ("rule", "规则指南"),
        ("script", "CLI 脚本"),
        ("example", "示例代码"),
    ]
    # 当 token 预算不足时，example 类首先被省略
```

同时 `_skills_prompt_cache`（[jianying_agent.py:85](../../../backend/app/agent/skills_agent/jianying_agent.py#L85)）缓存了整个技能提示。

**现状**：

1. 当前是"一级注入"：所有技能摘要 + GUIDE_TEMPLATE 一次性注入 system prompt
2. 对于包含 30+ 个技能的 `jianying-editor-skill` 目录，即使用 token 预算截断，初始注入量仍可能达到 3000-5000 tokens
3. 大部分规则/脚本/示例在具体任务中并不需要，浪费上下文窗口

**PRD 的分级设计**：
- **Level 0 — 路由摘要**（始终注入）：仅技能名称 + 类别标记，约 300 tokens
- **Level 1 — 规则摘要**（按需注入）：当 LLM 意图匹配某类别时注入该类别的 rule 摘要，约 1500 tokens
- **Level 2 — 完整内容**（工具加载）：通过现有的 `load_skill` 工具按需加载

### 代码库校验结论

- `skill_parser.py` 的 `get_skills_by_category(category)` 方法（第 179-181 行）已支持按分类获取技能
- `jianying_agent.py:92-106` 的 `load_skill` 工具已提供 Level 2 加载能力
- 改造重点是拆分 `_generate_skills_prompt()` 为两级：`_route_summary()`（L0）+ `_category_detail(category)`（L1）
- `_skills_prompt_cache` 需要改为缓存路由摘要（L0），L1 在 LLM 意图匹配时动态注入

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `backend/app/agent/skills_agent/jianying_agent.py` | 拆分 L0/L1 注入逻辑 |
| 修改 | `backend/app/agent/skills_agent/skill_parser.py` | 可能需要新增 `get_route_summary()` 方法 |

### 核心技术细节

**Level 0 — 路由摘要**：

```python
# jianying_agent.py — JianYingSkillMiddleware 新增方法

def _build_route_summary(self) -> str:
    """
    L0 — 始终注入的路由摘要（约 300 tokens）。

    仅包含：技能名称 + 一句话描述 + 类别标记。
    让 LLM 知道"有哪些技能可用"，但不加载完整内容。
    """
    lines = ["## 可用技能目录\n"]
    categories = {"main": "主技能", "rule": "规则", "script": "脚本", "example": "示例"}

    for cat, label in categories.items():
        skills = self.parser.get_skills_by_category(cat)
        if not skills:
            continue
        lines.append(f"\n### {label}")
        for s in skills:
            lines.append(f"- `{s['name']}`: {s['description'][:60]}")

    return "\n".join(lines) + "\n\n> 使用 `load_skill(name)` 获取任何技能的完整内容"


def _build_category_detail(self, category: str) -> str:
    """
    L1 — 按需注入的规则摘要（约 1500 tokens）。

    当 LLM 通过 tool_choice 表示意图匹配某类别时，注入该类别的完整 rule。
    """
    skills = self.parser.get_skills_by_category(category)
    if not skills:
        return ""

    lines = [f"\n## {category} 类技能详情\n"]
    for s in skills:
        # 对 rule 类注入完整内容的前 200 行；对 script/example 注入 docstring
        if category == "rule":
            content = s["content"][:2000]  # 约 500-800 tokens
        else:
            content = s["description"]

        lines.append(f"### {s['name']}")
        lines.append(content)
        lines.append("")

    return "\n".join(lines)
```

**拆分 `_build_skills_addendum` 逻辑**：

```python
def _build_skills_addendum(
    self, token_budget: int | None = None, category_hint: str | None = None,
) -> str:
    """
    构建技能附录（L0 + GUIDE_TEMPLATE + 可选的 L1）。

    - L0（路由摘要）始终注入
    - L1（类别详情）仅在 category_hint 非空时注入
    """
    if token_budget is None:
        token_budget = calculate_context_budget()

    # L0 路由摘要（约 300 tokens）
    route_summary = self._build_route_summary()
    guide_tokens = estimate_tokens(GUIDE_TEMPLATE)

    remaining = token_budget - guide_tokens - estimate_tokens(route_summary)

    # L1 类别详情（按需，约 1500 tokens）
    category_detail = ""
    if category_hint and remaining > 1500:
        category_detail = self._build_category_detail(category_hint)

    # 媒体文件列表（动态）
    media_summary = ""
    try:
        available_files = self.media_resolver.list_available()
        if available_files and remaining > 200:
            media_summary = self._build_media_summary(available_files, remaining)
    except Exception:
        pass

    return (
        f"{route_summary}\n\n{category_detail}\n{media_summary}\n{GUIDE_TEMPLATE}"
    )
```

**与 `wrap_model_call` / `awrap_model_call` 的集成**：
- 初始调用时 `category_hint=None`，仅注入 L0（最轻量）
- 当 LLM 调用 `load_skill("rule_media")` 后，后续轮次可以检测意图并注入 L1（但无需额外处理，因为 `load_skill` 已经是 L2 的按需加载）

### 容错与边界

- L0 路由摘要必须 < 500 tokens 以确保低开销（基于 req_01 的精确计数）
- `_build_category_detail` 对 rule 类技能限制内容为前 2000 字符
- `category_hint` 传入未知类别时不应崩溃，静默忽略
- 保持 `_skills_prompt_cache` 兼容：缓存 L0 路由摘要（在 Agent 生命周期中不变）
- GUIDE_TEMPLATE 的 token 计算使用 req_01 的精确方法

## 4. 验收标准 (DoD)

- [ ] L0 路由摘要 token 数 < 500（通过 `estimate_tokens()` 验证）
- [ ] L0 + GUIDE_TEMPLATE + 媒体摘要的组合注入量在 token 预算内
- [ ] `load_skill("rule_media")` 工具仍然正常工作（L2 按需加载）
- [ ] 当 `category_hint="rule"` 时，L1 正确注入 rule 类技能的详细内容
- [ ] 当 `category_hint=None` 时，不注入 L1 内容
- [ ] 现有 Agent 行为（对话、工具调用）在改造后不受影响
- [ ] 上下文 token 占用量相比改造前减少至少 40%（L0 替代全量注入）
