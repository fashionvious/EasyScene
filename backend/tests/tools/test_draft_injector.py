"""
Functional tests for draft_injector.py — 7 JSON injection tools.

Uses real temp directories with draft_content.json stubs.
Verifies injected JSON nodes, backup creation, and rollback.
"""

import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ===================================================================
# Fixtures — 构建真实的 draft_content.json 桩结构
# ===================================================================

def _make_base_draft() -> dict:
    return {
        "tracks": [
            {
                "id": "track-001",
                "type": "video",
                "name": "MainVideo",
                "segments": [
                    {
                        "id": "seg-video-1",
                        "material_id": "mat-v-1",
                        "target_timerange": {"start": 0, "duration": 5_000_000},
                        "source_timerange": {"start": 0, "duration": 5_000_000},
                        "common_keyframes": [],
                        "extra_material_refs": [],
                    },
                    {
                        "id": "seg-video-2",
                        "material_id": "mat-v-2",
                        "target_timerange": {"start": 5_000_000, "duration": 5_000_000},
                        "source_timerange": {"start": 0, "duration": 5_000_000},
                        "common_keyframes": [],
                        "extra_material_refs": [],
                    },
                ],
            },
            {
                "id": "track-002",
                "type": "audio",
                "name": "BGM",
                "segments": [
                    {
                        "id": "seg-bgm-1",
                        "material_id": "mat-a-1",
                        "target_timerange": {"start": 0, "duration": 15_000_000},
                        "source_timerange": {"start": 0, "duration": 15_000_000},
                        "common_keyframes": [],
                        "extra_material_refs": [],
                    },
                ],
            },
        ],
        "materials": {
            "videos": [
                {"id": "mat-v-1", "path": "/fake/video_a.mp4"},
                {"id": "mat-v-2", "path": "/fake/video_b.mp4"},
            ],
            "audios": [
                {"id": "mat-a-1", "path": "/fake/bgm.mp3"},
            ],
        },
        "canvas_config": {"width": 1920, "height": 1080},
    }


