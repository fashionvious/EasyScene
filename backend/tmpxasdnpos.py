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


project = JyProject("缉凶者6_第一幕预告片", width=1920, height=1080, fps=30)

v1 = "D:/1-6 AI_Agent/2_local_project/EasyScene/backend/47d8edbd-5299-4917-be75-1f36396b156d/generated_images/缉凶者6_1_20260504_063448.mp4"
v2 = "D:/1-6 AI_Agent/2_local_project/EasyScene/backend/47d8edbd-5299-4917-be75-1f36396b156d/generated_images/缉凶者6_2_20260504_064703.mp4"
v3 = "D:/1-6 AI_Agent/2_local_project/EasyScene/backend/47d8edbd-5299-4917-be75-1f36396b156d/generated_images/缉凶者6_3_20260503_133541.mp4"
v4 = "D:/1-6 AI_Agent/2_local_project/EasyScene/backend/47d8edbd-5299-4917-be75-1f36396b156d/generated_images/缉凶者6_4_20260504_073607.mp4"
v5 = "D:/1-6 AI_Agent/2_local_project/EasyScene/backend/47d8edbd-5299-4917-be75-1f36396b156d/generated_images/缉凶者6_5_20260504_074040.mp4"

# 标题 "缉凶者6" 在第一个视频前展示3秒
project.add_text_simple("缉凶者6", start_time="0s", duration="3s", track_name="TitleTrack", font_size=80)

# 依次串联5个视频片段
project.add_media_safe(v1, "3s", track_name="main")
t1 = project.get_track_duration("main")

project.add_media_safe(v2, t1, track_name="main")
t2 = project.get_track_duration("main")

# 视频3 + 旁白"证据链已经完整"
project.add_media_safe(v3, t2, track_name="main")
t3 = project.get_track_duration("main")
project.add_narrated_subtitles("证据链已经完整", speaker="zh_male_huoli", start_time=t2, track_name="Narration3")

project.add_media_safe(v4, t3, track_name="main")
t4 = project.get_track_duration("main")

# 视频5 + 旁白"真相即将揭晓"
project.add_media_safe(v5, t4, track_name="main")
project.add_narrated_subtitles("真相即将揭晓", speaker="zh_male_huoli", start_time=t4, track_name="Narration5")

_trim_project_duration(project)
project.save()