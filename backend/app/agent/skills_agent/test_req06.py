"""
需求 06 验证测试 — FFmpeg 进程组管理与优雅终止（Windows）
"""
import os
import sys
import time

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

SCRIPTS_DIR = os.path.abspath(
    os.path.join(current_dir, "..", "..", "..", "jianying-editor-skill", "scripts")
)
sys.path.insert(0, SCRIPTS_DIR)


def test_process_utils_import_and_syntax():
    """验证 process_utils.py 可导入且语法正确"""
    import py_compile

    scripts_utils = os.path.join(SCRIPTS_DIR, "utils")
    py_compile.compile(
        os.path.join(scripts_utils, "process_utils.py"),
        doraise=True,
    )
    print("  ✅ process_utils.py 语法正确")


def test_run_with_timeout_normal():
    """run_with_timeout 正常执行返回成功"""
    from utils.process_utils import run_with_timeout

    result = run_with_timeout(
        ["python", "-c", "print('hello from run_with_timeout')"],
        timeout=30,
    )

    assert result["success"] is True
    assert result["timeout_expired"] is False
    assert result["was_force_killed"] is False
    assert "hello from run_with_timeout" in result["stdout"]
    assert result["returncode"] == 0
    print("  ✅ run_with_timeout 正常执行成功")


def test_run_with_timeout_failure():
    """run_with_timeout 命令失败返回正确的结构"""
    from utils.process_utils import run_with_timeout

    result = run_with_timeout(
        ["python", "-c", "raise SystemExit(1)"],
        timeout=30,
    )

    assert result["success"] is False
    assert result["timeout_expired"] is False
    assert result["returncode"] == 1
    print("  ✅ run_with_timeout 失败返回 success=False")


def test_run_with_timeout_actual_timeout():
    """
    run_with_timeout 超时后杀死进程并返回 timeout_expired=True.

    启动一个长时间睡眠的 Python 进程，设置极短超时来触发。
    """
    from utils.process_utils import run_with_timeout

    start = time.time()
    result = run_with_timeout(
        ["python", "-c", "import time; time.sleep(999)"],
        timeout=3,
    )
    elapsed = time.time() - start

    assert result["success"] is False
    assert result["timeout_expired"] is True, (
        f"应触发超时, actual: {result}"
    )
    assert elapsed < 10, f"超时后不应阻塞过长: {elapsed:.1f}s"
    print(
        f"  ✅ run_with_timeout 超时终止成功"
        f" (elapsed={elapsed:.1f}s, "
        f"force_killed={result['was_force_killed']})"
    )


def test_kill_process_tree():
    """_kill_process_tree 正常执行不抛异常"""
    from utils.process_utils import _kill_process_tree

    # 对不存在的 PID 调用应不抛异常
    _kill_process_tree(99999)
    print("  ✅ _kill_process_tree 对无效 PID 不抛异常")


def test_is_windows_flag():
    """验证平台检测函数"""
    from utils.process_utils import _is_windows
    import sys as _sys

    expected = _sys.platform == "win32"
    assert _is_windows() == expected
    print(f"  ✅ _is_windows() = {_is_windows()} (sys.platform = {_sys.platform})")


def test_media_normalizer_syntax():
    """验证 media_normalizer.py 语法正确"""
    import py_compile

    scripts_utils = os.path.join(SCRIPTS_DIR, "utils")
    py_compile.compile(
        os.path.join(scripts_utils, "media_normalizer.py"),
        doraise=True,
    )
    print("  ✅ media_normalizer.py 语法正确")


def test_media_normalizer_nonexistent_file():
    """不存在的文件 → 返回 None（不触发 FFmpeg）"""
    from utils.media_normalizer import normalize_webm_for_jianying

    result = normalize_webm_for_jianying("nonexistent_file_for_test_06.webm")
    assert result is None, f"不存在的文件应返回 None, actual: {result}"
    print("  ✅ media_normalizer 不存在的文件返回 None")


def test_no_tenacity_in_media_normalizer():
    """验证 media_normalizer.py 不再使用 tenacity（已由 run_with_timeout 替代）"""
    scripts_utils = os.path.join(SCRIPTS_DIR, "utils")
    content = open(
        os.path.join(scripts_utils, "media_normalizer.py"),
        encoding="utf-8",
    ).read()

    assert "tenacity" not in content, (
        "media_normalizer.py 不应再导入 tenacity（已由 run_with_timeout 替代）"
    )
    assert "_run_ffmpeg_normalize" not in content, (
        "media_normalizer.py 不应再包含 _run_ffmpeg_normalize 辅助函数"
    )
    assert "run_with_timeout" in content, (
        "media_normalizer.py 应使用 run_with_timeout"
    )
    print("  ✅ media_normalizer.py 已移除 tenacity，改用 run_with_timeout")


if __name__ == "__main__":
    print("=" * 60)
    print("需求 06 验证测试")
    print("=" * 60)

    test_process_utils_import_and_syntax()
    test_run_with_timeout_normal()
    test_run_with_timeout_failure()
    test_run_with_timeout_actual_timeout()
    test_kill_process_tree()
    test_is_windows_flag()
    test_media_normalizer_syntax()
    test_media_normalizer_nonexistent_file()
    test_no_tenacity_in_media_normalizer()

    print("\n" + "=" * 60)
    print("全部测试完成")
    print("=" * 60)
