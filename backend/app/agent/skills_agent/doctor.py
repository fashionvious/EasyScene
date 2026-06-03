"""
jianying-agent doctor — 健康检查命令。

检查 9 项：Python 版本 / FFmpeg / 剪映 App / Redis / PostgreSQL /
磁盘空间 / Skill 根目录 / Langfuse / Feature Flags。

用法:
    python -m app.agent.skills_agent.doctor
    python -m app.agent.skills_agent.doctor --json --verbose
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CheckResult:
    name: str
    status: str       # "PASS" | "FAIL" | "WARN" | "SKIP"
    message: str
    detail: str = ""
    duration_ms: float = 0.0


CHECKS: list[dict[str, Any]] = []


def register_check(name: str, category: str):
    """装饰器：注册健康检查项"""
    def decorator(func):
        CHECKS.append({"name": name, "category": category, "func": func})
        return func
    return decorator


# ====================================================================
# 检查项
# ====================================================================

@register_check("Python 版本", "系统环境")
def check_python() -> CheckResult:
    t0 = time.time()
    v = sys.version_info
    ok = v >= (3, 11)
    return CheckResult(
        name="Python 版本",
        status="PASS" if ok else "FAIL",
        message=f"Python {v.major}.{v.minor}.{v.micro}",
        detail="需要 Python >= 3.11" if not ok else "",
        duration_ms=(time.time() - t0) * 1000,
    )


@register_check("FFmpeg", "系统环境")
def check_ffmpeg() -> CheckResult:
    t0 = time.time()
    path = shutil.which("ffmpeg")
    if path:
        return CheckResult(
            name="FFmpeg", status="PASS",
            message=f"已安装: {path}",
            duration_ms=(time.time() - t0) * 1000,
        )
    return CheckResult(
        name="FFmpeg", status="FAIL",
        message="未找到 ffmpeg",
        detail="请安装 FFmpeg 并添加到 PATH 环境变量",
        duration_ms=(time.time() - t0) * 1000,
    )


@register_check("剪映 App", "系统环境")
def check_jianying_app() -> CheckResult:
    t0 = time.time()
    try:
        import winreg
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\JianyingPro.exe"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                exe_path = winreg.QueryValue(key, None)
                return CheckResult(
                    name="剪映 App", status="PASS",
                    message=f"已安装: {exe_path}",
                    duration_ms=(time.time() - t0) * 1000,
                )
        except FileNotFoundError:
            pass
        # Fallback: 常见安装路径
        for p in (
            r"C:\Program Files\JianyingPro\JianyingPro.exe",
            r"D:\Program Files\JianyingPro\JianyingPro.exe",
        ):
            if os.path.exists(p):
                return CheckResult(
                    name="剪映 App", status="PASS",
                    message=f"已安装: {p}",
                    duration_ms=(time.time() - t0) * 1000,
                )
        return CheckResult(
            name="剪映 App", status="WARN",
            message="未检测到剪映专业版安装",
            detail="自动剪辑功能需要安装剪映专业版",
            duration_ms=(time.time() - t0) * 1000,
        )
    except ImportError:
        return CheckResult(
            name="剪映 App", status="SKIP",
            message="非 Windows 平台，跳过",
            duration_ms=(time.time() - t0) * 1000,
        )


@register_check("Redis 连接", "基础设施")
def check_redis() -> CheckResult:
    t0 = time.time()
    try:
        from app.agent.utils.redis import get_video_project_manager
        manager = get_video_project_manager()
        import asyncio
        loop = asyncio.new_event_loop()
        pong = loop.run_until_complete(manager.redis_client.ping())
        loop.close()
        duration = (time.time() - t0) * 1000
        if pong:
            return CheckResult(
                name="Redis 连接", status="PASS",
                message=f"连通 ({duration:.0f}ms)",
                duration_ms=duration,
            )
        return CheckResult(
            name="Redis 连接", status="FAIL",
            message="Ping 返回非预期值",
            duration_ms=duration,
        )
    except Exception as e:
        return CheckResult(
            name="Redis 连接", status="FAIL",
            message=str(e)[:80],
            detail="请检查 Redis 是否已启动 (localhost:6379)",
            duration_ms=(time.time() - t0) * 1000,
        )


@register_check("PostgreSQL 连接", "基础设施")
def check_postgresql() -> CheckResult:
    t0 = time.time()
    try:
        from app.core.db import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.scalar()
        duration = (time.time() - t0) * 1000
        return CheckResult(
            name="PostgreSQL 连接", status="PASS",
            message=f"连通 ({duration:.0f}ms)",
            detail=str(version)[:100],
            duration_ms=duration,
        )
    except Exception as e:
        return CheckResult(
            name="PostgreSQL 连接", status="FAIL",
            message=str(e)[:80],
            detail="请检查 PostgreSQL 是否已启动",
            duration_ms=(time.time() - t0) * 1000,
        )


@register_check("Skill 根目录完整性", "应用完整性")
def check_skill_root() -> CheckResult:
    t0 = time.time()
    skill_root = os.getenv("JY_SKILL_ROOT", "").strip()
    if not skill_root:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        skill_root = os.path.abspath(
            os.path.join(current_dir, "..", "..", "..", "jianying-editor-skill"),
        )

    checks = {
        "SKILL.md": os.path.exists(os.path.join(skill_root, "SKILL.md")),
        "scripts/jy_wrapper.py": os.path.exists(
            os.path.join(skill_root, "scripts", "jy_wrapper.py"),
        ),
        "rules/": os.path.isdir(os.path.join(skill_root, "rules")),
    }

    all_ok = all(checks.values())
    failed = [k for k, v in checks.items() if not v]
    return CheckResult(
        name="Skill 根目录", status="PASS" if all_ok else "FAIL",
        message=f"路径: {skill_root}",
        detail=(
            f"核心文件完整 ({len(checks)} 项通过)"
            if all_ok else f"缺失: {failed}"
        ),
        duration_ms=(time.time() - t0) * 1000,
    )


@register_check("磁盘空间", "系统环境")
def check_disk_space() -> CheckResult:
    t0 = time.time()
    usage = shutil.disk_usage(os.getcwd())
    free_gb = usage.free / (1024 ** 3)
    ok = free_gb >= 1.0
    return CheckResult(
        name="磁盘空间", status="PASS" if ok else "WARN",
        message=f"剩余 {free_gb:.1f} GB",
        detail="磁盘空间不足 1GB" if not ok else "",
        duration_ms=(time.time() - t0) * 1000,
    )


@register_check("Langfuse 配置", "可观测性")
def check_langfuse() -> CheckResult:
    t0 = time.time()
    secret = os.getenv("LANGFUSE_SECRET_KEY")
    public = os.getenv("LANGFUSE_PUBLIC_KEY")
    if secret and public:
        return CheckResult(
            name="Langfuse", status="PASS",
            message="已配置",
            detail=f"Host: {os.getenv('LANGFUSE_HOST', 'cloud.langfuse.com')}",
            duration_ms=(time.time() - t0) * 1000,
        )
    return CheckResult(
        name="Langfuse", status="SKIP",
        message="未配置（可观测性将静默降级）",
        detail="设置 LANGFUSE_SECRET_KEY 和 LANGFUSE_PUBLIC_KEY 以启用",
        duration_ms=(time.time() - t0) * 1000,
    )


@register_check("Feature Flags", "配置")
def check_feature_flags() -> CheckResult:
    t0 = time.time()
    try:
        from app.agent.skills_agent.feature_flags import EditFeatureFlags
        all_flags = EditFeatureFlags.dump_all()
        non_default = {
            k: v for k, v in all_flags.items()
            if v["value"] != v["default"]
        }
        return CheckResult(
            name="Feature Flags", status="PASS",
            message=(
                f"共 {len(all_flags)} 个 Flag, "
                f"{len(non_default)} 个非默认值"
            ),
            detail=(
                "\n".join(
                    f"  {k}: {v['value']} (默认: {v['default']})"
                    for k, v in non_default.items()
                ) if non_default else "所有 Flag 使用默认值"
            ),
            duration_ms=(time.time() - t0) * 1000,
        )
    except Exception as e:
        return CheckResult(
            name="Feature Flags", status="SKIP",
            message=str(e)[:80],
            duration_ms=(time.time() - t0) * 1000,
        )


# ====================================================================
# 核心函数
# ====================================================================

def run_all_checks() -> list[CheckResult]:
    """执行所有注册的检查项（单项失败不影响其他检查）。"""
    results: list[CheckResult] = []
    for check_def in CHECKS:
        try:
            result = check_def["func"]()
        except Exception as e:
            result = CheckResult(
                name=check_def["name"], status="FAIL",
                message=f"检查执行异常: {e}",
            )
        results.append(result)
    return results


def format_doctor_output(
    results: list[CheckResult], verbose: bool = False,
) -> str:
    """格式化 doctor 输出为可读报告。"""
    status_icons = {"PASS": "v", "FAIL": "x", "WARN": "!", "SKIP": "o"}

    # Group by category
    categories: dict[str, list[CheckResult]] = {}
    for check_def, result in zip(CHECKS, results):
        cat = check_def["category"]
        categories.setdefault(cat, []).append(result)

    lines = [
        "=" * 60,
        "  jianying-agent doctor — 健康检查报告",
        "=" * 60,
        "",
    ]

    for cat, cat_results in categories.items():
        lines.append(f"-- {cat} --")
        for r in cat_results:
            icon = status_icons.get(r.status, "?")
            dur = f" ({round(r.duration_ms)}ms)" if r.duration_ms > 0 else ""
            lines.append(f"  [{icon}] {r.name}: {r.message}{dur}")
            if verbose and r.detail:
                lines.append(f"       {r.detail}")
        lines.append("")

    pass_n = sum(1 for r in results if r.status == "PASS")
    fail_n = sum(1 for r in results if r.status == "FAIL")
    warn_n = sum(1 for r in results if r.status == "WARN")

    lines.append(
        f"Total: {len(results)} | PASS={pass_n} | FAIL={fail_n} | WARN={warn_n}"
    )
    if fail_n > 0:
        lines.append(f"\n  {fail_n} 项检查失败，请解决后再使用 Agent。")
    if warn_n > 0:
        lines.append(f"  {warn_n} 项警告，Agent 功能可能受限。")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.agent.skills_agent.doctor",
        description="jianying-agent 健康检查",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="显示详细信息",
    )
    parser.add_argument(
        "--json", action="store_true", help="JSON 格式输出（CI/自动化）",
    )

    args = parser.parse_args()
    results = run_all_checks()

    if args.json:
        print(json.dumps(
            [
                {
                    "name": r.name, "status": r.status,
                    "message": r.message, "detail": r.detail,
                    "duration_ms": r.duration_ms,
                }
                for r in results
            ],
            ensure_ascii=False, indent=2,
        ))
    else:
        print(format_doctor_output(results, verbose=args.verbose))

    fail_count = sum(1 for r in results if r.status == "FAIL")
    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
