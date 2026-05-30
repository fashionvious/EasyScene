# 需求 05：集成 Langfuse 可观测性埋点

## 1. 需求背景与目标

当前 Agent 系统没有任何可观测性基础设施——无法追踪每次 Agent 调用的 token 消耗、工具调用链、执行耗时和成功率。当系统在线上出现异常时，只能靠用户反馈或事后翻日志定位问题。

**核心目标**：使用 Langfuse Python SDK v3+ 为 Agent 和工具调用建立完整的 trace/span 体系，实现端到端可观测。

## 2. 现有代码分析

### 当前状态

- [pyproject.toml](backend/pyproject.toml) 中 **未包含 `langfuse` 依赖**
- 现有代码中无任何埋点逻辑
- Agent 使用 `langchain.agents.create_agent` + `InMemorySaver`（无回调集成）

### 审查发现

#### 发现 1：langfuse 依赖缺失

原始需求文档中的示例代码使用 `Langfuse()` 客户端 API（v2+ 风格），但未提及需要先添加依赖。当前最新稳定版为 `langfuse>=3.0`，API 与 v2 有差异。

#### 发现 2：应优先使用 LangChain 原生 Callback 集成

原始需求文档建议手动创建 trace/span，这在使用 LangChain 的项目中是重复劳动。Langfuse 提供了 `langfuse-langchain` 包，通过 LangChain 的 `BaseCallbackHandler` 自动拦截所有 LLM 调用和工具调用，自动生成 trace/span，无需手动埋点。

**推荐方案对比**：

| 方案 | 优点 | 缺点 |
|------|------|------|
| A: `langfuse-langchain` CallbackHandler | 零侵入，自动拦截 LLM 和 tool 调用 | 粒度由 LangChain 回调决定 |
| B: 手动 trace/span API | 完全控制粒度和 metadata | 代码侵入性强，维护成本高 |
| **C: 混合（推荐）** | Callback 覆盖 LLM + 自动工具，手动 span 覆盖 FFmpeg 等外部调用 | 需要理解两种 API |

### 3. 技术实现方案

### 3.1 添加依赖

在 [pyproject.toml](backend/pyproject.toml) 中添加：

```toml
dependencies = [
    # ... 现有依赖 ...
    "langfuse>=3.0.0",
]
```

**注意**：`langfuse-langchain` 包已合并到 `langfuse` 主包中（v3+），无需额外安装。

### 3.2 初始化 Langfuse 客户端

新建 `backend/app/agent/skills_agent/observability.py`：

```python
"""Langfuse 可观测性集成"""
import os
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

_langfuse_client: Langfuse | None = None

def get_langfuse_client() -> Langfuse:
    """获取全局 Langfuse 客户端（懒初始化）"""
    global _langfuse_client
    if _langfuse_client is None:
        _langfuse_client = Langfuse(
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
    return _langfuse_client

def create_langchain_callback(
    trace_name: str = "jianying_agent",
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> CallbackHandler:
    """创建 LangChain 自动埋点回调处理器"""
    client = get_langfuse_client()
    return CallbackHandler(
        client=client,
        trace_name=trace_name,
        user_id=user_id,
        session_id=session_id,
        tags=tags or [],
    )

def create_manual_span(trace_id: str, name: str, input_data: dict) -> str:
    """
    为 LangChain 无法自动覆盖的外部调用（FFmpeg, TTS）创建手动 span。
    返回 span_id 供后续 update_span 使用。
    """
    client = get_langfuse_client()
    span = client.span(
        trace_id=trace_id,
        name=name,
        input=input_data,
    )
    return span.id
```

### 3.3 集成到 Agent 创建流程

在 [jianying_agent.py](backend/app/agent/skills_agent/jianying_agent.py) 的 `create_jianying_agent()` 中：

```python
from .observability import create_langchain_callback

def create_jianying_agent(
    skill_root: str,
    ...
    enable_observability: bool = False,  # 新增参数
):
    callbacks = []
    if enable_observability:
        callback = create_langchain_callback(
            trace_name="jianying_agent",
            tags=["easy-scene", "video-editing"],
        )
        callbacks.append(callback)

    # 传给 agent.invoke 或 create_agent
    # LangChain 的 create_agent 支持 callbacks 配置
    agent = create_agent(
        model,
        system_prompt=system_prompt,
        middleware=[middleware],
        checkpointer=InMemorySaver(),
    )
    return agent, middleware, callbacks
```

### 3.4 外部工具的手动埋点

对于 LangChain Callback 无法自动覆盖的 subprocess 调用（如 CLI 执行器、FFmpeg），在关键节点添加手动 span 记录：

```python
# 在 CLIScriptExecutor.execute() 中：
from .observability import get_langfuse_client

def execute(self, script_name, args, timeout=300, trace_id=None):
    langfuse = get_langfuse_client() if trace_id else None
    span = None

    if langfuse and trace_id:
        span = langfuse.span(
            trace_id=trace_id,
            name=f"cli_execute:{script_name}",
            input={"script": script_name, "args": args},
        )

    try:
        result = subprocess.run(...)
        if span:
            span.update(output={"success": result.returncode == 0})
        return result
    except Exception as e:
        if span:
            span.update(level="ERROR", status_message=str(e))
        raise
    finally:
        if span:
            span.end()
```

### 3.5 环境变量配置

在项目的 `.env` 或部署配置中添加：

```bash
LANGFUSE_SECRET_KEY=sk-lf-xxx
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_HOST=https://cloud.langfuse.com  # 或自建实例地址
```

## 4. 验收标准

1. **LLM 调用自动追踪**：通过 `langfuse-langchain` CallbackHandler，每次 Agent 调用自动记录 LLM 请求/响应、token 消耗和耗时。
2. **工具调用自动追踪**：LangChain `@tool` 装饰的工具函数被自动纳入 trace 的 span 树，无需手动埋点。
3. **外部工具手动埋点**：FFmpeg、TTS 等非 LangChain 管理的 subprocess 调用通过手动 span API 记录执行状态。
