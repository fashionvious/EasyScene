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


# 现在了解了情况。缉凶者_场景1 是一个空草稿（只有 meta_info，没有 draft_content.json）
# 上次导出的可能是空内容

# 用户说"这次用 zh_female_qingse，其他不变"
# 意味着需要重新创建缉凶者_场景1 的内容，使用新的发音人

# 但我不知道这个项目的原始内容是什么 - 它是空的
# 可能用户之前已经创建了项目但在剪映中手动编辑过

# 让我用 JyProject 加载这个项目看看会发生什么
project = JyProject("缉凶者_场景1")
print(f"Project name: {project.name}")
# 查看是否有轨道
print(f"Tracks count: {len(project.tracks)}")
for track in project.tracks:
    print(f"  Track: {track.type}, segments: {len(track.segments)}")