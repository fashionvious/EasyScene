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

## 3. 技术实现方案

### 3.1 Windows 进程管理基础

Windows 上替代 Unix 进程组管理的方案：

```python
import subprocess
import signal
import time
import os

# Windows 等效操作：
# 1. 创建进程组 → creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
# 2. 优雅终止 → 发送 CTRL_BREAK_EVENT（等效 SIGTERM）
# 3. 强制终止 → taskkill /T /F /PID <pid>
```

### 3.2 新增通用进程执行工具

在 `backend/jianying-editor-skill/scripts/utils/` 下新建 `process_utils.py`：

```python
"""跨平台进程执行工具 —— Windows 兼容"""
import subprocess
import signal
import time
import sys
from pathlib import Path
from typing import Any


def _is_windows() -> bool:
    return sys.platform == "win32"


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
        graceful_timeout: 优雅终止等待时间（秒），超时后强制终止
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
    popen_kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }

    if cwd:
        popen_kwargs["cwd"] = str(cwd)

    if _is_windows():
        # Windows: 创建新进程组（CREATE_NEW_PROCESS_GROUP = 0x00000200）
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

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
        # 阶段 1: 优雅终止
        was_force_killed = False

        if _is_windows():
            # Windows: 发送 CTRL_BREAK_EVENT 到进程组
            # 注意：CTRL_BREAK_EVENT 只能发送到当前控制台的进程组
            # 更可靠的做法是直接用 taskkill
            proc.terminate()  # Windows 上等效于 TerminateProcess
        else:
            proc.terminate()  # Unix: 发送 SIGTERM

        try:
            stdout, stderr = proc.communicate(timeout=graceful_timeout)
        except subprocess.TimeoutExpired:
            # 阶段 2: 强制终止
            was_force_killed = True
            if _is_windows():
                # Windows 强制终止: taskkill /T /F
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                )
            else:
                proc.kill()  # Unix: 发送 SIGKILL

            stdout, stderr = proc.communicate()

        return {
            "success": False,
            "stdout": (stdout or "") if stdout else "",
            "stderr": (stderr or "") + f"\n[超时 {timeout}s，{'已强制终止' if was_force_killed else '已优雅终止'}]",
            "returncode": proc.returncode,
            "timeout_expired": True,
            "was_force_killed": was_force_killed,
        }
```

### 3.3 改造 media_normalizer.py

```python
# 改造前（第 69-76 行）
try:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
except FileNotFoundError:
    ...

# 改造后
from utils.process_utils import run_with_timeout

result = run_with_timeout(cmd, timeout=120, graceful_timeout=5)

if not result["success"]:
    err = result["stderr"].strip()
    if result["timeout_expired"]:
        print(f"FFmpeg 超时（120s），{'已强制终止' if result['was_force_killed'] else '已终止'}")
        # 清理不完整的输出文件
        if os.path.exists(dst):
            os.unlink(dst)
    else:
        print(f"FFmpeg 失败 (returncode={result['returncode']}): {err[:500]}")
    return None

return dst
```

### 3.4 设计方案选择说明

**为什么不用 `preexec_fn=os.setsid`（Unix 方案）+ `subprocess.CREATE_NEW_PROCESS_GROUP`（Windows 方案）的分支逻辑？**

因为原始需求文档仅覆盖了 Unix 路径，而本项目明确运行在 Windows 上。`process_utils.py` 的设计通过 `_is_windows()` 在运行时自动选择正确的实现，同时保持对外接口一致。这确保了代码在两种平台都能正确运行。

## 4. 验收标准

1. **FFmpeg 调用非阻塞**：`media_normalizer.py` 中 `subprocess.run` 替换为基于 `Popen` + `communicate(timeout=...)` 的实现。
2. **优雅终止 + 强制终止**：超时后先尝试 `proc.terminate()`，等待 5 秒后仍未退出则用 `taskkill /T /F /PID` (Windows) 或 `proc.kill()` (Unix) 强制终止。
3. **中间文件清理**：FFmpeg 超时/失败后自动删除不完整的输出文件（`dst`），避免残留损坏的 `.mp4`。
