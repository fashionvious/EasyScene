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
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
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
            _kill_process_tree(proc.pid)
            was_force_killed = True
        else:
            import os
            # Unix: 先尝试 SIGTERM（优雅终止）
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=graceful_timeout)
                return {
                    "success": False,
                    "stdout": stdout or "",
                    "stderr": (stderr or "")
                    + f"\n[超时 {timeout}s，已优雅终止]",
                    "returncode": proc.returncode,
                    "timeout_expired": True,
                    "was_force_killed": False,
                }
            except subprocess.TimeoutExpired:
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
            "stderr": (stderr or "")
            + f"\n[超时 {timeout}s，{'已强制终止' if was_force_killed else '已优雅终止'}]",
            "returncode": proc.returncode,
            "timeout_expired": True,
            "was_force_killed": was_force_killed,
        }
