"""Phase 1—3 Agent 剪辑工具集。

timeline_ops   — J-Cut, L-Cut, reorder_segments
layout_ops     — split_screen
subtitle_ops   — dual_subtitles
transition_ops — zoom/push transitions (待实现)
draft_injector — Phase 2 JSON 注入工具 (待实现)
external_tools — Phase 3 外部原子工具 (待实现)
"""

from .timeline_ops import apply_jcut, apply_lcut, reorder_segments
from .layout_ops import apply_split_screen
from .subtitle_ops import add_dual_subtitles
from .transition_ops import apply_zoom_transition, apply_push_transition
from .draft_injector import (
    inject_mask_transition,
    inject_color_transition,
    add_karaoke_subtitle,
    inject_subtitle_slide,
    apply_bgm_ducking,
    apply_audio_speed,
    apply_speed_ramp,
)

__all__ = [
    "apply_jcut",
    "apply_lcut",
    "reorder_segments",
    "apply_split_screen",
    "add_dual_subtitles",
    "apply_zoom_transition",
    "apply_push_transition",
    "inject_mask_transition",
    "inject_color_transition",
    "add_karaoke_subtitle",
    "inject_subtitle_slide",
    "apply_bgm_ducking",
    "apply_audio_speed",
    "apply_speed_ramp",
]
