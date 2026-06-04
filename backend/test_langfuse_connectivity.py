"""Langfuse 连接测试 —— 验证鉴权、Trace 拉取、指标提取全链路。"""
import os
import sys
from pathlib import Path

# 加载 .env
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_path)

from langfuse import Langfuse

PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
BASE_URL = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

print("=" * 60)
print("Langfuse 连接诊断")
print("=" * 60)
print(f"  Base URL : {BASE_URL}")
print(f"  Public   : {PUBLIC_KEY[:30]}..." if PUBLIC_KEY else "  Public   : MISSING")
print(f"  Secret   : {'***' if SECRET_KEY else 'MISSING'}")

if not PUBLIC_KEY or not SECRET_KEY:
    print("\n[FAIL] 缺少 LANGFUSE_PUBLIC_KEY 或 LANGFUSE_SECRET_KEY")
    sys.exit(1)

# ---------- Step 1: 初始化客户端 ----------
print("\n[1] 初始化 Langfuse 客户端...")
try:
    client = Langfuse(
        public_key=PUBLIC_KEY,
        secret_key=SECRET_KEY,
        host=BASE_URL,
    )
    print("    OK — 客户端对象创建成功")
except Exception as e:
    print(f"    FAIL — {type(e).__name__}: {e}")
    sys.exit(1)

# ---------- Step 2: 鉴权检查 ----------
print("\n[2] 鉴权检查 (auth_check)...")
try:
    client.auth_check()
    print("    OK — 鉴权通过")
except Exception as e:
    print(f"    FAIL — {type(e).__name__}: {e}")
    sys.exit(1)

# ---------- Step 3: 健康检查 (REST API) ----------
print("\n[3] 健康检查 (GET /api/public/health)...")
import requests
try:
    r = requests.get(
        f"{BASE_URL}/api/public/health",
        auth=(PUBLIC_KEY, SECRET_KEY),
        timeout=10,
    )
    print(f"    status={r.status_code}  body={r.text[:200]}")
except Exception as e:
    print(f"    FAIL — {type(e).__name__}: {e}")

# ---------- Step 4: 获取项目列表 ----------
print("\n[4] 获取项目列表 (GET /api/public/projects)...")
try:
    r = requests.get(
        f"{BASE_URL}/api/public/projects",
        auth=(PUBLIC_KEY, SECRET_KEY),
        timeout=10,
    )
    if r.status_code == 200:
        projects = r.json().get("data", [])
        print(f"    OK — {len(projects)} 个项目: {[p['name'] for p in projects]}")
    else:
        print(f"    status={r.status_code}  body={r.text[:200]}")
except Exception as e:
    print(f"    FAIL — {type(e).__name__}: {e}")

# ---------- Step 5: 列出最近 Traces ----------
print("\n[5] 列出最近 Traces (GET /api/public/traces?limit=3)...")
recent_trace_ids = []
try:
    r = requests.get(
        f"{BASE_URL}/api/public/traces?limit=3&order_by=timestamp.desc",
        auth=(PUBLIC_KEY, SECRET_KEY),
        timeout=10,
    )
    if r.status_code == 200:
        data = r.json().get("data", [])
        for t in data:
            tid = t["id"]
            tname = t.get("name", "")[:40]
            tlatency = t.get("latency", "N/A")
            recent_trace_ids.append(tid)
            print(f"    {tid[:36]}...  name={tname}  latency={tlatency}s")
        if not data:
            print("    (空 — 该项目暂无 Trace 数据)")
    else:
        print(f"    status={r.status_code}  body={r.text[:200]}")
except Exception as e:
    print(f"    FAIL — {type(e).__name__}: {e}")

# ---------- Step 6: 拉取第一个 Trace 的详细信息 ----------
if recent_trace_ids:
    tid = recent_trace_ids[0]
    print(f"\n[6] 拉取 Trace 详情 (trace_id={tid[:36]}...)...")
    try:
        trace = client.api.trace.get(tid)
        total_tokens = 0
        total_cost = 0.0
        obs_count = 0
        if trace.observations:
            obs_count = len(trace.observations)
            for obs in trace.observations:
                if obs.usage:
                    total_tokens += obs.usage.total or 0
                if obs.calculated_total_cost is not None:
                    total_cost += float(obs.calculated_total_cost)
        print(f"    OK")
        print(f"       name     = {trace.name}")
        print(f"       latency  = {trace.latency}s")
        print(f"       observations = {obs_count}")
        print(f"       total_tokens = {total_tokens}")
        print(f"       total_cost   = ${total_cost:.6f}")
    except Exception as e:
        print(f"    FAIL — {type(e).__name__}: {e}")
else:
    print("\n[6] 跳过 (无可用 Trace 供拉取)")

# ---------- Step 7: 用 SDK 方式拉取 (fetch_trace_with_retry 模拟) ----------
if recent_trace_ids:
    tid = recent_trace_ids[0]
    print(f"\n[7] 模拟 fetch_trace_with_retry (3 次重试)...")
    import time
    for attempt in range(1, 4):
        try:
            trace = client.api.trace.get(tid)
            total_tokens = sum(
                obs.usage.total or 0
                for obs in (trace.observations or [])
                if obs.usage
            )
            total_cost = sum(
                float(obs.calculated_total_cost)
                for obs in (trace.observations or [])
                if obs.calculated_total_cost is not None
            )
            print(
                f"    attempt {attempt}: OK  tokens={total_tokens}  "
                f"latency={trace.latency}s  cost=${total_cost:.6f}"
            )
            break
        except Exception as e:
            print(f"    attempt {attempt}: FAIL — {type(e).__name__}: {e}")
            if attempt < 3:
                time.sleep(2)

print("\n" + "=" * 60)
print("诊断完成")
print("=" * 60)
