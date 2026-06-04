"""
transition_ops.py — Phase 1 转场效果工具

提供 apply_zoom_transition 和 apply_push_transition Agent Tools。
仅依赖 pyJianYingDraft + JyProject，无外部二进制依赖。

关键帧强制使用 KeyframeProperty 枚举值，禁止字符串入参。
"""

import os
import sys
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
    _jy_wrapper = _candidate / "jy_wrapper.py"
    if _jy_wrapper.exists():
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
JyProject = None
KeyframeProperty = None


def _ensure_imports():
    global JyProject, KeyframeProperty
    if JyProject is None:
        from jy_wrapper import JyProject as _JP
        JyProject = _JP
    if KeyframeProperty is None:
        import pyJianYingDraft as _draft
        KeyframeProperty = _draft.KeyframeProperty


# ---------------------------------------------------------------------------
# LangFuse
# ---------------------------------------------------------------------------
_langfuse = None


class _NoopSpan:
    def update(self, **kwargs): pass
    def end(self): pass
    def start_observation(self, name, as_type=None, **kwargs) -> "_NoopSpan":
        return _NoopSpan()


class _NoopTrace:
    def start_observation(self, name, as_type=None, **kwargs) -> _NoopSpan:
        return _NoopSpan()
    def update(self, **kwargs): pass
    def end(self): pass


class _NoopLangfuse:
    def start_observation(self, name, as_type=None, **kwargs) -> _NoopTrace:
        return _NoopTrace()


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
# 方向映射表 — apply_push_transition
# ---------------------------------------------------------------------------

_VALID_DIRECTIONS = frozenset({"left", "right", "up", "down"})

# (KeyframeProperty attribute, seg1_end_val, seg2_start_val)
_DIRECTION_MAP: dict[str, tuple[str, float, float]] = {
    "left":  ("position_x", -1.0, +1.0),
    "right": ("position_x", +1.0, -1.0),
    "up":    ("position_y", +1.0, -1.0),
    "down":  ("position_y", -1.0, +1.0),
}


def _clamp_ramp(ramp_duration_us: int, seg_duration: int) -> tuple[int, bool]:
    """钳制 ramp 不超过片段时长的 20%。返回 (actual_ramp, clamped)。"""
    max_ramp = max(int(seg_duration * 0.2), 1)
    if ramp_duration_us > max_ramp:
        return max_ramp, True
    return ramp_duration_us, False


# ---------------------------------------------------------------------------
# Tool — apply_zoom_transition
# ---------------------------------------------------------------------------

