# 需求 06：为 FFmpeg 调用增加进程组管理与优雅终止（Windows）

## 1. 需求背景与目标

当前 `media_normalizer.py` 中的 FFmpeg 调用使用阻塞式 `subprocess.run`，无超时控制，无优雅终止机制。FFmpeg 在处理大视频时可能运行数十分钟，期间无法被外部中断。若 FFmpeg 进程卡死，整个 Agent 会永久挂起。

**核心目标**：将 FFmpeg 调用从 `subprocess.run` 改为 `subprocess.Popen`，支持超时检测、优雅终止（SIGTERM 等效 → 强制终止），并清理产生的中间文件。

## 2. 现有代码分析

### 问题代码

[media_normalizer.py](backend/jianying-editor-skill/scripts/utils/media_normalizer.py#L69-L83)：

```python
# 第 70 行：阻塞式调用，无超时
proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
```

### 关键发现：原始需求文档的方案不兼容 Windows

原始需求文档（第 6 节）的实现方案使用了以下 Unix 专有 API：

| 原始建议 API | Windows 兼容性 | 说明 |
|-------------|---------------|------|
| `os.setsid` | **不支持** | `preexec_fn` 在 Windows 上被忽略 |
| `os.killpg()` | **不支持** | 仅 Unix |
| `os.getpgid()` | **不支持** | 仅 Unix |
| `signal.SIGTERM` | **部分支持** | Windows 上 `os.kill()` 可用 `signal.SIGTERM` 但行为不同 |
| `signal.SIGKILL` | **不支持** | Windows 无此信号 |

**项目运行环境为 Windows 11**（[pyproject.toml](backend/pyproject.toml) 使用 `hatchling` 构建），必须使用 Windows 兼容的进程管理方案。

### 审查发现

1. **`subprocess.CREATE_NEW_PROCESS_GROUP` 在 FFmpeg 场景下可能无效**：`CREATE_NEW_PROCESS_GROUP` 标志的作用是让子进程成为新控制台进程组的领导者，以便向其发送 `CTRL_BREAK_EVENT`。但 FFmpeg 通常不会处理 `CTRL_BREAK_EVENT` 信号，发送该信号可能导致"该信号只能发送到与当前控制台关联的进程组"的 `OSError`。更可靠的 Windows 终止方案是直接使用 `proc.terminate()`（等效 `TerminateProcess`）或 `taskkill /T /F /PID`。

2. **`proc.terminate()` 在 Windows 上是强制终止**：与 Unix 上发送 `SIGTERM`（可被捕获）不同，Windows 上的 `proc.terminate()` 直接调用 `TerminateProcess`，等效于 Unix 的 `SIGKILL`。因此 Windows 上不存在"优雅终止 → 强制终止"的两阶段过程，`graceful_timeout` 参数在 Windows 上无实际意义。

3. **`taskkill /T /F` 会终止进程树**：FFmpeg 可能启动子进程（如通过管道连接的 `libx264` 编码器），`taskkill /T`（`/T` = Tree）会递归终止所有子进程，而 `proc.terminate()` / `proc.kill()` 只终止主进程。若 FFmpeg 启动了子进程，仅 `proc.terminate()` 可能留下孤儿进程。

4. **与需求 01 的超时方案冲突**：需求 01 建议为 `media_normalizer.py` 添加 `subprocess.run(timeout=120)`，而本需求建议改为 `Popen` + `communicate(timeout=...)`。两者应合并为本需求的 `Popen` 方案（功能更完整），需求 01 的 `subprocess.run(timeout=...)` 方案不再适用于 `media_normalizer.py`。

5. **`run_with_timeout` 返回值与 `media_normalizer.py` 现有逻辑不兼容**：现有代码通过 `proc.returncode` 和 `proc.stderr` 判断结果，改为 `run_with_timeout` 后返回 dict，需适配。

## 3. 技术实现方案

### 3.1 Windows 进程管理基础

Windows 上替代 Unix 进程组管理的方案：

```python
import subprocess
import signal
import time
import sys

# Windows 等效操作：
# 1. 终止进程树 → taskkill /T /F /PID <pid>（递归终止所有子进程）
# 2. 终止单进程 → proc.terminate()（等效 TerminateProcess，不可捕获）
# 3. 注意：Windows 上不存在优雅终止（SIGTERM），terminate() 即为强制终止
```

### 3.2 新增通用进程执行工具

在 `backend/jianying-editor-skill/scripts/utils/` 下新建 `process_utils.py`：

```python
"""跨平台进程执行工具 —— Windows 兼容"""
import subprocess
import sys
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _kill_process_tree(pid: int) -> None:
    """
    终止进程树（递归终止所有子进程）。

    Windows: taskkill /T /F /PID
    Unix: os.killpg(pid, signal.SIGKILL)
    """
    if _is_windows():
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"taskkill failed for PID {pid}: {e}")
    else:
        import os
        import signal
        try:
            os.killpg(pid, signal.SIGKILL)
        except Exception as e:
            logger.warning(f"killpg failed for PID {pid}: {e}")


def run_with_timeout(
    cmd: list[str],
    timeout: int = 120,
    graceful_timeout: int = 5,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """
    执行命令，支持超时和优雅终止。

    Args:
        cmd: 命令和参数列表
        timeout: 总超时时间（秒）
        graceful_timeout: 优雅终止等待时间（秒），仅 Unix 有效；
            Windows 上 terminate() 即为强制终止，此参数被忽略
        cwd: 工作目录

    Returns:
        {
            "success": bool,
            "stdout": str,
            "stderr": str,
            "returncode": int | None,
            "timeout_expired": bool,
            "was_force_killed": bool,
        }
    """
    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }

    if cwd:
        popen_kwargs["cwd"] = str(cwd)

    if _is_windows():
        # Windows: CREATE_NEW_PROCESS_GROUP 使子进程独立于父进程控制台
        # 注意：这不影响 terminate() 的行为，但允许 taskkill /T 正确终止进程树
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # Unix: 创建新进程组，以便 killpg 终止整个组
        import os
        popen_kwargs["preexec_fn"] = os.setsid

    proc = subprocess.Popen(cmd, **popen_kwargs)

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return {
            "success": proc.returncode == 0,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "returncode": proc.returncode,
            "timeout_expired": False,
            "was_force_killed": False,
        }

    except subprocess.TimeoutExpired:
        was_force_killed = False

        if _is_windows():
            # Windows: terminate() 等效 TerminateProcess（强制终止，不可捕获）
            # 直接使用 taskkill /T /F 终止进程树（处理 FFmpeg 子进程）
            _kill_process_tree(proc.pid)
            was_force_killed = True
        else:
            # Unix: 先尝试 SIGTERM（优雅终止）
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=graceful_timeout)
                # 优雅终止成功
                return {
                    "success": False,
                    "stdout": stdout or "",
                    "stderr": (stderr or "") + f"\n[超时 {timeout}s，已优雅终止]",
                    "returncode": proc.returncode,
                    "timeout_expired": True,
                    "was_force_killed": False,
                }
            except subprocess.TimeoutExpired:
                # 优雅终止超时，强制终止
                _kill_process_tree(proc.pid)
                was_force_killed = True

        # 强制终止后收集剩余输出
        try:
            stdout, stderr = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""

        return {
            "success": False,
            "stdout": stdout or "",
            "stderr": (stderr or "") + f"\n[超时 {timeout}s，{'已强制终止' if was_force_killed else '已优雅终止'}]",
            "returncode": proc.returncode,
            "timeout_expired": True,
            "was_force_killed": was_force_killed,
        }
```

> **修正说明**：
> 1. 原始文档的 Windows 路径直接调用 `proc.terminate()`，但未说明 Windows 上 `terminate()` 等效 `TerminateProcess`（不可捕获的强制终止），与"优雅终止"语义不符。修正版在 Windows 上直接使用 `taskkill /T /F` 终止进程树，并明确注释说明。
> 2. 原始文档未处理 FFmpeg 子进程（如编码器子进程）的终止。补充 `_kill_process_tree()` 函数，使用 `taskkill /T` 递归终止。
> 3. 原始文档的 Unix 路径缺少 `preexec_fn=os.setsid`，无法用 `killpg` 终止进程组。补充了 Unix 路径的进程组创建。
> 4. 原始文档在强制终止后直接 `proc.communicate()` 无超时，若进程仍不退出会再次阻塞。改为 `proc.communicate(timeout=3)`，超时后放弃收集输出。
> 5. 将 `print` 替换为 `logger.warning`，遵循 AGENTS.md 规范。

### 3.3 改造 media_normalizer.py

```python
# 改造前（第 69-76 行）
try:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
except FileNotFoundError:
    ...

# 改造后
from utils.process_utils import run_with_timeout

try:
    result = run_with_timeout(cmd, timeout=120, graceful_timeout=5)
except FileNotFoundError:
    print("❌ FFmpeg not found. Cannot normalize WEBM for JianYing import.")
    return None

if not result["success"]:
    err = result["stderr"].strip()
    if result["timeout_expired"]:
        print(f"❌ FFmpeg 超时（120s），{'已强制终止' if result['was_force_killed'] else '已终止'}")
        # 清理不完整的输出文件
        if os.path.exists(dst):
            os.unlink(dst)
    else:
        print(f"❌ WEBM normalization failed (ffmpeg={result['returncode']}): {err[:500]}")
    return None

if not os.path.exists(dst):
    print(f"❌ WEBM normalization failed: output file not created")
    return None

return dst
```

> **修正说明**：
> 1. 原始文档的改造代码未处理 `FileNotFoundError`（FFmpeg 未安装），补充了 `try/except FileNotFoundError` 分支。
> 2. 原始文档在 `result["success"]` 为 False 时直接 return None，但未检查 `dst` 文件是否存在。FFmpeg 可能返回码为 0 但输出文件损坏，补充了 `os.path.exists(dst)` 检查。
> 3. 注意：此处使用 `print` 而非 `logging`，因为 `media_normalizer.py` 位于 `jianying-editor-skill/scripts/` 下，该目录下的脚本统一使用 `print` 输出（与 `api_validator.py`、`smart_rough_cut.py` 等保持一致）。

### 3.4 与需求 01 的关系

需求 01 建议为 `media_normalizer.py` 添加 `subprocess.run(timeout=120)`，而本需求将其改为 `Popen` + `communicate(timeout=...)` + 进程树终止。**两者应合并为本需求的方案**，因为：

- `Popen` 方案功能更完整（支持优雅终止 + 进程树清理 + 中间文件清理）
- `subprocess.run(timeout=...)` 在超时时会强制终止进程但不清理中间文件，且 Windows 上可能留下孤儿子进程

需求 01 中 `media_normalizer.py` 的改造方案应替换为本需求的 `run_with_timeout()` 调用。

### 3.5 设计方案选择说明

**为什么不用 `preexec_fn=os.setsid`（Unix 方案）+ `subprocess.CREATE_NEW_PROCESS_GROUP`（Windows 方案）的分支逻辑？**

修正版已同时实现了两个平台的分支逻辑：
- Unix: `preexec_fn=os.setsid` 创建进程组 → `proc.terminate()` (SIGTERM) → `os.killpg()` (SIGKILL)
- Windows: `CREATE_NEW_PROCESS_GROUP` → `taskkill /T /F` 终止进程树

通过 `_is_windows()` 在运行时自动选择正确的实现，同时保持对外接口一致。

## 4. 验收标准

1. **FFmpeg 调用非阻塞**：`media_normalizer.py` 中 `subprocess.run` 替换为基于 `Popen` + `communicate(timeout=...)` 的实现。
2. **优雅终止 + 强制终止**：
   - Unix：超时后先 `proc.terminate()` (SIGTERM)，等待 `graceful_timeout` 秒后仍未退出则 `os.killpg()` (SIGKILL)。
   - Windows：超时后直接 `taskkill /T /F /PID` 终止进程树（Windows 无优雅终止语义）。
3. **进程树清理**：FFmpeg 启动的子进程（如编码器）一并被终止，不留孤儿进程。
4. **中间文件清理**：FFmpeg 超时/失败后自动删除不完整的输出文件（`dst`），避免残留损坏的 `.mp4`。
5. **FileNotFoundError 处理**：FFmpeg 未安装时返回 `None`，与现有行为一致。
6. **输出文件存在性检查**：FFmpeg 返回码为 0 但输出文件不存在时返回 `None`。

## 5. 原始文档问题汇总

| # | 问题 | 严重程度 | 修正措施 |
|---|------|----------|----------|
| 1 | Windows 上 `proc.terminate()` 等效 `TerminateProcess`（强制终止），与"优雅终止"语义不符，文档未说明 | 高 | 明确注释 Windows 上无优雅终止，直接用 `taskkill /T /F` |
| 2 | 未处理 FFmpeg 子进程（编码器等）的终止，可能留下孤儿进程 | 高 | 新增 `_kill_process_tree()` 使用 `taskkill /T` 递归终止 |
| 3 | Unix 路径缺少 `preexec_fn=os.setsid`，无法用 `killpg` 终止进程组 | 高 | 补充 Unix 进程组创建 |
| 4 | 强制终止后 `proc.communicate()` 无超时，可能再次阻塞 | 中 | 改为 `proc.communicate(timeout=3)` |
| 5 | `graceful_timeout` 参数在 Windows 上无意义，文档未说明 | 中 | 补充说明 Windows 上此参数被忽略 |
| 6 | 改造代码未处理 `FileNotFoundError`（FFmpeg 未安装） | 中 | 补充 `try/except FileNotFoundError` |
| 7 | 改造代码未检查 `dst` 文件是否存在（FFmpeg 返回 0 但文件可能损坏） | 中 | 补充 `os.path.exists(dst)` 检查 |
| 8 | 未说明与需求 01 的 `subprocess.run(timeout=...)` 方案的冲突和合并策略 | 中 | 补充 3.4 节说明两者合并为本需求方案 |
| 9 | 使用 `print` 而非 `logging` 输出日志 | 低 | `media_normalizer.py` 所在目录统一使用 `print`，保持一致 |