@pytest.fixture
def draft_dir(tmp_path):
    """Creates a real draft directory with draft_content.json."""
    d = tmp_path / "TestDraft"
    d.mkdir()
    content = d / "draft_content.json"
    content.write_text(json.dumps(_make_base_draft()), encoding="utf-8")
    meta = d / "draft_meta_info.json"
    meta.write_text("{}", encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def _patch_langfuse():
    with patch(
        "app.agent.skills_agent.tools.draft_injector._get_langfuse",
        return_value=MagicMock(),
    ):
        yield


def _make_jyproject_stub(draft_dir, cloud_audio=None, cloud_text=None):
    """构建 JyProject 桩，指向真实草稿目录。"""
    proj = MagicMock()
    proj.root = str(draft_dir.parent)
    proj.name = draft_dir.name
    proj._cloud_audio_patches = cloud_audio or {}
    proj._cloud_text_patches = cloud_text or {}

    def _fake_patch_cloud():
        pass

    def _fake_force_adjust():
        pass

    proj._patch_cloud_material_ids.side_effect = _fake_patch_cloud
    proj._force_activate_adjustments.side_effect = _fake_force_adjust
    return proj


# ===================================================================
# inject_mask_transition
# ===================================================================

class TestInjectMaskTransition:

    def test_mask_node_injected_correctly(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import (
            inject_mask_transition,
        )

        proj = _make_jyproject_stub(draft_dir)
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            result = inject_mask_transition.invoke({
                "project_name": draft_dir.name,
                "segment_id": "seg-video-1",
                "direction": "left_to_right",
                "feather": 0.2,
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        assert result["keyframes_count"] == 2
        assert result["direction"] == "left_to_right"

        # 验证 JSON
        content = json.loads(
            (draft_dir / "draft_content.json").read_text(encoding="utf-8")
        )
        seg = content["tracks"][0]["segments"][0]
        assert "mask" in seg
        mask = seg["mask"]
        assert mask["type"] == "mask"
        assert mask["resource_id"] == "636071"
        assert mask["platform"] == "all"
        assert mask["config"]["feather"] == 0.2

        # 验证关键帧
        kfs = seg["common_keyframes"]
        mask_kf = next(k for k in kfs if k["property_type"] == "KFTypeMaskCenterX")
        assert len(mask_kf["keyframe_list"]) == 2

    def test_segment_not_found(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import (
            inject_mask_transition,
        )

        proj = _make_jyproject_stub(draft_dir)
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            result = inject_mask_transition.invoke({
                "project_name": draft_dir.name,
                "segment_id": "nonexistent-id",
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "segment_not_found"

    def test_mask_already_exists_rejected(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import (
            inject_mask_transition,
        )

        proj = _make_jyproject_stub(draft_dir)
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            # first injection succeeds
            r1 = inject_mask_transition.invoke({
                "project_name": draft_dir.name,
                "segment_id": "seg-video-1",
            })
            # second on same segment fails
            r2 = inject_mask_transition.invoke({
                "project_name": draft_dir.name,
                "segment_id": "seg-video-1",
            })

        r1 = json.loads(r1) if isinstance(r1, str) else r1
        r2 = json.loads(r2) if isinstance(r2, str) else r2
        assert r1["ok"] is True
        assert r2["ok"] is False
        assert r2["reason"] == "mask_already_exists"

    def test_backup_created_on_success(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import (
            inject_mask_transition,
        )

        proj = _make_jyproject_stub(draft_dir)
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            inject_mask_transition.invoke({
                "project_name": draft_dir.name,
                "segment_id": "seg-video-1",
            })

        baks = list(draft_dir.glob("draft_content.json.bak.*"))
        assert len(baks) == 1


# ===================================================================
# inject_color_transition
# ===================================================================

class TestInjectColorTransition:

    def test_color_material_inserted(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import (
            inject_color_transition,
        )

        proj = _make_jyproject_stub(draft_dir)
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            result = inject_color_transition.invoke({
                "project_name": draft_dir.name,
                "seg1_id": "seg-video-1",
                "seg2_id": "seg-video-2",
                "color": "#FF0000",
                "duration_us": 300_000,
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True

        content = json.loads(
            (draft_dir / "draft_content.json").read_text(encoding="utf-8")
        )

        # 验证 color material 在 materials.videos 中
        videos = content["materials"]["videos"]
        color_mats = [m for m in videos if m.get("type") == "color"]
        assert len(color_mats) == 1
        assert color_mats[0]["color"] == "#FF0000"

        # 验证 ColorTrack 存在
        color_tracks = [t for t in content["tracks"] if t["name"] == "ColorTrack"]
        assert len(color_tracks) == 1
        assert len(color_tracks[0]["segments"]) == 1

    def test_invalid_color_format_rejected(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import (
            inject_color_transition,
        )

        proj = _make_jyproject_stub(draft_dir)
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            result = inject_color_transition.invoke({
                "project_name": draft_dir.name,
                "seg1_id": "seg-video-1",
                "seg2_id": "seg-video-2",
                "color": "red",  # invalid
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_color_format"


# ===================================================================
# inject_subtitle_slide
# ===================================================================

class TestInjectSubtitleSlide:

    def _draft_with_text_segment(self, draft_dir):
        """修改 draft 使其包含一条 text track。"""
        content = json.loads(
            (draft_dir / "draft_content.json").read_text(encoding="utf-8")
        )
        content["tracks"].append({
            "id": "track-text-1",
            "type": "text",
            "name": "Subtitles",
            "segments": [
                {
                    "id": "seg-text-1",
                    "material_id": "mat-text-1",
                    "target_timerange": {"start": 0, "duration": 2_000_000},
                    "common_keyframes": [],
                    "extra_material_refs": [],
                },
            ],
        })
        (draft_dir / "draft_content.json").write_text(
            json.dumps(content), encoding="utf-8"
        )
        return content

    def test_position_kf_injected(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import (
            inject_subtitle_slide,
        )

        self._draft_with_text_segment(draft_dir)
        proj = _make_jyproject_stub(draft_dir)

        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            result = inject_subtitle_slide.invoke({
                "project_name": draft_dir.name,
                "segment_id": "seg-text-1",
                "slide_from": "right",
                "slide_distance": 1.5,
                "duration_us": 500_000,
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        assert result["overwritten"] is False
        assert result["keyframes"][0]["value"] == 1.5  # right → +distance
        assert result["keyframes"][1]["value"] == 0.0

        # 验证 JSON
        content = json.loads(
            (draft_dir / "draft_content.json").read_text(encoding="utf-8")
        )
        text_seg = next(
            s for t in content["tracks"] if t["type"] == "text"
            for s in t["segments"] if s["id"] == "seg-text-1"
        )
        kfs = text_seg["common_keyframes"]
        pos_kf = next(k for k in kfs if k["property_type"] == "KFTypePositionX")
        assert pos_kf["keyframe_list"][0]["values"] == [1.5]

    def test_invalid_direction_rejected(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import (
            inject_subtitle_slide,
        )

        self._draft_with_text_segment(draft_dir)
        proj = _make_jyproject_stub(draft_dir)

        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            result = inject_subtitle_slide.invoke({
                "project_name": draft_dir.name,
                "segment_id": "seg-text-1",
                "slide_from": "north",
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_slide_direction"


# ===================================================================
# apply_bgm_ducking
# ===================================================================

class TestApplyBgmDucking:

    def test_volume_kfs_injected(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import apply_bgm_ducking

        proj = _make_jyproject_stub(draft_dir)
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            result = apply_bgm_ducking.invoke({
                "project_name": draft_dir.name,
                "voice_segments": [
                    {"start_us": 2_000_000, "end_us": 5_000_000},
                    {"start_us": 8_000_000, "end_us": 12_000_000},
                ],
                "bgm_track_name": "BGM",
                "duck_volume": 0.2,
                "fade_us": 200_000,
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        assert result["duck_windows"] == 2
        assert result["keyframes_generated"] == 8  # 4 kfs per window

        # 验证 JSON
        content = json.loads(
            (draft_dir / "draft_content.json").read_text(encoding="utf-8")
        )
        bgm_seg = content["tracks"][1]["segments"][0]  # BGM track
        vol_kfs = [
            k for k in bgm_seg["common_keyframes"]
            if k["property_type"] == "KFTypeVolume"
        ]
        assert len(vol_kfs) == 1
        assert len(vol_kfs[0]["keyframe_list"]) == 8

    def test_track_not_found(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import apply_bgm_ducking

        proj = _make_jyproject_stub(draft_dir)
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            result = apply_bgm_ducking.invoke({
                "project_name": draft_dir.name,
                "voice_segments": [{"start_us": 0, "end_us": 1_000_000}],
                "bgm_track_name": "NonexistentTrack",
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "track_not_found"


# ===================================================================
# apply_audio_speed
# ===================================================================

class TestApplyAudioSpeed:

    def test_speed_applied_and_segments_shifted(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import apply_audio_speed

        proj = _make_jyproject_stub(draft_dir)
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            result = apply_audio_speed.invoke({
                "project_name": draft_dir.name,
                "track_name": "BGM",
                "segment_index": 0,
                "speed": 2.0,
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        assert result["speed"] == 2.0
        assert result["new_target_duration_us"] == 7_500_000  # 15M / 2

        content = json.loads(
            (draft_dir / "draft_content.json").read_text(encoding="utf-8")
        )
        seg = content["tracks"][1]["segments"][0]
        assert seg["speed"] == 2.0
        assert seg["target_timerange"]["duration"] == 7_500_000

    def test_speed_out_of_range_rejected(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import apply_audio_speed

        proj = _make_jyproject_stub(draft_dir)
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            result = apply_audio_speed.invoke({
                "project_name": draft_dir.name,
                "track_name": "BGM",
                "segment_index": 0,
                "speed": 15.0,
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_speed"


# ===================================================================
# apply_speed_ramp
# ===================================================================

class TestApplySpeedRamp:

    def test_speed_curve_injected(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import apply_speed_ramp

        proj = _make_jyproject_stub(draft_dir)
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            result = apply_speed_ramp.invoke({
                "project_name": draft_dir.name,
                "segment_id": "seg-video-1",
                "speed_curve": [
                    {"target_time_us": 0, "speed": 0.5},
                    {"target_time_us": 1_000_000, "speed": 1.0},
                    {"target_time_us": 2_000_000, "speed": 2.0},
                ],
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        assert result["curve_points"] == 3

        content = json.loads(
            (draft_dir / "draft_content.json").read_text(encoding="utf-8")
        )
        seg = content["tracks"][0]["segments"][0]
        speed_kfs = [
            k for k in seg["common_keyframes"]
            if k["property_type"] == "KFTypeSpeed"
        ]
        assert len(speed_kfs) == 1
        assert len(speed_kfs[0]["keyframe_list"]) == 3

    def test_invalid_speed_in_curve_rejected(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import apply_speed_ramp

        proj = _make_jyproject_stub(draft_dir)
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ):
            result = apply_speed_ramp.invoke({
                "project_name": draft_dir.name,
                "segment_id": "seg-video-1",
                "speed_curve": [
                    {"target_time_us": 0, "speed": 50.0},
                ],
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_speed"


# ===================================================================
# Rollback on exception
# ===================================================================

class TestRollback:

    def test_rollback_restores_original_json(self, draft_dir):
        from app.agent.skills_agent.tools.draft_injector import (
            inject_mask_transition,
        )

        original = (draft_dir / "draft_content.json").read_text(encoding="utf-8")

        proj = _make_jyproject_stub(draft_dir)
        # Make the _find_segment lookup work but then force a crash during injection
        with patch(
            "app.agent.skills_agent.tools.draft_injector._JyProject",
            return_value=proj,
        ), patch(
            "app.agent.skills_agent.tools.draft_injector._write_json",
            side_effect=RuntimeError("simulated disk error"),
        ):
            result = inject_mask_transition.invoke({
                "project_name": draft_dir.name,
                "segment_id": "seg-video-1",
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "RuntimeError"

        # JSON 应该和原始一致
        restored = (draft_dir / "draft_content.json").read_text(encoding="utf-8")
        assert restored == original
