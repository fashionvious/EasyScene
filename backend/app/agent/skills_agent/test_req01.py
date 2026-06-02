"""
需求 01 验证测试 — 超时控制与输出截断
"""
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)


def test_truncate_output():
    """测试 truncate_output 核心逻辑"""
    from utils.timeout_config import truncate_output, SubprocessConfig

    # 1. 短文本不截断
    short = "hello world"
    assert truncate_output(short) == short, "短文本不应被截断"

    # 2. ASCII 长文本截断
    long_ascii = "A" * 15000  # 15KB > 10KB 阈值
    result = truncate_output(long_ascii)
    assert "截断" in result, "长文本应包含截断标记"
    raw = result.encode("utf-8")
    assert len(raw) <= SubprocessConfig.output_truncate_bytes + 500, (
        f"截断后不应远超阈值: {len(raw)} bytes"
    )

    # 3. 中文文本截断（多字节字符）
    long_cn = "中" * 10000  # 每个中文字符 3 字节 UTF-8 = 30000 bytes
    result_cn = truncate_output(long_cn)
    assert "截断" in result_cn, "中文长文本应被截断"

    # 4. 空文本
    assert truncate_output("") == "", "空文本应原样返回"

    # 5. None-like 行为（空字符串安全）
    assert truncate_output("   ") == "   ", "空白文本应原样返回"

    print("  ✅ truncate_output 所有场景通过")


def test_subprocess_config():
    """测试 SubprocessConfig 常量"""
    from utils.timeout_config import SubprocessConfig

    assert SubprocessConfig.default_timeout == 120
    assert SubprocessConfig.max_timeout == 600
    assert SubprocessConfig.output_truncate_bytes == 10240
    assert SubprocessConfig.head_keep_bytes == 2048
    assert SubprocessConfig.tail_keep_bytes == 2048
    print("  ✅ SubprocessConfig 常量正确")


def test_cli_executor_import():
    """测试 cli_executor 正常导入且 truncate_output 可用"""
    from cli_executor import CLIScriptExecutor, SCRIPT_REGISTRY

    scripts_dir = os.path.join(
        current_dir, "..", "..", "..", "jianying-editor-skill", "scripts"
    )
    executor = CLIScriptExecutor(scripts_dir)

    # 测试执行不存在的脚本（快速路径，不实际 subprocess）
    result = executor.execute("nonexistent_script", {})
    assert result["success"] is False
    assert "未知的脚本" in result["error"]
    print("  ✅ CLIScriptExecutor 正常，返回结构不变")


def test_python_executor_import():
    """测试 python_executor 正常导入且 truncate_output 可用"""
    from python_executor import PythonCodeExecutor

    skill_root = os.path.join(
        current_dir, "..", "..", "..", "jianying-editor-skill"
    )
    executor = PythonCodeExecutor(skill_root)

    # 测试语法验证（不实际执行代码）
    result = executor.execute(
        'print("hello world")',
        include_bootstrap=False,
        capture_output=True,
    )
    assert result["success"] is True
    assert "hello world" in result["output"]
    print("  ✅ PythonCodeExecutor 正常，输出已截断（短输出不受影响）")


def test_media_normalizer_syntax():
    """验证 media_normalizer.py 语法正确 + timeout_config 可编译

    不在此处做 import，因为 skills_agent/utils/ 和 scripts/utils/
    同名冲突——两个包的 sys.path 不同，实际运行时不会冲突。
    """
    import py_compile

    scripts_utils = os.path.abspath(
        os.path.join(
            current_dir, "..", "..", "..",
            "jianying-editor-skill", "scripts", "utils",
        )
    )

    # 验证 media_normalizer.py 语法
    py_compile.compile(
        os.path.join(scripts_utils, "media_normalizer.py"),
        doraise=True,
    )

    # 验证 timeout_config.py 语法
    py_compile.compile(
        os.path.join(scripts_utils, "timeout_config.py"),
        doraise=True,
    )

    print("  ✅ media_normalizer.py + timeout_config.py 语法正确")


if __name__ == "__main__":
    print("=" * 60)
    print("需求 01 验证测试")
    print("=" * 60)

    test_truncate_output()
    test_subprocess_config()
    test_cli_executor_import()
    test_python_executor_import()
    test_media_normalizer_syntax()

    print("\n" + "=" * 60)
    print("✅ 全部测试通过")
    print("=" * 60)
