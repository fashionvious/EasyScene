"""
Python 代码执行器 - 用于执行 LLM 生成的 JyProject 编排代码
"""

import os
import sys
import subprocess
import tempfile
import json
import time
import logging
from typing import Optional, Any
from pathlib import Path
from langchain.tools import tool

try:
    from .utils.timeout_config import truncate_output
except ImportError:
    from utils.timeout_config import truncate_output

logger = logging.getLogger(__name__)

# 与需求 01 中的 truncate_output 保持一致的阈值（基于 UTF-8 字节数）
MAX_OUTPUT_BYTES = 10240  # 10KB，stdout 截断阈值
MAX_ERROR_BYTES = 5120  # 5KB，stderr 截断阈值（更激进）
MAX_SUMMARY_LINES = 50  # 摘要保留的最大行数


def summarize_output(stdout: str, stderr: str, success: bool) -> str:
    """
    执行结果智能摘要。

    成功时：提取首 5 行 + 尾 5 行（通常尾部包含 "已裁剪项目时长" 等关键信息）
    失败时：提取包含 'Error', 'Traceback', '❌' 的行
    """
    if success:
        lines = stdout.strip().split("\n")
        if len(lines) <= MAX_SUMMARY_LINES:
            return stdout
        head_count = 5
        tail_count = 5
        omitted = len(lines) - head_count - tail_count
        return (
            "\n".join(lines[:head_count])
            + f"\n... [{omitted} 行省略] ...\n"
            + "\n".join(lines[-tail_count:])
        )
    else:
        all_output = stdout + "\n" + stderr
        keywords = ["Error", "Traceback", "❌", "失败", "错误", "Exception"]
        error_lines = []
        for line in all_output.split("\n"):
            if any(kw in line for kw in keywords):
                error_lines.append(line)
        if error_lines:
            return "关键错误信息:\n" + "\n".join(error_lines[-20:])
        return "\n".join(all_output.split("\n")[-20:])


def _artifacts_dir(work_dir: Path) -> Path:
    """返回统一的临时产物目录，自动创建子目录。"""
    base = work_dir / ".agent_artifacts"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _cleanup_old_artifacts(work_dir: Path, max_age_days: int = 7) -> None:
    """清理超过 max_age_days 天的旧脚本和日志。"""
    artifacts = _artifacts_dir(work_dir)
    cutoff = time.time() - max_age_days * 86400
    for subdir in ("scripts", "logs"):
        d = artifacts / subdir
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file():
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                except OSError:
                    pass


