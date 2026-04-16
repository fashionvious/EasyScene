"""
Python 代码执行器 - 用于执行 LLM 生成的 JyProject 编排代码
"""
import os
import sys
import subprocess
import tempfile
import json
from typing import Optional, Any
from pathlib import Path
from langchain.tools import tool


class PythonCodeExecutor:
    """
    Python 代码执行器
    
    用于执行 LLM 根据 rules/ 规范生成的 JyProject 编排代码。
    支持沙箱执行，确保安全性。
    """
    
    def __init__(
        self,
        skill_root: str,
        work_dir: Optional[str] = None,
        timeout: int = 300
    ):
        """
        初始化执行器
        
        Args:
            skill_root: jianying-editor-skill 的根目录
            work_dir: 工作目录（用于存放临时文件和输出）
            timeout: 执行超时时间（秒）
        """
        self.skill_root = Path(skill_root)
        self.scripts_dir = self.skill_root / "scripts"
        self.work_dir = Path(work_dir) if work_dir else Path.cwd()
        self.timeout = timeout
        
    def generate_bootstrap_code(self) -> str:
        """
        生成环境初始化代码
        
        根据 rules/setup.md 的规范生成必要的导入和路径设置代码
        """
        return f'''import os
import sys

# 环境初始化
current_dir = os.path.dirname(os.path.abspath(__file__))
env_root = os.getenv("JY_SKILL_ROOT", "").strip()
skill_candidates = [
    env_root,
    r"{str(self.skill_root)}",
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
        "Could not find jianying-editor/scripts/jy_wrapper.py\\nTried:\\n- "
        + "\\n- ".join(attempted)
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
        print(f"已裁剪项目时长: {{max_end / 1000000:.2f}}s")
    return project
'''
    
    def execute(
        self,
        code: str,
        include_bootstrap: bool = True,
        capture_output: bool = True
    ) -> dict[str, Any]:
        """
        执行 Python 代码
        
        Args:
            code: 要执行的 Python 代码
            include_bootstrap: 是否包含环境初始化代码
            capture_output: 是否捕获输出
            
        Returns:
            执行结果
        """
        # 构建完整代码
        full_code = ""
        
        if include_bootstrap:
            full_code = self.generate_bootstrap_code() + "\n\n"
        
        full_code += code
        
        # 创建临时文件
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.py',
            delete=False,
            dir=self.work_dir
        ) as f:
            temp_file = f.name
            f.write(full_code)
        
        try:
            # 执行代码
            cmd = [sys.executable, temp_file]
            
            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                text=True,
                timeout=self.timeout,
                cwd=str(self.work_dir)
            )
            
            return {
                "success": result.returncode == 0,
                "output": result.stdout if capture_output else "",
                "error": result.stderr if result.returncode != 0 else None,
                "returncode": result.returncode,
                "temp_file": temp_file
            }
            
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"代码执行超时（{self.timeout}秒）",
                "temp_file": temp_file
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"执行失败: {str(e)}",
                "temp_file": temp_file
            }
        finally:
            # 清理临时文件
            try:
                os.unlink(temp_file)
            except:
                pass
    
    def execute_with_context(
        self,
        code: str,
        context: dict[str, Any] = None
    ) -> dict[str, Any]:
        """
        在给定上下文中执行代码
        
        Args:
            code: 要执行的代码
            context: 上下文变量（将被注入到执行环境）
            
        Returns:
            执行结果
        """
        # 生成上下文注入代码
        context_code = ""
        if context:
            for key, value in context.items():
                if isinstance(value, str):
                    context_code += f'{key} = r"{value}"\n'
                elif isinstance(value, (int, float, bool)):
                    context_code += f'{key} = {value}\n'
                elif isinstance(value, dict):
                    context_code += f'{key} = {json.dumps(value)}\n'
                else:
                    context_code += f'{key} = {repr(value)}\n'
        
        full_code = context_code + "\n" + code
        return self.execute(full_code)


