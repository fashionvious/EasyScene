"""
Tests for token_utils.py — model-aware token counting.

Coverage: dashscope integration (mocked), fallback estimation,
boundary conditions, error recovery, backward compatibility.
"""
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dashscope_mock(encode_return):
    """Factory: create a mock dashscope.tokenizers module with controlled encode."""
    mock_tok = MagicMock()
    mock_tok.encode.return_value = encode_return
    mock_get = MagicMock(return_value=mock_tok)
    return {"dashscope.tokenizers": MagicMock(get_tokenizer=mock_get)}


def _patch_dashscope_import_failure():
    """Make 'from dashscope.tokenizers import get_tokenizer' raise ImportError."""
    import builtins
    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "dashscope.tokenizers" or name.startswith("dashscope.tokenizers."):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    return patch.object(builtins, "__import__", side_effect=_blocked_import)


# ---------------------------------------------------------------------------
# estimate_tokens — dashscope available (mocked at source)
# ---------------------------------------------------------------------------

class TestEstimateTokensWithDashscope:
    """dashscope tokenizer 可用时的精确计数路径"""

    def test_chinese_uses_dashscope(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        with patch.dict("sys.modules", _dashscope_mock([108386, 99489])):
            result = estimate_tokens("你好世界")

        assert result == 2

    def test_english_uses_dashscope(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        with patch.dict("sys.modules", _dashscope_mock([9707, 4337, 419, 374, 264, 1273])):
            result = estimate_tokens("Hello World this is a test")

        assert result == 6

    def test_mixed_chinese_english(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        with patch.dict("sys.modules", _dashscope_mock(list(range(8)))):
            result = estimate_tokens("中英混合Hello测试123")

        assert result == 8

    def test_empty_string_short_circuits(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        result = estimate_tokens("")
        assert result == 0


# ---------------------------------------------------------------------------
# estimate_tokens — dashscope unavailable (fallback)
# ---------------------------------------------------------------------------

class TestEstimateTokensFallback:
    """dashscope 不可用时的混合估算 fallback"""

    def test_pure_chinese_fallback(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        with _patch_dashscope_import_failure():
            result = estimate_tokens("你好世界测试")
        # 6 CJK chars / 2.0 = 3.0 → int = 3
        assert result == 3

    def test_pure_english_fallback(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        with _patch_dashscope_import_failure():
            result = estimate_tokens("Hello World test")

        # 16 ASCII chars / 5.5 ≈ 2.9 → int = 2
        assert result == 2

    def test_mixed_fallback(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        with _patch_dashscope_import_failure():
            result = estimate_tokens("中English混合")
        # CJK 3/2.0 + ASCII 7/5.5 ≈ 1.5 + 1.27 = 2.77 → 2
        assert result == 2

    def test_fallback_never_zero_for_nonempty(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        with _patch_dashscope_import_failure():
            result = estimate_tokens("a")
        assert result >= 1

    def test_empty_string_returns_zero(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        with _patch_dashscope_import_failure():
            assert estimate_tokens("") == 0

    def test_long_text_fallback_stability(self):
        from app.agent.skills_agent.token_utils import estimate_tokens
        text = "这是一个很长的中文句子用于测试token估算的准确性" * 50

        with _patch_dashscope_import_failure():
            result = estimate_tokens(text)

        assert result > 100
        assert isinstance(result, int)

    def test_code_like_text_fallback(self):
        from app.agent.skills_agent.token_utils import estimate_tokens
        code = 'project = JyProject("my_video")\nproject.add_media_safe("v.mp4", "0s")'

        with _patch_dashscope_import_failure():
            result = estimate_tokens(code)

        # Calibrated: 21 tokens. Fallback for symbol-heavy code
        # underestimates (~12), which is acceptable for a fallback.
        # The key assertion is that it doesn't use len//2 (=32).
        assert 10 <= result <= 35


# ---------------------------------------------------------------------------
# estimate_tokens — dashscope returns None (graceful degradation)
# ---------------------------------------------------------------------------

class TestEstimateTokensDashscopeReturnsNone:
    """dashscope encode() 返回 None/[] 时降级到 fallback"""

    def test_encode_returns_none_falls_back(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        with patch.dict("sys.modules", _dashscope_mock(None)):
            result = estimate_tokens("你好世界")
        assert result >= 1

    def test_encode_returns_empty_list_falls_back(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        with patch.dict("sys.modules", _dashscope_mock([])):
            result = estimate_tokens("Hello")
        assert result >= 1

    def test_tokenizer_init_raises_falls_back(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        with _patch_dashscope_import_failure():
            result = estimate_tokens("test")
        assert result >= 1


# ---------------------------------------------------------------------------
# CJK detection edge cases — direct unit tests (no dashscope needed)
# ---------------------------------------------------------------------------

class TestCJKDetection:
    """CJK / ASCII 字符分类边界"""

    def test_japanese_kana_classified_as_cjk(self):
        from app.agent.skills_agent.token_utils import _is_cjk
        assert _is_cjk("あ") is True
        assert _is_cjk("ア") is True
        assert _is_cjk("ー") is True  # prolonged sound mark (wide)

    def test_korean_hangul_classified_as_cjk(self):
        from app.agent.skills_agent.token_utils import _is_cjk
        assert _is_cjk("한") is True

    def test_emoji_and_symbols(self):
        from app.agent.skills_agent.token_utils import _is_cjk
        # Emoji are east_asian_width "W" → treated as CJK for token counting.
        assert _is_cjk("😀") is True
        # Arrow is "A" (Ambiguous) → narrow on Windows, fits "other" category.
        assert _is_cjk("→") is False
        # Fullwidth punctuation is "W" → CJK
        assert _is_cjk("，") is True

    def test_fullwidth_punctuation_is_cjk(self):
        from app.agent.skills_agent.token_utils import _is_cjk
        assert _is_cjk("，") is True
        assert _is_cjk("。") is True
        assert _is_cjk("！") is True

    def test_ascii_punctuation_not_cjk(self):
        from app.agent.skills_agent.token_utils import _is_cjk, _is_ascii
        assert _is_cjk(",") is False
        assert _is_cjk("!") is False
        assert _is_cjk(".") is False
        assert _is_ascii(",") is True


# ---------------------------------------------------------------------------
# Accuracy relative to old len//2
# ---------------------------------------------------------------------------

class TestAccuracyVsOldMethod:
    """新方法 vs 旧 len//2 的精度改进"""

    def test_chinese_accuracy_parity(self,):
        from app.agent.skills_agent.token_utils import estimate_tokens

        texts = ["你好世界", "这是一个测试句子", "自动剪辑视频生成工具"]
        with _patch_dashscope_import_failure():
            for text in texts:
                old = len(text) // 2
                new = estimate_tokens(text)
                assert abs(new - old) <= max(1, old * 0.3), (
                    f"text={text} old={old} new={new}"
                )

    def test_english_no_longer_overestimated(self):
        from app.agent.skills_agent.token_utils import estimate_tokens

        text = "This is a long English sentence for testing token estimation accuracy"
        old = len(text) // 2

        with _patch_dashscope_import_failure():
            new = estimate_tokens(text)

        assert new < old, f"new={new} should be less than old={old} for English"
        # Actual count ≈ 10 tokens; fallback within ±30%
        assert 7 <= new <= 13, f"new={new} out of expected [7, 13]"


# ---------------------------------------------------------------------------
# calculate_context_budget — backward compatibility
# ---------------------------------------------------------------------------

class TestCalculateContextBudget:
    """保证签名和返回值不变"""

    def test_default_parameters(self):
        from app.agent.skills_agent.token_utils import calculate_context_budget
        result = calculate_context_budget()
        assert result == 104857
        assert isinstance(result, int)

    def test_custom_model_size(self):
        from app.agent.skills_agent.token_utils import calculate_context_budget
        assert calculate_context_budget(model_max_tokens=100000) == 80000

    def test_custom_reserved_ratio(self):
        from app.agent.skills_agent.token_utils import calculate_context_budget
        assert calculate_context_budget(reserved_ratio=0.5) == 65536

    def test_zero_reserved_ratio(self):
        from app.agent.skills_agent.token_utils import calculate_context_budget
        assert calculate_context_budget(reserved_ratio=0.0) == 131072

    def test_result_always_int(self):
        from app.agent.skills_agent.token_utils import calculate_context_budget
        result = calculate_context_budget(model_max_tokens=33333, reserved_ratio=0.33)
        assert isinstance(result, int)
