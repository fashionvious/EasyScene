"""
需求 03 验证测试 — Token 感知的上下文注入机制
"""
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

SKILL_ROOT = os.path.abspath(
    os.path.join(current_dir, "..", "..", "..", "jianying-editor-skill")
)


def _get_middleware():
    """创建 middleware 实例，依赖缺失时跳过"""
    try:
        from jianying_agent import JianYingSkillMiddleware
    except ModuleNotFoundError as e:
        return None, str(e)
    return JianYingSkillMiddleware(SKILL_ROOT), None


def test_estimate_tokens():
    """测试 token 估算函数（独立模块，不依赖 langchain）"""
    from token_utils import estimate_tokens, calculate_context_budget

    assert estimate_tokens("") == 0
    assert estimate_tokens("hello") == 2       # 5 chars // 2
    assert estimate_tokens("你好世界") == 2     # 4 chars // 2
    assert estimate_tokens("a" * 100) == 50    # 100 chars // 2

    budget = calculate_context_budget()
    assert budget == 104857  # 131072 * 0.8

    budget_custom = calculate_context_budget(model_max_tokens=100000, reserved_ratio=0.10)
    assert budget_custom == 90000

    print("  ✅ estimate_tokens 和 calculate_context_budget 正确")


def test_generate_skills_prompt_with_budget():
    """测试 _generate_skills_prompt 的 budget 控制"""
    middleware, err = _get_middleware()
    if middleware is None:
        print(f"  ⏭ 跳过（缺少依赖: {err}）")
        return

    full_prompt = middleware._generate_skills_prompt()
    assert "主技能" in full_prompt
    print(f"  ✅ 全量技能提示: {len(full_prompt)} 字符")

    tiny_prompt = middleware._generate_skills_prompt(token_budget=50)
    assert len(tiny_prompt) < len(full_prompt), (
        f"极小 budget 应触发截断: {len(tiny_prompt)} >= {len(full_prompt)}"
    )
    print(f"  ✅ 极小 budget({len(tiny_prompt)} chars) < 全量({len(full_prompt)} chars)，截断已触发")

    med_prompt = middleware._generate_skills_prompt(token_budget=200)
    assert len(med_prompt) > 0
    print(f"  ✅ 中等 budget: {len(med_prompt)} 字符")

    assert hasattr(middleware, '_skills_prompt_cache')
    assert len(middleware._skills_prompt_cache) > 0
    print(f"  ✅ 缓存 _skills_prompt_cache 已就绪")


def test_build_media_summary():
    """测试 _build_media_summary 动态文件数量"""
    middleware, err = _get_middleware()
    if middleware is None:
        print(f"  ⏭ 跳过（缺少依赖: {err}）")
        return

    sample_files = [
        {"name": f"video_{i}.mp4", "type": "video", "size_mb": 5.0}
        for i in range(30)
    ]

    summary_full = middleware._build_media_summary(sample_files, token_budget_remaining=10000)
    assert "video_0" in summary_full
    assert "video_29" in summary_full

    summary_tiny = middleware._build_media_summary(sample_files, token_budget_remaining=60)
    assert "还有" in summary_tiny, f"应显示省略标记，实际: {summary_tiny[:100]}"

    assert middleware._build_media_summary([], 1000) == ""

    print("  ✅ _build_media_summary 动态文件数量正确")


def test_build_skills_addendum():
    """测试 _build_skills_addendum 集成"""
    middleware, err = _get_middleware()
    if middleware is None:
        print(f"  ⏭ 跳过（缺少依赖: {err}）")
        return

    addendum = middleware._build_skills_addendum()

    assert "## 可用技能" in addendum
    assert "## 工具使用指南" in addendum
    assert "## 工作流程" in addendum
    assert "resolve_media" in addendum

    print(f"  ✅ _build_skills_addendum 完整输出了 {len(addendum)} 字符")


def test_wrap_model_call_no_duplication():
    """测试 wrap_model_call 和 awrap_model_call 共享构建逻辑"""
    import inspect

    try:
        from jianying_agent import JianYingSkillMiddleware
    except ModuleNotFoundError as e:
        print(f"  ⏭ 跳过（缺少依赖: {e}）")
        return

    sync_source = inspect.getsource(JianYingSkillMiddleware.wrap_model_call)
    async_source = inspect.getsource(JianYingSkillMiddleware.awrap_model_call)

    assert "_build_skills_addendum" in sync_source
    assert "_build_skills_addendum" in async_source
    # 两者都不应包含内联的重复文本
    assert sync_source.count("## 工具使用指南") == 0
    assert async_source.count("## 工具使用指南") == 0

    print("  ✅ wrap_model_call 和 awrap_model_call 共享 _build_skills_addendum，无重复代码")


if __name__ == "__main__":
    print("=" * 60)
    print("需求 03 验证测试")
    print("=" * 60)

    test_estimate_tokens()
    test_generate_skills_prompt_with_budget()
    test_build_media_summary()
    test_build_skills_addendum()
    test_wrap_model_call_no_duplication()

    print("\n" + "=" * 60)
    print("✅ 全部测试完成")
    print("=" * 60)
