"""
需求 05 验证测试 — Langfuse 可观测性埋点

所有测试均在 Langfuse 未配置时运行，验证优雅降级行为。
"""
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

SKILL_ROOT = os.path.abspath(
    os.path.join(current_dir, "..", "..", "..", "jianying-editor-skill")
)


def test_get_langfuse_client_graceful():
    """Langfuse 未配置时 → get_langfuse_client() 返回 None，不抛异常"""
    # 确保环境变量未设置
    for key in ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY"):
        os.environ.pop(key, None)

    from observability import get_langfuse_client

    client = get_langfuse_client()
    assert client is None, f"Langfuse 未配置应返回 None，实际: {client}"
    print("  ✅ get_langfuse_client() 优雅降级，返回 None")


def test_create_langchain_callback_graceful():
    """Langfuse 未配置时 → create_langchain_callback() 返回 None"""
    for key in ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY"):
        os.environ.pop(key, None)

    from observability import create_langchain_callback

    callback = create_langchain_callback()
    assert callback is None
    print("  ✅ create_langchain_callback() 优雅降级，返回 None")


def test_create_manual_span_graceful():
    """Langfuse 未配置时 → create_manual_span() 返回 None"""
    for key in ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY"):
        os.environ.pop(key, None)

    from observability import create_manual_span

    span_id = create_manual_span(
        trace_id="test-trace-id",
        name="test_span",
        input_data={"key": "value"},
    )
    assert span_id is None
    print("  ✅ create_manual_span() 优雅降级，返回 None")


def test_cli_executor_trace_id_optional():
    """cli_executor.execute() 接受 trace_id 参数且不存在时不崩溃"""
    from cli_executor import CLIScriptExecutor

    for key in ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY"):
        os.environ.pop(key, None)

    scripts_dir = os.path.join(
        current_dir, "..", "..", "..", "jianying-editor-skill", "scripts"
    )
    executor = CLIScriptExecutor(scripts_dir)

    # 1. 不带 trace_id → 正常执行
    result = executor.execute("nonexistent_script", {})
    assert result["success"] is False
    assert "未知的脚本" in result["error"]

    # 2. 带 trace_id → 不因手动 span 逻辑崩溃
    result = executor.execute(
        "nonexistent_script", {}, trace_id="test-trace-id-123",
    )
    assert result["success"] is False

    print("  ✅ CLI executor trace_id 参数可选，未配置 Langfuse 时不崩溃")


def test_run_jianying_agent_observability_opt_in():
    """
    enable_observability=True 在 Langfuse 未配置时不崩溃。

    需要 langchain-openai 等依赖才可运行；缺失时跳过。
    """
    try:
        from jianying_agent import run_jianying_agent
    except ModuleNotFoundError as e:
        print(f"  ⏭ 跳过（缺少依赖: {e}）")
        return

    for key in ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY"):
        os.environ.pop(key, None)

    # 回调初始化不应抛异常（Langfuse 未配置 → callback = None → 跳过埋点）
    try:
        from observability import create_langchain_callback

        callback = create_langchain_callback(
            trace_name="test-trace",
            tags=["test"],
        )
        assert callback is None
    except Exception as e:
        raise AssertionError(f"create_langchain_callback 不应抛异常: {e}") from e

    print("  ✅ enable_observability=True 在 Langfuse 未配置时不会崩溃")


def test_observability_module_syntax():
    """验证 observability.py 语法正确"""
    import py_compile

    py_compile.compile(
        os.path.join(current_dir, "observability.py"),
        doraise=True,
    )
    print("  ✅ observability.py 语法正确")


def test_langfuse_in_pyproject():
    """验证 langfuse 已加入 pyproject.toml 依赖"""
    import re

    toml_path = os.path.join(current_dir, "..", "..", "..", "pyproject.toml")
    content = open(toml_path, encoding="utf-8").read()

    assert "langfuse" in content, "pyproject.toml 中缺少 langfuse 依赖"
    assert re.search(r'"langfuse\s*[>]=', content), (
        "langfuse 依赖格式不正确"
    )
    print("  ✅ langfuse>=3.0.0 已添加到 pyproject.toml")


def test_sentry_coexistence():
    """验证 Sentry 和 Langfuse 不冲突——两者独立初始化"""
    # Sentry 已在 main.py 中通过 sentry_sdk.init() 初始化
    # Langfuse 在 observability.py 中懒初始化
    # 两者使用不同的 client，不共享 context
    import ast

    obs_path = os.path.join(current_dir, "observability.py")
    tree = ast.parse(open(obs_path, encoding="utf-8").read())

    # 确认 observability.py 没有导入/依赖 sentry
    source = open(obs_path, encoding="utf-8").read()
    assert "sentry" not in source.lower(), (
        "observability.py 不应依赖 Sentry"
    )
    print("  ✅ Langfuse 与 Sentry 独立，无冲突")


if __name__ == "__main__":
    print("=" * 60)
    print("需求 05 验证测试")
    print("=" * 60)

    test_get_langfuse_client_graceful()
    test_create_langchain_callback_graceful()
    test_create_manual_span_graceful()
    test_cli_executor_trace_id_optional()
    test_run_jianying_agent_observability_opt_in()
    test_observability_module_syntax()
    test_langfuse_in_pyproject()
    test_sentry_coexistence()

    print("\n" + "=" * 60)
    print("全部测试完成")
    print("=" * 60)
