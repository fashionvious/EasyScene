"""
layout_ops.py — Phase 1 分屏布局工具

提供 apply_split_screen Agent Tool。
仅依赖 pyJianYingDraft + JyProject，无外部二进制依赖。
"""

import os
import sys
from pathlib import Path
from typing import Optional

from langchain.tools import tool

# ---------------------------------------------------------------------------
# 路径注入 — 与 timeline_ops 共享的寻径策略
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

# pyJianYingDraft 在 scripts/vendor 目录下
_VENDOR_DIR = str(Path(_SKILL_ROOT) / "vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

# ---------------------------------------------------------------------------
# 延迟导入
# ---------------------------------------------------------------------------
JyProject = None
safe_tim = None
ClipSettings = None


def _ensure_imports():
    global JyProject, safe_tim, ClipSettings
    if JyProject is None:
        from jy_wrapper import JyProject as _JP
        JyProject = _JP
    if safe_tim is None:
        from utils.formatters import safe_tim as _st
        safe_tim = _st
    if ClipSettings is None:
        # pyJianYingDraft 已由 jy_wrapper 加载到 sys.modules
        import pyJianYingDraft as _draft
        ClipSettings = _draft.ClipSettings

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
# 模板布局表 — 5 种分屏模板
# ---------------------------------------------------------------------------

_MAX_TRACKS = 10  # 剪映轨道数上限

# 每个模板定义一个 cells 列表，每格包含 (scale_x, scale_y, transform_x, transform_y)
_LAYOUT_TEMPLATES: dict[str, dict] = {
    "2H": {
        "cells": [
            {"sx": 0.5, "sy": 1.0, "tx": -0.5, "ty": 0.0},
            {"sx": 0.5, "sy": 1.0, "tx":  0.5, "ty": 0.0},
        ],
    },
    "2V": {
        "cells": [
            {"sx": 1.0, "sy": 0.5, "tx": 0.0, "ty":  0.5},
            {"sx": 1.0, "sy": 0.5, "tx": 0.0, "ty": -0.5},
        ],
    },
    "2x2": {
        "cells": [
            {"sx": 0.5, "sy": 0.5, "tx": -0.5, "ty":  0.5},
            {"sx": 0.5, "sy": 0.5, "tx":  0.5, "ty":  0.5},
            {"sx": 0.5, "sy": 0.5, "tx": -0.5, "ty": -0.5},
            {"sx": 0.5, "sy": 0.5, "tx":  0.5, "ty": -0.5},
        ],
    },
    "3x3": {
        "cells": [
            {"sx": 0.333, "sy": 0.333, "tx": -0.667, "ty":  0.667},
            {"sx": 0.333, "sy": 0.333, "tx":  0.0,   "ty":  0.667},
            {"sx": 0.333, "sy": 0.333, "tx":  0.667, "ty":  0.667},
            {"sx": 0.333, "sy": 0.333, "tx": -0.667, "ty":  0.0},
            {"sx": 0.333, "sy": 0.333, "tx":  0.0,   "ty":  0.0},
            {"sx": 0.333, "sy": 0.333, "tx":  0.667, "ty":  0.0},
            {"sx": 0.333, "sy": 0.333, "tx": -0.667, "ty": -0.667},
            {"sx": 0.333, "sy": 0.333, "tx":  0.0,   "ty": -0.667},
            {"sx": 0.333, "sy": 0.333, "tx":  0.667, "ty": -0.667},
        ],
    },
    "1+2": {
        "cells": [
            {"sx": 0.6, "sy": 1.0,  "tx": -0.33, "ty":  0.0},   # 左侧大
            {"sx": 0.4, "sy": 0.5,  "tx":  0.50, "ty":  0.5},   # 右上小
            {"sx": 0.4, "sy": 0.5,  "tx":  0.50, "ty": -0.5},   # 右下小
        ],
    },
}


def _layout_cell_to_dict(cell: dict) -> dict:
    return {"scale": (cell["sx"], cell["sy"]), "pos": (cell["tx"], cell["ty"])}


# ---------------------------------------------------------------------------
# Tool — apply_split_screen
# ---------------------------------------------------------------------------

@tool
def apply_split_screen(
    project_name: str,
    video_paths: list[str],
    template: str = "2x2",
    duration: str = "10s",
    fill_mode: str = "loop",
) -> dict:
    """分屏效果 — 将多段视频同时播放在一个画面的不同格中。

    支持 5 种布局模板: 2H(左右), 2V(上下), 2x2(四宫格),
    3x3(九宫格), 1+2(左侧大右侧两小)。

    Args:
        project_name: 剪映草稿名称
        video_paths: N 段视频绝对路径列表
        template: 布局模板，默认 "2x2"
        duration: 每格持续时长，默认 "10s"
        fill_mode: 时长不足时的填充策略 — "loop"/"freeze"/"stretch"

    Returns:
        {"ok": True, "template": "2x2", "layout": [...], "segment_ids": [...], ...}
    """
    _ensure_imports()
    lf = _get_langfuse()
    trace = lf.start_observation(as_type="trace",
        name="apply_split_screen",
        metadata={
            "tool": "apply_split_screen",
            "phase": "1",
            "handler": "layout_ops",
            "capability_id": "V-04",
        },
        input={
            "project_name": project_name,
            "template": template,
            "duration": duration,
            "video_count": len(video_paths),
            "fill_mode": fill_mode,
        },
    )

    try:
        # --- 模板校验 ---
        tmpl_span = trace.start_observation(as_type="span",
            name="apply_split_screen.template_validation",
            input={"template": template, "video_count": len(video_paths)},
        )

        layout = _LAYOUT_TEMPLATES.get(template)
        if layout is None:
            tmpl_span.update(
                level="ERROR",
                status_message=f"Unknown template: {template}",
            )
            tmpl_span.end()
            return _fail(
                "unknown_template",
                f"Unknown template: '{template}'",
                available=list(_LAYOUT_TEMPLATES.keys()),
            )

        cell_count = len(layout["cells"])

        if len(video_paths) != cell_count:
            tmpl_span.update(level="ERROR", status_message="Video count mismatch")
            tmpl_span.end()
            return _fail(
                "video_count_mismatch",
                f"template '{template}' requires {cell_count} videos, got {len(video_paths)}",
            )

        if cell_count > _MAX_TRACKS:
            tmpl_span.update(level="ERROR", status_message="Track count exceeds limit")
            tmpl_span.end()
            return _fail(
                "track_limit_exceeded",
                f"template '{template}' requires {cell_count} tracks, max is {_MAX_TRACKS}. "
                "Consider using nested PIP.",
            )

        # 校验合法 fill_mode
        if fill_mode not in ("loop", "freeze", "stretch"):
            tmpl_span.update(level="WARNING", status_message=f"Unknown fill_mode: {fill_mode}, fallback to loop")
            fill_mode = "loop"

        # 校验所有视频文件存在
        for i, vp in enumerate(video_paths):
            if not Path(vp).exists():
                tmpl_span.update(
                    level="ERROR",
                    status_message=f"Video not found: {vp}",
                )
                tmpl_span.end()
                return _fail("file_not_found", f"Video[{i}] not found: {vp}")

        tmpl_span.update(output={"valid": True, "cell_count": cell_count})
        tmpl_span.end()

        # --- 执行 ---
        exec_span = trace.start_observation(as_type="span",
            name="apply_split_screen.execution",
            input={"template": template, "duration": duration, "fill_mode": fill_mode},
        )

        project = JyProject(project_name, overwrite=True)
        segment_ids = []

        for i, (vpath, cell) in enumerate(zip(video_paths, layout["cells"])):
            track_name = f"SplitTrack_{i}"

            seg = project.add_media_safe(
                vpath,
                start_time="0s",
                duration=duration,
                track_name=track_name,
            )

            if seg is not None:
                seg.clip_settings = ClipSettings(
                    scale_x=cell["sx"],
                    scale_y=cell["sy"],
                    transform_x=cell["tx"],
                    transform_y=cell["ty"],
                )
                segment_ids.append(getattr(seg, "segment_id", "unknown"))
            else:
                segment_ids.append("failed")

        project.save()

        layout_result = []
        for i, cell in enumerate(layout["cells"]):
            layout_result.append({
                "track": i,
                "scale": (cell["sx"], cell["sy"]),
                "pos": (cell["tx"], cell["ty"]),
            })

        result = _ok(
            template=template,
            layout=layout_result,
            segment_ids=segment_ids,
            duration=duration,
        )

        exec_span.update(output=result)
        exec_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        try:
            tmpl_span.update(level="ERROR", status_message=str(e))
            tmpl_span.end()
        except Exception:
            pass
        return _fail(type(e).__name__, str(e))
