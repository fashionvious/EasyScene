"""
draft_injector.py — Phase 2 JSON 注入工具

直接操作 draft_content.json 实现剪映 API 不支持的高级能力：
- 遮罩转场 / 颜色过渡转场 / 逐字高亮字幕
- 动态字幕条滑入 / BGM 音量闪避 / 音频变速 / 曲线变速

架构约束：
1. 备份优先 — 修改前创建 .bak.{timestamp}
2. 注入后不调用 JyProject.save()（会覆盖注入），改为直接调用
   _patch_cloud_material_ids + _force_activate_adjustments
3. 异常时回滚到备份
"""

import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from langchain.tools import tool

# ---------------------------------------------------------------------------
# 路径注入
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = None
for _candidate in [
    _THIS_DIR.parent.parent.parent.parent / "jianying-editor-skill" / "scripts",
    _THIS_DIR.parent.parent.parent.parent.parent / "backend" / "jianying-editor-skill" / "scripts",
]:
    if (_candidate / "jy_wrapper.py").exists():
        _SKILL_ROOT = str(_candidate)
        break

if _SKILL_ROOT is None:
    raise ImportError("Cannot locate jianying-editor-skill/scripts/jy_wrapper.py")
if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)

_VENDOR_DIR = str(Path(_SKILL_ROOT) / "vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

# ---------------------------------------------------------------------------
# 延迟导入
# ---------------------------------------------------------------------------
_JyProject = None
_safe_tim = None
_ClipSettings = None
_TextStyle = None


def _ensure_imports():
    global _JyProject, _safe_tim, _ClipSettings, _TextStyle
    if _JyProject is None:
        from jy_wrapper import JyProject as _JP
        _JyProject = _JP
    if _safe_tim is None:
        from utils.formatters import safe_tim as _st
        _safe_tim = _st
    if _ClipSettings is None:
        import pyJianYingDraft as _draft
        _ClipSettings = _draft.ClipSettings
        _TextStyle = _draft.TextStyle


# ---------------------------------------------------------------------------
# LangFuse
# ---------------------------------------------------------------------------
_langfuse = None


class _NoopSpan:
    def update(self, **kwargs): pass
    def end(self): pass
    def start_observation(self, name, as_type=None, **kwargs) -> "_NoopSpan": return _NoopSpan()


class _NoopTrace:
    def start_observation(self, name, as_type=None, **kwargs) -> _NoopSpan: return _NoopSpan()
    def update(self, **kwargs): pass
    def end(self): pass


class _NoopLangfuse:
    def start_observation(self, name, as_type=None, **kwargs) -> _NoopTrace: return _NoopTrace()


def _get_langfuse():
    global _langfuse
    if _langfuse is not None:
        return _langfuse
    try:
        from langfuse import Langfuse
        _langfuse = Langfuse(
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
            public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
            host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
    except Exception:
        _langfuse = _NoopLangfuse()
    return _langfuse


# ---------------------------------------------------------------------------
# 标准化出参
# ---------------------------------------------------------------------------

def _ok(**kwargs) -> dict:
    d = {"ok": True}
    d.update(kwargs)
    return d


def _fail(reason: str, detail: str, **extra) -> dict:
    d = {"ok": False, "reason": reason, "detail": detail}
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# 核心 — 备份 / 加载 / 收尾（改写 JyProject.save() 避免 script.save() 覆盖注入）
# ---------------------------------------------------------------------------

_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


def _load_project(project_name: str):
    """加载已有草稿，返回 (project, content_path)。"""
    _ensure_imports()
    project = _JyProject(project_name)
    content_path = os.path.join(project.root, project.name, "draft_content.json")
    if not os.path.exists(content_path):
        raise FileNotFoundError(f"Draft '{project_name}' not found at {content_path}")
    return project, content_path


def _backup(content_path: str) -> str:
    """创建 .bak.{timestamp}，返回 bak_path。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = f"{content_path}.bak.{ts}"
    shutil.copy2(content_path, bak_path)
    return bak_path


def _read_json(content_path: str) -> dict:
    with open(content_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(data: dict, content_path: str) -> None:
    with open(content_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _finalize(project, content_path: str) -> None:
    """写入 JSON 后的收尾：仅调用必需的 patch 函数，不触发 script.save()。"""
    try:
        project._patch_cloud_material_ids()
    except Exception:
        pass
    try:
        project._force_activate_adjustments()
    except Exception:
        pass
    draft_dir = os.path.join(project.root, project.name)
    if os.path.isdir(draft_dir):
        try:
            os.utime(draft_dir, None)
        except OSError:
            pass


def _rollback(bak_path: str, content_path: str) -> None:
    if os.path.exists(bak_path):
        shutil.copy2(bak_path, content_path)


# ---------------------------------------------------------------------------
# 方向常量
# ---------------------------------------------------------------------------

_MASK_DIRECTIONS = frozenset({
    "left_to_right", "right_to_left", "top_to_bottom", "bottom_to_top",
})

_SLIDE_DIRECTIONS = frozenset({"left", "right", "top", "bottom"})

_SLIDE_DIR_MAP: dict[str, tuple[str, float]] = {
    "right":  ("KFTypePositionX", +1.0),
    "left":   ("KFTypePositionX", -1.0),
    "top":    ("KFTypePositionY", +1.0),
    "bottom": ("KFTypePositionY", -1.0),
}

_MASK_KF_MAP: dict[str, tuple[float, float]] = {
    "left_to_right":  (-1.0, 1.0),
    "right_to_left":  (1.0, -1.0),
    "top_to_bottom":  (1.0, -1.0),
    "bottom_to_top":  (-1.0, 1.0),
}


def _find_segment(data: dict, segment_id: str) -> dict | None:
    for track in data.get("tracks", []):
        for seg in track.get("segments", []):
            if seg.get("id") == segment_id:
                return seg
    return None


# ===================================================================
# Tool 1 — inject_mask_transition
# ===================================================================

@tool
def inject_mask_transition(
    project_name: str,
    segment_id: str,
    direction: str = "left_to_right",
    feather: float = 0.1,
) -> dict:
    """遮罩转场 — 为视频片段注入线性蒙版位置关键帧。"""
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="inject_mask_transition",
        metadata={"tool": "inject_mask_transition", "phase": "2",
                  "handler": "draft_injector", "capability_id": "TR-02",
                  "injection_type": "mask"},
        input={"project_name": project_name, "segment_id": segment_id,
               "direction": direction, "feather": feather},
    )
    bak_path = None
    try:
        prep_span = trace.start_observation(as_type="span",name="inject_mask_transition.prepare",
                               input={"segment_id": segment_id})

        feather = max(0.0, min(1.0, feather))
        direction = direction if direction in _MASK_DIRECTIONS else "left_to_right"
        project, content_path = _load_project(project_name)
        bak_path = _backup(content_path)
        data = _read_json(content_path)

        target_seg = _find_segment(data, segment_id)
        if target_seg is None:
            prep_span.update(level="ERROR", status_message="segment_not_found")
            prep_span.end()
            return _fail("segment_not_found",
                         f"segment_id '{segment_id}' not found in draft")

        # 已有 mask 检查 — mask 节点直接挂在 segment 上
        if "mask" in target_seg:
            prep_span.update(level="WARNING", status_message="mask_already_exists")
            prep_span.end()
            return _fail("mask_already_exists", "segment already has a mask")

        prep_span.update(output={"segment_found": True})
        prep_span.end()

        inject_span = trace.start_observation(as_type="span",name="inject_mask_transition.inject",
                                 input={"direction": direction, "feather": feather})

        mask_id = uuid.uuid4().hex
        dur = target_seg["target_timerange"]["duration"]
        start_val, end_val = _MASK_KF_MAP.get(direction, (-1.0, 1.0))

        target_seg.setdefault("extra_material_refs", []).append(mask_id)
        target_seg["mask"] = {
            "id": mask_id, "name": "线性", "type": "mask",
            "resource_type": "mask_type", "resource_id": "636071",
            "platform": "all", "position_info": "",
            "config": {
                "centerX": 0.0, "centerY": 0.0,
                "width": 1.0, "height": 1.0,
                "rotation": 0.0, "feather": feather,
                "invert": False, "roundCorner": 0.0, "aspectRatio": 1.0,
            },
        }
        target_seg.setdefault("common_keyframes", []).append({
            "id": uuid.uuid4().hex,
            "property_type": "KFTypeMaskCenterX",
            "material_id": mask_id,
            "keyframe_list": [
                {"id": uuid.uuid4().hex, "time_offset": 0, "values": [start_val], "curveType": "Line"},
                {"id": uuid.uuid4().hex, "time_offset": dur, "values": [end_val], "curveType": "Line"},
            ],
        })

        _write_json(data, content_path)
        _finalize(project, content_path)

        result = _ok(mask_id=mask_id, keyframes_count=2, direction=direction)
        inject_span.update(output=result)
        inject_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        if bak_path:
            _rollback(bak_path, content_path)
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        return _fail(type(e).__name__, str(e))


# ===================================================================
# Tool 2 — inject_color_transition
# ===================================================================

@tool
def inject_color_transition(
    project_name: str,
    seg1_id: str,
    seg2_id: str,
    color: str = "#000000",
    duration_us: int = 500_000,
) -> dict:
    """颜色过渡转场 — 在 seg1/seg2 之间插入纯色片段实现 A->纯色->B 过渡。"""
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="inject_color_transition",
        metadata={"tool": "inject_color_transition", "phase": "2",
                  "handler": "draft_injector", "capability_id": "TR-05",
                  "injection_type": "color_material"},
        input={"project_name": project_name, "seg1_id": seg1_id,
               "seg2_id": seg2_id, "color": color, "duration_us": duration_us},
    )
    bak_path = None
    try:
        prep_span = trace.start_observation(as_type="span",name="inject_color_transition.prepare",
                               input={"seg1_id": seg1_id, "seg2_id": seg2_id})

        if not _COLOR_RE.match(color):
            prep_span.update(level="ERROR", status_message="invalid_color_format")
            prep_span.end()
            return _fail("invalid_color_format",
                         f"color '{color}' does not match #RRGGBB or #RRGGBBAA")

        project, content_path = _load_project(project_name)
        bak_path = _backup(content_path)
        data = _read_json(content_path)

        seg1 = _find_segment(data, seg1_id)
        seg2 = _find_segment(data, seg2_id)
        if seg1 is None or seg2 is None:
            prep_span.update(level="ERROR", status_message="segment_not_found")
            prep_span.end()
            return _fail("segment_not_found", f"seg1_id or seg2_id not found")

        # 如果有间隙不足，将 seg2 及后续向后偏移
        seg1_end = seg1["target_timerange"]["start"] + seg1["target_timerange"]["duration"]
        seg2_start = seg2["target_timerange"]["start"]
        gap = seg2_start - seg1_end
        if gap < duration_us:
            offset = duration_us - gap
            for track in data.get("tracks", []):
                for seg in track.get("segments", []):
                    if seg["target_timerange"]["start"] >= seg2_start:
                        seg["target_timerange"]["start"] += offset

        prep_span.update(output={"valid": True})
        prep_span.end()

        inject_span = trace.start_observation(as_type="span",name="inject_color_transition.inject",
                                 input={"color": color, "duration_us": duration_us})

        mat_id = uuid.uuid4().hex
        seg_id = uuid.uuid4().hex

        canvas = data.get("canvas_config", {})
        w = canvas.get("width", 1920)
        h = canvas.get("height", 1080)

        data.setdefault("materials", {}).setdefault("videos", []).append({
            "id": mat_id, "type": "color", "color": color,
            "duration": duration_us, "width": w, "height": h,
        })

        # 注入到新的 ColorTrack
        color_track = None
        for t in data.get("tracks", []):
            if t.get("name") == "ColorTrack":
                color_track = t
                break
        if color_track is None:
            color_track = {
                "id": uuid.uuid4().hex, "type": "video",
                "name": "ColorTrack", "segments": [],
                "attribute": {"render_index": 0},
            }
            data["tracks"].append(color_track)

        color_track["segments"].append({
            "id": seg_id, "material_id": mat_id,
            "target_timerange": {"start": seg1_end, "duration": duration_us},
            "common_keyframes": [], "extra_material_refs": [],
        })

        _write_json(data, content_path)
        _finalize(project, content_path)

        result = _ok(color_segment_id=seg_id, color_material_id=mat_id,
                     transition_duration_us=duration_us)
        inject_span.update(output=result)
        inject_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        if bak_path:
            _rollback(bak_path, content_path)
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        return _fail(type(e).__name__, str(e))


# ===================================================================
# Tool 3 — add_karaoke_subtitle
# ===================================================================

@tool
def add_karaoke_subtitle(
    project_name: str,
    text: str,
    char_timestamps: list[dict],
    track_name: str = "Karaoke",
    y_position: float = -0.75,
) -> dict:
    """逐字高亮字幕 — 每个字/词作为独立 TextSegment，通过颜色区分高亮。"""
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="add_karaoke_subtitle",
        metadata={"tool": "add_karaoke_subtitle", "phase": "2",
                  "handler": "draft_injector", "capability_id": "TX-02",
                  "injection_type": "karaoke"},
        input={"project_name": project_name, "char_count": len(char_timestamps),
               "track_name": track_name, "y_position": y_position},
    )
    try:
        if not char_timestamps:
            return _fail("empty_input", "char_timestamps must not be empty")

        project, content_path = _load_project(project_name)

        # 降级策略: >50 字符 -> per_word
        METHOD_PER_WORD_LIMIT = 50
        method = "per_char"
        items = char_timestamps

        if len(char_timestamps) > METHOD_PER_WORD_LIMIT:
            method = "per_word"
            items = _group_by_word(char_timestamps)

        exec_span = trace.start_observation(as_type="span",name="add_karaoke_subtitle.inject",
                               input={"method": method, "item_count": len(items)})

        failed_chars = []
        segment_count = 0

        proj = _JyProject(project_name)

        for idx, item in enumerate(items):
            ch = item.get("char", "").strip()
            if not ch:
                failed_chars.append({"index": idx, "char": ch})
                continue

            start_us = int(item.get("start_us", 0))
            dur_us = int(item.get("duration_us", _safe_tim()("0.2s")))

            # 高亮色 vs 普通色
            is_highlighted = item.get("highlighted", True)
            clr = (1.0, 1.0, 0.0) if is_highlighted else (0.5, 0.5, 0.5)

            style = _TextStyle(size=6.0, color=clr)
            cs = _ClipSettings(transform_y=y_position)

            proj.add_text_simple(
                ch, start_time=start_us, duration=dur_us,
                track_name=track_name, style=style, clip_settings=cs,
            )
            segment_count += 1

        proj.save()

        result = _ok(segment_count=segment_count, total_chars=len(char_timestamps),
                     track_name=track_name, method=method)
        if failed_chars:
            result["failed_chars"] = failed_chars

        exec_span.update(output=result)
        exec_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        return _fail(type(e).__name__, str(e))


def _group_by_word(timestamps: list[dict]) -> list[dict]:
    """按空格/标点将逐字时间戳合并为逐词。"""
    words = []
    buf_chars = []
    buf_start = None
    buf_end = None
    punct = set(" \t\n\r,.;:!?()[]{}\"'")

    for item in timestamps:
        ch = item.get("char", "")
        start_us = int(item.get("start_us", 0))
        dur_us = int(item.get("duration_us", 300000))
        end_us = start_us + dur_us

        buf_chars.append(ch)
        if buf_start is None:
            buf_start = start_us
        buf_end = end_us

        if ch in punct:
            if buf_chars:
                words.append({
                    "char": "".join(buf_chars),
                    "start_us": buf_start,
                    "duration_us": buf_end - buf_start,
                })
            buf_chars = []
            buf_start = None
            buf_end = None

    if buf_chars:
        words.append({
            "char": "".join(buf_chars),
            "start_us": buf_start,
            "duration_us": (buf_end or buf_start) - buf_start,
        })
    return words


# ===================================================================
# Tool 4 — inject_subtitle_slide
# ===================================================================

@tool
def inject_subtitle_slide(
    project_name: str,
    segment_id: str,
    slide_from: str = "right",
    slide_distance: float = 1.5,
    duration_us: int = 500_000,
) -> dict:
    """动态字幕条滑入 — 为 TextSegment 注入 transform_x/y 关键帧。"""
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="inject_subtitle_slide",
        metadata={"tool": "inject_subtitle_slide", "phase": "2",
                  "handler": "draft_injector", "capability_id": "TX-07",
                  "injection_type": "position_kf"},
        input={"project_name": project_name, "segment_id": segment_id,
               "slide_from": slide_from, "slide_distance": slide_distance,
               "duration_us": duration_us},
    )
    bak_path = None
    try:
        prep_span = trace.start_observation(as_type="span",name="inject_subtitle_slide.prepare",
                               input={"slide_from": slide_from})

        if slide_from not in _SLIDE_DIRECTIONS:
            prep_span.update(level="ERROR", status_message="invalid_slide_direction")
            prep_span.end()
            return _fail("invalid_slide_direction",
                         f"Unknown slide_from: '{slide_from}'. Valid: {sorted(_SLIDE_DIRECTIONS)}")

        project, content_path = _load_project(project_name)
        bak_path = _backup(content_path)
        data = _read_json(content_path)

        target_seg = _find_segment(data, segment_id)
        if target_seg is None:
            prep_span.update(level="ERROR", status_message="segment_not_found")
            prep_span.end()
            return _fail("segment_not_found",
                         f"segment_id '{segment_id}' not found in draft")

        prop_type, start_sign = _SLIDE_DIR_MAP[slide_from]
        start_val = start_sign * slide_distance
        overwritten = False
        kf_id = uuid.uuid4().hex

        # 检查已有同名 KF
        existing_kfs = target_seg.get("common_keyframes", [])
        for kf_list in existing_kfs:
            if kf_list.get("property_type") == prop_type:
                overwritten = True
                kf_id = kf_list["id"]
                kf_list["keyframe_list"] = [
                    {"id": uuid.uuid4().hex, "time_offset": 0,
                     "values": [start_val], "curveType": "EaseOut"},
                    {"id": uuid.uuid4().hex, "time_offset": duration_us,
                     "values": [0.0], "curveType": "EaseOut"},
                ]
                break
        else:
            target_seg.setdefault("common_keyframes", []).append({
                "id": kf_id,
                "property_type": prop_type,
                "material_id": target_seg.get("material_id", ""),
                "keyframe_list": [
                    {"id": uuid.uuid4().hex, "time_offset": 0,
                     "values": [start_val], "curveType": "EaseOut"},
                    {"id": uuid.uuid4().hex, "time_offset": duration_us,
                     "values": [0.0], "curveType": "EaseOut"},
                ],
            })

        prep_span.update(output={"overwritten": overwritten})
        prep_span.end()

        _write_json(data, content_path)
        _finalize(project, content_path)

        keyframes = [{"time_offset": 0, "value": start_val},
                     {"time_offset": duration_us, "value": 0.0}]
        result = _ok(keyframe_list_id=kf_id,
                     keyframes=keyframes, overwritten=overwritten)

        trace.update(output=result)
        return result

    except Exception as e:
        if bak_path:
            _rollback(bak_path, content_path)
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        return _fail(type(e).__name__, str(e))


# ===================================================================
# Tool 5 — apply_bgm_ducking
# ===================================================================

@tool
def apply_bgm_ducking(
    project_name: str,
    voice_segments: list[dict],
    bgm_track_name: str = "BGM",
    duck_volume: float = 0.2,
    fade_us: int = 300_000,
) -> dict:
    """BGM 音量闪避 — 为人声区间注入 BGM 音量降低关键帧。"""
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="apply_bgm_ducking",
        metadata={"tool": "apply_bgm_ducking", "phase": "2",
                  "handler": "draft_injector", "capability_id": "A-03",
                  "injection_type": "volume_kf"},
        input={"project_name": project_name, "voice_count": len(voice_segments),
               "bgm_track_name": bgm_track_name, "duck_volume": duck_volume,
               "fade_us": fade_us},
    )
    bak_path = None
    try:
        prep_span = trace.start_observation(as_type="span",name="apply_bgm_ducking.prepare",
                               input={"voice_count": len(voice_segments)})

        if not voice_segments:
            prep_span.update(level="ERROR", status_message="empty_input")
            prep_span.end()
            return _fail("empty_input", "voice_segments must not be empty")

        # 合并重叠区间
        merged = _merge_intervals(voice_segments)

        project, content_path = _load_project(project_name)
        bak_path = _backup(content_path)
        data = _read_json(content_path)

        bgm_track = None
        for t in data.get("tracks", []):
            if t.get("name") == bgm_track_name:
                bgm_track = t
                break

        if bgm_track is None:
            prep_span.update(level="ERROR", status_message="track_not_found")
            prep_span.end()
            return _fail("track_not_found",
                         f"BGM track '{bgm_track_name}' not found",
                         track=bgm_track_name)

        prep_span.update(output={"track_found": True, "merged_windows": len(merged)})
        prep_span.end()

        inject_span = trace.start_observation(as_type="span",name="apply_bgm_ducking.inject",
                                 input={"merged_windows": len(merged)})

        total_kfs = 0
        NORMAL_VOLUME = 1.0

        for seg in bgm_track.get("segments", []):
            bgm_start = seg["target_timerange"]["start"]
            bgm_end = bgm_start + seg["target_timerange"]["duration"]

            volume_kfs = []
            for vs in merged:
                vs_start = vs["start_us"]
                vs_end = vs["end_us"]

                if vs_end <= bgm_start or vs_start >= bgm_end:
                    continue

                rel_start = max(0, vs_start - bgm_start)
                rel_end = vs_end - bgm_start

                # 渐弱点 (在 vs 开始前 fade_us)
                fade_start = max(0, rel_start - fade_us)
                volume_kfs.append({
                    "id": uuid.uuid4().hex,
                    "time_offset": fade_start,
                    "values": [NORMAL_VOLUME],
                    "curveType": "Line",
                })
                volume_kfs.append({
                    "id": uuid.uuid4().hex,
                    "time_offset": rel_start,
                    "values": [duck_volume],
                    "curveType": "Line",
                })
                # 渐强点
                volume_kfs.append({
                    "id": uuid.uuid4().hex,
                    "time_offset": rel_end,
                    "values": [duck_volume],
                    "curveType": "Line",
                })
                fade_end = rel_end + fade_us
                volume_kfs.append({
                    "id": uuid.uuid4().hex,
                    "time_offset": fade_end,
                    "values": [NORMAL_VOLUME],
                    "curveType": "Line",
                })

            if volume_kfs:
                # 按 time_offset 排序
                volume_kfs.sort(key=lambda k: k["time_offset"])
                seg.setdefault("common_keyframes", []).append({
                    "id": uuid.uuid4().hex,
                    "property_type": "KFTypeVolume",
                    "material_id": seg.get("material_id", ""),
                    "keyframe_list": volume_kfs,
                })
                total_kfs += len(volume_kfs)

        _write_json(data, content_path)
        _finalize(project, content_path)

        result = _ok(bgm_track=bgm_track_name, duck_windows=len(merged),
                     duck_volume=duck_volume, keyframes_generated=total_kfs)
        inject_span.update(output=result)
        inject_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        if bak_path:
            _rollback(bak_path, content_path)
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        return _fail(type(e).__name__, str(e))


def _merge_intervals(segments: list[dict]) -> list[dict]:
    """合并重叠的时间区间。"""
    if not segments:
        return []
    sorted_segs = sorted(segments, key=lambda s: s["start_us"])
    merged = [dict(sorted_segs[0])]
    for cur in sorted_segs[1:]:
        last = merged[-1]
        if cur["start_us"] <= last["end_us"]:
            last["end_us"] = max(last["end_us"], cur["end_us"])
        else:
            merged.append(dict(cur))
    return merged


# ===================================================================
# Tool 6 — apply_audio_speed
# ===================================================================

@tool
def apply_audio_speed(
    project_name: str,
    track_name: str,
    segment_index: int,
    speed: float,
) -> dict:
    """音频变速 — 为 AudioSegment 注入 speed 字段。"""
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="apply_audio_speed",
        metadata={"tool": "apply_audio_speed", "phase": "2",
                  "handler": "draft_injector", "capability_id": "A-08",
                  "injection_type": "speed"},
        input={"project_name": project_name, "track_name": track_name,
               "segment_index": segment_index, "speed": speed},
    )
    bak_path = None
    try:
        prep_span = trace.start_observation(as_type="span",name="apply_audio_speed.prepare",
                               input={"speed": speed})

        if speed <= 0 or speed > 10.0:
            prep_span.update(level="ERROR", status_message="invalid_speed")
            prep_span.end()
            return _fail("invalid_speed",
                         f"speed must be in (0, 10.0], got {speed}")

        project, content_path = _load_project(project_name)
        bak_path = _backup(content_path)
        data = _read_json(content_path)

        target_track = None
        for t in data.get("tracks", []):
            if t.get("name") == track_name:
                target_track = t
                break

        if target_track is None:
            return _fail("track_not_found",
                         f"track '{track_name}' not found", track=track_name)

        segs = target_track.get("segments", [])
        if segment_index < 0 or segment_index >= len(segs):
            return _fail("index_out_of_range",
                         f"segment_index {segment_index} out of range [0, {len(segs)})")

        target_seg = segs[segment_index]
        old_dur = target_seg["target_timerange"]["duration"]
        src_dur = target_seg.get("source_timerange", {}).get("duration", old_dur)

        # 货源不足时仅能降速 (speed < 1.0)，不可升速
        actual_speed = speed
        if speed > 1.0 and src_dur < old_dur:
            actual_speed = 1.0

        target_seg["speed"] = actual_speed
        new_dur = int(old_dur / actual_speed)
        target_seg["target_timerange"]["duration"] = new_dur
        target_seg.setdefault("source_timerange", {})["duration"] = src_dur

        # 偏移后续片段
        offset = new_dur - old_dur
        if offset != 0:
            for seg in segs[segment_index + 1:]:
                seg["target_timerange"]["start"] += offset

        prep_span.update(output={"speed_clamped": actual_speed != speed})
        prep_span.end()

        _write_json(data, content_path)
        _finalize(project, content_path)

        result = _ok(
            segment_id=target_seg.get("id", "unknown"),
            speed=actual_speed,
            original_duration_us=old_dur,
            new_target_duration_us=new_dur,
            speed_clamped=actual_speed != speed,
        )
        trace.update(output=result)
        return result

    except Exception as e:
        if bak_path:
            _rollback(bak_path, content_path)
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        return _fail(type(e).__name__, str(e))


# ===================================================================
# Tool 7 — apply_speed_ramp (Phase 2 JSON 注入，非 KeyframeProperty)
# ===================================================================

@tool
def apply_speed_ramp(
    project_name: str,
    segment_id: str,
    speed_curve: list[dict],
) -> dict:
    """曲线变速 — 直接注入 KFTypeSpeed 节点到 draft_content.json。

    pyJianYingDraft 的 KeyframeProperty 枚举无 speed 属性，
    因此本工具直接操作 JSON 节点，归入 Phase 2。
    """
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="apply_speed_ramp",
        metadata={"tool": "apply_speed_ramp", "phase": "2",
                  "handler": "draft_injector", "capability_id": "T-06",
                  "injection_type": "speed_ramp"},
        input={"project_name": project_name, "segment_id": segment_id,
               "curve_points": len(speed_curve)},
    )
    bak_path = None
    try:
        prep_span = trace.start_observation(as_type="span",name="apply_speed_ramp.prepare",
                               input={"curve_points": len(speed_curve)})

        if not speed_curve:
            prep_span.update(level="ERROR", status_message="empty_input")
            prep_span.end()
            return _fail("empty_input", "speed_curve must not be empty")

        for pt in speed_curve:
            sp = pt.get("speed", 1.0)
            if sp <= 0 or sp > 10.0:
                prep_span.update(level="ERROR", status_message=f"invalid_speed: {sp}")
                prep_span.end()
                return _fail("invalid_speed",
                             f"speed {sp} out of range (0, 10.0]")

        project, content_path = _load_project(project_name)
        bak_path = _backup(content_path)
        data = _read_json(content_path)

        target_seg = _find_segment(data, segment_id)
        if target_seg is None:
            prep_span.update(level="ERROR", status_message="segment_not_found")
            prep_span.end()
            return _fail("segment_not_found",
                         f"segment_id '{segment_id}' not found in draft")

        prep_span.update(output={"valid": True})
        prep_span.end()

        inject_span = trace.start_observation(as_type="span",name="apply_speed_ramp.inject",
                                 input={"curve_points": len(speed_curve)})

        curve_id = uuid.uuid4().hex
        target_seg["speed"] = speed_curve[-1]["speed"]
        target_seg.setdefault("common_keyframes", []).append({
            "id": curve_id,
            "property_type": "KFTypeSpeed",
            "material_id": target_seg.get("material_id", ""),
            "keyframe_list": [
                {
                    "id": uuid.uuid4().hex,
                    "time_offset": pt["target_time_us"],
                    "values": [pt["speed"]],
                    "curveType": "Line",
                }
                for pt in speed_curve
            ],
        })

        _write_json(data, content_path)
        _finalize(project, content_path)

        result = _ok(curve_points=len(speed_curve),
                     duration_us=speed_curve[-1]["target_time_us"])
        inject_span.update(output=result)
        inject_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        if bak_path:
            _rollback(bak_path, content_path)
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        return _fail(type(e).__name__, str(e))
