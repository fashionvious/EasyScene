# 需求 22：jianying-agent doctor 健康检查命令

> 原 PRD 编号: P2-6 | 优先级: P2

## 1. 依赖关系

- **前置依赖**：req_04（PG 数据模型）、req_16（Redis EditTaskState）、req_21（Feature Flag — dump 所有 Flag 状态）
- **被谁依赖**：无（运维工具，独立于其他功能需求）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库有一个 `api_validator.py`（[CLI 脚本](../../../backend/jianying-editor-skill/scripts/api_validator.py)）用于基础环境诊断，但功能有限。

**doctor 命令的检查清单**（PRD 定义）：

| 检查项 | 来源 | 检查方式 |
|--------|------|---------|
| Python 版本 | `sys.version` | ≥ 3.11 |
| FFmpeg 可用性 | `shutil.which("ffmpeg")` | 存在且可执行 |
| 剪映 App 安装 | Windows 注册表 / 路径探测 | `uiautomation` 可连接 |
| Redis 连接 | `redis.ping()` | 连通 + 响应时间 |
| PostgreSQL 连接 | `engine.connect()` | 连通 + 版本查询 |
| 磁盘空间 | `shutil.disk_usage` | 剩余 > 1GB |
| jianying-editor-skill 完整性 | 文件系统 | `SKILL.md` + `scripts/jy_wrapper.py` 存在 |
| Langfuse 配置 | 环境变量 | `LANGFUSE_SECRET_KEY` + `LANGFUSE_PUBLIC_KEY` |
| Celery Worker 状态 | `celery.control.inspect().ping()` | Worker 在线 |
| Feature Flag 状态 | `EditFeatureFlags.dump_all()` | 所有 Flag 当前值 |

### 代码库校验结论

- `api_validator.py` 已存在但仅检查基础 Python 环境，不够全面
- `media_resolver.py` 已有搜索路径探测模式（`rglob`），可用于检查 `SKILL.md` 和 `jy_wrapper.py`
- Redis 检查可复用 `utils/redis.py` 的 `VideoProjectRedisManager.redis_client`
- PG 检查使用项目已有的 `app.core.db.engine`

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `backend/app/agent/skills_agent/doctor.py` | 健康检查命令完整实现 |

### 核心技术细节

