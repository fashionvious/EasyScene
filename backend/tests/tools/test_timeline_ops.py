"""
Functional tests for timeline_ops.py.

Covers apply_jcut, apply_lcut, reorder_segments:
- Happy path with real logic verification (not blind mocks)
- Every boundary case from the spec's exception matrix
- JyProject is stubbed at the integration boundary;
  internal parameter calculations are exercised for real.
"""

import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Ensure the jianying-editor-skill scripts can be found by timeline_ops
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_PROJECT_ROOT = _THIS_FILE.parent.parent.parent
_SKILL_SCRIPTS = _PROJECT_ROOT / "jianying-editor-skill" / "scripts"

if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

# ---------------------------------------------------------------------------
# Helpers — build lightweight JyProject stubs
# ---------------------------------------------------------------------------

def _make_stub_segment(segment_id=None, material_duration_us=10_000_000):
    """Return a MagicMock that walks like a pyJianYingDraft segment."""
    sid = segment_id or uuid.uuid4().hex
    seg = MagicMock()
    seg.segment_id = sid
    seg.target_timerange = MagicMock()
    seg.target_timerange.duration = material_duration_us
    seg.material_instance = MagicMock()
    seg.material_instance.duration = material_duration_us
    return seg


def _make_stub_jyproject(segments=None):
    """Return a MagicMock JyProject with working add_media_safe / add_audio_safe."""
    proj = MagicMock()
    proj.root = "/fake/drafts"
    proj.name = "test_project"

    if segments is None:
        segments = []

    def _add_media_side_effect(media_path, start_time=None, duration=None,
                               track_name=None, source_start=None, **_kw):
        seg = _make_stub_segment()
        segments.append(seg)
        return seg

    def _add_audio_side_effect(media_path, start_time=None, duration=None,
                               track_name=None, **_kw):
        seg = _make_stub_segment()
        segments.append(seg)
        return seg

    proj.add_media_safe.side_effect = _add_media_side_effect
    proj.add_audio_safe.side_effect = _add_audio_side_effect
    proj.save.return_value = {"status": "SUCCESS", "draft_path": "/fake/drafts/test_project"}
    return proj


# ---------------------------------------------------------------------------
# Fixtures — setup timeline_ops module under controlled conditions
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_langfuse():
    """Silence LangFuse so tests never hit the network."""
    with patch(
        "app.agent.skills_agent.tools.timeline_ops._get_langfuse",
        return_value=MagicMock(),
    ):
        yield


@pytest.fixture
def fake_video(tmp_path):
    p = tmp_path / "test_video.mp4"
    p.write_bytes(b"\x00" * 1024)
    return str(p)


@pytest.fixture
def fake_audio(tmp_path):
    p = tmp_path / "test_audio.wav"
    p.write_bytes(b"\x00" * 512)
    return str(p)


# ===================================================================
# apply_jcut  tests
# ===================================================================

