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


project = JyProject("缉凶者6_丁锋片头", width=1920, height=1080)

# 1. 丁锋角色图作为片头（3秒）
img_path = r"D:\1-6 AI_Agent\2_local_project\EasyScene\backend\47d8edbd-5299-4917-be75-1f36396b156d\generated_images\丁锋_three_view_20260503_072939.png"
project.add_media_safe(img_path, "0s", duration="3s", track_name="main")

# 2. 缉凶者6_8 视频接在片头后（从3s开始）
video_path = r"D:\1-6 AI_Agent\2_local_project\EasyScene\backend\47d8edbd-5299-4917-be75-1f36396b156d\generated_images\缉凶者6_8_20260504_080706.mp4"
project.add_media_safe(video_path, "3s", track_name="main")

# 3. TTS旁白
project.add_tts_intelligent("白昊天是本案的关键嫌疑人", "zh_male_huoli", "3s", track_name="audio")

# 4. 转场：在图片和视频之间添加
project.add_transition_simple("模糊", "3s", duration="0.5s")

# 防黑屏 + 保存
_trim_project_duration(project)
project.save()