"""
剪辑 Agent 自动化测试运行器

从 剪辑测试用例.md 解析测试用例，逐个调用后端 Agent API，
收集响应结果并通过 Langfuse 拉取全维度性能指标，
最终生成 JSON + Markdown 报告。
"""

import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 加载 .env（先显式加载，确保 os.getenv 能读到 Langfuse 配置）
# ---------------------------------------------------------------------------
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

from langfuse import Langfuse  # noqa: E402

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_runner")

# ============================================================================
# 常量
# ============================================================================
API_V1 = os.getenv("BACKEND_API_URL", "http://localhost:8000/api/v1")
LOGIN_URL = f"{API_V1}/login/access-token"
CHAT_URL = f"{API_V1}/videoagent/chat/stream"
CONVERSATIONS_URL = f"{API_V1}/videoagent/conversations"
SCRIPT_ID = "1e445140-78a4-4406-ae26-c17860b2b473"
REQUEST_TIMEOUT = 120
INTER_TEST_DELAY = 2

SUPERUSER_EMAIL = "56837527@qq.com"
SUPERUSER_PASSWORD = "wulisen666"

MD_PATH = Path(__file__).resolve().parent.parent / "docs" / "剪辑测试用例.md"

# Langfuse 拉取重试配置
LANGFUSE_MAX_RETRIES = 3
LANGFUSE_RETRY_DELAY = 2.5  # 秒

AUTH_HEADERS: dict[str, str] = {}

# ============================================================================
# Langfuse 客户端
# ============================================================================

_langfuse_client: Langfuse | None = None


def get_langfuse_client() -> Langfuse | None:
    """懒初始化 Langfuse 客户端，从环境变量读取配置。

    用于 auth_check 等辅助功能；Trace 数据拉取通过 REST API (requests) 完成。

    设计说明：Langfuse SDK 底层 httpx 在本机代理环境 (127.0.0.1:7993) 下
    TLS 握手超时，而 requests (urllib3) 可正常工作。两者调用的都是同一个
    Langfuse REST API，数据完全一致，不存在功能差异。
    """
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    base_url = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        logger.warning("Langfuse 未配置，将跳过 trace 拉取")
        return None

    try:
        _langfuse_client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=base_url,
        )
        logger.info("Langfuse 客户端初始化成功 → %s", base_url)
        return _langfuse_client
    except Exception as e:
        logger.warning("Langfuse 客户端初始化失败: %s", e)
        return None