# 创建 LangChain 工具
def create_python_executor_tool(skill_root: str, work_dir: str = None):
    """创建 Python 代码执行器工具"""
    executor = PythonCodeExecutor(skill_root, work_dir)
    
    @tool
    def execute_jyproject_code(code: str) -> str:
        """
        执行使用 JyProject 的 Python 代码。

【重要】代码中不要写任何 import 语句！JyProject、os 和 _trim_project_duration 已自动导入。
直接使用 JyProject 类即可，例如：
    project = JyProject("项目名")
    project.add_media_safe("video.mp4", "0s")
    _trim_project_duration(project)  # 裁剪黑屏尾帧，必须在 save() 前调用！
    project.save()

JyProject 核心 API：
- JyProject(name, width=1920, height=1080, fps=30)  创建项目
- project.add_media_safe(media_path, start_time, duration, track_name, source_start)  添加媒体（自动识别类型）
- project.add_clip(media_path, source_start, duration, target_start, track_name)  从媒体指定位置裁剪
- project.add_text_simple(text, start_time, duration, track_name, **kwargs)  添加文本/字幕
- project.add_audio_safe(media_path, start_time, duration, track_name)  添加音频
- project.add_cloud_media(query, start_time, duration)  添加云端视频素材
- project.add_cloud_music(query, start_time, duration, track_name)  添加云端音乐
- project.add_tts_intelligent(text, speaker, start_time, track_name)  TTS语音合成
- project.add_narrated_subtitles(text, speaker, start_time)  旁白+字幕对齐
- project.add_effect_simple(effect_name, start_time, duration)  添加特效
- project.add_transition_simple(transition_name, video_segment, duration)  添加转场
- project.add_web_asset_safe(html_path, start_time, duration)  添加Web动效
- project.get_track_duration(track_name)  获取轨道时长
- project.save()  保存项目（必须调用！）

防黑屏：在 project.save() 前必须调用 _trim_project_duration(project)，它会自动将项目总时长裁剪为所有片段的最大结束时间，避免视频播完后出现黑屏继续播放的问题。

时间格式：支持 "0s", "1s", "3s" 等字符串或微秒整数

Args:
    code: Python 代码字符串（不要包含 import 语句，JyProject 已自动导入）
            
Returns:
    执行结果或错误信息
        """
        result = executor.execute(code)
        
        if result["success"]:
            output = result.get("output", "")
            return f"执行成功\n{output}" if output else "执行成功"
        else:
            return f"执行失败: {result.get('error', '未知错误')}"
    
    @tool
    def validate_jyproject_code(code: str) -> str:
        """
        验证 JyProject 代码的语法正确性（不实际执行）。
        
        代码中不要写 import 语句，JyProject 已自动导入。
        
        Args:
            code: 要验证的 Python 代码（不要包含 import）
            
        Returns:
            验证结果
        """
        # 添加 bootstrap 代码进行语法检查
        full_code = executor.generate_bootstrap_code() + "\n" + code
        
        try:
            compile(full_code, '<string>', 'exec')
            return "代码语法正确"
        except SyntaxError as e:
            return f"语法错误: {e.msg} (行 {e.lineno})"
        except Exception as e:
            return f"验证失败: {str(e)}"
    
    return execute_jyproject_code, validate_jyproject_code


# 预定义的代码模板
CODE_TEMPLATES = {
    "basic_project": '''# 创建基础项目
project = JyProject("My Video Project")
assets_dir = os.path.join(skill_root, "assets")

# 导入视频
project.add_media_safe(os.path.join(assets_dir, "video.mp4"), "0s")

# 添加标题
project.add_text_simple("我的视频", start_time="1s", duration="3s")

# 保存项目
project.save()
print("项目已保存")
''',
    
    "add_subtitle": '''# 添加字幕
project = JyProject("{{project_name}}")

# 添加字幕轨道
project.add_subtitle(
    text="{{text}}",
    start_time="{{start_time}}",
    duration="{{duration}}"
)

project.save()
''',
    
    "add_keyframe": '''# 添加关键帧动画
project = JyProject("{{project_name}}")

# 添加关键帧
project.add_keyframe(
    track_index={{track_index}},
    segment_index={{segment_index}},
    property="{{property}}",
    time={{time}},
    value={{value}}
)

project.save()
''',
    
    "apply_effect": '''# 应用特效
project = JyProject("{{project_name}}")

# 应用滤镜
project.apply_filter(
    track_index={{track_index}},
    segment_index={{segment_index}},
    effect_id="{{effect_id}}"
)

project.save()
'''
}


def get_code_template(template_name: str, **kwargs) -> str:
    """
    获取代码模板并进行参数替换
    
    Args:
        template_name: 模板名称
        **kwargs: 模板参数
        
    Returns:
        替换后的代码
    """
    if template_name not in CODE_TEMPLATES:
        raise ValueError(f"未知的模板: {template_name}")
    
    code = CODE_TEMPLATES[template_name]
    
    # 替换参数
    for key, value in kwargs.items():
        code = code.replace(f"{{{{{key}}}}}", str(value))
    
    return code