```python
"""doctor.py — jianying-agent 健康检查命令"""
import os
import sys
import shutil
import time
import argparse
from dataclasses import dataclass, field
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


# ---- 检查项实现 ----

@register_check("Python 版本", "系统环境")
def check_python() -> CheckResult:
    t0 = time.time()
    version = sys.version_info
    ok = version >= (3, 11)
    return CheckResult(
        name="Python 版本",
        status="PASS" if ok else "FAIL",
        message=f"Python {version.major}.{version.minor}.{version.micro}",
        detail="需要 Python >= 3.11" if not ok else "",
        duration_ms=(time.time() - t0) * 1000,
    )


@register_check("FFmpeg", "系统环境")
def check_ffmpeg() -> CheckResult:
    t0 = time.time()
    path = shutil.which("ffmpeg")
    if path:
        return CheckResult(
            name="FFmpeg",
            status="PASS",
            message=f"已安装: {path}",
            duration_ms=(time.time() - t0) * 1000,
        )
    return CheckResult(
        name="FFmpeg",
        status="FAIL",
        message="未找到 ffmpeg",
        detail="请安装 FFmpeg 并添加到 PATH 环境变量",
        duration_ms=(time.time() - t0) * 1000,
    )


@register_check("剪映 App", "系统环境")
def check_jianying_app() -> CheckResult:
    t0 = time.time()
    try:
        import uiautomation as auto
        # 尝试查找剪映窗口
        jy_window = auto.WindowControl(
            searchDepth=1, ClassName="JianyingPro", SubName="剪映专业版"
        )
        # 不强制要求剪映正在运行——检查已安装即可
        # 更好的方式：检查注册表
        import winreg
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\JianyingPro.exe"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                exe_path = winreg.QueryValue(key, None)
                return CheckResult(
                    name="剪映 App",
                    status="PASS",
                    message=f"已安装: {exe_path}",
                    duration_ms=(time.time() - t0) * 1000,
                )
        except FileNotFoundError:
            pass
        # 如果注册表也没找到，检查常见安装路径
        common_paths = [
            r"C:\Program Files\JianyingPro\JianyingPro.exe",
            r"D:\Program Files\JianyingPro\JianyingPro.exe",
        ]
        for p in common_paths:
            if os.path.exists(p):
                return CheckResult(
                    name="剪映 App",
                    status="PASS",
                    message=f"已安装: {p}",
                    duration_ms=(time.time() - t0) * 1000,
                )
        return CheckResult(
            name="剪映 App",
            status="WARN",
            message="未检测到剪映专业版安装",
            detail="自动剪辑功能需要安装剪映专业版",
            duration_ms=(time.time() - t0) * 1000,
        )
    except ImportError:
        return CheckResult(
            name="剪映 App",
            status="WARN",
            message="uiautomation 未安装，跳过检测",
            duration_ms=(time.time() - t0) * 1000,
        )


@register_check("Redis 连接", "基础设施")
def check_redis() -> CheckResult:
    t0 = time.time()
    try:
        from app.agent.utils.redis import get_video_project_manager
        manager = get_video_project_manager()
        # 简化检查：尝试 ping
        import asyncio
        async def _ping():
            return await manager.redis_client.ping()
        pong = asyncio.get_event_loop().run_until_complete(_ping())
        duration = (time.time() - t0) * 1000
        if pong:
            return CheckResult(
                name="Redis 连接",
                status="PASS",
                message=f"连通 (响应: {duration:.0f}ms)",
                duration_ms=duration,
            )
        return CheckResult(
            name="Redis 连接",
            status="FAIL",
            message="Ping 返回非预期值",
            duration_ms=duration,
        )
    except Exception as e:
        return CheckResult(
            name="Redis 连接",
            status="FAIL",
            message=str(e),
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
            name="PostgreSQL 连接",
            status="PASS",
            message=f"连通 (响应: {duration:.0f}ms)",
            detail=str(version)[:100],
            duration_ms=duration,
        )
    except Exception as e:
        return CheckResult(
            name="PostgreSQL 连接",
            status="FAIL",
            message=str(e),
            detail="请检查 PostgreSQL 是否已启动",
            duration_ms=(time.time() - t0) * 1000,
        )


@register_check("Skill 根目录完整性", "应用完整性")
def check_skill_root() -> CheckResult:
    t0 = time.time()
    skill_root = os.getenv("JY_SKILL_ROOT", "").strip()
    if not skill_root:
        # 自动探测
        current_dir = os.path.dirname(os.path.abspath(__file__))
        skill_root = os.path.abspath(
            os.path.join(current_dir, "..", "..", "..", "jianying-editor-skill")
        )

    checks = {
        "SKILL.md": os.path.exists(os.path.join(skill_root, "SKILL.md")),
        "scripts/jy_wrapper.py": os.path.exists(
            os.path.join(skill_root, "scripts", "jy_wrapper.py")
        ),
        "rules/": os.path.isdir(os.path.join(skill_root, "rules")),
    }

    all_ok = all(checks.values())
    failed = [k for k, v in checks.items() if not v]
    return CheckResult(
        name="Skill 根目录",
        status="PASS" if all_ok else "FAIL",
        message=f"路径: {skill_root}",
        detail=f"缺失: {failed}" if failed else f"核心文件完整 ({len(checks)} 项检查通过)",
        duration_ms=(time.time() - t0) * 1000,
    )


@register_check("Langfuse 配置", "可观测性")
def check_langfuse() -> CheckResult:
    t0 = time.time()
    secret = os.getenv("LANGFUSE_SECRET_KEY")
    public = os.getenv("LANGFUSE_PUBLIC_KEY")
    if secret and public:
        return CheckResult(
            name="Langfuse",
            status="PASS",
            message="已配置",
            detail=f"Host: {os.getenv('LANGFUSE_HOST', 'cloud.langfuse.com')}",
            duration_ms=(time.time() - t0) * 1000,
        )
    return CheckResult(
        name="Langfuse",
        status="SKIP",
        message="未配置（可观测性将静默降级）",
        detail="设置 LANGFUSE_SECRET_KEY 和 LANGFUSE_PUBLIC_KEY 以启用",
        duration_ms=(time.time() - t0) * 1000,
    )


@register_check("磁盘空间", "系统环境")
def check_disk_space() -> CheckResult:
    t0 = time.time()
    usage = shutil.disk_usage(os.getcwd())
    free_gb = usage.free / (1024 ** 3)
    ok = free_gb >= 1.0
    return CheckResult(
        name="磁盘空间",
        status="PASS" if ok else "WARN",
        message=f"剩余 {free_gb:.1f} GB",
        detail="磁盘空间不足 1GB" if not ok else "",
        duration_ms=(time.time() - t0) * 1000,
    )


@register_check("Feature Flags", "配置")
def check_feature_flags() -> CheckResult:
    t0 = time.time()
    try:
        from feature_flags import EditFeatureFlags
        all_flags = EditFeatureFlags.dump_all()
        non_default = {
            k: v for k, v in all_flags.items()
            if v["value"] != v["default"]
        }
        return CheckResult(
            name="Feature Flags",
            status="PASS",
            message=f"共 {len(all_flags)} 个 Flag, {len(non_default)} 个非默认值",
            detail="\n".join(
                f"  {k}: {v['value']} (默认: {v['default']})"
                for k, v in non_default.items()
            ) if non_default else "所有 Flag 使用默认值",
            duration_ms=(time.time() - t0) * 1000,
        )
    except Exception as e:
        return CheckResult(
            name="Feature Flags",
            status="SKIP",
            message=str(e),
            duration_ms=(time.time() - t0) * 1000,
        )


# ---- 主入口 ----

def run_all_checks() -> list[CheckResult]:
    """执行所有注册的检查项"""
    results: list[CheckResult] = []
    for check_def in CHECKS:
        try:
            result = check_def["func"]()
            results.append(result)
        except Exception as e:
            results.append(CheckResult(
                name=check_def["name"],
                status="FAIL",
                message=f"检查执行异常: {e}",
            ))
    return results


def format_doctor_output(results: list[CheckResult], verbose: bool = False) -> str:
    """格式化 doctor 输出"""
    status_icons = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠", "SKIP": "○"}
    lines = [
        "╔══════════════════════════════════════════════════════════╗",
        "║         jianying-agent doctor — 健康检查报告              ║",
        "╚══════════════════════════════════════════════════════════╝",
        "",
    ]

    # 按 category 分组
    categories: dict[str, list[CheckResult]] = {}
    for check_def, result in zip(CHECKS, results):
        cat = check_def["category"]
        categories.setdefault(cat, []).append(result)

    for cat, cat_results in categories.items():
        lines.append(f"── {cat} ──")
        for r in cat_results:
            icon = status_icons.get(r.status, "?")
            lines.append(
                f"  {icon} {r.name}: {r.message}"
                f"{'  (' + str(round(r.duration_ms)) + 'ms)' if r.duration_ms > 0 else ''}"
            )
            if verbose and r.detail:
                lines.append(f"     └ {r.detail}")
        lines.append("")

    # 汇总
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    warn_count = sum(1 for r in results if r.status == "WARN")

    lines.append(f"总计: {len(results)} 项 | "
                 f"PASS={pass_count} | FAIL={fail_count} | WARN={warn_count}")
    if fail_count > 0:
        lines.append(f"\n  {fail_count} 项检查失败，请解决后再使用 Agent。")
    if warn_count > 0:
        lines.append(f"  {warn_count} 项警告，Agent 功能可能受限。")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        prog="python -m skills_agent.doctor",
        description="jianying-agent 健康检查",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="显示详细信息",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="JSON 格式输出（用于 CI/自动化）",
    )
    args = parser.parse_args()

    results = run_all_checks()

    if args.json:
        import json
        print(json.dumps([
            {
                "name": r.name,
                "status": r.status,
                "message": r.message,
                "detail": r.detail,
                "duration_ms": r.duration_ms,
            }
            for r in results
        ], ensure_ascii=False, indent=2))
    else:
        print(format_doctor_output(results, verbose=args.verbose))

    # 退出码：有 FAIL 则非零
    fail_count = sum(1 for r in results if r.status == "FAIL")
    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
```

### 容错与边界

- 每个检查项独立 try/except，单个检查失败不影响其他检查
- 检查项不可用时标记为 SKIP（而非 FAIL），如 Langfuse 未配置、Feature Flags 模块不存在
- Redis 和 PG 检查使用已有的连接管理（不创建新连接）
- doctor 命令的退出码：有 FAIL → exit 1，全部 PASS/WARN/SKIP → exit 0（CI 友好）
- `--verbose` 模式显示每个检查的 detail 字段
- `--json` 模式输出机器可读格式（CI/Grafana 集成）

## 4. 验收标准 (DoD)

- [ ] `python -m skills_agent.doctor` 输出 10 项以上的健康检查报告
- [ ] Python 版本、FFmpeg、Skill 根目录检查 ≥ PASS
- [ ] Redis 连接失败时标记 FAIL（而非崩溃）
- [ ] PostgreSQL 连接失败时标记 FAIL（而非崩溃）
- [ ] 所有 FAIL 项 > 0 时退出码为 1
- [ ] `--json` 输出合法 JSON 格式
- [ ] `--verbose` 输出每个检查的 detail 详情
- [ ] Feature Flags 检查显示非默认 Flag 的当前值
