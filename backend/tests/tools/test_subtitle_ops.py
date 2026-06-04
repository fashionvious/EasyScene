"""
Functional tests for subtitle_ops.py — add_dual_subtitles.

Covers:
- Happy path: dual tracks created, correct Y positions, style differentiation
- Boundary: empty_input, duration=0 compensation, empty_text skip,
  primary/secondary style separation, subtitle_pairs format integrity
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — lightweight JyProject stub capturing add_text_simple calls
# ---------------------------------------------------------------------------

def _make_stub_jyproject(captured_calls=None):
    proj = MagicMock()
    proj.root = "/fake/drafts"
    proj.name = "SubTest"

    if captured_calls is None:
        captured_calls = []

    def _add_text_side_effect(text, start_time=None, duration=None,
                              track_name=None, **kwargs):
        captured_calls.append({
            "text": text,
            "start_time": start_time,
            "duration": duration,
            "track_name": track_name,
            "kwargs": kwargs,
        })
        return MagicMock()

    proj.add_text_simple.side_effect = _add_text_side_effect
    proj.save.return_value = {"status": "SUCCESS"}
    return proj


@pytest.fixture(autouse=True)
def _patch_langfuse():
    with patch(
        "app.agent.skills_agent.tools.subtitle_ops._get_langfuse",
        return_value=MagicMock(),
    ):
        yield


# ===================================================================
# Happy Path
# ===================================================================

class TestAddDualSubtitlesHappyPath:

    def _run_dual_subtitles(self, pairs, **extra):
        from app.agent.skills_agent.tools.subtitle_ops import add_dual_subtitles

        calls = []
        with patch(
            "app.agent.skills_agent.tools.subtitle_ops.JyProject",
            return_value=_make_stub_jyproject(calls),
        ):
            result = add_dual_subtitles.invoke({
                "project_name": "DualSubTest",
                "subtitle_pairs": pairs,
                **extra,
            })
        result = json.loads(result) if isinstance(result, str) else result
        return result, calls

    def test_dual_tracks_created(self):
        pairs = [
            {"start_us": 0, "duration_us": 2_000_000,
             "primary_text": "你好", "secondary_text": "Hello"},
            {"start_us": 2_000_000, "duration_us": 3_000_000,
             "primary_text": "世界", "secondary_text": "World"},
        ]

        result, calls = self._run_dual_subtitles(pairs)
        assert result["ok"] is True
        assert result["pair_count"] == 2
        assert result["primary_track"] == "Sub_Primary"
        assert result["secondary_track"] == "Sub_Secondary"
        assert result["skipped_pairs"] == 0

        # 2 primary + 2 secondary = 4 calls
        assert len(calls) == 4

    def test_primary_on_sub_primary_track(self):
        pairs = [
            {"start_us": 0, "duration_us": 1_000_000,
             "primary_text": "A", "secondary_text": "B"},
        ]
        result, calls = self._run_dual_subtitles(pairs)

        primary_calls = [c for c in calls if c["track_name"] == "Sub_Primary"]
        secondary_calls = [c for c in calls if c["track_name"] == "Sub_Secondary"]
        assert len(primary_calls) == 1
        assert len(secondary_calls) == 1

    def test_y_positions_differentiated(self):
        """Primary at ~-0.75, secondary at ~-0.85."""
        pairs = [
            {"start_us": 0, "duration_us": 1_000_000,
             "primary_text": "主", "secondary_text": "副"},
        ]
        result, calls = self._run_dual_subtitles(pairs)

        primary_call = next(c for c in calls if c["track_name"] == "Sub_Primary")
        secondary_call = next(c for c in calls if c["track_name"] == "Sub_Secondary")

        primary_cs = primary_call["kwargs"].get("clip_settings")
        secondary_cs = secondary_call["kwargs"].get("clip_settings")
        assert primary_cs is not None
        assert secondary_cs is not None
        assert primary_cs.transform_y == -0.75
        assert secondary_cs.transform_y == -0.85

    def test_primary_has_background_secondary_does_not(self):
        pairs = [
            {"start_us": 0, "duration_us": 1_000_000,
             "primary_text": "主", "secondary_text": "副"},
        ]
        result, calls = self._run_dual_subtitles(pairs)

        primary_call = next(c for c in calls if c["track_name"] == "Sub_Primary")
        secondary_call = next(c for c in calls if c["track_name"] == "Sub_Secondary")

        # primary has background
        assert primary_call["kwargs"].get("background") is not None
        # secondary does NOT have background
        assert secondary_call["kwargs"].get("background") is None

    def test_time_alignment_between_tracks(self):
        """Primary and secondary must share exactly the same start_time and duration."""
        pairs = [
            {"start_us": 100_000, "duration_us": 2_500_000,
             "primary_text": "Hello", "secondary_text": "World"},
        ]
        result, calls = self._run_dual_subtitles(pairs)

        primary_call = next(c for c in calls if c["track_name"] == "Sub_Primary")
        secondary_call = next(c for c in calls if c["track_name"] == "Sub_Secondary")

        assert primary_call["start_time"] == secondary_call["start_time"] == 100_000
        assert primary_call["duration"] == secondary_call["duration"] == 2_500_000


# ===================================================================
# Boundary cases
# ===================================================================

class TestAddDualSubtitlesBoundary:

    def test_empty_input_rejected(self):
        from app.agent.skills_agent.tools.subtitle_ops import add_dual_subtitles

        result = add_dual_subtitles.invoke({
            "project_name": "EmptySub",
            "subtitle_pairs": [],
        })
        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "empty_input"

    def test_zero_duration_compensated(self):
        """duration_us=0 should be auto-filled to 500ms (500_000 us)."""
        pairs = [
            {"start_us": 0, "duration_us": 0,
             "primary_text": "短", "secondary_text": "Short"},
        ]
        result, calls = TestAddDualSubtitlesHappyPath()._run_dual_subtitles(pairs)

        assert result["ok"] is True
        primary_call = next(c for c in calls if c["track_name"] == "Sub_Primary")
        assert primary_call["duration"] == 500_000

    def test_empty_primary_text_skipped(self):
        pairs = [
            {"start_us": 0, "duration_us": 1_000_000,
             "primary_text": "", "secondary_text": "ShouldBeSkipped"},
            {"start_us": 1_000_000, "duration_us": 2_000_000,
             "primary_text": "有效", "secondary_text": "Valid"},
        ]
        result, calls = TestAddDualSubtitlesHappyPath()._run_dual_subtitles(pairs)

        assert result["ok"] is True
        assert result["skipped_pairs"] == 1
        assert len(result["skipped_details"]) == 1
        assert result["skipped_details"][0]["index"] == 0
        assert result["skipped_details"][0]["reason"] == "empty_text"

        # Only 1 valid pair → 1 primary + 1 secondary = 2 calls
        assert len(calls) == 2

    def test_missing_secondary_text_still_creates_primary(self):
        """If secondary_text is empty, primary still gets created; secondary skipped."""
        pairs = [
            {"start_us": 0, "duration_us": 2_000_000,
             "primary_text": "只有中文", "secondary_text": ""},
        ]
        result, calls = TestAddDualSubtitlesHappyPath()._run_dual_subtitles(pairs)

        assert result["ok"] is True
        assert result["skipped_pairs"] == 0  # primary OK → not counted as skipped
        # only primary call (no secondary)
        track_names = [c["track_name"] for c in calls]
        assert track_names == ["Sub_Primary"]

    def test_multiple_empty_accumulate_in_skipped_details(self):
        pairs = [
            {"start_us": 0, "duration_us": 1_000_000,
             "primary_text": "", "secondary_text": "A"},
            {"start_us": 1_000_000, "duration_us": 1_000_000,
             "primary_text": "", "secondary_text": "B"},
            {"start_us": 2_000_000, "duration_us": 1_000_000,
             "primary_text": "OK", "secondary_text": "C"},
        ]
        result, calls = TestAddDualSubtitlesHappyPath()._run_dual_subtitles(pairs)

        assert result["skipped_pairs"] == 2
        assert [d["index"] for d in result["skipped_details"]] == [0, 1]
        assert len(calls) == 2  # 1 primary + 1 secondary for the valid pair

    def test_primary_text_whitespace_only_is_skipped(self):
        pairs = [
            {"start_us": 0, "duration_us": 1_000_000,
             "primary_text": "   ", "secondary_text": "X"},
        ]
        result, calls = TestAddDualSubtitlesHappyPath()._run_dual_subtitles(pairs)

        assert result["skipped_pairs"] == 1
        assert len(calls) == 0

    def test_negative_duration_compensated(self):
        """Negative duration_us should be compensated to min."""
        pairs = [
            {"start_us": 0, "duration_us": -100,
             "primary_text": "负时长", "secondary_text": "Neg"},
        ]
        result, calls = TestAddDualSubtitlesHappyPath()._run_dual_subtitles(pairs)

        assert result["ok"] is True
        primary_call = next(c for c in calls if c["track_name"] == "Sub_Primary")
        assert primary_call["duration"] == 500_000
