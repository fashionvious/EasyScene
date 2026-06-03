import os
import sys

# 环境初始化
current_dir = os.path.dirname(os.path.abspath(__file__))
env_root = os.getenv("JY_SKILL_ROOT", "").strip()
skill_candidates = [
    env_root,
    r"D:\1-6 AI_Agent\2_local_project\EasyScene\backend\jianying-editor-skill",
    os.path.join(current_dir, ".agent", "skills", "jianying-editor"),
    os.path.join(current_dir, ".trae", "skills", "jianying-editor"),
    os.path.join(current_dir, ".claude", "skills", "jianying-editor"),
    os.path.join(current_dir, "skills", "jianying-editor"),
    os.path.abspath(".agent/skills/jianying-editor"),
    os.path.dirname(current_dir),
]

scripts_path = None
attempted = []
for p in skill_candidates:
    if not p:
        continue
    p = os.path.abspath(p)
    attempted.append(p)
    if os.path.exists(os.path.join(p, "scripts", "jy_wrapper.py")):
        scripts_path = os.path.join(p, "scripts")
        break

if not scripts_path:
    raise ImportError(
        "Could not find jianying-editor/scripts/jy_wrapper.py\nTried:\n- "
        + "\n- ".join(attempted)
    )

if scripts_path not in sys.path:
    sys.path.insert(0, scripts_path)

from jy_wrapper import JyProject

def _trim_project_duration(project):
    """自动裁剪项目总时长为所有轨道中片段的最大结束时间，避免黑屏尾帧。"""
    max_end = 0
    for track in project.script.tracks.values():
        for seg in track.segments:
            seg_end = seg.target_timerange.start + seg.target_timerange.duration
            if seg_end > max_end:
                max_end = seg_end
    if max_end > 0 and project.script.duration > max_end:
        project.script.duration = max_end
        print(f"已裁剪项目时长: {max_end / 1000000:.2f}s")
    return project


# 直接读取草稿的 draft_content.json 来查找和修改 TTS 发音人
import json

draft_dir = "C:/Users/ali/AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft/缉凶者_场景1"
draft_file = os.path.join(draft_dir, "draft_content.json")

with open(draft_file, 'r', encoding='utf-8') as f:
    content = json.load(f)

# 查找所有包含 speaker/TTS 信息的 materials
# TTS 音频通常在 materials.audio 或 materials.texts 中
materials = content.get("materials", {})

# 检查 audios
audios = materials.get("audios", [])
print(f"Found {len(audios)} audio materials")
for a in audios:
    # 查找有 TTS 标记的音频
    if "tts" in str(a).lower() or "path" in a:
        print(f"  Audio ID: {a.get('id', 'N/A')}, path: {a.get('path', 'N/A')}")
        # 查看所有 key
        print(f"  Keys: {list(a.keys())}")

# 检查 texts (字幕)
texts = materials.get("texts", [])
print(f"Found {len(texts)} text materials")