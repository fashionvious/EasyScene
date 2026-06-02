# 需求 02：声明式工具注册表 (ToolRegistry)

> 原 PRD 编号: P0-4 (B-1) | 优先级: P0

## 1. 依赖关系

- **前置依赖**：无（基于现有 7 个工具，无需新基础设施）
- **被谁依赖**：req_03（StepOrchestrator，需要 ToolSpec.exec_mode 字段）、req_10（素材导入并发，需要 concurrency_safe 标记）、req_11（错误分类与重试，需要 retry_strategy 字段）、req_17（CLI 脚本自动发现）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库状态（[jianying_agent.py:87-135](../../../backend/app/agent/skills_agent/jianying_agent.py#L87-L135)）：

```python
# 工具注册为静态 @tool 装饰器 + 硬编码 tools = [...] 列表
self.tools = [
    self.load_skill_tool,
    self.resolve_media_tool,
    self.list_media_tool,
    self.execute_cli_tool,
    self.list_cli_tool,
    self.execute_python_tool,
    self.validate_python_tool
]
```

**存在的问题**：

1. 工具元信息（超时、重试策略、并发安全性）分散在 `cli_executor.py`、`python_executor.py`、`retry_config.py` 中，无统一视图
2. `exec_mode`（sync/async 步骤分发）是 P0-7 的前置条件，当前无此标记
3. 新增工具需修改多处代码（tool 函数、tools 列表、重试配置）
4. 现有工具 7 个，够用但缺乏统一的元数据描述

### 代码库校验结论

- PRD 提到的 `retry_strategy` 映射当前以临时字典形式存在于 PRD 正文，实际代码中不存在，需要本需求创建
- `cli_executor.py:215-373` 的 `execute()` 方法已支持 `timeout` 和 `max_retries` 参数，为 ToolRegistry 集成提供了良好基础
- `python_executor.py:163-335` 同样已支持 `timeout` 和 `max_retries`
- 7 个工具均为 LangChain `@tool` 装饰器创建，ToolRegistry 需要与 LangChain tool 类型兼容

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `backend/app/agent/skills_agent/tool_registry.py` | ToolSpec 数据类 + ToolRegistry 类 |
| 修改 | `backend/app/agent/skills_agent/jianying_agent.py` | 使用 ToolRegistry 替代硬编码 tools 列表 |

### 核心技术细节

```python
"""tool_registry.py — 声明式工具注册表"""
from dataclasses import dataclass, field
from typing import Literal, Callable, Any

ToolCategory = Literal["read", "write", "compute"]
ExecMode = Literal["sync", "async"]
RetryStrategy = Literal["none", "transient", "recoverable"]


@dataclass(frozen=True)
class ToolSpec:
    """工具的声明式元数据"""
    name: str
    handler: str                     # 执行器名称（如 "media_resolver", "cli_executor"）
    category: ToolCategory           # read | write | compute
    func: Callable                   # LangChain @tool 函数引用
    exec_mode: ExecMode = "sync"     # sync → Agent 直接调用, async → Celery Task
    concurrency_safe: bool = True    # 是否可以并发执行（read 类通常为 True）
    timeout: int = 30                # 默认超时（秒）
    max_retries: int = 0             # 默认重试次数
    retry_strategy: RetryStrategy = "none"  # none | transient | recoverable
    description: str = ""


class ToolRegistry:
    """声明式工具注册表，统一管理所有 Agent 工具的元信息"""

    def __init__(self):
        self._specs: dict[str, ToolSpec] = {}
        self._tools: list[Callable] = []

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec
        self._tools.append(spec.func)

    def register_many(self, specs: list[ToolSpec]) -> None:
        for s in specs:
            self.register(s)

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def get_all_tools(self) -> list[Callable]:
        return list(self._tools)

    def get_tools_by_category(self, category: ToolCategory) -> list[ToolSpec]:
        return [s for s in self._specs.values() if s.category == category]

    def get_tools_by_exec_mode(self, mode: ExecMode) -> list[ToolSpec]:
        return [s for s in self._specs.values() if s.exec_mode == mode]

    def get_concurrency_safe_tools(self) -> list[ToolSpec]:
        return [s for s in self._specs.values() if s.concurrency_safe]

    def __len__(self) -> int:
        return len(self._specs)


# 预定义的 7 工具注册表（基于实际代码库校验）
def create_default_registry(
    load_skill_fn, resolve_media_fn, list_media_fn,
    execute_cli_fn, list_cli_fn, execute_py_fn, validate_py_fn,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many([
        ToolSpec(
            name="load_skill", handler="skill_parser",
            category="read", func=load_skill_fn,
            exec_mode="sync", concurrency_safe=True, timeout=10,
        ),
        ToolSpec(
            name="resolve_media", handler="media_resolver",
            category="read", func=resolve_media_fn,
            exec_mode="sync", concurrency_safe=True, timeout=30,
            retry_strategy="transient", max_retries=2,
        ),
        ToolSpec(
            name="list_media", handler="media_resolver",
            category="read", func=list_media_fn,
            exec_mode="sync", concurrency_safe=True, timeout=30,
        ),
        ToolSpec(
            name="execute_cli_script", handler="cli_executor",
            category="compute", func=execute_cli_fn,
            exec_mode="async", concurrency_safe=False, timeout=600,
            retry_strategy="recoverable", max_retries=2,
        ),
        ToolSpec(
            name="list_cli_scripts", handler="cli_executor",
            category="read", func=list_cli_fn,
            exec_mode="sync", concurrency_safe=True, timeout=10,
        ),
        ToolSpec(
            name="execute_jyproject_code", handler="python_executor",
            category="write", func=execute_py_fn,
            exec_mode="sync", concurrency_safe=False, timeout=300,
            retry_strategy="transient", max_retries=1,
        ),
        ToolSpec(
            name="validate_jyproject_code", handler="python_executor",
            category="read", func=validate_py_fn,
            exec_mode="sync", concurrency_safe=True, timeout=10,
        ),
    ])
    return registry
```

### 容错与边界

- `ToolSpec` 使用 `frozen=True` 防止运行时意外修改
- `exec_mode` 的 `"async"` 标记仅声明意图，实际 Celery 分发由 StepOrchestrator (req_03) 实现
- ToolRegistry 不替代 LangChain 的 `@tool` 装饰器，仅作为元数据管理层
- 向后兼容：`jianying_agent.py` 中的 `self.tools` 列表可通过 `registry.get_all_tools()` 生成

## 4. 验收标准 (DoD)

- [ ] `ToolRegistry` 正确注册 7 个现有工具，元数据完整
- [ ] `get_tools_by_exec_mode("sync")` 返回 5 个同步工具
- [ ] `get_tools_by_exec_mode("async")` 返回 `execute_cli_script`（1 个标记为 async）
- [ ] `get_concurrency_safe_tools()` 返回 5 个工具（load_skill, resolve_media, list_media, list_cli_scripts, validate_jyproject_code）
- [ ] 现有 `jianying_agent.py` 使用 `registry.get_all_tools()` 后 Agent 行为不变
- [ ] `ToolSpec` 实例创建后不可变（frozen=True）
