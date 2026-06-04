"""
timeline_ops.py — Phase 1 时间线编排工具

提供 J-Cut、L-Cut、片段重排三个 Agent Tool。
仅依赖 pyJianYingDraft (vendored) + JyProject，无外部二进制依赖。
"""

import os
import sys
from pathlib import Path

from langchain.tools import tool

# ---------------------------------------------------------------------------
# 路径注入 — 复用 python_executor 的寻径策略
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = None
for _candidate in [
    _THIS_DIR.parent.parent.parent.parent / "jianying-editor-skill" / "scripts",
    _THIS_DIR.parent.parent.parent.parent.parent / "backend" / "jianying-editor-skill" / "scripts",
]:
    _jy_wrapper = _candidate / "jy_wrapper.py"
    if _jy_wrapper.exists():
        _SKILL_ROOT = str(_candidate)
        break

if _SKILL_ROOT is None:
    raise ImportError(
        "Cannot locate jianying-editor-skill/scripts/jy_wrapper.py"
    )

if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)

# ---------------------------------------------------------------------------
# 延迟导入 — 避免模块加载时触发 pyJianYingDraft / langfuse 的 heavy init。
# 这些 wrapper 在首次调用时才真正 import，方便测试 monkey-patch。
# ---------------------------------------------------------------------------
_langfuse = None

# 模块级占位符 — 测试可通过 patch 替换
JyProject = None
safe_tim = None
get_duration_ffprobe_cached = None


def _ensure_imports():
    """首次调用时懒加载 jy_wrapper / formatters。"""
    global JyProject, safe_tim, get_duration_ffprobe_cached
    if JyProject is None:
        from jy_wrapper import JyProject as _JP
        JyProject = _JP
    if safe_tim is None:
        from utils.formatters import safe_tim as _st
        safe_tim = _st
    if get_duration_ffprobe_cached is None:
        from utils.formatters import get_duration_ffprobe_cached as _gdfc
        get_duration_ffprobe_cached = _gdfc


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


class _NoopSpan:
    """无操作 Span — 当 LangFuse 不可用时安静降级。"""
    def update(self, **kwargs): pass
    def end(self): pass
    def start_observation(self, name, as_type=None, **kwargs) -> "_NoopSpan":
        return _NoopSpan()


class _NoopTrace:
    """无操作 Trace — 当 LangFuse 不可用时安静降级。"""
    def start_observation(self, name, as_type=None, **kwargs) -> _NoopSpan:
        return _NoopSpan()
    def update(self, **kwargs): pass
    def end(self): pass


class _NoopLangfuse:
    def start_observation(self, name, as_type=None, **kwargs) -> _NoopTrace:
        return _NoopTrace()


# ---------------------------------------------------------------------------
# 标准化出参 — 成功/失败
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
# Tool — apply_jcut
# ---------------------------------------------------------------------------