@tool
def apply_zoom_transition(
    project_name: str,
    video_path_1: str,
    video_path_2: str,
    zoom_peak: float = 1.5,
    ramp_duration_us: int = 300_000,
) -> dict:
    """缩放转场 — 片段1尾部放大→片段2头部缩小，产生连续缩放视觉流。

    Args:
        project_name: 剪映草稿名称
        video_path_1: 前一段视频绝对路径
        video_path_2: 后一段视频绝对路径
        zoom_peak: 最大缩放倍数，必须 > 1.0
        ramp_duration_us: 过渡时长(微秒)，默认 0.3s

    Returns:
        {"ok": True, "seg1_id": "...", "seg2_id": "...",
         "seg1_end_scale": 1.5, "seg2_start_scale": 1.5,
         "ramp_duration_us": 300000, "ramp_clamped": False}
    """
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="apply_zoom_transition",
        metadata={
            "tool": "apply_zoom_transition",
            "phase": "1",
            "handler": "transition_ops",
            "capability_id": "TR-04",
        },
        input={
            "project_name": project_name,
            "zoom_peak": zoom_peak,
            "ramp_duration_us": ramp_duration_us,
        },
    )

    try:
        # --- 校验 ---
        val_span = trace.start_observation(as_type="span",
            name="apply_zoom_transition.validation",
            input={"zoom_peak": zoom_peak},
        )

        if zoom_peak <= 1.0:
            val_span.update(
                level="ERROR",
                status_message=f"Invalid zoom_peak: {zoom_peak}",
            )
            val_span.end()
            return _fail(
                "invalid_zoom_peak",
                f"zoom_peak must be > 1.0, got {zoom_peak}",
            )

        if not Path(video_path_1).exists():
            val_span.update(level="ERROR", status_message=f"File not found: {video_path_1}")
            val_span.end()
            return _fail("file_not_found", f"video_path_1 not found: {video_path_1}")

        if not Path(video_path_2).exists():
            val_span.update(level="ERROR", status_message=f"File not found: {video_path_2}")
            val_span.end()
            return _fail("file_not_found", f"video_path_2 not found: {video_path_2}")

        val_span.update(output={"valid": True})
        val_span.end()

        # --- 执行 ---
        exec_span = trace.start_observation(as_type="span",
            name="apply_zoom_transition.execution",
            input={"ramp_duration_us": ramp_duration_us, "zoom_peak": zoom_peak},
        )

        project = JyProject(project_name, overwrite=True)
        seg1 = project.add_media_safe(video_path_1, "0s", "5s", "Track1")
        seg2 = project.add_media_safe(video_path_2, "5s", "5s", "Track2")

        seg1_dur = seg1.target_timerange.duration
        actual_ramp, ramp_clamped = _clamp_ramp(ramp_duration_us, seg1_dur)

        # seg1: 尾部放大 1.0 → zoom_peak
        seg1.add_keyframe(KeyframeProperty.uniform_scale, seg1_dur - actual_ramp, 1.0)
        seg1.add_keyframe(KeyframeProperty.uniform_scale, seg1_dur, zoom_peak)

        # seg2: 继承 zoom_peak → 缩回 1.0
        seg2.add_keyframe(KeyframeProperty.uniform_scale, 0, zoom_peak)
        seg2.add_keyframe(KeyframeProperty.uniform_scale, actual_ramp, 1.0)

        project.save()

        result = _ok(
            seg1_id=getattr(seg1, "segment_id", "unknown"),
            seg2_id=getattr(seg2, "segment_id", "unknown"),
            seg1_end_scale=zoom_peak,
            seg2_start_scale=zoom_peak,
            ramp_duration_us=actual_ramp,
            ramp_clamped=ramp_clamped,
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
# Tool — apply_push_transition
# ---------------------------------------------------------------------------

@tool
def apply_push_transition(
    project_name: str,
    video_path_1: str,
    video_path_2: str,
    direction: str = "left",
    ramp_duration_us: int = 300_000,
) -> dict:
    """推拉转场 — 前段推出画面 → 后段推入画面，方向可控。

    支持 4 方向: left(推左), right(推右), up(推上), down(推下)。

    Args:
        project_name: 剪映草稿名称
        video_path_1: 前一段视频绝对路径
        video_path_2: 后一段视频绝对路径
        direction: 推出方向，默认 "left"
        ramp_duration_us: 过渡时长(微秒)，默认 0.3s

    Returns:
        {"ok": True, "direction": "left", "seg1_id": "...", "seg2_id": "...",
         "seg1_end_pos": -1.0, "seg2_start_pos": 1.0,
         "ramp_duration_us": 300000, "ramp_clamped": False}
    """
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="apply_push_transition",
        metadata={
            "tool": "apply_push_transition",
            "phase": "1",
            "handler": "transition_ops",
            "capability_id": "TR-07",
        },
        input={
            "project_name": project_name,
            "direction": direction,
            "ramp_duration_us": ramp_duration_us,
        },
    )

    try:
        # --- 校验 ---
        val_span = trace.start_observation(as_type="span",
            name="apply_push_transition.validation",
            input={"direction": direction},
        )

        if direction not in _VALID_DIRECTIONS:
            val_span.update(
                level="ERROR",
                status_message=f"Invalid direction: {direction}",
            )
            val_span.end()
            return _fail(
                "invalid_direction",
                f"Unknown direction: '{direction}'. Valid: {sorted(_VALID_DIRECTIONS)}",
            )

        if not Path(video_path_1).exists():
            val_span.update(level="ERROR", status_message=f"File not found: {video_path_1}")
            val_span.end()
            return _fail("file_not_found", f"video_path_1 not found: {video_path_1}")

        if not Path(video_path_2).exists():
            val_span.update(level="ERROR", status_message=f"File not found: {video_path_2}")
            val_span.end()
            return _fail("file_not_found", f"video_path_2 not found: {video_path_2}")

        val_span.update(output={"valid": True, "direction": direction})
        val_span.end()

        # --- 执行 ---
        exec_span = trace.start_observation(as_type="span",
            name="apply_push_transition.execution",
            input={"direction": direction, "ramp_duration_us": ramp_duration_us},
        )

        prop_name, seg1_end_val, seg2_start_val = _DIRECTION_MAP[direction]
        keyframe_prop = getattr(KeyframeProperty, prop_name)

        project = JyProject(project_name, overwrite=True)
        seg1 = project.add_media_safe(video_path_1, "0s", "5s", "Track1")
        seg2 = project.add_media_safe(video_path_2, "5s", "5s", "Track2")

        seg1_dur = seg1.target_timerange.duration
        actual_ramp, ramp_clamped = _clamp_ramp(ramp_duration_us, seg1_dur)

        # seg1: 中心 → 屏幕外
        seg1.add_keyframe(keyframe_prop, seg1_dur - actual_ramp, 0.0)
        seg1.add_keyframe(keyframe_prop, seg1_dur, seg1_end_val)

        # seg2: 屏幕外 → 中心
        seg2.add_keyframe(keyframe_prop, 0, seg2_start_val)
        seg2.add_keyframe(keyframe_prop, actual_ramp, 0.0)

        project.save()

        result = _ok(
            direction=direction,
            seg1_id=getattr(seg1, "segment_id", "unknown"),
            seg2_id=getattr(seg2, "segment_id", "unknown"),
            seg1_end_pos=seg1_end_val,
            seg2_start_pos=seg2_start_val,
            ramp_duration_us=actual_ramp,
            ramp_clamped=ramp_clamped,
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
