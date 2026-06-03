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


# 检查底层 vfx_ops.py 中 add_transition_simple 的实现
# 以及 pyJianYingDraft 中是否有其他转场相关类

import pyJianYingDraft as draft_module
attrs = [a for a in dir(draft_module) if 'trans' in a.lower() or 'Trans' in a]
print("Transition-related attrs in pyJianYingDraft:", attrs)

# 检查 JyProject 的 add_transition_simple 实际代码
import inspect
src_file = inspect.getfile(project.add_transition_simple)
print("Source file:", src_file)

# 读取源代码
with open(src_file, 'r', encoding='utf-8') as f:
    content = f.read()
    
# 找到 add_transition_simple 方法
start_idx = content.find('def add_transition_simple')
if start_idx >= 0:
    # 读取该方法直到下一个 def
    end_idx = content.find('\ndef ', start_idx + 1)
    method_code = content[start_idx:end_idx]
    print(method_code)