@tool
def apply_jcut(
    project_name: str,
    video_path: str,
    audio_path: str,
    audio_lead_us: int = 1_500_000,
    video_start: str = "0s",
    video_duration: str = "10s",
) -> dict:
    """J-Cut 声音先行 — 音频比视频提前开始，制造悬念感。

    视频片段整体后移 audio_lead_us 微秒，音频从 0 开始，
    利用轨道独立性实现"音频早于视频"的感知效果。

    Args:
        project_name: 剪映草稿名称
        video_path: 视频文件绝对路径
        audio_path: 音频文件绝对路径（可与 video_path 相同）
        audio_lead_us: 音频提前量(微秒)，默认 1_500_000 = 1.5s
        video_start: 视频素材起始时间，如 "5s"
        video_duration: 视频片段持续时长，如 "10s"

    Returns:
        {"ok": True, "video_segment_id": "...", "audio_segment_id": "...",
         "video_actual_start_us": 1500000, "audio_start_us": 0}
    """
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="apply_jcut",
        metadata={
            "tool": "apply_jcut",
            "phase": "1",
            "handler": "timeline_ops",
            "capability_id": "T-01",
        },
        input={
            "project_name": project_name,
            "video_path": video_path,
            "audio_path": audio_path,
            "audio_lead_us": audio_lead_us,
            "video_start": video_start,
            "video_duration": video_duration,
        },
    )

    validation_span = trace.start_observation(as_type="span",
        name="apply_jcut.validation",
        input={"audio_lead_us": audio_lead_us, "video_path": video_path},
    )

    try:
        # --- validation ---
        if audio_lead_us <= 0:
            validation_span.update(
                level="ERROR",
                status_message=f"Invalid audio_lead_us: {audio_lead_us}",
            )
            validation_span.end()
            return _fail(
                "invalid_audio_lead",
                f"audio_lead_us must be positive, got {audio_lead_us}",
            )

        if not Path(video_path).exists():
            validation_span.update(
                level="ERROR",
                status_message=f"Video not found: {video_path}",
            )
            validation_span.end()
            return _fail(
                "file_not_found",
                f"Video not found: {video_path}",
            )

        if not Path(audio_path).exists():
            validation_span.update(
                level="ERROR",
                status_message=f"Audio not found: {audio_path}",
            )
            validation_span.end()
            return _fail(
                "file_not_found",
                f"Audio not found: {audio_path}",
            )

        # --- duration calc ---
        duration_us = safe_tim(video_duration)

        # --- audio source check ---
        try:
            audio_source_dur_s = get_duration_ffprobe_cached(audio_path)
            audio_source_dur_us = int(audio_source_dur_s * 1_000_000)
        except Exception:
            audio_source_dur_us = None

        if audio_source_dur_us is not None and audio_source_dur_us < duration_us + audio_lead_us:
            validation_span.update(
                level="ERROR",
                status_message="audio_source_too_short",
            )
            validation_span.end()
            return _fail(
                "audio_source_too_short",
                f"Audio source ({audio_source_dur_us / 1_000_000:.1f}s) shorter than "
                f"required ({(duration_us + audio_lead_us) / 1_000_000:.1f}s)",
                required_us=duration_us + audio_lead_us,
                available_us=audio_source_dur_us,
            )

        validation_span.update(output={"valid": True})
        validation_span.end()

        # --- execution ---
        exec_span = trace.start_observation(as_type="span",
            name="apply_jcut.execution",
            input={
                "video_duration": video_duration,
                "video_start": video_start,
                "audio_lead_us": audio_lead_us,
            },
        )

        project = JyProject(project_name, overwrite=True)
        video_actual_start_us = audio_lead_us

        audio_seg = project.add_audio_safe(
            audio_path,
            start_time="0s",
            duration=duration_us + audio_lead_us,
            track_name="AudioTrack",
        )

        video_seg = project.add_media_safe(
            video_path,
            start_time=video_actual_start_us,
            duration=duration_us,
            track_name="VideoTrack",
            source_start=video_start,
        )

        project.save()

        video_seg_id = (
            getattr(video_seg, "segment_id", "unknown")
            if video_seg is not None
            else "unknown"
        )
        audio_seg_id = (
            getattr(audio_seg, "segment_id", "unknown")
            if audio_seg is not None
            else "unknown"
        )

        result = _ok(
            video_segment_id=video_seg_id,
            audio_segment_id=audio_seg_id,
            video_actual_start_us=video_actual_start_us,
            audio_start_us=0,
        )

        exec_span.update(output=result)
        exec_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        try:
            exec_span.update(level="ERROR", status_message=str(e))
            exec_span.end()
        except Exception:
            pass
        return _fail(type(e).__name__, str(e))


# ---------------------------------------------------------------------------
# Tool — apply_lcut
# ---------------------------------------------------------------------------

