"""
Functional tests for transition_ops.py —
apply_zoom_transition and apply_push_transition.

Covers:
- Happy path: correct keyframe property/values, ramp clamping
- All 4 push directions: left/right/up/down → correct position_x/y + sign
- Boundary: invalid zoom_peak, invalid direction, file not found,
  ramp clamping at 20%
- Keyframe enum enforcement: no strings allowed
"""

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stub_segment(segment_id=None, duration_us=5_000_000):
    sid = segment_id or uuid.uuid4().hex
    seg = MagicMock()
    seg.segment_id = sid
    seg.target_timerange = MagicMock()
    seg.target_timerange.duration = duration_us
    seg.material_instance = MagicMock()
    seg.material_instance.duration = duration_us
    return seg


def _make_stub_jyproject(segments_out=None):
    if segments_out is None:
        segments_out = []

    proj = MagicMock()
    proj.root = "/fake/drafts"
    proj.name = "TransitionTest"

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
        "app.agent.skills_agent.tools.transition_ops._get_langfuse",
        return_value=MagicMock(),
    ):
        yield


@pytest.fixture
def fake_videos(tmp_path):
    p1 = tmp_path / "vid_a.mp4"
    p2 = tmp_path / "vid_b.mp4"
    p1.write_bytes(b"\x00" * 512)
    p2.write_bytes(b"\x00" * 512)
    return str(p1), str(p2)


# ===================================================================
# apply_zoom_transition
# ===================================================================

