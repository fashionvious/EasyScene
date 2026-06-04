"""
subtitle_ops.py — Phase 1 多语言字幕工具

提供 add_dual_subtitles Agent Tool。
仅依赖 pyJianYingDraft + JyProject，无外部二进制依赖。
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
safe_tim = None
ClipSettings = None
TextStyle = None
TextBackground = None


def _ensure_imports():
    global JyProject, safe_tim, ClipSettings, TextStyle, TextBackground
    if JyProject is None:
        from jy_wrapper import JyProject as _JP
        JyProject = _JP
    if safe_tim is None:
        from utils.formatters import safe_tim as _st
        safe_tim = _st
    if ClipSettings is None:
        import pyJianYingDraft as _draft
        ClipSettings = _draft.ClipSettings
        TextStyle = _draft.TextStyle
        TextBackground = _draft.TextBackground

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

_MIN_DURATION_US = 500_000  # 字幕最短 0.5s


def _ok(**kwargs) -> dict:
    d = {"ok": True}
    d.update(kwargs)
    return d


def _fail(reason: str, detail: str, **extra) -> dict:
    d = {"ok": False, "reason": reason, "detail": detail}
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# 字幕样式预设 — 按语言区分
# ---------------------------------------------------------------------------

_STYLE_PRESETS = {
    # primary (zh): 白色大字 + 底部 + 半透明黑底
    "primary": {
        "size": 4.0,
        "color": (1.0, 1.0, 1.0),
        "transform_y": -0.75,
        "background": True,
    },
    # secondary (en): 灰色小字 + 更下方 + 无底板
    "secondary": {
        "size": 3.0,
        "color": (0.7, 0.7, 0.7),
        "transform_y": -0.85,
        "background": False,
    },
}


def _make_text_style(preset: dict) -> "TextStyle":
    return TextStyle(
        size=preset["size"],
        color=preset["color"],
    )


def _make_text_background() -> "TextBackground":
    return TextBackground(
        color=(0.0, 0.0, 0.0, 0.5),  # 半透明黑
        round_radius=0.05,
        alpha=0.6,
    )


# ---------------------------------------------------------------------------
# Tool — add_dual_subtitles
# ---------------------------------------------------------------------------

@tool
def add_dual_subtitles(
    project_name: str,
    subtitle_pairs: list[dict],
    primary_lang: str = "zh",
    secondary_lang: str = "en",
) -> dict:
    """多语言字幕 — 为已有草稿添加双轨双语字幕。

    主字幕在 Y=-0.75 (白色大字+底板)，副字幕在 Y=-0.85 (灰色小字)。
    两轨道使用相同的 start_time 和 duration，确保逐句对齐。

    Args:
        project_name: 已有草稿名称
        subtitle_pairs: 字幕对列表，每项 {"start_us": int, "duration_us": int,
                        "primary_text": str, "secondary_text": str}
        primary_lang: 主语言标签，默认 "zh"
        secondary_lang: 副语言标签，默认 "en"

    Returns:
        {"ok": True, "pair_count": 45, "primary_track": "Sub_Primary",
         "secondary_track": "Sub_Secondary", "skipped_pairs": 0, ...}
    """
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="add_dual_subtitles",
        metadata={
            "tool": "add_dual_subtitles",
            "phase": "1",
            "handler": "subtitle_ops",
            "capability_id": "TX-06",
        },
        input={
            "project_name": project_name,
            "pair_count": len(subtitle_pairs),
            "primary_lang": primary_lang,
            "secondary_lang": secondary_lang,
        },
    )

    try:
        # --- 校验 ---
        val_span = trace.start_observation(as_type="span",
            name="add_dual_subtitles.validation",
            input={"pair_count": len(subtitle_pairs)},
        )

        if not subtitle_pairs:
            val_span.update(level="ERROR", status_message="empty_input")
            val_span.end()
            return _fail("empty_input", "subtitle_pairs must not be empty")

        val_span.update(output={"valid": True})
        val_span.end()

        # --- 执行 ---
        exec_span = trace.start_observation(as_type="span",
            name="add_dual_subtitles.execution",
            input={"pair_count": len(subtitle_pairs)},
        )

        project = JyProject(project_name, overwrite=False)

        primary_preset = _STYLE_PRESETS["primary"]
        secondary_preset = _STYLE_PRESETS["secondary"]
        primary_style = _make_text_style(primary_preset)
        primary_bg = _make_text_background()
        secondary_style = _make_text_style(secondary_preset)

        primary_clip = ClipSettings(transform_y=primary_preset["transform_y"])
        secondary_clip = ClipSettings(transform_y=secondary_preset["transform_y"])

        skipped_details = []
        total = len(subtitle_pairs)

        for idx, pair in enumerate(subtitle_pairs):
            primary_text = str(pair.get("primary_text", "")).strip()
            secondary_text = str(pair.get("secondary_text", "")).strip()

            # 跳过空文本
            if not primary_text:
                skipped_details.append({"index": idx, "reason": "empty_text"})
                continue

            start_us = int(pair.get("start_us", 0))
            duration_us = int(pair.get("duration_us", 0))

            # duration=0 自动补偿
            if duration_us <= 0:
                duration_us = _MIN_DURATION_US

            # 主字幕
            project.add_text_simple(
                primary_text,
                start_time=start_us,
                duration=duration_us,
                track_name="Sub_Primary",
                style=primary_style,
                background=primary_bg,
                clip_settings=primary_clip,
            )

            # 副字幕 — 时间严格对齐主字幕
            if secondary_text:
                project.add_text_simple(
                    secondary_text,
                    start_time=start_us,
                    duration=duration_us,
                    track_name="Sub_Secondary",
                    style=secondary_style,
                    clip_settings=secondary_clip,
                )

        project.save()

        result = _ok(
            pair_count=total,
            primary_track="Sub_Primary",
            secondary_track="Sub_Secondary",
            skipped_pairs=len(skipped_details),
        )
        if skipped_details:
            result["skipped_details"] = skipped_details

        exec_span.update(output=result)
        exec_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        return _fail(type(e).__name__, str(e))
