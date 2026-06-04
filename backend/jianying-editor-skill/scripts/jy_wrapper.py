"""
JianYing Editor Skill - High Level Wrapper (Mixin Based)
旨在解决路径依赖、API 复杂度及严格校验问题。
"""

import os
import sys
import uuid
from typing import Union, Optional

# 环境初始化
from utils.env_setup import setup_env
setup_env()

# 导入工具函数
from utils.constants import SYNONYMS
from utils.formatters import (
    resolve_enum_with_synonyms, format_srt_time, safe_tim, 
    get_duration_ffprobe_cached, get_default_drafts_root, get_all_drafts
)

# 导入基类与 Mixins
from core.project_base import JyProjectBase
from core.media_ops import MediaOpsMixin
from core.text_ops import TextOpsMixin
from core.vfx_ops import VfxOpsMixin
from core.mocking_ops import MockingOpsMixin

try:
    import pyJianYingDraft as draft
    from pyJianYingDraft import VideoSceneEffectType, TransitionType
except ImportError:
    draft = None

class JyProject(JyProjectBase, MediaOpsMixin, TextOpsMixin, VfxOpsMixin, MockingOpsMixin):
    """
    高层封装工程类。通过多重继承 Mixins 实现功能解耦。
    """
    def _resolve_enum(self, enum_cls, name: str):
        return resolve_enum_with_synonyms(enum_cls, name, SYNONYMS)

    def add_clip(self, media_path: str, source_start: Union[str, int], duration: Union[str, int],
                 target_start: Union[str, int] = None, track_name: str = "VideoTrack", **kwargs):
        """高层剪辑接口：从媒体指定位置裁剪指定长度，并放入轨道。"""
        if target_start is None:
            target_start = self.get_track_duration(track_name)
        return self.add_media_safe(media_path, target_start, duration, track_name, source_start=source_start, **kwargs)

    def _sync_segment_materials(self):
        """
        将各轨道 segment 上后添加的 materials（滤镜、淡入淡出、特效等）
        同步到 script.materials 中，确保 save() 时不会丢失。

        背景：add_media_safe() 内部调用 add_segment() 时收集 materials，
        但 LLM 生成的代码在拿到 segment 之后才会调用 .add_filter() / .add_fade() 等，
        此时 materials 已错过收集窗口。此方法在 save 前补救。
        """
        try:
            from pyJianYingDraft import (
                VideoSegment, AudioSegment, TextSegment, StickerSegment,
                EffectSegment, FilterSegment,
            )
        except ImportError:
            return

        mats = self.script.materials

        for track in self.script.tracks.values():
            for seg in track.segments:
                if isinstance(seg, VideoSegment):
                    if seg.fade is not None and seg.fade not in mats:
                        mats.audio_fades.append(seg.fade)
                    for f in seg.filters:
                        if f not in mats:
                            mats.filters.append(f)
                    for e in seg.effects:
                        if e not in mats:
                            mats.video_effects.append(e)
                    if seg.animations_instance is not None and seg.animations_instance not in mats:
                        mats.animations.append(seg.animations_instance)
                    if seg.mask is not None:
                        mats.masks.append(seg.mask.export_json())
                    if seg.transition is not None and seg.transition not in mats:
                        mats.transitions.append(seg.transition)
                    if seg.background_filling is not None:
                        mats.canvases.append(seg.background_filling)
                elif isinstance(seg, AudioSegment):
                    if seg.fade is not None and seg.fade not in mats:
                        mats.audio_fades.append(seg.fade)
                    for e in seg.effects:
                        if e not in mats:
                            mats.audio_effects.append(e)
                elif isinstance(seg, TextSegment):
                    if seg.animations_instance is not None and seg.animations_instance not in mats:
                        mats.animations.append(seg.animations_instance)
                    if seg.bubble is not None:
                        mats.filters.append(seg.bubble)
                    if seg.effect is not None:
                        mats.filters.append(seg.effect)
                elif isinstance(seg, EffectSegment):
                    if seg.effect_inst is not None and seg.effect_inst not in mats:
                        mats.video_effects.append(seg.effect_inst)
                elif isinstance(seg, FilterSegment):
                    if seg.material is not None and seg.material not in mats:
                        mats.filters.append(seg.material)

    def save(self):
        """保存并执行质检报告。"""
        self._sync_segment_materials()
        self.script.save()
        self._patch_cloud_material_ids()
        self._force_activate_adjustments()

        draft_path = os.path.join(self.root, self.name)
        if os.path.exists(draft_path):
            os.utime(draft_path, None)
        print(f"✅ Project '{self.name}' saved and patched.")
        return {"status": "SUCCESS", "draft_path": draft_path}

# 导出工具函数以便向下兼容
__all__ = ["JyProject", "get_default_drafts_root", "get_all_drafts", "safe_tim", "format_srt_time"]

if __name__ == "__main__":
    # 测试代码
    try:
        project = JyProject("Refactor_Test_Project", overwrite=True)
        print("🚀 Refactored JyProject initialized successfully.")
    except Exception as e:
        print(f"❌ Initialization failed: {e}")