@tool
def apply_lcut(
    project_name: str,
    video_path: str,
    audio_path: str,
    audio_tail_us: int = 1_500_000,
    video_start: str = "0s",
    video_duration: str = "10s",
) -> dict:
    """L-Cut 画面先行 — 音频比视频晚结束，增强叙事流畅感。

    视频正常从 0 开始，音频 duration 延长 audio_tail_us，
    使其尾部延伸到视频结束后。

    注意: 单次调用仅实现"音频延长"。完整的多片段 L-Cut
    （下一个画面在音频尾部播放时就开始）需要 Agent 协调
    多次调用。

    Args:
        project_name: 剪映草稿名称
        video_path: 视频文件绝对路径
        audio_path: 音频文件绝对路径
        audio_tail_us: 音频尾部延长量(微秒)，默认 1.5s
        video_start: 视频素材起始时间
        video_duration: 视频片段持续时长

    Returns:
        {"ok": True, "video_segment_id": "...", "audio_segment_id": "...",
         "audio_actual_duration_us": 11500000}
    """
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="apply_lcut",
        metadata={
            "tool": "apply_lcut",
            "phase": "1",
            "handler": "timeline_ops",
            "capability_id": "T-02",
        },
        input={
            "project_name": project_name,
            "video_path": video_path,
            "audio_path": audio_path,
            "audio_tail_us": audio_tail_us,
        },
    )

    try:
        # --- validation ---
        if audio_tail_us <= 0:
            return _fail(
                "invalid_audio_tail",
                f"audio_tail_us must be positive, got {audio_tail_us}",
            )

        if not Path(video_path).exists():
            return _fail("file_not_found", f"Video not found: {video_path}")

        if not Path(audio_path).exists():
            return _fail("file_not_found", f"Audio not found: {audio_path}")

        duration_us = safe_tim(video_duration)

        # --- audio source check ---
        try:
            audio_source_dur_s = get_duration_ffprobe_cached(audio_path)
            audio_source_dur_us = int(audio_source_dur_s * 1_000_000)
        except Exception:
            audio_source_dur_us = None

        truncated = False
        if audio_source_dur_us is not None and audio_source_dur_us < duration_us + audio_tail_us:
            truncated = True

        # --- execution ---
        exec_span = trace.start_observation(as_type="span",
            name="apply_lcut.execution",
            input={"audio_tail_us": audio_tail_us, "truncated": truncated},
        )

        project = JyProject(project_name, overwrite=True)
        audio_duration = duration_us + audio_tail_us

        video_seg = project.add_media_safe(
            video_path,
            start_time="0s",
            duration=duration_us,
            track_name="VideoTrack",
            source_start=video_start,
        )

        audio_seg = project.add_audio_safe(
            audio_path,
            start_time="0s",
            duration=audio_duration,
            track_name="AudioTrack",
        )

        project.save()

        video_seg_id = (
            getattr(video_seg, "segment_id", "unknown")
            if video_seg is not None
            else "unknown"
        )
        audio_seg_id = (
            getattr(audio_seg, "segment_id", "unknown")
            if audio_seg is not None
            else "unknown"
        )

        result = _ok(
            video_segment_id=video_seg_id,
            audio_segment_id=audio_seg_id,
            audio_actual_duration_us=audio_duration,
        )
        if truncated:
            result["truncated"] = True
            result["audio_note"] = (
                f"Source too short; requested {audio_duration/1_000_000:.1f}s "
                f"but source is {audio_source_dur_us/1_000_000:.1f}s"
            )

        exec_span.update(output=result)
        exec_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        return _fail(type(e).__name__, str(e))


# ---------------------------------------------------------------------------
# Tool — reorder_segments
# ---------------------------------------------------------------------------

