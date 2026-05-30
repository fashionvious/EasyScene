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

### 审查发现

原始需求文档建议 `MAX_OUTPUT_BYTES = 10240`（10KB），与需求 01 中的输出截断阈值一致。合理。但"完整输出写入临时文件"的提议与现有 `finally` 中的删除逻辑冲突——需要增加保留/删除的条件判断。

## 3. 技术实现方案

### 3.1 定义输出大小阈值

在 `python_executor.py` 顶部新增：

```python
# 与需求 01 中的 truncate_output 保持一致的阈值
MAX_OUTPUT_CHARS = 10000       # 约 10KB
MAX_ERROR_CHARS = 5000         # 错误信息截断阈值（更激进）
```

### 3.2 新增输出摘要函数

```python
def summarize_output(stdout: str, stderr: str, success: bool) -> str:
    """
    执行结果智能摘要。

    成功时：提取最后 5 行（通常包含 "已裁剪项目时长" 等关键信息）
    失败时：提取包含 'Error', 'Traceback', '❌' 的行
    """
    if success:
        lines = stdout.strip().split("\n")
        if len(lines) <= 20:
            return stdout
        # 返回首 5 行 + 尾 5 行
        return "\n".join(lines[:5]) + f"\n... [{len(lines) - 10} 行省略] ...\n" + "\n".join(lines[-5:])
    else:
        # 提取错误关键词所在行
        all_output = (stdout + "\n" + stderr)
        keywords = ["Error", "Traceback", "❌", "失败", "错误", "Exception"]
        error_lines = []
        for line in all_output.split("\n"):
            if any(kw in line for kw in keywords):
                error_lines.append(line)
        if error_lines:
            return "关键错误信息:\n" + "\n".join(error_lines[-20:])
        # fallback: 返回最后 20 行
        return "\n".join(all_output.split("\n")[-20:])
```

### 3.3 改造 execute() 方法的临时文件逻辑

```python
def execute(self, code, include_bootstrap=True, capture_output=True,
            keep_temp_on_error=True):
    # ...
    try:
        result = subprocess.run(...)
        output = result.stdout or ""
        error = result.stderr or ""

        return {
            "success": result.returncode == 0,
            "output": summarize_output(output, error, result.returncode == 0),
            "raw_output_truncated": len(output) > MAX_OUTPUT_CHARS,
            "error": error[:MAX_ERROR_CHARS] if result.returncode != 0 else None,
            "full_output_path": temp_file
                if (len(output) > MAX_OUTPUT_CHARS or not result.returncode == 0)
                else None,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"执行超时（{self.timeout}秒）",
            "temp_file": temp_file  # 保留供排查
        }
    finally:
        # 仅删除无问题的小输出临时文件
        should_keep = (
            keep_temp_on_error and  # 保留错误时的文件
            (result.returncode != 0 or len(result.stdout) > MAX_OUTPUT_CHARS)
        )
        if not should_keep:
            try:
                os.unlink(temp_file)
            except Exception:
                pass
```

### 3.4 对 LLM 返回的消息格式

```python
# 在 create_python_executor_tool 中的 execute_jyproject_code 工具函数里：
if result["success"]:
    msg = f"执行成功\n{result['output']}"
    if result.get("raw_output_truncated"):
        msg += f"\n\n(完整输出较大，已截断。完整日志: {result['full_output_path']})"
else:
    msg = f"执行失败: {result.get('error', '未知错误')}"
    if result.get("full_output_path"):
        msg += f"\n(完整日志: {result['full_output_path']})"
return msg
```

## 4. 验收标准

1. **输出自动截断**：stdout 超过 10000 字符时自动摘要，返回首尾各 5 行 + 省略标记；stderr 超过 5000 字符时仅返回错误关键词行。
2. **错误现场保留**：执行失败或输出被截断时，保留临时文件不删除，路径随结果返回供开发者/用户排查。
3. **正常情况清理**：执行成功且输出未超过阈值时，临时文件正常删除，不留残留。
