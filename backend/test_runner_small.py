"""小型局部测试：P0/P1/P2 各跑 2 个用例 (Langfuse 集成验证)。"""
import json
import os
import sys
import time
import uuid

import requests

from test_runner import (
    login,
    parse_test_cases,
    call_agent_stream,
    fetch_trace_with_retry,
    _has_tool_error,
    AUTH_HEADERS,
    MD_PATH,
    INTER_TEST_DELAY,
)

BASE_URL = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")


def main():
    print("=" * 60)
    print("小型局部测试 (P0/P1/P2 各 2 个) + Langfuse Trace 拉取")
    print("=" * 60)

    # ---- Langfuse 连接验证 (用 requests, 不走 httpx) ----
    if not PUBLIC_KEY or not SECRET_KEY:
        print("Langfuse: 未配置, 退出")
        sys.exit(1)
    try:
        r = requests.get(
            f"{BASE_URL}/api/public/health",
            auth=(PUBLIC_KEY, SECRET_KEY),
            timeout=10,
        )
        if r.status_code == 200 and r.json().get("status") == "OK":
            print(f"Langfuse 连接验证: OK (version={r.json().get('version')})")
        else:
            print(f"Langfuse 连接验证: status={r.status_code}")
    except Exception as e:
        print(f"Langfuse 连接验证: FAIL — {type(e).__name__}: {e}")
        print("将继续测试，但 Trace 拉取会降级")

    # ---- 筛选用例 ----
    md_text = MD_PATH.read_text(encoding="utf-8")
    all_cases = parse_test_cases(md_text)
    p0 = [c for c in all_cases if c["id"].startswith("P0")][:2]
    p1 = [c for c in all_cases if c["id"].startswith("P1")][:2]
    p2 = [c for c in all_cases if c["id"].startswith("P2")][:2]
    cases = p0 + p1 + p2
    print(f"筛选出 {len(cases)} 个用例: {[c['id'] for c in cases]}")

    token = login()
    AUTH_HEADERS["Authorization"] = f"Bearer {token}"
    print(f"登录成功!")

    results = []
    for idx, case in enumerate(cases):
        case_id = case["id"]
        prompt = case["用户提示词"]
        print(f"\n[{'='*40}]")
        print(f"[{idx + 1}/{len(cases)}] {case_id}: {case['测试场景'][:40]}")
        print(f"  提示词: {prompt[:100]}...")

        conv_id = str(uuid.uuid4())
        t0 = time.time()
        ar = call_agent_stream(prompt, conv_id)
        elapsed = time.time() - t0

        steps = len(ar["tool_calls"])
        has_tool_error = _has_tool_error(ar["tool_results"])
        tool_error_rate = None
        if steps > 0:
            tool_error_count = sum(
                1 for tr in ar["tool_results"] if _has_tool_error([tr])
            )
            tool_error_rate = round(tool_error_count / steps, 4)

        print(f"  Agent 响应: {ar['text_response'][:120]}...")
        print(f"  拉取 Langfuse trace (session_id={conv_id[:8]}...)...")
        tm = fetch_trace_with_retry(conv_id)

        result = {
            "id": case_id,
            "场景": case["测试场景"],
            "conversation_id": conv_id,
            "elapsed": round(elapsed, 2),
            "steps": steps,
            "tools": [tc["tool_name"] for tc in ar["tool_calls"]],
            "has_tool_error": has_tool_error,
            "tool_error_rate": tool_error_rate,
            "total_tokens": tm["total_tokens"]
            if tm["total_tokens"] is not None
            else "N/A",
            "backend_latency": tm["backend_latency"]
            if tm["backend_latency"] is not None
            else "N/A",
            "total_cost": tm["total_cost"]
            if tm["total_cost"] is not None
            else "N/A",
            "langfuse_success": tm["success"],
            "langfuse_error": tm["error"],
        }

        status = "ERROR" if ar["error"] else "OK"
        print(
            f"  -> {status} | {elapsed:.1f}s | steps: {steps} | "
            f"tokens: {result['total_tokens']} | cost: {result['total_cost']} | "
            f"latency: {result['backend_latency']}"
        )
        if tm["error"]:
            print(f"  [WARN] Langfuse: {tm['error']}")
        results.append(result)

        if idx < len(cases) - 1:
            time.sleep(INTER_TEST_DELAY)

    print(f"\n{'='*60}")
    print("全部结果:")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