def _fetch_trace_by_session_id(
    session_id: str,
    base_url: str,
    public_key: str,
    secret_key: str,
    timeout: int = 15,
) -> dict[str, Any] | None:
    """通过 Langfuse REST API (requests) 按 session_id 搜索 Trace 详情。

    流程：
    1. GET /api/public/traces?session_id=xxx → 搜索匹配 trace_id
    2. GET /api/public/traces/{trace_id} → 拉取完整详情（含 observations/usage）

    使用 requests (urllib3) 而非 SDK 的 httpx，因为本机代理环境下
    httpx 的 TLS 握手会超时，而 urllib3 的代理实现可正常工作。

    Returns: {
        "total_tokens": int,
        "backend_latency": float | None,
        "total_cost": float | None,
    } or None if not found
    """
    # Step 1: 按 session_id 搜索 trace
    resp = requests.get(
        f"{base_url}/api/public/traces",
        params={"session_id": session_id, "limit": 3, "order_by": "timestamp.desc"},
        auth=(public_key, secret_key),
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    if not data:
        return None

    # Step 2: 取最新 trace_id，拉完整详情
    trace_id = data[0]["id"]
    resp2 = requests.get(
        f"{base_url}/api/public/traces/{trace_id}",
        auth=(public_key, secret_key),
        timeout=timeout,
    )
    resp2.raise_for_status()
    trace = resp2.json()

    # Step 3: 聚合指标
    total_tokens = 0
    total_cost = 0.0

    for obs in trace.get("observations", []) or []:
        if not isinstance(obs, dict):
            continue
        usage = obs.get("usage") or {}
        total_tokens += usage.get("total", 0) or 0
        calc_cost = obs.get("calculatedTotalCost") or obs.get("calculated_total_cost")
        if calc_cost is not None:
            total_cost += float(calc_cost)

    if total_tokens == 0:
        for obs in trace.get("observations", []) or []:
            if not isinstance(obs, dict):
                continue
            ud = obs.get("usageDetails") or obs.get("usage_details") or {}
            total_tokens += (ud.get("input", 0) or 0) + (ud.get("output", 0) or 0)

    backend_latency = None
    if trace.get("latency") is not None:
        backend_latency = round(float(trace["latency"]), 2)

    trace_total_cost = trace.get("totalCost") or trace.get("total_cost")
    if trace_total_cost is not None and total_cost == 0.0:
        total_cost = float(trace_total_cost)

    return {
        "total_tokens": total_tokens,
        "backend_latency": backend_latency,
        "total_cost": round(total_cost, 6) if total_cost else None,
    }


def fetch_trace_with_retry(
    conversation_id: str,
    max_retries: int = LANGFUSE_MAX_RETRIES,
    delay: float = LANGFUSE_RETRY_DELAY,
) -> dict[str, Any]:
    """通过 Langfuse REST API 按 session_id 搜索 Trace，带重试。

    流程（两阶段）：
    1. GET /api/public/traces?session_id=xxx → 定位 trace_id
    2. GET /api/public/traces/{trace_id} → 拉取完整详情 + 提取指标

    使用 requests (urllib3 代理自动检测)，避免 SDK 底层 httpx 的代理 TLS 问题。
    调用的就是 Langfuse REST API，与 SDK 功能等价。

    Returns:
        {"success": bool, "total_tokens": int|None, "backend_latency": float|None,
         "total_cost": float|None, "error": str|None}
    """
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    base_url = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        return {
            "success": False, "total_tokens": None,
            "backend_latency": None, "total_cost": None,
            "error": "Langfuse not configured",
        }

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            metrics = _fetch_trace_by_session_id(
                conversation_id, base_url, public_key, secret_key
            )
            if metrics is None:
                if attempt < max_retries:
                    logger.info(
                        "session %s 的 Trace 尚未到达 Langfuse (attempt %d/%d)，"
                        "等待 %.1fs...",
                        conversation_id[:8], attempt, max_retries, delay,
                    )
                    time.sleep(delay)
                    continue
                return {
                    "success": False, "total_tokens": None,
                    "backend_latency": None, "total_cost": None,
                    "error": f"No trace for session_id={conversation_id[:8]}...",
                }

            logger.info(
                "session %s 拉取成功 (attempt %d): tokens=%s latency=%s cost=%s",
                conversation_id[:8], attempt,
                metrics["total_tokens"], metrics["backend_latency"], metrics["total_cost"],
            )
            return {"success": True, "error": None, **metrics}

        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.warning(
                "session %s 拉取失败 (attempt %d/%d): %s",
                conversation_id[:8], attempt, max_retries, last_error,
            )
            if attempt < max_retries:
                time.sleep(delay)

    logger.error("session %s 拉取最终失败: %s", conversation_id[:8], last_error)
    return {
        "success": False, "total_tokens": None,
        "backend_latency": None, "total_cost": None,
        "error": last_error,
    }


# ============================================================================
# 原有逻辑
# ============================================================================


def login() -> str:
    resp = requests.post(
        LOGIN_URL,
        data={"username": SUPERUSER_EMAIL, "password": SUPERUSER_PASSWORD},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    return token


def parse_test_cases(md_text: str) -> list[dict]:
    cases = []
    current_id = None
    current_section = None
    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        header_match = re.match(r"^###\s+(P\d+-\d+)", line)
        if header_match:
            current_id = header_match.group(1)
            current_section = {}
            i += 1
            continue
        if current_id and line.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            rows = []
            for tl in table_lines:
                if re.match(r"^\|[\s\-|]+\|$", tl):
                    continue
                cells = [c.strip() for c in tl.split("|")[1:-1]]
                if len(cells) >= 2:
                    rows.append(cells)
            for row in rows:
                if len(row) >= 2:
                    key = re.sub(r"\*\*", "", row[0]).strip()
                    val = row[1].strip()
                    current_section[key] = val
            if "用户提示词" in current_section:
                prompt = current_section["用户提示词"]
                prompt = re.sub(r'^["""]|["""]$', "", prompt)
                prompt = prompt.strip()
                current_section["用户提示词"] = prompt
            cases.append(
                {
                    "id": current_id,
                    "测试场景": current_section.get("测试场景", ""),
                    "用户提示词": current_section.get("用户提示词", ""),
                    "预期行为": current_section.get("预期行为", ""),
                }
            )
            current_id = None
            current_section = None
            continue
        i += 1
    return cases


def _has_tool_error(tool_results: list[dict]) -> bool:
    """检测 tool_results 中是否包含明显的报错信息。"""
    error_keywords = [
        "error", "Error", "ERROR",
        "fail", "Fail",
        "exception", "Exception",
        "traceback", "Traceback",
        "timeout", "Timeout",
        "aborted",
        "PermissionError",
        "FileNotFoundError",
    ]
    for tr in tool_results:
        result_str = str(tr.get("result", ""))
        for kw in error_keywords:
            if kw in result_str:
                return True
    return False


def call_agent_stream(prompt: str, conversation_id: str | None = None) -> dict:
    session_id = conversation_id or str(uuid.uuid4())
    payload = {
        "message": prompt,
        "script_id": SCRIPT_ID,
        "conversation_id": session_id,
    }
    result = {
        "conversation_id": session_id,
        "text_response": "",
        "tool_calls": [],
        "tool_results": [],
        "error": None,
    }
    try:
        resp = requests.post(
            CHAT_URL,
            json=payload,
            headers=AUTH_HEADERS,
            stream=True,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 401:
            result["error"] = "401 Unauthorized - token 可能已过期"
            return result
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text[:500])
            except Exception:
                detail = resp.text[:500]
            result["error"] = f"{resp.status_code} Error: {detail}"
            return result
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            if raw_line.startswith("data:"):
                data_str = raw_line[5:].strip()
                if not data_str:
                    continue
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                evt_type = event.get("type", "")
                if evt_type == "text":
                    result["text_response"] += event.get("content", "")
                elif evt_type == "thinking":
                    pass
                elif evt_type == "tool_call":
                    result["tool_calls"].append(
                        {
                            "tool_name": event.get("tool_name", ""),
                            "tool_args": event.get("tool_args", {}),
                        }
                    )
                elif evt_type == "tool_result":
                    result["tool_results"].append(
                        {
                            "tool_name": event.get("tool_name", ""),
                            "result": str(event.get("result", ""))[:2000],
                        }
                    )
                elif evt_type == "error":
                    result["error"] = event.get("content", "unknown error")
                elif evt_type == "done":
                    result["conversation_id"] = event.get("conversation_id", session_id)
    except requests.exceptions.Timeout:
        result["error"] = f"Timeout after {REQUEST_TIMEOUT}s"
    except requests.exceptions.ConnectionError:
        result["error"] = "ConnectionError: 后端服务未启动或不可达"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


# ============================================================================
# 测试执行
# ============================================================================


def run_all_tests(cases: list[dict]) -> list[dict]:
    results = []
    total = len(cases)
    global_total_tokens = 0
    global_total_cost = 0.0

    for idx, case in enumerate(cases):
        case_id = case["id"]
        prompt = case["用户提示词"]
        print(f"\n[{idx + 1}/{total}] {case_id}: {case['测试场景'][:40]}...")
        print(f"  提示词: {prompt[:80]}...")

        conversation_id = str(uuid.uuid4())
        t0 = time.time()
        agent_result = call_agent_stream(prompt, conversation_id)
        elapsed = time.time() - t0

        # ---- 提取 Agent 侧指标 ----
        tool_names = [tc["tool_name"] for tc in agent_result["tool_calls"]]
        steps = len(agent_result["tool_calls"])
        has_stream_error = agent_result["error"] is not None
        has_tool_error = _has_tool_error(agent_result["tool_results"])
        tool_error_rate = None
        if steps > 0:
            tool_error_count = sum(
                1 for tr in agent_result["tool_results"] if _has_tool_error([tr])
            )
            tool_error_rate = round(tool_error_count / steps, 4)

        # ---- 拉取 Langfuse Trace 指标 ----
        trace_metrics = fetch_trace_with_retry(conversation_id)
        total_tokens = trace_metrics["total_tokens"]
        backend_latency = trace_metrics["backend_latency"]
        total_cost = trace_metrics["total_cost"]
        langfuse_error = trace_metrics["error"]

        if total_tokens is not None:
            global_total_tokens += total_tokens
        if total_cost is not None:
            global_total_cost += total_cost

        result = {
            "id": case_id,
            "测试场景": case["测试场景"],
            "用户提示词": prompt,
            "预期行为": case["预期行为"],
            "conversation_id": agent_result["conversation_id"],
            "agent_response": agent_result["text_response"],
            "tool_calls": agent_result["tool_calls"],
            "tool_results": agent_result["tool_results"],
            "tools_called": tool_names,
            "error": agent_result["error"],
            "elapsed_seconds": round(elapsed, 2),
            # ---- 派生指标 ----
            "steps": steps,
            "has_stream_error": has_stream_error,
            "has_tool_error": has_tool_error,
            "tool_error_rate": tool_error_rate,
            # ---- Langfuse 指标 ----
            "langfuse_success": trace_metrics["success"],
            "langfuse_error": langfuse_error,
            "total_tokens": total_tokens if total_tokens is not None else "N/A",
            "backend_latency": backend_latency if backend_latency is not None else "N/A",
            "total_cost": total_cost if total_cost is not None else "N/A",
        }
        results.append(result)

        # ---- 控制台输出 ----
        status = "ERROR" if agent_result["error"] else "OK"
        token_str = f"{total_tokens}" if total_tokens is not None else "N/A"
        cost_str = f"${total_cost:.6f}" if total_cost is not None else "N/A"
        latency_str = f"{backend_latency}s" if backend_latency is not None else "N/A"
        print(
            f"  -> {status} | {elapsed:.1f}s | tools: {steps} | "
            f"tokens: {token_str} | cost: {cost_str} | "
            f"backend_latency: {latency_str}"
        )
        if langfuse_error:
            print(f"  [WARN] Langfuse: {langfuse_error}")

        if idx < total - 1:
            time.sleep(INTER_TEST_DELAY)

    # ---- 注入全局汇总 ----
    for r in results:
        r["_global_total_tokens"] = global_total_tokens
        r["_global_total_cost"] = round(global_total_cost, 6)

    return results


# ============================================================================
# 报告生成
# ============================================================================


def generate_json_report(results: list[dict], output_path: Path):
    ok_count = sum(1 for r in results if not r["error"])
    err_count = sum(1 for r in results if r["error"])

    global_tokens = results[0]["_global_total_tokens"] if results else 0
    global_cost = results[0]["_global_total_cost"] if results else 0.0

    # 清理内部字段
    clean_results = []
    for r in results:
        cr = {k: v for k, v in r.items() if not k.startswith("_")}
        clean_results.append(cr)

    report = {
        "generated_at": datetime.now().isoformat(),
        "total_cases": len(results),
        "summary": {
            "ok": ok_count,
            "error": err_count,
            "global_total_tokens": global_tokens,
            "global_total_cost": global_cost,
        },
        "results": clean_results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nJSON 报告已保存: {output_path}")


def generate_md_report(results: list[dict], output_path: Path):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ok_count = sum(1 for r in results if not r["error"])
    err_count = sum(1 for r in results if r["error"])
    total = len(results)

    global_tokens = results[0]["_global_total_tokens"] if results else 0
    global_cost = results[0]["_global_total_cost"] if results else 0.0

    p0 = [r for r in results if r["id"].startswith("P0")]
    p1 = [r for r in results if r["id"].startswith("P1")]
    p2 = [r for r in results if r["id"].startswith("P2")]

    lines = [
        "# 剪辑 Agent 自动化测试报告",
        "",
        f"> 生成时间: {now}",
        f"> 总用例: {total} | 成功: {ok_count} | 失败: {err_count}",
        f"> 全局 Token 消耗: {global_tokens} | 全局 Cost: ${global_cost:.6f}",
        "",
        "## 汇总",
        "",
        "| 级别 | 总数 | 成功 | 失败 |",
        "|------|------|------|------|",
    ]
    for label, group in [("P0", p0), ("P1", p1), ("P2", p2)]:
        g_ok = sum(1 for r in group if not r["error"])
        g_err = sum(1 for r in group if r["error"])
        lines.append(f"| {label} | {len(group)} | {g_ok} | {g_err} |")
    lines.append("")

    # 汇总 Token 与 Cost
    lines.append("## 性能概览")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|----|")
    lines.append(f"| 全局总 Token | {global_tokens} |")
    lines.append(f"| 全局总 Cost | ${global_cost:.6f} |")
    lines.append(f"| 平均 Token / 用例 | {global_tokens // total if total else 0} |")
    lines.append("")

    lines.append("## 详细结果")
    lines.append("")
    for r in results:
        status = "OK" if not r["error"] else "FAIL"
        lines.append(f"### [{status}] {r['id']}: {r['测试场景']}")
        lines.append("")
        lines.append(f"**提示词**: {r['用户提示词']}")
        lines.append("")
        lines.append(
            f"**预期行为**: {r['预期行为'][:200]}{'...' if len(r['预期行为']) > 200 else ''}"
        )
        lines.append("")
        lines.append(f"**耗时**: {r['elapsed_seconds']}s")
        lines.append("")

        # ---- 性能指标块 ----
        lines.append("**性能指标**:")
        lines.append("")
        lines.append(
            f"| Token | 后端延迟 | Cost | 执行步骤 | 工具报错率 |"
        )
        lines.append(
            f"|-------|---------|------|---------|-----------|"
        )
        tokens_display = (
            f"{r['total_tokens']}" if r["total_tokens"] != "N/A" else "N/A"
        )
        latency_display = (
            f"{r['backend_latency']}s" if r["backend_latency"] != "N/A" else "N/A"
        )
        cost_display = (
            f"${r['total_cost']:.6f}"
            if r["total_cost"] != "N/A"
            else "N/A"
        )
        error_rate_display = (
            f"{r['tool_error_rate']:.2%}"
            if r["tool_error_rate"] is not None
            else "N/A"
        )
        lines.append(
            f"| {tokens_display} | {latency_display} | {cost_display} | "
            f"{r['steps']} | {error_rate_display} |"
        )
        lines.append("")

        if r["tools_called"]:
            lines.append(f"**调用的工具**: {', '.join(r['tools_called'])}")
            lines.append("")
        if r["agent_response"]:
            resp = r["agent_response"][:500]
            lines.append("**Agent 回复**:")
            lines.append("```")
            lines.append(resp)
            lines.append("```")
            lines.append("")
        if r["tool_calls"]:
            lines.append("**工具调用详情**:")
            lines.append("```json")
            lines.append(
                json.dumps(r["tool_calls"], ensure_ascii=False, indent=2)[:1000]
            )
            lines.append("```")
            lines.append("")
        if r["error"]:
            lines.append(f"**错误**: {r['error']}")
            lines.append("")
        if r.get("langfuse_error"):
            lines.append(f"**Langfuse 警告**: {r['langfuse_error']}")
            lines.append("")
        lines.append("---")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Markdown 报告已保存: {output_path}")


# ============================================================================
# 主入口
# ============================================================================


def main():
    print("=" * 60)
    print("剪辑 Agent 自动化测试运行器 (Langfuse 集成)")
    print("=" * 60)

    if not MD_PATH.exists():
        print(f"错误: 找不到测试用例文件 {MD_PATH}")
        sys.exit(1)

    md_text = MD_PATH.read_text(encoding="utf-8")
    cases = parse_test_cases(md_text)
    if not cases:
        print("错误: 未解析到任何测试用例")
        sys.exit(1)

    print(f"\n解析到 {len(cases)} 个测试用例:")
    for c in cases:
        print(f"  {c['id']}: {c['测试场景'][:50]}")

    print("\n登录获取 access_token...")
    try:
        token = login()
        global AUTH_HEADERS
        AUTH_HEADERS = {"Authorization": f"Bearer {token}"}
        print(f"登录成功! token={token[:30]}...")
    except Exception as e:
        print(f"登录失败: {e}")
        print("请确认后端服务已启动且超级用户凭据正确")
        sys.exit(1)

    try:
        resp = requests.get(
            f"{CONVERSATIONS_URL}?script_id={SCRIPT_ID}",
            headers=AUTH_HEADERS,
            timeout=5,
        )
        print(f"后端连接测试: OK (status={resp.status_code})")
    except Exception as e:
        print(f"后端连接测试失败: {e}")
        print("请确认后端服务已启动 (http://localhost:8000)")
        sys.exit(1)

    # 预热 Langfuse 客户端
    lf_client = get_langfuse_client()
    if lf_client:
        try:
            lf_client.auth_check()
            print("Langfuse 连接测试: OK")
        except Exception as e:
            print(f"Langfuse 连接测试失败: {e} (trace 拉取将降级)")
    else:
        print("Langfuse: 未配置或不可用 (trace 拉取将降级)")

    print(f"\n开始执行测试 (每个用例独立 session, 间隔 {INTER_TEST_DELAY}s)...\n")
    results = run_all_tests(cases)

    ts = datetime.now().strftime("%Y%m%d")
    json_path = Path(__file__).parent / f"test_results_{ts}.json"
    md_report_path = Path(__file__).parent / f"test_results_{ts}.md"
    generate_json_report(results, json_path)
    generate_md_report(results, md_report_path)

    ok_count = sum(1 for r in results if not r["error"])
    err_count = sum(1 for r in results if r["error"])
    print(f"\n{'=' * 60}")
    print(
        f"测试完成! 总计 {len(results)} 个用例, 成功 {ok_count} 个, 失败 {err_count} 个"
    )
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
