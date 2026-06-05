"""
Langfuse 连接诊断脚本

验证 Langfuse 鉴权、Trace 拉取和指标提取的全链路。
用法:
    cd backend
    python test_langfuse_connectivity.py

需要配置的环境变量:
    LANGFUSE_PUBLIC_KEY
    LANGFUSE_SECRET_KEY
    LANGFUSE_BASE_URL  (默认 https://cloud.langfuse.com)
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

# 将 backend 加入路径，确保可以导入 app 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _check_env() -> dict:
    """检查必需的环境变量"""
    keys = {
        "LANGFUSE_PUBLIC_KEY": os.getenv("LANGFUSE_PUBLIC_KEY", ""),
        "LANGFUSE_SECRET_KEY": os.getenv("LANGFUSE_SECRET_KEY", ""),
        "LANGFUSE_BASE_URL": os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
    }
    return keys


def test_auth() -> tuple[bool, str]:
    """
    测试 Langfuse 鉴权。
    通过创建客户端并尝试拉取 traces 来验证密钥有效性。
    """
    from app.agent.skills_agent.observability import get_langfuse_client

    client = get_langfuse_client()
    if client is None:
        return False, "Langfuse 客户端初始化失败 — 请检查 LANGFUSE_SECRET_KEY 和 LANGFUSE_PUBLIC_KEY"

    try:
        # 尝试拉取最近的 1 条 trace 验证鉴权
        traces = client.fetch_traces(
            limit=1,
            from_timestamp=datetime.now(timezone.utc) - timedelta(days=7),
        )
        # 如果能成功调用 API（即使返回 0 条），说明鉴权通过
        return True, f"鉴权成功（API 可达，近 7 天有 {len(traces.data) if hasattr(traces, 'data') else '?'} 条 trace）"
    except Exception as e:
        return False, f"鉴权失败: {type(e).__name__}: {e}"


def test_trace_pull() -> tuple[bool, str, list[dict]]:
    """
    拉取最近的 traces 并提取关键字段。
    返回 (成功, 消息, traces 摘要列表)。
    """
    from app.agent.skills_agent.observability import get_langfuse_client

    client = get_langfuse_client()
    if client is None:
        return False, "客户端不可用", []

    traces_raw = []
    try:
        result = client.fetch_traces(
            limit=10,
            from_timestamp=datetime.now(timezone.utc) - timedelta(days=7),
        )
        if hasattr(result, "data"):
            traces_raw = result.data
        elif isinstance(result, list):
            traces_raw = result
        else:
            return False, f"未知的返回类型: {type(result).__name__}", []
    except Exception as e:
        return False, f"拉取失败: {type(e).__name__}: {e}", []

    summaries = []
    for t in traces_raw:
        info = {}
        if hasattr(t, "id"):
            info["id"] = t.id
        elif isinstance(t, dict):
            info["id"] = t.get("id", "?")
        else:
            info["id"] = str(t)

        if hasattr(t, "name"):
            info["name"] = t.name
        elif isinstance(t, dict):
            info["name"] = t.get("name", "?")

        if hasattr(t, "timestamp"):
            info["timestamp"] = str(t.timestamp)
        elif isinstance(t, dict):
            info["timestamp"] = t.get("timestamp", "?")

        summaries.append(info)

    return True, f"成功拉取 {len(summaries)} 条 trace", summaries


def test_metrics(trace_summaries: list[dict]) -> dict:
    """
    从 traces 中提取指标摘要。
    包括: trace 数量、时间跨度、名称分布。
    """
    if not trace_summaries:
        return {"trace_count": 0, "message": "无 trace 数据可供分析"}

    names = {}
    timestamps = []
    for t in trace_summaries:
        name = t.get("name", "unknown")
        names[name] = names.get(name, 0) + 1
        ts = t.get("timestamp")
        if ts:
            timestamps.append(ts)

    return {
        "trace_count": len(trace_summaries),
        "unique_names": len(names),
        "top_names": sorted(names.items(), key=lambda x: x[1], reverse=True)[:5],
        "time_range": f"{timestamps[0]} ~ {timestamps[-1]}" if timestamps else "N/A",
    }


def main():
    print("=" * 60)
    print("  Langfuse 连接诊断")
    print("=" * 60)

    # 1. 环境变量检查
    env = _check_env()
    print("\n[1/4] 环境变量检查")
    for k, v in env.items():
        masked = v[:8] + "***" if len(v) > 8 else "(未设置)"
        status = "OK" if v else "MISSING"
        print(f"  {k}: {masked} [{status}]")

    if not env["LANGFUSE_PUBLIC_KEY"] or not env["LANGFUSE_SECRET_KEY"]:
        print("\n  [SKIP] 缺少必需的环境变量，请设置后重试。")
        return

    # 2. 鉴权测试
    print("\n[2/4] 鉴权测试")
    t0 = time.time()
    auth_ok, auth_msg = test_auth()
    elapsed = (time.time() - t0) * 1000
    status = "PASS" if auth_ok else "FAIL"
    print(f"  [{status}] {auth_msg} ({elapsed:.0f}ms)")

    if not auth_ok:
        print("\n  诊断终止: 鉴权未通过。请检查密钥和网络连接。")
        return

    # 3. Trace 拉取
    print("\n[3/4] Trace 拉取")
    t0 = time.time()
    pull_ok, pull_msg, trace_summaries = test_trace_pull()
    elapsed = (time.time() - t0) * 1000
    status = "PASS" if pull_ok else "FAIL"
    print(f"  [{status}] {pull_msg} ({elapsed:.0f}ms)")

    if pull_ok and trace_summaries:
        print(f"\n  最近 {min(5, len(trace_summaries))} 条 Traces:")
        for t in trace_summaries[:5]:
            tid = t.get("id", "?")
            name = t.get("name", "?")
            ts = t.get("timestamp", "?")
            print(f"    [{tid[:12]}...] {name} @ {ts}")

    # 4. 指标提取
    print("\n[4/4] 指标提取")
    metrics = test_metrics(trace_summaries)
    print(f"  Trace 总数: {metrics['trace_count']}")
    print(f"  唯一名称数: {metrics['unique_names']}")
    if metrics.get("top_names"):
        print(f"  高频名称:")
        for name, count in metrics["top_names"]:
            print(f"    - {name}: {count}")
    if metrics.get("time_range") and metrics["time_range"] != "N/A":
        print(f"  时间跨度: {metrics['time_range']}")

    print("\n" + "=" * 60)
    print("  诊断完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
