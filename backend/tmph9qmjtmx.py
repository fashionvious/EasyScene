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


project = JyProject("缉凶者_场景1", width=1920, height=1080, fps=30)

# 分镜视频路径
videos = [
    "D:/1-6 AI_Agent/2_local_project/EasyScene/backend/47d8edbd-5299-4917-be75-1f36396b156d/generated_images/缉凶者6_1_20260504_063448.mp4",
    "D:/1-6 AI_Agent/2_local_project/EasyScene/backend/47d8edbd-5299-4917-be75-1f36396b156d/generated_images/缉凶者6_2_20260504_064703.mp4",
    "D:/1-6 AI_Agent/2_local_project/EasyScene/backend/47d8edbd-5299-4917-be75-1f36396b156d/generated_images/缉凶者6_3_20260503_133541.mp4",
    "D:/1-6 AI_Agent/2_local_project/EasyScene/backend/47d8edbd-5299-4917-be75-1f36396b156d/generated_images/缉凶者6_4_20260504_073607.mp4",
    "D:/1-6 AI_Agent/2_local_project/EasyScene/backend/47d8edbd-5299-4917-be75-1f36396b156d/generated_images/缉凶者6_5_20260504_074040.mp4",
]

# 依次添加5个分镜到main轨道，自动计算起始时间
current_start = "0s"
for i, video_path in enumerate(videos):
    track_name = "main"
    project.add_media_safe(video_path, current_start, track_name=track_name)
    # 获取当前轨道时长，作为下一个视频的起始时间
    duration = project.get_track_duration(track_name)
    current_start = str(duration) + "us" if isinstance(duration, int) else duration
    print(f"分镜{i+1} 已添加，当前轨道时长: {duration}")

_trim_project_duration(project)
project.save()
print("项目保存成功！")