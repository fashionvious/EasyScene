# 需求 05：集成 Langfuse 可观测性埋点

## 1. 需求背景与目标

当前 Agent 系统没有任何可观测性基础设施——无法追踪每次 Agent 调用的 token 消耗、工具调用链、执行耗时和成功率。当系统在线上出现异常时，只能靠用户反馈或事后翻日志定位问题。

**核心目标**：使用 Langfuse Python SDK v3+ 为 Agent 和工具调用建立完整的 trace/span 体系，实现端到端可观测。

## 2. 现有代码分析

### 当前状态

- [pyproject.toml](backend/pyproject.toml) 中 **未包含 `langfuse` 依赖**
- 现有代码中无任何 Langfuse 埋点逻辑
- Agent 使用 `langchain.agents.create_agent` + `InMemorySaver`（无回调集成）
- **已有 Sentry 集成**：[main.py](backend/app/main.py#L16-L17) 中通过 `sentry_sdk.init()` 初始化了 Sentry（含 `enable_tracing=True`），[config.py](backend/app/core/config.py#L52) 中有 `SENTRY_DSN` 配置

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

#### 发现 3：Langfuse 与 Sentry 的职责划分

项目已集成 Sentry（`sentry-sdk[fastapi]==1.45.1`），两者职责不同：

| 维度 | Sentry | Langfuse |
|------|--------|----------|
| 核心职责 | 异常捕获、错误追踪、崩溃报告 | LLM 可观测性、token 消耗、trace/span |
| 数据模型 | 事件（exception + breadcrumb） | trace → span → generation |
| 适用场景 | 基础异常监控（FastAPI、数据库等） | Agent 推理链、工具调用链分析 |

**两者应共存而非替代**。Sentry 继续负责 FastAPI 层的异常监控，Langfuse 负责 Agent 层的可观测性。需确保两者不产生冲突（如重复记录同一异常）。

#### 发现 4：`create_agent` 的 callbacks 传递方式

原始文档将 `callbacks` 作为 `create_jianying_agent` 的返回值，但未说明如何将其传入 LangChain 的 `agent.invoke()`。实际上 LangChain 的 `create_agent` 返回的 agent 对象在 `invoke()` 时接受 `config={"callbacks": [...]}` 参数，而非在 `create_agent()` 构造时传入。

## 3. 技术实现方案

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
import logging
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

logger = logging.getLogger(__name__)

_langfuse_client: Langfuse | None = None

def get_langfuse_client() -> Langfuse | None:
    """
    获取全局 Langfuse 客户端（懒初始化）。

    Returns:
        Langfuse 客户端实例，若环境变量未配置则返回 None。
    """
    global _langfuse_client
    if _langfuse_client is None:
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        if not secret_key or not public_key:
            logger.info("Langfuse 未配置（缺少 LANGFUSE_SECRET_KEY 或 LANGFUSE_PUBLIC_KEY），跳过初始化")
            return None
        _langfuse_client = Langfuse(
            secret_key=secret_key,
            public_key=public_key,
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
    return _langfuse_client

def create_langchain_callback(
    trace_name: str = "jianying_agent",
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> CallbackHandler | None:
    """
    创建 LangChain 自动埋点回调处理器。

    Returns:
        CallbackHandler 实例，若 Langfuse 未配置则返回 None。
    """
    client = get_langfuse_client()
    if client is None:
        return None
    return CallbackHandler(
        client=client,
        trace_name=trace_name,
        user_id=user_id,
        session_id=session_id,
        tags=tags or [],
    )

def create_manual_span(trace_id: str, name: str, input_data: dict) -> str | None:
    """
    为 LangChain 无法自动覆盖的外部调用（FFmpeg, TTS）创建手动 span。
    返回 span_id 供后续 update_span 使用。
    """
    client = get_langfuse_client()
    if client is None:
        return None
    span = client.span(
        trace_id=trace_id,
        name=name,
        input=input_data,
    )
    return span.id
```

> **修正说明**：
> 1. 原始文档的 `get_langfuse_client()` 在环境变量未配置时会抛异常。改为返回 `None`，让调用方优雅降级（无 Langfuse 时不埋点，不影响业务逻辑）。
> 2. 补充了 `create_langchain_callback()` 返回 `None` 的处理，与 `get_langfuse_client()` 的降级逻辑一致。

### 3.3 集成到 Agent 创建和调用流程

在 [jianying_agent.py](backend/app/agent/skills_agent/jianying_agent.py) 中：

```python
from .observability import create_langchain_callback

def create_jianying_agent(
    skill_root: str,
    ...
    enable_observability: bool = False,  # 新增参数
):
    # ... 现有逻辑 ...

    agent = create_agent(
        model,
        system_prompt=system_prompt,
        middleware=[middleware],
        checkpointer=InMemorySaver(),
    )

    # Langfuse callback 不在 create_agent 时传入，
    # 而是在 agent.invoke() 时通过 config 传入
    return agent, middleware


def run_jianying_agent(
    skill_root: str,
    user_message: str,
    thread_id: str = None,
    enable_observability: bool = False,
    **kwargs
):
    agent, middleware = create_jianying_agent(skill_root, **kwargs)

    if thread_id is None:
        thread_id = str(uuid.uuid4())

    # 构建 config
    config = {"configurable": {"thread_id": thread_id}}

    # 添加 Langfuse callback
    if enable_observability:
        callback = create_langchain_callback(
            trace_name="jianying_agent",
            tags=["easy-scene", "video-editing"],
        )
        if callback:
            config["callbacks"] = [callback]

    result = agent.invoke(
        {"messages": [{"role": "user", "content": user_message}]},
        config,
    )
    return result
```

> **修正说明**：
> 1. 原始文档将 `callbacks` 作为 `create_jianying_agent` 的返回值，但未说明如何使用。实际上 LangChain 的 callback 应在 `agent.invoke()` 时通过 `config={"callbacks": [...]}` 传入，而非 `create_agent()` 构造时。
> 2. 将 `enable_observability` 参数移到 `run_jianying_agent()` 中，因为 callback 的作用时机是 `invoke()` 而非 `create_agent()`。

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

> **注意**：`trace_id` 需要从 LangChain 的自动 trace 中获取。在 `langfuse-langchain` CallbackHandler 自动创建的 trace 中，可通过 `callback_handler.get_trace_id()` 获取当前 trace_id，然后传递给手动 span。原始文档未说明 `trace_id` 的来源和传递方式。

### 3.5 环境变量配置

在项目的 `.env` 或部署配置中添加：

```bash
# Langfuse 配置（可选，未配置时自动禁用）
LANGFUSE_SECRET_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
```

> **修正说明**：原始文档直接给出示例值 `sk-lf-xxx`，可能误导开发者直接提交密钥。改为空值 + 注释说明"可选，未配置时自动禁用"，与 Sentry 的 `.env` 配置风格一致（`SENTRY_DSN=` 空值）。

### 3.6 与 Sentry 的共存策略

- **Sentry**：继续负责 FastAPI 层的异常监控（已有集成，不改动）
- **Langfuse**：负责 Agent 层的 trace/span/token 消耗
- **冲突避免**：Sentry 的 `enable_tracing=True` 会创建 APM trace，Langfuse 也会创建 trace。两者使用不同的 trace context，不会冲突。但需注意：
  - Sentry 的 `sentry_sdk.init()` 应在 Langfuse 初始化之前执行（已有，在 `main.py` 中）
  - Langfuse 客户端初始化延迟到首次使用时（懒初始化），避免在 `main.py` 启动时增加延迟

## 4. 验收标准

1. **LLM 调用自动追踪**：通过 `langfuse-langchain` CallbackHandler，每次 Agent 调用自动记录 LLM 请求/响应、token 消耗和耗时。
2. **工具调用自动追踪**：LangChain `@tool` 装饰的工具函数被自动纳入 trace 的 span 树，无需手动埋点。
3. **外部工具手动埋点**：FFmpeg、TTS 等非 LangChain 管理的 subprocess 调用通过手动 span API 记录执行状态。
4. **优雅降级**：Langfuse 环境变量未配置时，所有埋点逻辑自动跳过，不影响业务功能。
5. **与 Sentry 共存**：Sentry 继续负责 FastAPI 层异常监控，Langfuse 负责 Agent 层可观测性，两者不冲突。
6. **回调正确传递**：Langfuse CallbackHandler 在 `agent.invoke(config={"callbacks": [...]})` 时传入，而非 `create_agent()` 构造时。

## 5. 原始文档问题汇总

| # | 问题 | 严重程度 | 修正措施 |
|---|------|----------|----------|
| 1 | `get_langfuse_client()` 环境变量未配置时会抛异常，无降级逻辑 | 高 | 返回 `None`，调用方优雅降级 |
| 2 | 未考虑项目已有的 Sentry 集成，两者职责和共存策略未说明 | 高 | 补充 3.6 节 Sentry 共存策略 |
| 3 | Callback 传递方式错误：应在 `agent.invoke(config=...)` 时传入，而非 `create_agent()` 构造时 | 高 | 修正为在 `run_jianying_agent()` 中通过 `config["callbacks"]` 传入 |
| 4 | `create_jianying_agent` 返回 `callbacks` 但未说明调用方如何使用 | 高 | 移除返回值中的 `callbacks`，改为在 `invoke` 时动态创建 |
| 5 | 手动埋点的 `trace_id` 来源和传递方式未说明 | 中 | 补充说明从 `callback_handler.get_trace_id()` 获取 |
| 6 | `.env` 示例中直接给出密钥值 `sk-lf-xxx`，可能误导开发者提交密钥 | 中 | 改为空值 + "可选"注释 |
| 7 | 验收标准未覆盖优雅降级和 Sentry 共存 | 中 | 补充第 4、5、6 条验收标准 |
