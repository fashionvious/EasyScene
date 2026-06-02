"""
需求 04 验证测试 — Python 执行器输出截断与结果摘要
"""
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

SKILL_ROOT = os.path.abspath(
    os.path.join(current_dir, "..", "..", "..", "jianying-editor-skill")
)


def test_summarize_output_success():
    """测试成功输出的摘要——短输出不截断，长输出取首尾"""
    from python_executor import summarize_output, MAX_SUMMARY_LINES

    # 短输出（≤50 行）→ 不截断
    short = "\n".join(f"line {i}" for i in range(30))
    result = summarize_output(short, "", success=True)
    assert "line 0" in result
    assert "line 29" in result
    assert "省略" not in result
    print(f"  ✅ 短输出（30 行）不截断")

    # 长输出（>50 行）→ 取首尾 5 行
    long_out = "\n".join(f"line {i}" for i in range(100))
    result = summarize_output(long_out, "", success=True)
    assert "line 0" in result
    assert "line 99" in result
    assert "省略" in result
    # 应该是首 5 + 省略 + 尾 5
    assert "line 4" in result
    assert "line 95" in result
    print(f"  ✅ 长输出（100 行）正确截断为首尾 5 行")


def test_summarize_output_failure():
    """测试失败输出的摘要——提取错误关键词行"""
    from python_executor import summarize_output

    # 包含 Traceback 的输出
    stdout = "some normal output\n"
    stderr = "Traceback (most recent call last):\n  File 'x.py', line 5\nValueError: bad value\n"
    result = summarize_output(stdout, stderr, success=False)
    assert "关键错误信息" in result
    assert "Traceback" in result
    assert "ValueError" in result
    print(f"  ✅ 失败输出正确提取错误关键词行")

    # 无关键字的失败输出 → 回退到最后 20 行
    no_key = "\n".join(f"generic line {i}" for i in range(50))
    result = summarize_output(no_key, "", success=False)
    assert "generic line 31" in result  # 50 行 + 空行 = 51 元素，[-20:] 从 index 31 起
    print(f"  ✅ 无关键词时回退到最后 20 行")


def test_save_full_output():
    """测试 save_full_output 持久化"""
    import tempfile
    from pathlib import Path
    from python_executor import save_full_output

    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = Path(tmpdir)
        stdout = "hello world"
        stderr = "some error"

        path = save_full_output(stdout, stderr, work_dir)
        assert path is not None
        assert "jy_exec_log_" in path
        assert os.path.exists(path)

        content = open(path, encoding="utf-8").read()
        assert "=== STDOUT ===" in content
        assert "hello world" in content
        assert "=== STDERR ===" in content
        assert "some error" in content

    # 空输出 → 不创建文件
    assert save_full_output("", "", work_dir) is None
    print(f"  ✅ save_full_output 正确持久化，空输出不创建文件")


def test_execute_success_small_output():
    """成功 + 小输出 → 不截断，temp_file 被清理"""
    from python_executor import PythonCodeExecutor

    executor = PythonCodeExecutor(SKILL_ROOT)
    result = executor.execute(
        'print("small output")',
        include_bootstrap=False,
        capture_output=True,
        max_retries=0,
    )

    assert result["success"] is True
    assert "small output" in result["output"]
    assert result["raw_output_truncated"] is False
    assert result["full_log_path"] is None
    # 小输出 + 成功 → temp_file 应为 None（已清理）
    assert result["temp_file"] is None, f"小输出成功时应清理 temp_file，实际: {result['temp_file']}"
    print(f"  ✅ 成功小输出：temp_file 已清理，无截断")


def test_execute_failure_keeps_temp():
    """执行失败 → 保留 temp_file 和 full_log_path"""
    from python_executor import PythonCodeExecutor

    executor = PythonCodeExecutor(SKILL_ROOT)
    result = executor.execute(
        'raise ValueError("test error for req04")',
        include_bootstrap=False,
        capture_output=True,
        max_retries=0,
    )

    assert result["success"] is False
    assert result["temp_file"] is not None, "失败时应保留 temp_file"
    assert os.path.exists(result["temp_file"]), f"temp_file 应存在: {result['temp_file']}"
    assert result["full_log_path"] is not None
    assert os.path.exists(result["full_log_path"])

    # 清理测试产生的文件
    try:
        os.unlink(result["temp_file"])
    except OSError:
        pass
    try:
        os.unlink(result["full_log_path"])
    except OSError:
        pass

    print(f"  ✅ 失败时 temp_file + full_log_path 均保留")


def test_execute_large_output_truncated():
    """大输出 → 触发截断，保留日志"""
    from python_executor import PythonCodeExecutor

    executor = PythonCodeExecutor(SKILL_ROOT)
    # 生成超过 10KB 的多行输出（每行 ~200 字节 × 60 行 ≈ 12KB）
    big_print = "for i in range(60): print('x' * 200)"
    result = executor.execute(
        big_print,
        include_bootstrap=False,
        capture_output=True,
        max_retries=0,
    )

    assert result["success"] is True
    assert result["raw_output_truncated"] is True
    assert result["full_log_path"] is not None
    assert os.path.exists(result["full_log_path"])
    assert "省略" in result["output"]

    # 清理
    try:
        os.unlink(result["full_log_path"])
    except OSError:
        pass

    print(f"  ✅ 大输出触发截断，full_log_path 已保留")


def test_bare_except_eliminated():
    """验证所有裸 except: 已被替换为 except OSError:"""
    import ast

    filepath = os.path.join(current_dir, "python_executor.py")
    tree = ast.parse(open(filepath, encoding="utf-8").read())

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                raise AssertionError(
                    f"第 {node.lineno} 行存在裸 except:，应改为 except OSError:"
                )

    print(f"  ✅ 无裸 except:，均指定了异常类型")


def test_tool_message_format():
    """测试工具函数返回的消息格式包含日志路径和源代码路径"""
    from python_executor import PythonCodeExecutor

    executor = PythonCodeExecutor(SKILL_ROOT)

    # 失败场景：检查原始 result dict 的字段
    result = executor.execute(
        'raise RuntimeError("req04 test")',
        include_bootstrap=False,
        capture_output=True,
    )
    assert result["success"] is False
    assert "full_log_path" in result
    assert "temp_file" in result
    assert "raw_output_truncated" in result

    # 清理
    try:
        os.unlink(result["full_log_path"])
    except OSError:
        pass
    try:
        os.unlink(result["temp_file"])
    except OSError:
        pass

    print(f"  ✅ 返回 dict 包含 full_log_path, temp_file, raw_output_truncated 字段")


if __name__ == "__main__":
    print("=" * 60)
    print("需求 04 验证测试")
    print("=" * 60)

    test_summarize_output_success()
    test_summarize_output_failure()
    test_save_full_output()
    test_execute_success_small_output()
    test_execute_failure_keeps_temp()
    test_execute_large_output_truncated()
    test_bare_except_eliminated()
    test_tool_message_format()

    print("\n" + "=" * 60)
    print("全部测试完成")
    print("=" * 60)
