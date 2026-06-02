"""
声明式工具注册表 — 统一管理 Agent 工具的元信息。

为 StepOrchestrator（req_03）提供 exec_mode 分类，为并发编排（req_10）
提供 concurrency_safe 标记，为错误处理（req_11）提供 retry_strategy 映射。
"""
from dataclasses import dataclass
from typing import Callable, Literal

ToolCategory = Literal["read", "write", "compute"]
ExecMode = Literal["sync", "async"]
RetryStrategy = Literal["none", "transient", "recoverable"]


@dataclass(frozen=True)
class ToolSpec:
    """工具的声明式元数据。frozen=True 防止运行时意外修改。"""

    name: str
    handler: str                      # 执行器名称（如 "media_resolver", "cli_executor"）
    category: ToolCategory            # read | write | compute
    func: Callable                    # LangChain @tool 函数引用
    exec_mode: ExecMode = "sync"      # sync → Agent 直接调用，async → Celery Task
    concurrency_safe: bool = True     # 是否可安全并发执行（read 类通常为 True）
    timeout: int = 30                 # 默认超时（秒）
    max_retries: int = 0              # 默认重试次数
    retry_strategy: RetryStrategy = "none"  # none | transient | recoverable
    description: str = ""


class ToolRegistry:
    """声明式工具注册表，不替代 LangChain @tool 装饰器，仅作为元数据管理层。"""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._tools: list[Callable] = []

    # ---- 注册 ----

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec
        self._tools.append(spec.func)

    def register_many(self, specs: list[ToolSpec]) -> None:
        for s in specs:
            self.register(s)

    # ---- 查询 ----

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def get_all_tools(self) -> list[Callable]:
        """返回 LangChain @tool 函数列表，可直接传给 create_agent(tools=...)。"""
        return list(self._tools)

    def get_tools_by_category(self, category: ToolCategory) -> list[ToolSpec]:
        return [s for s in self._specs.values() if s.category == category]

    def get_tools_by_exec_mode(self, mode: ExecMode) -> list[ToolSpec]:
        return [s for s in self._specs.values() if s.exec_mode == mode]

    def get_concurrency_safe_tools(self) -> list[ToolSpec]:
        return [s for s in self._specs.values() if s.concurrency_safe]

    def list_names(self) -> list[str]:
        return list(self._specs.keys())

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def __repr__(self) -> str:
        return f"ToolRegistry({len(self._specs)} tools: {self.list_names()})"