class TestApplyZoomTransitionHappyPath:

    def test_happy_path_produces_correct_keys(self, fake_videos):
        from app.agent.skills_agent.tools.transition_ops import apply_zoom_transition

        segments = []
        with patch(
            "app.agent.skills_agent.tools.transition_ops.JyProject",
            return_value=_make_stub_jyproject(segments),
        ):
            result = apply_zoom_transition.invoke({
                "project_name": "ZoomTest",
                "video_path_1": fake_videos[0],
                "video_path_2": fake_videos[1],
                "zoom_peak": 2.0,
                "ramp_duration_us": 200_000,
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        assert result["seg1_id"] != "unknown"
        assert result["seg2_id"] != "unknown"
        assert result["seg1_end_scale"] == 2.0
        assert result["seg2_start_scale"] == 2.0
        assert result["ramp_duration_us"] == 200_000
        assert result["ramp_clamped"] is False

    def test_uses_default_zoom_peak_1_5(self, fake_videos):
        from app.agent.skills_agent.tools.transition_ops import apply_zoom_transition

        segments = []
        with patch(
            "app.agent.skills_agent.tools.transition_ops.JyProject",
            return_value=_make_stub_jyproject(segments),
        ):
            result = apply_zoom_transition.invoke({
                "project_name": "ZoomDefault",
                "video_path_1": fake_videos[0],
                "video_path_2": fake_videos[1],
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["seg1_end_scale"] == 1.5
        assert result["seg2_start_scale"] == 1.5

    def test_keyframe_enum_used_not_string(self, fake_videos):
        """Position keyframes must use KeyframeProperty enum."""
        from app.agent.skills_agent.tools.transition_ops import apply_zoom_transition

        seg1 = _make_stub_segment()
        seg2 = _make_stub_segment()
        proj = MagicMock()
        proj.add_media_safe.side_effect = [seg1, seg2]
        proj.save.return_value = {"status": "SUCCESS"}

        with patch(
            "app.agent.skills_agent.tools.transition_ops.JyProject",
            return_value=proj,
        ):
            apply_zoom_transition.invoke({
                "project_name": "EnumTest",
                "video_path_1": fake_videos[0],
                "video_path_2": fake_videos[1],
                "zoom_peak": 1.3,
                "ramp_duration_us": 100_000,
            })

        # Verify add_keyframe received KeyframeProperty enum, not a string
        call_args_list = seg1.add_keyframe.call_args_list
        for call in call_args_list:
            prop = call[0][0]  # first positional arg
            assert not isinstance(prop, str), (
                f"add_keyframe received string '{prop}' instead of KeyframeProperty enum"
            )

    def test_ramp_clamps_at_20_percent(self, fake_videos):
        """When ramp > 20% of seg duration, it must be clamped."""
        from app.agent.skills_agent.tools.transition_ops import apply_zoom_transition

        # segment duration = 5_000_000 us, 20% = 1_000_000 us
        seg1 = _make_stub_segment(duration_us=5_000_000)
        seg2 = _make_stub_segment()
        proj = MagicMock()
        proj.add_media_safe.side_effect = [seg1, seg2]
        proj.save.return_value = {"status": "SUCCESS"}

        with patch(
            "app.agent.skills_agent.tools.transition_ops.JyProject",
            return_value=proj,
        ):
            result = apply_zoom_transition.invoke({
                "project_name": "ClampTest",
                "video_path_1": fake_videos[0],
                "video_path_2": fake_videos[1],
                "ramp_duration_us": 2_000_000,  # 40%, should clamp to 20%=1_000_000
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        assert result["ramp_clamped"] is True
        assert result["ramp_duration_us"] == 1_000_000  # clamped at 20%


class TestApplyZoomTransitionBoundary:

    def test_zoom_peak_le_1_rejected(self, fake_videos):
        from app.agent.skills_agent.tools.transition_ops import apply_zoom_transition

        result = apply_zoom_transition.invoke({
            "project_name": "BadZoom",
            "video_path_1": fake_videos[0],
            "video_path_2": fake_videos[1],
            "zoom_peak": 0.8,
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_zoom_peak"

    def test_zoom_peak_equal_1_rejected(self, fake_videos):
        from app.agent.skills_agent.tools.transition_ops import apply_zoom_transition

        result = apply_zoom_transition.invoke({
            "project_name": "OneZoom",
            "video_path_1": fake_videos[0],
            "video_path_2": fake_videos[1],
            "zoom_peak": 1.0,
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_zoom_peak"

    def test_file_not_found_video_1(self, fake_videos):
        from app.agent.skills_agent.tools.transition_ops import apply_zoom_transition

        result = apply_zoom_transition.invoke({
            "project_name": "NoFile1",
            "video_path_1": "/nonexistent/v.mp4",
            "video_path_2": fake_videos[1],
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "file_not_found"

    def test_file_not_found_video_2(self, fake_videos):
        from app.agent.skills_agent.tools.transition_ops import apply_zoom_transition

        result = apply_zoom_transition.invoke({
            "project_name": "NoFile2",
            "video_path_1": fake_videos[0],
            "video_path_2": "/nonexistent/v.mp4",
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "file_not_found"


# ===================================================================
# apply_push_transition
# ===================================================================

class TestApplyPushTransitionHappyPath:

    def _run_push(self, direction, fake_videos):
        from app.agent.skills_agent.tools.transition_ops import apply_push_transition

        segments = []
        with patch(
            "app.agent.skills_agent.tools.transition_ops.JyProject",
            return_value=_make_stub_jyproject(segments),
        ):
            result = apply_push_transition.invoke({
                "project_name": f"Push_{direction}",
                "video_path_1": fake_videos[0],
                "video_path_2": fake_videos[1],
                "direction": direction,
                "ramp_duration_us": 200_000,
            })

        result = json.loads(result) if isinstance(result, str) else result
        return result, segments

    def test_left_direction_maps_correctly(self, fake_videos):
        result, _ = self._run_push("left", fake_videos)
        assert result["ok"] is True
        assert result["direction"] == "left"
        assert result["seg1_end_pos"] == -1.0
        assert result["seg2_start_pos"] == 1.0

    def test_right_direction_maps_correctly(self, fake_videos):
        result, _ = self._run_push("right", fake_videos)
        assert result["ok"] is True
        assert result["seg1_end_pos"] == 1.0
        assert result["seg2_start_pos"] == -1.0

    def test_up_direction_maps_correctly(self, fake_videos):
        result, _ = self._run_push("up", fake_videos)
        assert result["ok"] is True
        assert result["seg1_end_pos"] == 1.0
        assert result["seg2_start_pos"] == -1.0

    def test_down_direction_maps_correctly(self, fake_videos):
        result, _ = self._run_push("down", fake_videos)
        assert result["ok"] is True
        assert result["seg1_end_pos"] == -1.0
        assert result["seg2_start_pos"] == 1.0

    def test_keyframe_enum_used_for_position(self, fake_videos):
        from app.agent.skills_agent.tools.transition_ops import apply_push_transition

        seg1 = _make_stub_segment()
        seg2 = _make_stub_segment()
        proj = MagicMock()
        proj.add_media_safe.side_effect = [seg1, seg2]
        proj.save.return_value = {"status": "SUCCESS"}

        with patch(
            "app.agent.skills_agent.tools.transition_ops.JyProject",
            return_value=proj,
        ):
            apply_push_transition.invoke({
                "project_name": "PushEnum",
                "video_path_1": fake_videos[0],
                "video_path_2": fake_videos[1],
                "direction": "right",
            })

        for call in seg1.add_keyframe.call_args_list:
            prop = call[0][0]
            assert not isinstance(prop, str), (
                f"add_keyframe received string '{prop}' instead of KeyframeProperty enum"
            )

    def test_ramp_clamps_for_push(self, fake_videos):
        from app.agent.skills_agent.tools.transition_ops import apply_push_transition

        seg1 = _make_stub_segment(duration_us=5_000_000)
        seg2 = _make_stub_segment()
        proj = MagicMock()
        proj.add_media_safe.side_effect = [seg1, seg2]
        proj.save.return_value = {"status": "SUCCESS"}

        with patch(
            "app.agent.skills_agent.tools.transition_ops.JyProject",
            return_value=proj,
        ):
            result = apply_push_transition.invoke({
                "project_name": "PushClamp",
                "video_path_1": fake_videos[0],
                "video_path_2": fake_videos[1],
                "direction": "up",
                "ramp_duration_us": 1_500_000,  # 30% → clamp to 20%=1_000_000
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        assert result["ramp_clamped"] is True
        assert result["ramp_duration_us"] == 1_000_000


class TestApplyPushTransitionBoundary:

    def test_invalid_direction_rejected(self, fake_videos):
        from app.agent.skills_agent.tools.transition_ops import apply_push_transition

        result = apply_push_transition.invoke({
            "project_name": "BadDir",
            "video_path_1": fake_videos[0],
            "video_path_2": fake_videos[1],
            "direction": "diagonal",
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_direction"

    def test_empty_direction_rejected(self, fake_videos):
        from app.agent.skills_agent.tools.transition_ops import apply_push_transition

        result = apply_push_transition.invoke({
            "project_name": "EmptyDir",
            "video_path_1": fake_videos[0],
            "video_path_2": fake_videos[1],
            "direction": "",
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_direction"

    def test_file_not_found_for_push(self, fake_videos):
        from app.agent.skills_agent.tools.transition_ops import apply_push_transition

        result = apply_push_transition.invoke({
            "project_name": "PushNoFile",
            "video_path_1": fake_videos[0],
            "video_path_2": "/nonexistent/b.mp4",
            "direction": "left",
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "file_not_found"


# ===================================================================
# Composite — both tools can be applied to the same segment pair
# ===================================================================

class TestCompositeTransition:

    def test_zoom_then_push_no_conflict(self, fake_videos):
        """Applying zoom then push to same segment pair must not conflict — each
        property_type has its own keyframe list."""
        from app.agent.skills_agent.tools.transition_ops import (
            apply_zoom_transition,
            apply_push_transition,
        )

        seg1 = _make_stub_segment(duration_us=5_000_000)
        seg2 = _make_stub_segment()
        proj = MagicMock()
        proj.add_media_safe.side_effect = [seg1, seg2, seg1, seg2]
        proj.save.return_value = {"status": "SUCCESS"}

        with patch(
            "app.agent.skills_agent.tools.transition_ops.JyProject",
            return_value=proj,
        ):
            r1 = apply_zoom_transition.invoke({
                "project_name": "Composite",
                "video_path_1": fake_videos[0],
                "video_path_2": fake_videos[1],
            })
            r2 = apply_push_transition.invoke({
                "project_name": "Composite",
                "video_path_1": fake_videos[0],
                "video_path_2": fake_videos[1],
            })

        r1 = json.loads(r1) if isinstance(r1, str) else r1
        r2 = json.loads(r2) if isinstance(r2, str) else r2
        assert r1["ok"] is True
        assert r2["ok"] is True
        # Keyframes on seg1: uniform_scale (from zoom) + position_x (from push — left default)
        assert seg1.add_keyframe.call_count == 4  # 2 zoom + 2 push