@tool
def reorder_segments(
    project_name: str,
    new_order: list[int],
) -> dict:
    """片段重排 — 改变已有草稿中视频片段的播放顺序。

    通过重建草稿实现: 读取原草稿 → 提取片段 metadata →
    按新顺序清空重建。原始草稿在操作前自动备份。

    Args:
        project_name: 已有草稿名称（必须已存在）
        new_order: 新顺序索引列表，如 [2, 0, 1, 3]

    Returns:
        {"ok": True, "reordered_count": 4, "original_order": [...], "new_order": [...]}
    """
    import json
    import shutil
    from datetime import datetime

    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="reorder_segments",
        metadata={
            "tool": "reorder_segments",
            "phase": "1",
            "handler": "timeline_ops",
            "capability_id": "T-05",
        },
        input={
            "project_name": project_name,
            "new_order": new_order,
        },
    )

    try:
        # --- load project ---
        validate_span = trace.start_observation(as_type="span",
            name="reorder_segments.validation",
            input={"new_order": new_order},
        )

        project = JyProject(project_name, overwrite=False)
        content_path = os.path.join(project.root, project.name, "draft_content.json")

        if not os.path.exists(content_path):
            validate_span.update(level="ERROR", status_message="draft_not_found")
            validate_span.end()
            return _fail(
                "draft_not_found",
                f"Draft '{project_name}' does not exist at {content_path}",
            )

        # --- backup ---
        bak_path = (
            f"{content_path}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(content_path, bak_path)

        with open(content_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # --- collect original segments (video tracks only) ---
        original_segments = []
        for track in data.get("tracks", []):
            if track.get("type") == "video":
                for seg in track.get("segments", []):
                    original_segments.append({
                        "material_id": seg.get("material_id"),
                        "target_timerange": seg.get("target_timerange", {}),
                        "source_timerange": seg.get("source_timerange", {}),
                        "track_name": track.get("name", "VideoTrack"),
                    })

        total = len(original_segments)

        # --- validate new_order ---
        if len(new_order) != total:
            validate_span.update(level="ERROR", status_message="New order length mismatch")
            validate_span.end()
            return _fail(
                "invalid_order",
                f"new_order length ({len(new_order)}) != segment count ({total})",
            )

        if len(set(new_order)) != total:
            validate_span.update(level="ERROR", status_message="Duplicate or missing indices")
            validate_span.end()
            return _fail(
                "invalid_order",
                "duplicate or missing indices in new_order",
            )

        for idx in new_order:
            if idx < 0 or idx >= total:
                validate_span.update(level="ERROR", status_message=f"Index {idx} out of range")
                validate_span.end()
                return _fail(
                    "index_out_of_range",
                    f"index {idx} out of range, only {total} segments available",
                )

        original_order = list(range(total))

        validate_span.update(output={"valid": True, "total_segments": total})
        validate_span.end()

        # --- rebuild via fresh project ---
        exec_span = trace.start_observation(as_type="span",
            name="reorder_segments.rebuild",
            input={"new_order": new_order, "total": total},
        )

        reordered = [original_segments[i] for i in new_order]
        new_project = JyProject(project_name, overwrite=True)

        # 需要原始素材的路径 — 从 materials 中查找
        materials_map = {}
        for mat_type in ("videos", "audios"):
            for mat in data.get("materials", {}).get(mat_type, []):
                mat_id = mat.get("id")
                mat_path = mat.get("path", mat.get("url", ""))
                if mat_id and mat_path:
                    materials_map[mat_id] = mat_path

        for seg_meta in reordered:
            mid = seg_meta["material_id"]
            src = seg_meta.get("source_timerange", {})
            tgt = seg_meta.get("target_timerange", {})
            media_path = materials_map.get(mid, "")

            if not media_path:
                continue

            source_start_us = src.get("start", 0)
            duration_us = tgt.get("duration", 5_000_000)

            new_project.add_media_safe(
                media_path,
                start_time=None,  # auto-append
                duration=duration_us,
                track_name=seg_meta.get("track_name", "VideoTrack"),
                source_start=source_start_us,
            )

        new_project.save()

        result = _ok(
            reordered_count=total,
            original_order=original_order,
            new_order=new_order,
        )

        exec_span.update(output=result)
        exec_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        # rollback if possible
        try:
            if 'bak_path' in dir() and os.path.exists(bak_path):
                shutil.copy2(bak_path, content_path)
        except Exception:
            pass
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        return _fail(type(e).__name__, str(e))
