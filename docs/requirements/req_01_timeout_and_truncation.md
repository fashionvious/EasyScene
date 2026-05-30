# 需求 01：为外部工具调用增加超时控制与输出截断机制

## 1. 需求背景与目标

当前部分外部工具调用（尤其是 FFmpeg 转码、AI 视频分析等长时任务）缺乏超时控制，一旦卡死会导致 Agent 永久挂起。同时，FFmpeg 等工具的 stderr 输出可能产生数 MB 日志，直接全量返回给 LLM 会浪费大量 token 预算。

**核心目标**：为所有外部工具调用统一增加超时控制与输出截断，确保系统在任何情况下都能在可控时间内返回结构化结果。

## 2. 现有代码分析

### 已具备超时控制的模块（无需改动）

| 文件 | 当前行为 | 评估 |
|------|----------|------|
| [cli_executor.py](backend/app/agent/skills_agent/cli_executor.py#L239-L244) | `subprocess.run(cmd, timeout=timeout)`，默认 300s | 已有超时，但缺少输出截断 |
| [python_executor.py](backend/app/agent/skills_agent/python_executor.py#L139-L145) | `subprocess.run(cmd, timeout=self.timeout)`，默认 300s | 已有超时，但缺少输出截断 |

### 缺失超时控制的关键模块

| 文件 | 当前行为 | 风险 |
|------|----------|------|
| [media_normalizer.py](backend/jianying-editor-skill/scripts/utils/media_normalizer.py#L70) | `subprocess.run(cmd, ...)` **无 timeout 参数** | FFmpeg 转码大视频可能永远挂起 |
| [smart_rough_cut.py](backend/jianying-editor-skill/scripts/smart_rough_cut.py#L45-L80) | HTTP API 调用无超时 | 网络异常时阻塞 |

### 审查发现

原始需求文档将 CLI 执行器和 Python 执行器也列为改造目标，但实际它们已有 timeout 参数。**改造重点应放在 `media_normalizer.py` 和其他缺少超时的外部脚本上**。同时，所有 `subprocess.run` 返回的 stdout/stderr 都应进行截断处理。

## 3. 技术实现方案

### 3.1 定义统一的超时配置

在 `backend/jianying-editor-skill/scripts/utils/` 下新建 `timeout_config.py`：

```python
"""统一超时配置"""
from dataclasses import dataclass

@dataclass(frozen=True)
class SubprocessConfig:
    default_timeout: int = 120      # 默认 2 分钟
    max_timeout: int = 600          # 最大 10 分钟
    output_truncate_bytes: int = 10240  # 10KB 截断阈值
```

### 3.2 新增输出截断工具函数

在上述文件中新增：

```python
def truncate_output(text: str, max_bytes: int = 10240) -> str:
    """截断输出，保留首尾各 2KB，中间用标记省略"""
    if len(text) <= max_bytes:
        return text
    head_size = 2048
    tail_size = 2048
    return (
        text[:head_size]
        + f"\n\n...[截断 {len(text) - head_size - tail_size} 字节]...\n\n"
        + text[-tail_size:]
    )
```

### 3.3 改造 media_normalizer.py

```python
# 改造前（第 69-76 行）
proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

# 改造后
from utils.timeout_config import SubprocessConfig

try:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=SubprocessConfig.default_timeout,  # 120s
    )
except subprocess.TimeoutExpired:
    return {
        "success": False,
        "error": f"FFmpeg 执行超时（{SubprocessConfig.default_timeout}秒）",
    }
```

### 3.4 改造 smart_rough_cut.py

为 `AntigravityClient.chat_completion()` 调用添加 `timeout` 参数（httpx/requests 均支持），默认 180s。

### 3.5 在 CLI/Python 执行器中追加输出截断

`cli_executor.py` 和 `python_executor.py` 返回结果前，对 stdout 和 stderr 调用 `truncate_output()`，避免大体积输出撑爆上下文。

## 4. 验收标准

1. **所有 `subprocess.run` 调用均具备 `timeout` 参数**：搜索 `backend/jianying-editor-skill/scripts/` 下所有 `.py` 文件，不存在无 `timeout` 的 `subprocess.run` 调用。
2. **超时返回结构化错误**：所有超时捕获返回 `{"success": false, "error": "timeout", "timeout_seconds": N}` 格式的 dict。
3. **输出截断**：任何 stdout/stderr 超过 10KB 时自动截断为"首 2KB + 标记 + 尾 2KB"，单次注入 LLM 的文本不超过 10KB。
