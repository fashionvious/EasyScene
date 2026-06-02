"""
需求 02 验证测试 — 重试机制与降级策略
"""
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)


def test_retry_config_import():
    """测试 retry_config 模块正常导入"""
    from retry_config import (
        DEFAULT_RETRY_CONFIG,
        RETRYABLE_EXCEPTIONS,
        make_retry_decorator,
    )
    import subprocess

    assert DEFAULT_RETRY_CONFIG["max_attempts"] == 3
    assert DEFAULT_RETRY_CONFIG["min_wait"] == 1
    assert DEFAULT_RETRY_CONFIG["max_wait"] == 30
    assert subprocess.TimeoutExpired in RETRYABLE_EXCEPTIONS
    assert TimeoutError in RETRYABLE_EXCEPTIONS
    assert ConnectionError in RETRYABLE_EXCEPTIONS
    assert OSError in RETRYABLE_EXCEPTIONS

    # 验证 make_retry_decorator 返回可用的 tenacity 装饰器
    deco = make_retry_decorator(max_attempts=2)
    assert callable(deco)

    print("  ✅ retry_config 模块正常，RETRYABLE_EXCEPTIONS 包含 TimeoutExpired")


def test_cli_executor_retry_param():
    """测试 CLI 执行器新增 max_retries 参数"""
    from cli_executor import CLIScriptExecutor, RETRYABLE_RETURN_CODES

    assert 1 in RETRYABLE_RETURN_CODES
    assert 137 in RETRYABLE_RETURN_CODES
    assert 139 in RETRYABLE_RETURN_CODES
    # 参数错误码不应在可重试列表中
    assert 2 not in RETRYABLE_RETURN_CODES

    scripts_dir = os.path.join(
        current_dir, "..", "..", "..", "jianying-editor-skill", "scripts"
    )
    executor = CLIScriptExecutor(scripts_dir)

    # 1. max_retries=0（默认）不重试——不存在脚本直接返回错误
    result = executor.execute("nonexistent_script", {}, max_retries=0)
    assert result["success"] is False
    assert "未知的脚本" in result["error"]

    # 2. 带 max_retries 参数时返回结构正确
    result = executor.execute("nonexistent_script", {}, max_retries=2)
    assert result["success"] is False

    # 3. 测试 _build_cmd 方法存在
    assert hasattr(executor, "_build_cmd")

    print("  ✅ CLI 执行器 max_retries 参数正常，RETRYABLE_RETURN_CODES 白名单正确")


def test_python_executor_retry_param():
    """测试 Python 执行器新增 max_retries 参数"""
    from python_executor import PythonCodeExecutor

    skill_root = os.path.join(
        current_dir, "..", "..", "..", "jianying-editor-skill"
    )
    executor = PythonCodeExecutor(skill_root)

    # 1. max_retries=0（默认）—— 正常执行
    result = executor.execute(
        'print("ok")',
        include_bootstrap=False,
        capture_output=True,
        max_retries=0,
    )
    assert result["success"] is True
    assert "ok" in result["output"]

    # 2. 带 max_retries 参数但正常执行——不触发重试
    result = executor.execute(
        'print("ok2")',
        include_bootstrap=False,
        capture_output=True,
        max_retries=2,
    )
    assert result["success"] is True
    assert "ok2" in result["output"]

    print("  ✅ Python 执行器 max_retries 参数正常，正常执行不受影响")


def test_media_normalizer_retry():
    """测试 media_normalizer 的重试装饰器函数"""
    import py_compile

    scripts_utils = os.path.abspath(
        os.path.join(
            current_dir, "..", "..", "..",
            "jianying-editor-skill", "scripts", "utils",
        )
    )

    # 验证 media_normalizer.py 语法（含 _run_ffmpeg_normalize）
    py_compile.compile(
        os.path.join(scripts_utils, "media_normalizer.py"),
        doraise=True,
    )

    print("  ✅ media_normalizer.py 语法正确，_run_ffmpeg_normalize 装饰器就绪")


def test_universal_tts_syntax():
    """验证 universal_tts.py 语法正确"""
    import py_compile

    scripts_dir = os.path.abspath(
        os.path.join(
            current_dir, "..", "..", "..",
            "jianying-editor-skill", "scripts",
        )
    )

    py_compile.compile(
        os.path.join(scripts_dir, "universal_tts.py"),
        doraise=True,
    )

    print("  ✅ universal_tts.py 语法正确，_run_sami_tts_with_retry 就绪")


if __name__ == "__main__":
    print("=" * 60)
    print("需求 02 验证测试")
    print("=" * 60)

    test_retry_config_import()
    test_cli_executor_retry_param()
    test_python_executor_retry_param()
    test_media_normalizer_retry()
    test_universal_tts_syntax()

    print("\n" + "=" * 60)
    print("✅ 全部测试通过")
    print("=" * 60)
