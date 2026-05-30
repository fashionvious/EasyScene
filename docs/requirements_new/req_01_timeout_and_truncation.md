# 需求 01：为外部工具调用增加超时控制与输出截断机制

## 1. 需求背景与目标

当前部分外部工具调用（尤其是 FFmpeg 转码、AI 视频分析等长时任务）缺乏超时控制，一旦卡死会导致 Agent 永久挂起。同时，FFmpeg 等工具的 stderr 输出可能产生数 MB 日志，直接全量返回给 LLM 会浪费大量 token 预算。

**核心目标**：为所有外部工具调用统一增加超时控制与输出截断，确保系统在任何情况下都能在可控时间内返回结构化结果。

## 2. 现有代码分析

### 已具备超时控制的模块（无需改动超时，但需补充输出截断）

| 文件 | 当前行为 | 评估 |
|------|----------|------|
| [cli_executor.py](backend/app/agent/skills_agent/cli_executor.py#L239-L244) | `subprocess.run(cmd, timeout=timeout)`，默认 300s | 已有超时，但缺少输出截断 |
| [python_executor.py](backend/app/agent/skills_agent/python_executor.py#L139-L145) | `subprocess.run(cmd, timeout=self.timeout)`，默认 300s | 已有超时，但缺少输出截断 |
| [api_validator.py](backend/jianying-editor-skill/scripts/api_validator.py#L27-L33) | `subprocess.run(["ffprobe", "-version"], timeout=5)` | 已有超时，输出极短无需截断 |
| [sync_jy_assets.py](backend/jianying-editor-skill/scripts/sync_jy_assets.py#L21-L24) | `subprocess.run(["ffprobe", ...], timeout=5)` | 已有超时，输出极短无需截断 |
| [formatters.py](backend/jianying-editor-skill/scripts/utils/formatters.py#L145-L159) | `subprocess.run(["ffprobe", ...], timeout=5)` | 已有超时，输出极短无需截断 |

### 缺失超时控制的关键模块

| 文件 | 当前行为 | 风险 |
|------|----------|------|
| [media_normalizer.py](backend/jianying-editor-skill/scripts/utils/media_normalizer.py#L70) | `subprocess.run(cmd, ...)` **无 timeout 参数** | FFmpeg 转码大视频可能永远挂起 |
| [smart_rough_cut.py](backend/jianying-editor-skill/scripts/smart_rough_cut.py#L45-L49) | `AntigravityClient.chat_completion()` 调用无超时 | 网络异常时阻塞；且 `api_client` 模块为外部依赖，需确认其底层 HTTP 客户端是否支持 timeout 参数 |

### 审查发现

1. 原始需求文档将 CLI 执行器和 Python 执行器也列为改造目标，但实际它们已有 timeout 参数。**改造重点应放在 `media_normalizer.py` 和其他缺少超时的外部脚本上**。
2. 原始需求文档遗漏了 `api_validator.py`、`sync_jy_assets.py`、`formatters.py` 三个已有超时的 `subprocess.run` 调用点，虽然它们输出极短无需截断，但应在"已具备超时"表中列出以保证审查完整性。
3. `cloud_manager.py` 中存在 `requests.get(url, stream=True, timeout=60)` 调用（第 225 行），已有 60s 超时，但未在原始文档中提及。
4. 所有 `subprocess.run` 返回的 stdout/stderr 都应进行截断处理（尤其是 `cli_executor.py` 和 `python_executor.py`，它们的输出会直接注入 LLM 上下文）。

## 3. 技术实现方案

### 3.1 定义统一的超时配置

在 `backend/jianying-editor-skill/scripts/utils/` 下新建 `timeout_config.py`：

```python
"""统一超时配置"""
from dataclasses import dataclass

@dataclass(frozen=True)
class SubprocessConfig:
    default_timeout: int = 120          # 默认 2 分钟（适用于 FFmpeg 转码等）
    max_timeout: int = 600              # 最大 10 分钟
    output_truncate_bytes: int = 10240  # 10KB 截断阈值
    head_keep_bytes: int = 2048         # 截断时保留头部字节数
    tail_keep_bytes: int = 2048         # 截断时保留尾部字节数
```

### 3.2 新增输出截断工具函数

在上述文件中新增：

```python
def truncate_output(text: str, max_bytes: int = 10240) -> str:
    """截断输出，保留首尾各 2KB，中间用标记省略

    注意：此处 max_bytes 按 UTF-8 编码后的字节数计算，
    而非 Python str 的字符数。对于纯 ASCII 文本两者一致，
    对于含中文的文本，len(text.encode('utf-8')) 才是真实字节数。
    """
    raw_bytes = text.encode("utf-8")
    if len(raw_bytes) <= max_bytes:
        return text

    head_size = SubprocessConfig.head_keep_bytes
    tail_size = SubprocessConfig.tail_keep_bytes
    omitted = len(raw_bytes) - head_size - tail_size

    head_text = raw_bytes[:head_size].decode("utf-8", errors="replace")
    tail_text = raw_bytes[-tail_size:].decode("utf-8", errors="replace")

    return (
        head_text
        + f"\n\n...[截断 {omitted} 字节]...\n\n"
        + tail_text
    )
```

> **修正说明**：原始文档的 `truncate_output` 使用 `len(text)` 判断截断，这对于含中文/多字节字符的文本是不准确的——一个中文字符在 UTF-8 下占 3 字节，但 `len()` 只计为 1。修正版改为基于 `encode("utf-8")` 的真实字节数计算，避免截断阈值形同虚设。

### 3.3 改造 media_normalizer.py

```python
# 改造前（第 69-76 行）
proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

# 改造后
from utils.timeout_config import SubprocessConfig, truncate_output

try:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=SubprocessConfig.default_timeout,  # 120s
    )
except subprocess.TimeoutExpired:
    return None  # 保持与现有 return None 语义一致；上层调用方需检查 None

# 截断 stderr 用于错误日志输出
if proc.returncode != 0 or not os.path.exists(dst):
    err = truncate_output((proc.stderr or proc.stdout or "").strip())
    print(f"❌ WEBM normalization failed (ffmpeg={proc.returncode}): {err}")
    return None
```

> **修正说明**：原始文档将超时返回值改为 `{"success": False, "error": ...}` dict，但 `normalize_webm_for_jianying` 的返回类型是 `Optional[str]`（返回路径或 None），改为 dict 会破坏所有调用方的类型约定。应保持 `return None` 语义，与 `FileNotFoundError` 等其他异常分支一致。

### 3.4 改造 smart_rough_cut.py

为 `AntigravityClient.chat_completion()` 调用添加 `timeout` 参数。

**注意事项**：
- `api_client` 模块不在本项目代码库中（为外部 Skill 依赖），需确认其底层 HTTP 客户端（httpx/requests）是否支持 `timeout` 参数传递。
- 如果 `chat_completion()` 不支持 timeout 参数，则需在外层用 `asyncio.wait_for()` 包裹（该函数已是 async 调用上下文）：

```python
# 方案 A：若 api_client 支持 timeout 参数
response = client.chat_completion(
    messages=[...], model=model, file_paths=[video_path],
    timeout=180,  # 3 分钟
)

# 方案 B：若 api_client 不支持 timeout 参数，用 asyncio.wait_for 包裹
try:
    response = await asyncio.wait_for(
        client.chat_completion(messages=[...], model=model, file_paths=[video_path]),
        timeout=180,
    )
except asyncio.TimeoutError:
    print(f"[-] API Timeout after 180s")
    return []
```

> **修正说明**：原始文档仅简单提及"添加 timeout 参数"，未说明 `api_client` 为外部依赖且可能不支持该参数，也未提供 fallback 方案。

### 3.5 在 CLI/Python 执行器中追加输出截断

`cli_executor.py` 和 `python_executor.py` 返回结果前，对 stdout 和 stderr 调用 `truncate_output()`，避免大体积输出撑爆上下文。

```python
# cli_executor.py 改造示例（第 247-260 行）
from app.agent.skills_agent.utils.timeout_config import truncate_output

output = truncate_output(result.stdout.strip())
error = truncate_output(result.stderr.strip()) if result.returncode != 0 else None
```

> **注意**：`truncate_output` 需对 `cli_executor.py` 和 `python_executor.py` 均可导入。若两个执行器与 `jianying-editor-skill/scripts/utils/` 不在同一 Python 包路径下，需将 `truncate_output` 提取到共享位置（如 `backend/app/agent/skills_agent/utils/`），或在两处各放一份。

## 4. 验收标准

1. **所有 `subprocess.run` 调用均具备 `timeout` 参数**：搜索 `backend/jianying-editor-skill/scripts/` 下所有 `.py` 文件，不存在无 `timeout` 的 `subprocess.run` 调用。
2. **超时返回值与函数签名一致**：`media_normalizer.py` 超时返回 `None`（保持 `Optional[str]` 签名），`cli_executor.py` / `python_executor.py` 超时返回 `{"success": False, "error": ...}` dict（保持现有签名）。
3. **输出截断基于 UTF-8 字节数**：任何 stdout/stderr 超过 10KB（UTF-8 编码后字节数）时自动截断为"首 2KB + 标记 + 尾 2KB"，单次注入 LLM 的文本不超过 10KB。
4. **smart_rough_cut.py 具备 API 超时保护**：`chat_completion()` 调用在 180s 内无响应时返回空列表而非永久阻塞。

## 5. 原始文档问题汇总

| # | 问题 | 严重程度 | 修正措施 |
|---|------|----------|----------|
| 1 | `truncate_output` 使用 `len(text)` 判断截断阈值，对中文/多字节文本不准确 | 高 | 改为基于 `text.encode("utf-8")` 的真实字节数 |
| 2 | `media_normalizer.py` 超时返回 `{"success": False, ...}` dict，与函数签名 `Optional[str]` 不一致 | 高 | 保持 `return None`，与现有异常分支一致 |
| 3 | 遗漏 `api_validator.py`、`sync_jy_assets.py`、`formatters.py` 三个已有超时的调用点 | 中 | 补充到"已具备超时"表中 |
| 4 | 遗漏 `cloud_manager.py` 中的 `requests.get(timeout=60)` 调用 | 中 | 补充到"已具备超时"表中 |
| 5 | `smart_rough_cut.py` 改造未考虑 `api_client` 为外部依赖、可能不支持 timeout 参数 | 高 | 提供 `asyncio.wait_for` fallback 方案 |
| 6 | 验收标准第 2 条要求超时返回 `{"success": false, "error": "timeout", "timeout_seconds": N}` 格式，但 `media_normalizer` 返回类型为 `Optional[str]` | 高 | 区分不同模块的返回值约定 |
| 7 | 未说明 `truncate_output` 在 `cli_executor.py` / `python_executor.py` 中的导入路径问题 | 中 | 补充导入路径说明 |