class TestApplyJcutHappyPath:
    """Happy-path: verifying that the core J-Cut offset calculation is correct."""

    def test_video_offset_equals_audio_lead(self, fake_video, fake_audio):
        """video_actual_start_us must equal audio_lead_us."""
        from app.agent.skills_agent.tools.timeline_ops import apply_jcut

        segments = []

        with patch(
            "app.agent.skills_agent.tools.timeline_ops.JyProject",
            return_value=_make_stub_jyproject(segments),
        ), patch(
            "app.agent.skills_agent.tools.timeline_ops.get_duration_ffprobe_cached",
            return_value=30.0,  # 30s source — plenty
        ):
            result = apply_jcut.invoke({
                "project_name": "JCutTest",
                "video_path": fake_video,
                "audio_path": fake_audio,
                "audio_lead_us": 2_000_000,
                "video_start": "0s",
                "video_duration": "8s",
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        assert result["video_actual_start_us"] == 2_000_000
        assert result["audio_start_us"] == 0

    def test_default_audio_lead_is_1_5_seconds(self, fake_video, fake_audio):
        """Without explicit audio_lead_us, default to 1.5s."""
        from app.agent.skills_agent.tools.timeline_ops import apply_jcut

        segments = []

        with patch(
            "app.agent.skills_agent.tools.timeline_ops.JyProject",
            return_value=_make_stub_jyproject(segments),
        ), patch(
            "app.agent.skills_agent.tools.timeline_ops.get_duration_ffprobe_cached",
            return_value=30.0,
        ):
            result = apply_jcut.invoke({
                "project_name": "JCutDefault",
                "video_path": fake_video,
                "audio_path": fake_audio,
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        assert result["video_actual_start_us"] == 1_500_000

    def test_both_segments_receive_ids(self, fake_video, fake_audio):
        from app.agent.skills_agent.tools.timeline_ops import apply_jcut

        segments = []

        with patch(
            "app.agent.skills_agent.tools.timeline_ops.JyProject",
            return_value=_make_stub_jyproject(segments),
        ), patch(
            "app.agent.skills_agent.tools.timeline_ops.get_duration_ffprobe_cached",
            return_value=30.0,
        ):
            result = apply_jcut.invoke({
                "project_name": "JCutIDs",
                "video_path": fake_video,
                "audio_path": fake_audio,
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        assert result["video_segment_id"] != "unknown"
        assert result["audio_segment_id"] != "unknown"


class TestApplyJcutBoundary:
    """Boundary / error cases — spec exceptions."""

    def test_negative_audio_lead_rejected(self, fake_video, fake_audio):
        from app.agent.skills_agent.tools.timeline_ops import apply_jcut

        result = apply_jcut.invoke({
            "project_name": "JCutNeg",
            "video_path": fake_video,
            "audio_path": fake_audio,
            "audio_lead_us": -500_000,
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_audio_lead"

    def test_zero_audio_lead_rejected(self, fake_video, fake_audio):
        from app.agent.skills_agent.tools.timeline_ops import apply_jcut

        result = apply_jcut.invoke({
            "project_name": "JCutZero",
            "video_path": fake_video,
            "audio_path": fake_audio,
            "audio_lead_us": 0,
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_audio_lead"

    def test_missing_video_file(self, fake_audio):
        from app.agent.skills_agent.tools.timeline_ops import apply_jcut

        result = apply_jcut.invoke({
            "project_name": "JCutNoVid",
            "video_path": "/nonexistent/video.mp4",
            "audio_path": fake_audio,
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "file_not_found"

    def test_missing_audio_file(self, fake_video):
        from app.agent.skills_agent.tools.timeline_ops import apply_jcut

        result = apply_jcut.invoke({
            "project_name": "JCutNoAud",
            "video_path": fake_video,
            "audio_path": "/nonexistent/audio.wav",
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "file_not_found"

    def test_audio_source_too_short(self, fake_video, fake_audio):
        from app.agent.skills_agent.tools.timeline_ops import apply_jcut

        with patch(
            "app.agent.skills_agent.tools.timeline_ops.get_duration_ffprobe_cached",
            return_value=3.0,  # only 3s available
        ):
            result = apply_jcut.invoke({
                "project_name": "JCutShort",
                "video_path": fake_video,
                "audio_path": fake_audio,
                "audio_lead_us": 1_500_000,
                "video_duration": "10s",
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "audio_source_too_short"
        assert "required_us" in result
        assert "available_us" in result


# ===================================================================
# apply_lcut  tests
# ===================================================================

class TestApplyLcutHappyPath:
    """Happy-path for L-Cut."""

    def test_audio_duration_equals_video_plus_tail(self, fake_video, fake_audio):
        from app.agent.skills_agent.tools.timeline_ops import apply_lcut

        segments = []

        with patch(
            "app.agent.skills_agent.tools.timeline_ops.JyProject",
            return_value=_make_stub_jyproject(segments),
        ), patch(
            "app.agent.skills_agent.tools.timeline_ops.get_duration_ffprobe_cached",
            return_value=30.0,
        ):
            result = apply_lcut.invoke({
                "project_name": "LCutTest",
                "video_path": fake_video,
                "audio_path": fake_audio,
                "audio_tail_us": 2_000_000,
                "video_duration": "8s",
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        # 8s video + 2s tail = 10s audio
        assert result["audio_actual_duration_us"] == 10_000_000

    def test_truncated_flag_when_source_too_short(self, fake_video, fake_audio):
        from app.agent.skills_agent.tools.timeline_ops import apply_lcut

        segments = []

        with patch(
            "app.agent.skills_agent.tools.timeline_ops.JyProject",
            return_value=_make_stub_jyproject(segments),
        ), patch(
            "app.agent.skills_agent.tools.timeline_ops.get_duration_ffprobe_cached",
            return_value=5.0,  # only 5s
        ):
            result = apply_lcut.invoke({
                "project_name": "LCutShort",
                "video_path": fake_video,
                "audio_path": fake_audio,
                "audio_tail_us": 2_000_000,
                "video_duration": "10s",
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True  # doesn't fail; just marks truncated
        assert result.get("truncated") is True


class TestApplyLcutBoundary:

    def test_negative_tail_rejected(self, fake_video, fake_audio):
        from app.agent.skills_agent.tools.timeline_ops import apply_lcut

        result = apply_lcut.invoke({
            "project_name": "LCutNeg",
            "video_path": fake_video,
            "audio_path": fake_audio,
            "audio_tail_us": -500_000,
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_audio_tail"

    def test_zero_tail_rejected(self, fake_video, fake_audio):
        from app.agent.skills_agent.tools.timeline_ops import apply_lcut

        result = apply_lcut.invoke({
            "project_name": "LCutZero",
            "video_path": fake_video,
            "audio_path": fake_audio,
            "audio_tail_us": 0,
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_audio_tail"

    def test_missing_video_file(self, fake_audio):
        from app.agent.skills_agent.tools.timeline_ops import apply_lcut

        result = apply_lcut.invoke({
            "project_name": "LCutNoVid",
            "video_path": "/nonexistent/v.mp4",
            "audio_path": fake_audio,
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "file_not_found"

    def test_missing_audio_file(self, fake_video):
        from app.agent.skills_agent.tools.timeline_ops import apply_lcut

        result = apply_lcut.invoke({
            "project_name": "LCutNoAud",
            "video_path": fake_video,
            "audio_path": "/nonexistent/a.wav",
        })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "file_not_found"


# ===================================================================
# reorder_segments  tests
# ===================================================================

_REORDER_DRAFT_JSON = {
    "tracks": [
        {
            "id": "track-001",
            "type": "video",
            "name": "MainVideo",
            "segments": [
                {
                    "id": "seg-0",
                    "material_id": "mat-a",
                    "target_timerange": {"start": 0, "duration": 5_000_000},
                    "source_timerange": {"start": 0, "duration": 5_000_000},
                },
                {
                    "id": "seg-1",
                    "material_id": "mat-b",
                    "target_timerange": {"start": 5_000_000, "duration": 3_000_000},
                    "source_timerange": {"start": 0, "duration": 3_000_000},
                },
                {
                    "id": "seg-2",
                    "material_id": "mat-c",
                    "target_timerange": {"start": 8_000_000, "duration": 7_000_000},
                    "source_timerange": {"start": 2_000_000, "duration": 7_000_000},
                },
            ],
        },
    ],
    "materials": {
        "videos": [
            {"id": "mat-a", "path": "/fake/video_a.mp4"},
            {"id": "mat-b", "path": "/fake/video_b.mp4"},
            {"id": "mat-c", "path": "/fake/video_c.mp4"},
        ],
    },
    "canvas_config": {"width": 1920, "height": 1080},
}


class TestReorderSegmentsHappyPath:

    def test_reorder_preserves_segment_count(self, tmp_path):
        """After reorder, all segments are rebuilt."""
        from app.agent.skills_agent.tools.timeline_ops import reorder_segments

        draft_dir = tmp_path / "ReorderTest"
        draft_dir.mkdir()
        content_path = draft_dir / "draft_content.json"
        content_path.write_text(json.dumps(_REORDER_DRAFT_JSON), encoding="utf-8")

        # Minimal JyProject stub
        proj = MagicMock()
        proj.root = str(tmp_path)
        proj.name = "ReorderTest"
        proj.add_media_safe.return_value = _make_stub_segment()
        proj.save.return_value = {"status": "SUCCESS"}

        with patch(
            "app.agent.skills_agent.tools.timeline_ops.JyProject",
            return_value=proj,
        ):
            result = reorder_segments.invoke({
                "project_name": "ReorderTest",
                "new_order": [2, 0, 1],
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is True
        assert result["reordered_count"] == 3
        assert result["original_order"] == [0, 1, 2]
        assert result["new_order"] == [2, 0, 1]
        # 3 segments rebuilt
        assert proj.add_media_safe.call_count == 3

    def test_backup_created_before_rebuild(self, tmp_path):
        from app.agent.skills_agent.tools.timeline_ops import reorder_segments

        draft_dir = tmp_path / "ReorderBak"
        draft_dir.mkdir()
        content_path = draft_dir / "draft_content.json"
        content_path.write_text(json.dumps(_REORDER_DRAFT_JSON), encoding="utf-8")

        proj = MagicMock()
        proj.root = str(tmp_path)
        proj.name = "ReorderBak"
        proj.add_media_safe.return_value = _make_stub_segment()
        proj.save.return_value = {"status": "SUCCESS"}

        with patch(
            "app.agent.skills_agent.tools.timeline_ops.JyProject",
            return_value=proj,
        ):
            reorder_segments.invoke({
                "project_name": "ReorderBak",
                "new_order": [0, 1, 2],
            })

        # 1 backup .bak file
        baks = list(draft_dir.glob("draft_content.json.bak.*"))
        assert len(baks) == 1


class TestReorderSegmentsBoundary:

    def test_index_out_of_range(self, tmp_path):
        from app.agent.skills_agent.tools.timeline_ops import reorder_segments

        draft_dir = tmp_path / "ReorderOOB"
        draft_dir.mkdir()
        (draft_dir / "draft_content.json").write_text(
            json.dumps(_REORDER_DRAFT_JSON), encoding="utf-8"
        )

        proj = MagicMock()
        proj.root = str(tmp_path)
        proj.name = "ReorderOOB"

        with patch(
            "app.agent.skills_agent.tools.timeline_ops.JyProject",
            return_value=proj,
        ):
            result = reorder_segments.invoke({
                "project_name": "ReorderOOB",
                "new_order": [0, 1, 5],  # 5 is out of range (0-2)
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "index_out_of_range"

    def test_duplicate_indices_rejected(self, tmp_path):
        from app.agent.skills_agent.tools.timeline_ops import reorder_segments

        draft_dir = tmp_path / "ReorderDup"
        draft_dir.mkdir()
        (draft_dir / "draft_content.json").write_text(
            json.dumps(_REORDER_DRAFT_JSON), encoding="utf-8"
        )

        proj = MagicMock()
        proj.root = str(tmp_path)
        proj.name = "ReorderDup"

        with patch(
            "app.agent.skills_agent.tools.timeline_ops.JyProject",
            return_value=proj,
        ):
            result = reorder_segments.invoke({
                "project_name": "ReorderDup",
                "new_order": [0, 1, 0],  # duplicate 0
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_order"

    def test_wrong_length_rejected(self, tmp_path):
        from app.agent.skills_agent.tools.timeline_ops import reorder_segments

        draft_dir = tmp_path / "ReorderLen"
        draft_dir.mkdir()
        (draft_dir / "draft_content.json").write_text(
            json.dumps(_REORDER_DRAFT_JSON), encoding="utf-8"
        )

        proj = MagicMock()
        proj.root = str(tmp_path)
        proj.name = "ReorderLen"

        with patch(
            "app.agent.skills_agent.tools.timeline_ops.JyProject",
            return_value=proj,
        ):
            result = reorder_segments.invoke({
                "project_name": "ReorderLen",
                "new_order": [0, 1],  # only 2 indices for 3 segments
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "invalid_order"

    def test_draft_not_found(self, tmp_path):
        from app.agent.skills_agent.tools.timeline_ops import reorder_segments

        proj = MagicMock()
        proj.root = str(tmp_path)
        proj.name = "NonexistentDraft"

        with patch(
            "app.agent.skills_agent.tools.timeline_ops.JyProject",
            return_value=proj,
        ):
            result = reorder_segments.invoke({
                "project_name": "NonexistentDraft",
                "new_order": [0, 1, 2],
            })

        result = json.loads(result) if isinstance(result, str) else result
        assert result["ok"] is False
        assert result["reason"] == "draft_not_found"