def save_full_output(stdout: str, stderr: str, work_dir: Path) -> str | None:
    """
    将完整输出保存到日志文件。

    Returns:
        日志文件路径，如果输出为空则返回 None
    """
    if not stdout and not stderr:
        return None

    logs_dir = _artifacts_dir(work_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_filename = f"jy_exec_log_{timestamp}.txt"
    log_path = logs_dir / log_filename

    content = f"=== STDOUT ===\n{stdout}\n\n=== STDERR ===\n{stderr}\n"
    log_path.write_text(content, encoding="utf-8")

    return str(log_path)


class PythonCodeExecutor:
    """
    Python 代码执行器

    用于执行 LLM 根据 rules/ 规范生成的 JyProject 编排代码。
    支持沙箱执行，确保安全性。
    """

    def __init__(
        self, skill_root: str, work_dir: Optional[str] = None, timeout: int = 300
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
        capture_output: bool = True,
        max_retries: int = 0,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """
        执行 Python 代码

        Args:
            code: 要执行的 Python 代码
            include_bootstrap: 是否包含环境初始化代码
            capture_output: 是否捕获输出
            max_retries: 最大重试次数（默认 0 = 不重试）
                Python 执行器仅对 TimeoutExpired 和 OSError 重试，
                returncode != 0 不重试（LLM 代码逻辑错误重试无意义）
            trace_id: Langfuse trace_id，用于关联手动 span（None 时跳过）

        Returns:
            执行结果，包含 output, error, raw_output_truncated,
            full_log_path, temp_file 等字段
        """
        _cleanup_old_artifacts(self.work_dir)
        full_code = ""
        if include_bootstrap:
            full_code = self.generate_bootstrap_code() + "\n\n"
        full_code += code

        scripts_dir = _artifacts_dir(self.work_dir) / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            dir=str(scripts_dir),
            encoding="utf-8",
        ) as f:
            temp_file = f.name
            f.write(full_code)

        cmd = [sys.executable, temp_file]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        total_attempts = max(1, max_retries + 1)
        last_error = None

        # Langfuse 手动 span
        langfuse_span = None
        if trace_id:
            try:
                from .observability import get_langfuse_client
            except ImportError:
                from observability import get_langfuse_client
            langfuse = get_langfuse_client()
            if langfuse:
                langfuse_span = langfuse.span(
                    trace_id=trace_id,
                    name="python_executor:execute_jyproject",
                    input={
                        "code_length": len(code),
                        "include_bootstrap": include_bootstrap,
                    },
                )

        for attempt in range(total_attempts):
            result = None
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=capture_output,
                    timeout=self.timeout,
                    cwd=str(self.work_dir),
                    env=env,
                )

                # ——— 成功获得 subprocess 结果，应用摘要 / 日志逻辑 ———
                is_success = result.returncode == 0
                if capture_output:
                    output = (result.stdout or b"").decode("utf-8", errors="replace")
                    stderr_raw = (result.stderr or b"").decode(
                        "utf-8", errors="replace"
                    )
                else:
                    output = ""
                    stderr_raw = ""
                error = stderr_raw if result.returncode != 0 else ""

                # 截断判断（基于 UTF-8 字节数，与需求 01 一致）
                output_bytes = len(output.encode("utf-8"))
                error_bytes = len(error.encode("utf-8"))
                output_truncated = output_bytes > MAX_OUTPUT_BYTES
                error_truncated = error_bytes > MAX_ERROR_BYTES

                # 摘要化输出
                if is_success and output_truncated:
                    summarized_output = summarize_output(output, error, True)
                else:
                    summarized_output = output
                summarized_error = (
                    error[:MAX_ERROR_BYTES]
                    if (not is_success and error_truncated)
                    else error
                )

                # 持久化完整日志（截断或失败时）
                full_log_path = None
                if output_truncated or not is_success:
                    full_log_path = save_full_output(output, error, self.work_dir)

                # 临时文件清理：成功即删，失败保留用于调试
                if is_success:
                    try:
                        os.unlink(temp_file)
                    except OSError:
                        pass
                    temp_file_out = None
                else:
                    temp_file_out = temp_file

                if langfuse_span:
                    langfuse_span.update(output={
                        "success": is_success,
                        "returncode": result.returncode,
                        "output_bytes": output_bytes,
                        "truncated": output_truncated,
                    })
                    if not is_success:
                        langfuse_span.update(
                            level="ERROR",
                            status_message=truncate_output(error),
                        )
                    langfuse_span.end()

                return {
                    "success": is_success,
                    "output": summarized_output if is_success else summarized_output,
                    "error": summarized_error if not is_success else None,
                    "returncode": result.returncode,
                    "raw_output_truncated": output_truncated,
                    "full_log_path": full_log_path,
                    "temp_file": temp_file_out,
                }

            except subprocess.TimeoutExpired:
                last_error = f"代码执行超时（{self.timeout}秒）"
                if langfuse_span:
                    langfuse_span.update(
                        level="WARNING",
                        status_message=f"timeout({self.timeout}s)",
                    )
                    langfuse_span.end()
                logger.warning(
                    "[attempt %d/%d] timeout after %ds",
                    attempt + 1,
                    total_attempts,
                    self.timeout,
                )

            except OSError as e:
                last_error = str(e)
                logger.warning(
                    "[attempt %d/%d] OSError: %s",
                    attempt + 1,
                    total_attempts,
                    last_error,
                )

            except Exception as e:
                # 非预期异常不重试，保留源代码供排查
                if langfuse_span:
                    langfuse_span.update(
                        level="ERROR", status_message=str(e),
                    )
                    langfuse_span.end()
                return {
                    "success": False,
                    "error": f"执行失败: {str(e)}",
                    "temp_file": temp_file,
                }

            # 指数退避等待
            if attempt < max_retries:
                wait = min(2**attempt, 30)
                logger.info("等待 %ds 后重试...", wait)
                time.sleep(wait)

            # 清理上次的临时文件，创建新的（重试时需要新文件）
            try:
                os.unlink(temp_file)
            except OSError:
                pass

            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                dir=self.work_dir,
                encoding="utf-8",
            ) as f:
                temp_file = f.name
                f.write(full_code)

        # 重试耗尽——保留源代码供排查
        try:
            os.unlink(temp_file)
        except OSError:
            pass

        return {
            "success": False,
            "error": f"重试 {max_retries} 次后仍失败: {last_error}",
            "attempts": total_attempts,
        }

    def execute_with_context(
        self, code: str, context: dict[str, Any] = None
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
                    context_code += f"{key} = {value}\n"
                elif isinstance(value, dict):
                    context_code += f"{key} = {json.dumps(value)}\n"
                else:
                    context_code += f"{key} = {repr(value)}\n"

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
            msg = f"执行成功\n{output}" if output else "执行成功"
            if result.get("raw_output_truncated"):
                log_path = result.get("full_log_path")
                if log_path:
                    msg += f"\n\n(完整输出已截断，日志文件: {log_path})"
            return msg
        else:
            msg = f"执行失败: {result.get('error', '未知错误')}"
            log_path = result.get("full_log_path")
            if log_path:
                msg += f"\n(完整日志: {log_path})"
            temp_path = result.get("temp_file")
            if temp_path:
                msg += f"\n(源代码: {temp_path})"
            return msg

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
            compile(full_code, "<string>", "exec")
            return "代码语法正确"
        except SyntaxError as e:
            return f"语法错误: {e.msg} (行 {e.lineno})"
        except Exception as e:
            return f"验证失败: {str(e)}"

    return execute_jyproject_code, validate_jyproject_code


# 预定义的代码模板
CODE_TEMPLATES = {
    "basic_project": """# 创建基础项目
project = JyProject("My Video Project")
assets_dir = os.path.join(skill_root, "assets")

# 导入视频
project.add_media_safe(os.path.join(assets_dir, "video.mp4"), "0s")

# 添加标题
project.add_text_simple("我的视频", start_time="1s", duration="3s")

# 保存项目
project.save()
print("项目已保存")
""",
    "add_subtitle": """# 添加字幕
project = JyProject("{{project_name}}")

# 添加字幕轨道
project.add_subtitle(
    text="{{text}}",
    start_time="{{start_time}}",
    duration="{{duration}}"
)

project.save()
""",
    "add_keyframe": """# 添加关键帧动画
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
""",
    "apply_effect": """# 应用特效
project = JyProject("{{project_name}}")

# 应用滤镜
project.apply_filter(
    track_index={{track_index}},
    segment_index={{segment_index}},
    effect_id="{{effect_id}}"
)

project.save()
""",
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
