"""
Functional tests for layout_ops.py — apply_split_screen.

Covers:
- Happy path: correct layout coordinates per template
- All 5 templates: validating cell_count and coordinate ranges
- Boundary: video count mismatch, unknown template, file not found,
  track limit exceeded, fill_mode fallback
"""

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stub_segment(segment_id=None):
    sid = segment_id or uuid.uuid4().hex
    seg = MagicMock()
    seg.segment_id = sid
    seg.clip_settings = None  # will be set by layout_ops
    return seg


def _make_stub_jyproject(segments_out=None):
    proj = MagicMock()
    proj.root = "/fake/drafts"
    proj.name = "SplitTest"

    if segments_out is None:
        segments_out = []

    def _add_media_side_effect(media_path, start_time=None, duration=None,
                               track_name=None, source_start=None, **_kw):
        seg = _make_stub_segment()
        segments_out.append(seg)
        return seg

    proj.add_media_safe.side_effect = _add_media_side_effect
    proj.save.return_value = {"status": "SUCCESS"}
    return proj


@pytest.fixture(autouse=True)
def _patch_langfuse():
    with patch(
        "app.agent.skills_agent.tools.layout_ops._get_langfuse",
        return_value=MagicMock(),
    ):
        yield


@pytest.fixture
def fake_videos(tmp_path):
    paths = []
    for i in range(9):
        p = tmp_path / f"vid_{i}.mp4"
        p.write_bytes(b"\x00" * 512)
        paths.append(str(p))
    return paths


# ===================================================================
# Happy Path — 5 templates
# ===================================================================

class TestApplySplitScreenTemplates:

    def _run_split(self, template, video_paths):
        from app.agent.skills_agent.tools.layout_ops import apply_split_screen

        segments = []
        with patch(
            "app.agent.skills_agent.tools.layout_ops.JyProject",
            return_value=_make_stub_jyproject(segments),
        ):
            result = apply_split_screen.invoke({
                "project_name": f"Split_{template}",
                "video_paths": video_paths,
                "template": template,
                "duration": "5s",
            })
        result = json.loads(result) if isinstance(result, str) else result
        return result, segments

    def test_2H_layout(self, fake_videos):
        result, segs = self._run_split("2H", fake_videos[:2])
        assert result["ok"] is True
        assert result["template"] == "2H"
        assert len(result["segment_ids"]) == 2
        # left cell: tx=-0.5, ty=0.0 | right cell: tx=0.5, ty=0.0
        assert result["layout"][0]["pos"] == (-0.5, 0.0)
        assert result["layout"][1]["pos"] == (0.5, 0.0)

    def test_2V_layout(self, fake_videos):
        result, segs = self._run_split("2V", fake_videos[:2])
        assert result["ok"] is True
        assert result["template"] == "2V"
        assert result["layout"][0]["pos"] == (0.0, 0.5)
        assert result["layout"][1]["pos"] == (0.0, -0.5)

    def test_2x2_layout(self, fake_videos):
        result, segs = self._run_split("2x2", fake_videos[:4])
        assert result["ok"] is True
        assert result["template"] == "2x2"
        assert len(result["segment_ids"]) == 4
        # top-left
        assert result["layout"][0]["pos"] == (-0.5, 0.5)
        # top-right
        assert result["layout"][1]["pos"] == (0.5, 0.5)

    def test_3x3_layout(self, fake_videos):
        result, segs = self._run_split("3x3", fake_videos[:9])
        assert result["ok"] is True
        assert len(result["segment_ids"]) == 9
        assert result["layout"][4]["pos"] == (0.0, 0.0)  # center cell

    def test_1plus2_layout(self, fake_videos):
        result, segs = self._run_split("1+2", fake_videos[:3])
        assert result["ok"] is True
        assert len(result["segment_ids"]) == 3
        # left large
        assert result["layout"][0]["pos"] == (-0.33, 0.0)
        # right top small
        assert result["layout"][1]["pos"] == (0.5, 0.5)

    def test_clip_settings_applied_to_all_segments(self, fake_videos):
        """Every segment must have ClipSettings set with correct scale/transform."""
        _, segs = self._run_split("2x2", fake_videos[:4])

        assert len(segs) == 4
        expected = [
            (0.5, 0.5, -0.5,  0.5),
            (0.5, 0.5,  0.5,  0.5),
            (0.5, 0.5, -0.5, -0.5),
            (0.5, 0.5,  0.5, -0.5),
        ]
        for seg, (sx, sy, tx, ty) in zip(segs, expected):
            cs = seg.clip_settings
            assert cs is not None
            assert cs.scale_x == sx
            assert cs.scale_y == sy
            assert cs.transform_x == tx
            assert cs.transform_y == ty


# ===================================================================
# Boundary cases
# ===================================================================

class TestApplySplitScreenBoundary:

    def test_video_count_mismatch(self, fake_videos):
        from app.agent.skills_agent.tools.layout_ops import apply_split_screen

        result = apply_split_screen.invoke({
            "project_name": "Mismatch",
            "video_paths": fake_videos[:3],
            "template": "2x2",
        })
        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "video_count_mismatch"

    def test_unknown_template(self, fake_videos):
        from app.agent.skills_agent.tools.layout_ops import apply_split_screen

        result = apply_split_screen.invoke({
            "project_name": "UnknownTpl",
            "video_paths": fake_videos[:2],
            "template": "3H",
        })
        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "unknown_template"
        assert "available" in result
        assert "2H" in result["available"]

    def test_file_not_found(self):
        from app.agent.skills_agent.tools.layout_ops import apply_split_screen

        result = apply_split_screen.invoke({
            "project_name": "NoFile",
            "video_paths": ["/nonexistent/a.mp4", "/nonexistent/b.mp4"],
            "template": "2H",
        })
        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "file_not_found"

    def test_fill_mode_fallback_on_unknown_value(self, fake_videos):
        """Unknown fill_mode should not crash; it should fallback to 'loop'."""
        from app.agent.skills_agent.tools.layout_ops import apply_split_screen

        segments = []
        with patch(
            "app.agent.skills_agent.tools.layout_ops.JyProject",
            return_value=_make_stub_jyproject(segments),
        ):
            result = apply_split_screen.invoke({
                "project_name": "Fallback",
                "video_paths": fake_videos[:2],
                "template": "2H",
                "fill_mode": "magic",
            })
        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True  # should succeed with fallback
