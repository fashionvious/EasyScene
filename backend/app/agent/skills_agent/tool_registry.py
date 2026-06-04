"""
Tool Registry — 声明式工具注册表，支持按类别/执行模式/并发安全等纬度查询。

Registration is declarative: each tool declares its metadata (handler, category,
exec_mode, concurrency safety, timeout, retry strategy) once, and the registry
derives query views from that metadata.

Used by:
- jianying_agent._create_tools   → populates the registry
- step_orchestrator             → resolves action→tool mapping
- orchestrator_factory          → wires Agent middleware into job runtime
- error_retry_map               → maps retry_strategy → retry count
- videoagent.py                 → sets global singleton
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# ToolSpec — frozen dataclass (hashable, immutable)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    """声明式工具规格，注册后不可变。

    Fields:
        name:            工具名称（全局唯一 ID）
        handler:         处理器模块名（如 "media_resolver", "timeline_ops"）
        category:        类别标记: "read" | "write" | "compute"
        func:            LangChain @tool 装饰后的 callable
        exec_mode:       执行模式: "sync" | "async"
        concurrency_safe: 是否支持并发调用
        timeout:         超时时间（秒）
        max_retries:     最大重试次数
        retry_strategy:  重试策略: "none" | "transient" | "recoverable"
        description:     工具描述文本
    """
    name: str
    handler: str
    category: str
    func: Callable
    exec_mode: str = "sync"
    concurrency_safe: bool = True
    timeout: int = 30
    max_retries: int = 0
    retry_strategy: str = "none"
    description: str = ""


# ---------------------------------------------------------------------------
# ToolRegistry — ordered, name-unique, query-able
# ---------------------------------------------------------------------------

class ToolRegistry:
    """声明式工具注册表。

    - 同名覆盖：register("dup", ...) 第二次调用会覆盖第一次的 spec，
      但两个 func 都保留在工具列表中（避免丢失可调用引用）。
    - 查询视图：按 category / exec_mode / concurrency_safe 筛选。
    """

    def __init__(self) -> None:
        self._specs: OrderedDict[str, ToolSpec] = OrderedDict()
        self._tools: list[Callable] = []

    # -- registration --------------------------------------------------------

    def register(self, spec: ToolSpec) -> "ToolRegistry":
        """注册单个工具（同名 spec 覆盖，两个 func 均保留）。"""
        self._specs[spec.name] = spec
        self._tools.append(spec.func)
        return self

    def register_many(self, specs: list[ToolSpec]) -> "ToolRegistry":
        """批量注册，返回 self 以支持链式调用。"""
        for spec in specs:
            self.register(spec)
        return self

    # -- retrieval -----------------------------------------------------------

    def get_all_tools(self) -> list[Callable]:
        """返回所有已注册工具的 callable 列表（新 list，非内部引用）。"""
        return list(self._tools)

    def get_spec(self, name: str) -> ToolSpec | None:
        """按名称获取工具规格，不存在则返回 None。"""
        return self._specs.get(name)

    def get_tools_by_category(self, category: str) -> list[ToolSpec]:
        """按类别筛选（"read" | "write" | "compute"）。"""
        return [s for s in self._specs.values() if s.category == category]

    def get_tools_by_exec_mode(self, exec_mode: str) -> list[ToolSpec]:
        """按执行模式筛选（"sync" | "async"）。"""
        return [s for s in self._specs.values() if s.exec_mode == exec_mode]

    def get_concurrency_safe_tools(self) -> list[ToolSpec]:
        """返回所有 concurrency_safe=True 的工具。"""
        return [s for s in self._specs.values() if s.concurrency_safe]

    def list_names(self) -> list[str]:
        """返回所有工具名称列表（注册顺序）。"""
        return list(self._specs.keys())

    # -- dunder --------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def __repr__(self) -> str:
        names = ", ".join(self._specs.keys())
        names_display = names if len(names) < 120 else names[:117] + "..."
        return f"<ToolRegistry: {len(self)} tools [{names_display}]>"
