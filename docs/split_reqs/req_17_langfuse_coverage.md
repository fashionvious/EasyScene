# 需求 17：Langfuse 追踪补齐（python_executor + FFmpeg span）

> 原 PRD 编号: P2-1 (D-3) | 优先级: P2

## 1. 依赖关系

- **前置依赖**：无（独立于其他新需求，使用的 `observability.py` 已存在）
- **被谁依赖**：无

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库的 Langfuse 覆盖现状（[observability.py](../../../backend/app/agent/skills_agent/observability.py)）：

**已覆盖**：
- `cli_executor.py:253-265` — 手动 span 覆盖 CLI subprocess 调用（`trace_id` 参数传入 + span.update() + span.end()）
- `jianying_agent.py:416-427` — LangChain CallbackHandler 自动覆盖 LLM 调用
- Langfuse v3 懒初始化优雅降级（`get_langfuse_client()`）

**未覆盖**（本需求补齐）：
- `python_executor.py:211-275` — `execute()` 方法的 subprocess 调用无 span
- `media_normalizer.py` — FFmpeg 调用无 span
- `process_utils.py:40-133` — `run_with_timeout()` 的 Popen 调用无 span

### 代码库校验结论

- `observability.py:66-81` 已有 `create_manual_span(trace_id, name, input_data)` 返回值是 `span_id`（字符串），但实际使用直接调 `langfuse.span()` —— 需要加一个 `end_manual_span(span_id, output_data)` 辅助函数
- `cli_executor.py:253-291` 的模式（创建 span → update() → end()）可以直接复制到 python_executor 和 media_normalizer
- `trace_id` 需要从 LangChain 回调的 trace context 中获取，或从 Agent 的 `config["callbacks"]` 中提取

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `backend/app/agent/skills_agent/observability.py` | 新增 `end_manual_span()` + `get_current_trace_id()` 辅助函数 |
| 修改 | `backend/app/agent/skills_agent/python_executor.py` | 在 `execute()` 中添加 span 管理 |
| 修改 | `backend/jianying-editor-skill/scripts/utils/media_normalizer.py` | 在 FFmpeg 调用处添加 span |

### 核心技术细节

**observability.py 补充**：

```python
# observability.py — 新增函数

def end_manual_span(span_id: str, output_data: dict | None = None,
                    level: str | None = None, status_message: str | None = None) -> None:
    """
    结束手动 span（补充 cli_executor 用到的 span 生命周期管理）。

    Langfuse v3 API 中，span() 返回 Span 对象，直接调用 .end() 即可。
    此函数提供输出更新 + 异常安全的 end()。
    """
    client = get_langfuse_client()
    if client is None:
        return
    try:
        span = client.span(id=span_id)
        if output_data:
            span.update(output=output_data)
        if level:
            span.update(level=level)
        if status_message:
            span.update(status_message=status_message)
        span.end()
    except Exception as e:
        logger.warning(f"end_manual_span failed: {e}")


def get_current_trace_id() -> str | None:
    """
    尝试从当前上下文中获取 Langfuse trace_id。

    优先从 LangChain callback 的 run tree 中提取。
    若不可用，返回 None（调用方应检查并跳过手动 span 创建）。
    """
    try:
        from langfuse.langchain import CallbackHandler
        # Langfuse v3 的 CallbackHandler 内部维护 run tree
        # 如果 callback 已注册，trace 上下文可以通过 langfuse context 获取
        import langfuse
        current_trace = langfuse.get_current_trace_id()
        return current_trace
    except Exception:
        return None
```

**python_executor.py 集成**：

```python
# python_executor.py — execute() 方法中添加 span

def execute(self, code, include_bootstrap=True, capture_output=True,
            max_retries=0, keep_temp_on_error=True,
            trace_id: str | None = None) -> dict:  # ← 新增 trace_id 参数

    # ... 现有代码保持不变直到 subprocess.run ...

    # 在 subprocess.run 调用前后包裹手动 span
    langfuse_span = None
    if trace_id:
        try:
            from .observability import get_langfuse_client
        except ImportError:
            from observability import get_langfuse_client

        langfuse = get_langfuse_client()
        if langfuse:
            langfuse_span = langfuse.span(
                trace_id=trace_id,
                name="python_executor:execute_jyproject",
                input={"code_length": len(code), "include_bootstrap": include_bootstrap},
            )

    for attempt in range(total_attempts):
        try:
            result = subprocess.run(cmd, capture_output=capture_output, ...)
            # ... 现有结果处理逻辑 ...

            if langfuse_span:
                langfuse_span.update(
                    output={
                        "success": is_success,
                        "returncode": result.returncode,
                        "output_bytes": output_bytes,
                        "truncated": output_truncated,
                    },
                )
                if not is_success:
                    langfuse_span.update(level="ERROR", status_message=truncate_output(error))
                langfuse_span.end()

            return { ... }  # 现有返回

        except subprocess.TimeoutExpired:
            if langfuse_span:
                langfuse_span.update(level="WARNING", status_message=f"timeout({self.timeout}s)")
                langfuse_span.end()
            # ... 现有重试逻辑 ...

        except Exception as e:
            if langfuse_span:
                langfuse_span.update(level="ERROR", status_message=str(e))
                langfuse_span.end()
            # ... 现有异常处理 ...
```

**media_normalizer.py 集成**：

```python
# media_normalizer.py — FFmpeg 调用处添加 span
# 在调用 run_with_timeout 前后包裹：

from scripts.utils.observability_hook import with_ffmpeg_span

# 或简化版：在现有函数中添加
def normalize_webm_for_jianying(input_path: str, output_path: str,
                                trace_id: str | None = None) -> dict:
    langfuse_span = None
    if trace_id:
        try:
            from langfuse import Langfuse
            langfuse = Langfuse()  # 使用环境变量配置
            langfuse_span = langfuse.span(
                trace_id=trace_id,
                name="ffmpeg:normalize_webm",
                input={"input": input_path, "output": output_path},
            )
        except Exception:
            pass

    try:
        result = run_with_timeout(
            ["ffmpeg", "-i", input_path, ...],
            timeout=600,
        )
        if langfuse_span:
            langfuse_span.update(output={"success": result["success"]})
            langfuse_span.end()
        return result
    except Exception as e:
        if langfuse_span:
            langfuse_span.update(level="ERROR", status_message=str(e))
            langfuse_span.end()
        raise
```

### 容错与边界

- Langfuse 未配置时（环境变量缺失），所有 span 创建跳过（`get_langfuse_client()` 返回 None），不影响主流程
- `trace_id` 为 None 时不创建手动 span（静默跳过）
- span 操作（update/end）失败时使用 try/except 包裹 + logger.warning，不影响主业务逻辑
- 不引入 Perfetto 导出（PRD v2.0 已降级）
- 参考 `cli_executor.py` 成熟的 span 管理模式，保持风格一致

## 4. 验收标准 (DoD)

- [ ] `python_executor.execute()` 在 `trace_id` 非空时创建 Langfuse 手动 span
- [ ] FFmpeg 调用在 `trace_id` 非空时创建 Langfuse 手动 span
- [ ] span 在 subprocess 调用结束后正确 `.end()`
- [ ] 异常时 span 正确标记 ERROR level + status_message
- [ ] Langfuse 未配置时所有 span 操作静默跳过
- [ ] `trace_id=None` 时不创建 span（零开销）
- [ ] Langfuse Dashboard 中可看到完整的 Agent → tool → subprocess trace 链路
