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


project = JyProject("缉凶者拼接")

video1 = r"D:\1-6 AI_Agent\2_local_project\EasyScene\backend\47d8edbd-5299-4917-be75-1f36396b156d\generated_images\缉凶者6_1_20260504_063448.mp4"
video2 = r"D:\1-6 AI_Agent\2_local_project\EasyScene\backend\47d8edbd-5299-4917-be75-1f36396b156d\generated_images\缉凶者6_2_20260504_064703.mp4"

# 第一段视频：从 0s 开始，用 add_clip 精确控制
# 先添加第一个视频的全部时长
clip1 = project.add_clip(video1, "0s", "999999s", "0s", "main")
print(f"clip1: {clip1}")

# 获取第一个视频的实际结束时间（微秒）
# add_clip 返回的是 segment 对象
end1 = 10042000  # 从之前错误信息得知是 10042000 微秒

# 添加气泡转场到第一个视频末尾（转场会跨越两个片段）
project.add_transition_simple("气泡转场", clip1, "0.5s")

# 第二段视频：从第一个视频结束位置开始
clip2 = project.add_clip(video2, "0s", "999999s", end1, "main")
print(f"clip2: {clip2}")

_trim_project_duration(project)
project.save()