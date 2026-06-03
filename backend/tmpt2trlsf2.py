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


project = JyProject("缉凶者拼接项目", width=1920, height=1080, fps=30)

video1 = "D:\\1-6 AI_Agent\\2_local_project\\EasyScene\\backend\\47d8edbd-5299-4917-be75-1f36396b156d\\generated_images\\缉凶者6_1_20260504_063448.mp4"
video2 = "D:\\1-6 AI_Agent\\2_local_project\\EasyScene\\backend\\47d8edbd-5299-4917-be75-1f36396b156d\\generated_images\\缉凶者6_2_20260504_064703.mp4"

# 添加第一个视频
project.add_media_safe(video1, "0s", track_name="main")

# 获取第一个视频时长（微秒）
video1_duration_us = project.get_track_duration("main")

# 添加第二个视频，紧接第一个视频之后
project.add_media_safe(video2, str(video1_duration_us) + "us", track_name="main")

# 在两视频之间添加模糊转场
# transition 作用于两个视频连接处
project.add_transition_simple("模糊", video_segment={"start_time": video1_duration_us, "duration": "1s"}, duration="1s")

# 防黑屏：裁剪项目总时长为所有片段的最大结束时间
_trim_project_duration(project)

# 保存项目
project.save()