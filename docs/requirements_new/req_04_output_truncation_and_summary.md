# 需求 04：为 Python 执行器增加输出截断与结果摘要机制

## 1. 需求背景与目标

LLM 生成的 JyProject 编排代码执行后可能产生大量输出（如批量添加 100 个素材时每个都有日志），直接将全部 stdout/stderr 返回给 LLM 会严重浪费上下文窗口。同时，`python_executor.py` 当前在 `finally` 块中无条件删除临时文件，导致大输出无法追溯。

**核心目标**：对 Python 执行器的输出进行智能截断，超过阈值时保留完整日志文件供后续排查，返回给 LLM 的仅为摘要。

## 2. 现有代码分析

### 问题代码

[python_executor.py](backend/app/agent/skills_agent/python_executor.py#L100-L172)：

```python
# 第 147-152 行：返回完整 stdout/stderr，无截断
return {
    "success": result.returncode == 0,
    "output": result.stdout if capture_output else "",
    "error": result.stderr if result.returncode != 0 else None,
    ...
}

# 第 167-171 行：finally 无条件删除临时文件
finally:
    try:
        os.unlink(temp_file)
    except:
        pass
```

**具体问题**：
1. 输出无大小限制：100 个素材的批量操作可能产生 50KB+ 的 stdout
2. 临时文件无条件删除：即使执行失败，开发者也无法复盘代码
3. 无摘要提取：LLM 需要从海量输出中自行判断成功与否
4. `except:` 裸捕获（第 171 行）：违反 Ruff E722 规则，应指定异常类型

### 审查发现

1. 原始需求文档建议 `MAX_OUTPUT_BYTES = 10240`（10KB），与需求 01 中的输出截断阈值一致。合理。但"完整输出写入临时文件"的提议与现有 `finally` 中的删除逻辑冲突——需要增加保留/删除的条件判断。
2. **`full_output_path` 指向的是源代码临时文件而非日志文件**：原始文档将 `temp_file`（Python 源代码临时文件）作为 `full_output_path` 返回，但用户/开发者需要的是**执行输出日志**而非源代码。当输出被截断时，应将完整 stdout/stderr 写入独立的日志文件，而非返回源代码路径。
3. **`MAX_OUTPUT_CHARS = 10000` 与 `MAX_OUTPUT_BYTES` 混用**：原始文档标题写"10KB"但实际用字符数（10000 chars），对于含中文的输出，10000 字符 ≈ 30KB UTF-8 字节，与需求 01 的 10KB 字节阈值不一致。
4. **`summarize_output` 中成功路径的截断阈值不一致**：`len(lines) <= 20` 时返回全文，但失败路径返回最后 20 行——成功输出 21 行就会触发截断，阈值过低。

## 3. 技术实现方案

### 3.1 定义输出大小阈值

在 `python_executor.py` 顶部新增：

```python
# 与需求 01 中的 truncate_output 保持一致的阈值（基于 UTF-8 字节数）
MAX_OUTPUT_BYTES = 10240        # 10KB，stdout 截断阈值
MAX_ERROR_BYTES = 5120          # 5KB，stderr 截断阈值（更激进）
MAX_SUMMARY_LINES = 50          # 摘要保留的最大行数
```

> **修正说明**：将 `MAX_OUTPUT_CHARS` 改为 `MAX_OUTPUT_BYTES`，与需求 01 的字节级截断保持一致。所有截断判断基于 `len(text.encode('utf-8'))` 而非 `len(text)`。

### 3.2 新增输出摘要函数

```python
def summarize_output(stdout: str, stderr: str, success: bool) -> str:
    """
    执行结果智能摘要。

    成功时：提取首 5 行 + 尾 5 行（通常尾部包含 "已裁剪项目时长" 等关键信息）
    失败时：提取包含 'Error', 'Traceback', '❌' 的行
    """
    if success:
        lines = stdout.strip().split("\n")
        if len(lines) <= MAX_SUMMARY_LINES:
            return stdout
        head_count = 5
        tail_count = 5
        omitted = len(lines) - head_count - tail_count
        return (
            "\n".join(lines[:head_count])
            + f"\n... [{omitted} 行省略] ...\n"
            + "\n".join(lines[-tail_count:])
        )
    else:
        all_output = (stdout + "\n" + stderr)
        keywords = ["Error", "Traceback", "❌", "失败", "错误", "Exception"]
        error_lines = []
        for line in all_output.split("\n"):
            if any(kw in line for kw in keywords):
                error_lines.append(line)
        if error_lines:
            return "关键错误信息:\n" + "\n".join(error_lines[-20:])
        return "\n".join(all_output.split("\n")[-20:])
```

> **修正说明**：原始文档成功路径的截断阈值为 `len(lines) <= 20`，21 行就触发截断，过于激进。改为 `MAX_SUMMARY_LINES = 50`，避免对正常规模的输出（如添加 10 个素材的日志约 30 行）误触发截断。

### 3.3 新增完整日志持久化函数

当输出被截断时，将完整 stdout/stderr 写入独立日志文件，而非返回源代码临时文件路径：

```python
import time

def save_full_output(stdout: str, stderr: str, work_dir: Path) -> str | None:
    """
    将完整输出保存到日志文件。

    Returns:
        日志文件路径，如果输出为空则返回 None
    """
    if not stdout and not stderr:
        return None

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_filename = f"jy_exec_log_{timestamp}.txt"
    log_path = work_dir / log_filename

    content = f"=== STDOUT ===\n{stdout}\n\n=== STDERR ===\n{stderr}\n"
    log_path.write_text(content, encoding="utf-8")

    return str(log_path)
```

### 3.4 改造 execute() 方法

```python
def execute(
    self,
    code: str,
    include_bootstrap: bool = True,
    capture_output: bool = True,
    keep_temp_on_error: bool = True,
) -> dict[str, Any]:
    # ... 前置逻辑不变（创建临时文件等）...

    result = None  # 用于 finally 中判断
    try:
        result = subprocess.run(
            cmd, capture_output=capture_output, text=True,
            timeout=self.timeout, cwd=str(self.work_dir)
        )

        output = result.stdout if capture_output else ""
        error = result.stderr if capture_output else ""
        is_success = result.returncode == 0

        # 判断是否需要截断（基于 UTF-8 字节数）
        output_bytes = len(output.encode("utf-8"))
        error_bytes = len(error.encode("utf-8"))
        output_truncated = output_bytes > MAX_OUTPUT_BYTES
        error_truncated = error_bytes > MAX_ERROR_BYTES

        # 摘要化输出
        summarized_output = summarize_output(output, error, is_success) if output_truncated else output
        summarized_error = error[:MAX_ERROR_BYTES] if (not is_success and error_truncated) else error

        # 持久化完整日志（仅截断或失败时）
        full_log_path = None
        if output_truncated or not is_success:
            full_log_path = save_full_output(output, error, self.work_dir)

        return {
            "success": is_success,
            "output": summarized_output if is_success else summarized_output,
            "error": summarized_error if not is_success else None,
            "returncode": result.returncode,
            "raw_output_truncated": output_truncated,
            "full_log_path": full_log_path,
            "temp_file": temp_file if not is_success else None,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"代码执行超时（{self.timeout}秒）",
            "temp_file": temp_file,  # 保留源代码供排查
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"执行失败: {str(e)}",
            "temp_file": temp_file,
        }
    finally:
        # 仅在成功且输出未截断时删除临时文件
        should_keep = keep_temp_on_error and (
            result is None  # 异常情况
            or result.returncode != 0  # 执行失败
            or len((result.stdout or "").encode("utf-8")) > MAX_OUTPUT_BYTES  # 输出被截断
        )
        if not should_keep:
            try:
                os.unlink(temp_file)
            except OSError:
                pass
```

> **修正说明**：
> 1. 原始文档的 `full_output_path` 返回 `temp_file`（源代码路径），但用户需要的是执行日志。改为通过 `save_full_output()` 写入独立日志文件，返回 `full_log_path`。
> 2. 原始文档的 `finally` 块中引用 `result.returncode` 和 `result.stdout`，但如果 `subprocess.run` 抛出异常，`result` 为 `None`，会导致 `AttributeError`。修正版增加 `result is None` 判断。
> 3. 原始文档的 `except:` 裸捕获改为 `except OSError:`，遵循 Ruff E722 规则。
> 4. 截断判断改为基于 `len(text.encode("utf-8"))` 字节数，与需求 01 一致。

### 3.5 对 LLM 返回的消息格式

```python
# 在 create_python_executor_tool 中的 execute_jyproject_code 工具函数里：
if result["success"]:
    msg = f"执行成功\n{result['output']}"
    if result.get("raw_output_truncated"):
        log_path = result.get("full_log_path")
        if log_path:
            msg += f"\n\n(完整输出已截断，日志文件: {log_path})"
else:
    msg = f"执行失败: {result.get('error', '未知错误')}"
    log_path = result.get("full_log_path")
    if log_path:
        msg += f"\n(完整日志: {log_path})"
    temp_path = result.get("temp_file")
    if temp_path:
        msg += f"\n(源代码: {temp_path})"
return msg
```

> **修正说明**：区分 `full_log_path`（执行日志）和 `temp_file`（源代码），分别提示用户。失败时两者都保留，便于排查是代码问题还是运行时问题。

### 3.6 与需求 01 的关系说明

需求 01 为 `cli_executor.py` 和 `python_executor.py` 提供了通用的 `truncate_output()` 函数（首 2KB + 尾 2KB 截断）。本需求的 `summarize_output()` 提供了更智能的摘要（成功时首尾行，失败时提取错误关键词行），两者可共存：

- **需求 01 的 `truncate_output()`**：用于 `cli_executor.py` 的简单截断（CLI 工具输出格式统一，首尾截断即可）
- **本需求的 `summarize_output()`**：用于 `python_executor.py` 的智能摘要（JyProject 输出有结构，需提取关键行）

若希望统一，可将 `truncate_output()` 作为 `summarize_output()` 的 fallback（当关键词提取无结果时使用）。

## 4. 验收标准

1. **输出自动截断**：stdout 超过 10KB（UTF-8 字节数）时自动摘要，返回首尾各 5 行 + 省略标记；stderr 超过 5KB 时仅返回错误关键词行。
2. **错误现场保留**：执行失败或输出被截断时，完整 stdout/stderr 写入独立日志文件（`jy_exec_log_YYYYMMDD_HHMMSS.txt`），路径随结果返回供排查。
3. **源代码保留**：执行失败时保留源代码临时文件，成功时正常删除。
4. **正常情况清理**：执行成功且输出未超过阈值时，临时文件正常删除，不留残留。
5. **截断基于字节数**：截断判断使用 `len(text.encode("utf-8"))` 而非 `len(text)`，与需求 01 一致。
6. **摘要阈值合理**：成功输出不超过 50 行时不触发截断，避免对正常规模输出误截断。
7. **无裸异常捕获**：`finally` 块中使用 `except OSError:` 而非 `except:`，遵循 Ruff E722。

## 5. 原始文档问题汇总

| # | 问题 | 严重程度 | 修正措施 |
|---|------|----------|----------|
| 1 | `full_output_path` 返回源代码临时文件路径，而非执行日志文件路径 | 高 | 新增 `save_full_output()` 写入独立日志文件，返回 `full_log_path` |
| 2 | `MAX_OUTPUT_CHARS = 10000` 用字符数，与需求 01 的字节级截断不一致 | 高 | 改为 `MAX_OUTPUT_BYTES = 10240`，基于 UTF-8 字节数 |
| 3 | `summarize_output` 成功路径截断阈值 `len(lines) <= 20` 过低，21 行就截断 | 中 | 改为 `MAX_SUMMARY_LINES = 50` |
| 4 | `finally` 块中引用 `result.returncode`，但异常时 `result` 为 `None` 会导致 `AttributeError` | 高 | 增加 `result is None` 判断 |
| 5 | `except:` 裸捕获违反 Ruff E722 规则 | 中 | 改为 `except OSError:` |
| 6 | 未说明 `summarize_output` 与需求 01 的 `truncate_output` 的关系和选择依据 | 中 | 补充 3.6 节说明两者适用场景 |
| 7 | `keep_temp_on_error` 参数在 `execute()` 签名中新增，但未说明向后兼容性 | 低 | 默认 `True`，与现有行为兼容（现有代码无条件删除，新参数为 True 时仅在成功+小输出时删除） |
