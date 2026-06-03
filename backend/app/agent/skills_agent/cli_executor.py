"""
通用 CLI 脚本执行器 - 统一收敛所有一次性脚本，通过 script_name 动态路由
"""
import os
import subprocess
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

# 可重试的进程退出码——仅白名单中的退出码触发重试
# 不包含 2(ENOENT) 和 22(EINVAL) 等参数错误码
RETRYABLE_RETURN_CODES = frozenset({
    1,    # 一般错误（多数 CLI 工具的默认错误码）
    137,  # SIGKILL（OOM killer）
    139,  # SIGSEGV
})


# 脚本注册表 - 定义可用的脚本及其参数
SCRIPT_REGISTRY = {
    "asset_search": {
        "script": "asset_search.py",
        "description": "搜索特效、转场、动画等素材",
        "args": {
            "query": "搜索关键词（中文或英文）",
            "category": "分类（filters/transitions/text_animations等）"
        },
        "example": "python asset_search.py '复古' -c filters"
    },
    "auto_exporter": {
        "script": "auto_exporter.py",
        "description": "无头导出草稿为 MP4/SRT",
        "args": {
            "draft_name": "草稿名称",
            "output_path": "输出文件路径",
            "resolution": "分辨率（480/720/1080/2K/4K/8K）",
            "framerate": "帧率（24/25/30/50/60）"
        },
        "example": "python auto_exporter.py 'DraftName' 'output.mp4' --res 1080 --fps 60"
    },
    "draft_inspector": {
        "script": "draft_inspector.py",
        "description": "检查草稿列表和详情",
        "args": {
            "action": "操作类型（list/summary/show）",
            "name": "草稿名称（用于 summary/show）",
            "limit": "限制数量（用于 list）",
            "kind": "显示类型（用于 show）",
            "json": "是否输出 JSON 格式"
        },
        "example": "python draft_inspector.py list --limit 20"
    },
    "movie_commentary_builder": {
        "script": "movie_commentary_builder.py",
        "description": "从故事板 JSON 生成 60 秒解说视频",
        "args": {
            "video": "视频文件路径",
            "json": "故事板 JSON 文件路径"
        },
        "example": "python movie_commentary_builder.py --video 'video.mp4' --json 'storyboard.json'"
    },
    "sync_jy_assets": {
        "script": "sync_jy_assets.py",
        "description": "从剪映 App 同步收藏/播放过的 BGM",
        "args": {},
        "example": "python sync_jy_assets.py"
    },
    "api_validator": {
        "script": "api_validator.py",
        "description": "运行环境诊断",
        "args": {},
        "example": "python api_validator.py"
    },
    "smart_zoomer": {
        "script": "smart_zoomer.py",
        "description": "智能变焦工具",
        "args": {
            "video": "视频文件路径",
            "events_json": "事件 JSON 文件路径"
        },
        "example": "python smart_zoomer.py --video 'v.mp4' --events 'e.json'"
    },
    "smart_rough_cut": {
        "script": "smart_rough_cut.py",
        "description": "智能粗剪工具",
        "args": {
            "video": "视频文件路径"
        },
        "example": "python smart_rough_cut.py --video 'video.mp4'"
    },
    "universal_tts": {
        "script": "universal_tts.py",
        "description": "通用 TTS 工具",
        "args": {
            "text": "要转换的文本",
            "output": "输出音频路径",
            "speaker": "说话人"
        },
        "example": "python universal_tts.py --text '你好' --output 'audio.mp3'"
    },
    "web_recorder": {
        "script": "web_recorder.py",
        "description": "Web 录屏工具",
        "args": {
            "url": "要录制的 URL",
            "duration": "录制时长（秒）",
            "output": "输出文件路径"
        },
        "example": "python web_recorder.py --url 'https://example.com' --duration 10"
    }
}


class CLIScriptExecutor:
    """CLI 脚本执行器"""
    
    def __init__(self, scripts_dir: str):
        """
        初始化执行器
        
        Args:
            scripts_dir: scripts 目录的路径
        """
        self.scripts_dir = Path(scripts_dir)
        
    def list_available_scripts(self) -> str:
        """列出所有可用的脚本"""
        result = ["# 可用的 CLI 脚本\n"]
        
        for name, info in SCRIPT_REGISTRY.items():
            result.append(f"## {name}")
            result.append(f"描述: {info['description']}")
            result.append(f"示例: {info['example']}")
            
            if info['args']:
                result.append("参数:")
                for arg_name, arg_desc in info['args'].items():
                    result.append(f"  - {arg_name}: {arg_desc}")
            result.append("")
            
        return "\n".join(result)
    
    def _build_cmd(self, script_name: str, args: dict[str, Any]) -> list[str]:
        """构建命令行参数"""
        script_info = SCRIPT_REGISTRY[script_name]
        script_path = self.scripts_dir / script_info["script"]
        cmd = ["python", str(script_path)]

        if script_name == "asset_search":
            if "query" in args:
                cmd.append(args["query"])
            if "category" in args:
                cmd.extend(["-c", args["category"]])
        elif script_name == "auto_exporter":
            if "draft_name" in args:
                cmd.append(args["draft_name"])
            if "output_path" in args:
                cmd.append(args["output_path"])
            if "resolution" in args:
                cmd.extend(["--res", str(args["resolution"])])
            if "framerate" in args:
                cmd.extend(["--fps", str(args["framerate"])])
        elif script_name == "draft_inspector":
            action = args.get("action", "list")
            cmd.append(action)
            if action == "list" and "limit" in args:
                cmd.extend(["--limit", str(args["limit"])])
            elif action in ["summary", "show"] and "name" in args:
                cmd.extend(["--name", args["name"]])
                if action == "show":
                    if "kind" in args:
                        cmd.extend(["--kind", args["kind"]])
                    if args.get("json"):
                        cmd.append("--json")
        elif script_name == "movie_commentary_builder":
            if "video" in args:
                cmd.extend(["--video", args["video"]])
            if "json" in args:
                cmd.extend(["--json", args["json"]])
        elif script_name == "smart_zoomer":
            if "video" in args:
                cmd.extend(["--video", args["video"]])
            if "events_json" in args:
                cmd.extend(["--events", args["events_json"]])
        elif script_name == "smart_rough_cut":
            if "video" in args:
                cmd.extend(["--video", args["video"]])
        elif script_name == "universal_tts":
            if "text" in args:
                cmd.extend(["--text", args["text"]])
            if "output" in args:
                cmd.extend(["--output", args["output"]])
            if "speaker" in args:
                cmd.extend(["--speaker", args["speaker"]])
        elif script_name == "web_recorder":
            if "url" in args:
                cmd.extend(["--url", args["url"]])
            if "duration" in args:
                cmd.extend(["--duration", str(args["duration"])])
            if "output" in args:
                cmd.extend(["--output", args["output"]])

        return cmd

    def execute(
        self,
        script_name: str,
        args: dict[str, Any],
        timeout: int = 300,
        max_retries: int = 0,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """
        执行指定的脚本

        Args:
            script_name: 脚本名称
            args: 脚本参数
            timeout: 超时时间（秒）
            max_retries: 最大重试次数（默认 0 = 不重试）
            trace_id: Langfuse trace_id，用于关联手动 span 到父 trace

        Returns:
            执行结果，包含 success, output, error 等字段
        """
        if script_name not in SCRIPT_REGISTRY:
            return {
                "success": False,
                "error": f"未知的脚本: {script_name}",
                "available_scripts": list(SCRIPT_REGISTRY.keys()),
            }

        script_path = self.scripts_dir / SCRIPT_REGISTRY[script_name]["script"]
        if not script_path.exists():
            return {
                "success": False,
                "error": f"脚本文件不存在: {script_path}",
            }

        cmd = self._build_cmd(script_name, args)

        # 手动 span：LangChain 无法覆盖的 subprocess 调用
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
                    name=f"cli_execute:{script_name}",
                    input={"script": script_name, "args": args},
                )

        last_error = None
        total_attempts = max(1, max_retries + 1)

        for attempt in range(total_attempts):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=timeout,
                    cwd=str(self.scripts_dir),
                )

                if result.returncode == 0:
                    output = result.stdout.strip()
                    try:
                        parsed_output = json.loads(output)
                    except json.JSONDecodeError:
                        parsed_output = truncate_output(output)

                    if langfuse_span:
                        langfuse_span.update(
                            output={"success": True, "returncode": 0},
                        )
                        langfuse_span.end()

                    return {
                        "success": True,
                        "output": parsed_output,
                        "raw_output": truncate_output(output),
                        "error": None,
                        "returncode": 0,
                    }

                # 不可重试的错误码 → 立即返回
                if result.returncode not in RETRYABLE_RETURN_CODES:
                    if langfuse_span:
                        langfuse_span.update(
                            level="ERROR",
                            output={
                                "success": False,
                                "returncode": result.returncode,
                            },
                            status_message=truncate_output(
                                result.stderr.strip()
                            ),
                        )
                        langfuse_span.end()

                    return {
                        "success": False,
                        "output": None,
                        "raw_output": truncate_output(result.stdout.strip()),
                        "error": truncate_output(result.stderr.strip()),
                        "returncode": result.returncode,
                    }

                # 可重试的错误码 → 记录并等待
                last_error = (
                    f"returncode={result.returncode}, "
                    f"stderr={truncate_output(result.stderr.strip())}"
                )
                logger.warning(
                    "[attempt %d/%d] %s failed: %s",
                    attempt + 1, total_attempts, script_name, last_error,
                )

            except subprocess.TimeoutExpired:
                last_error = f"timeout({timeout}s)"
                logger.warning(
                    "[attempt %d/%d] %s %s",
                    attempt + 1, total_attempts, script_name, last_error,
                )

            except Exception:
                # 非预期异常 → 不重试
                logger.exception("Unexpected error executing %s", script_name)
                if langfuse_span:
                    langfuse_span.update(
                        level="ERROR",
                        status_message="非预期异常",
                    )
                    langfuse_span.end()
                return {
                    "success": False,
                    "error": f"执行失败: 非预期异常",
                    "attempts": attempt + 1,
                }

            # 指数退避等待（最后一次不等待）
            if attempt < max_retries:
                wait = min(2 ** attempt, 30)
                logger.info("等待 %ds 后重试...", wait)
                time.sleep(wait)

        # 重试耗尽
        if langfuse_span:
            langfuse_span.update(
                level="ERROR",
                status_message=f"重试 {max_retries} 次后仍失败: {last_error}",
            )
            langfuse_span.end()

        return {
            "success": False,
            "error": f"重试 {max_retries} 次后仍失败: {last_error}",
            "attempts": total_attempts,
        }


# 创建 LangChain 工具
def create_cli_executor_tool(scripts_dir: str):
    """创建 CLI 执行器工具"""
    executor = CLIScriptExecutor(scripts_dir)
    
    @tool
    def execute_cli_script(
        script_name: str,
        query: str = "",
        category: str = "",
        text: str = "",
        output: str = "",
        speaker: str = "zh_male_huoli",
        video: str = "",
        draft_name: str = "",
        output_path: str = "",
        resolution: str = "1080p",
        framerate: int = 30,
        url: str = "",
        duration: int = 10,
    ) -> str:
        """
        执行 jianying-editor-skill 中的 CLI 脚本。

        可用脚本:
        - asset_search: 搜索特效/转场/动画 (传 query + category)
        - auto_exporter: 导出草稿为 MP4 (传 draft_name + output_path + resolution + framerate)
        - draft_inspector: 检查草稿列表和详情
        - movie_commentary_builder: 从故事板生成解说视频 (传 video + json)
        - sync_jy_assets: 同步剪映 App 素材
        - api_validator: 环境诊断
        - smart_zoomer: 智能变焦 (传 video)
        - smart_rough_cut: 智能粗剪 (传 video)
        - universal_tts: TTS 语音合成 (传 text + output + speaker)
        - web_recorder: Web 录屏 (传 url + duration + output)

        参数说明:
            script_name: 脚本名称 (必填)
            query: 搜索关键词 (asset_search)
            category: 素材分类 filters/transitions/text_animations (asset_search)
            text: TTS 文本内容 (universal_tts)
            output: 输出文件路径 (universal_tts, web_recorder)
            speaker: 发音人 (universal_tts, 默认 zh_male_huoli)
            video: 视频文件路径 (smart_rough_cut, smart_zoomer)
            draft_name: 草稿名称 (auto_exporter)
            output_path: 导出输出路径 (auto_exporter)
            resolution: 分辨率 480/720/1080/2K/4K (auto_exporter)
            framerate: 帧率 24/25/30/50/60 (auto_exporter)
            url: 要录制的网址 (web_recorder)
            duration: 录制时长秒数 (web_recorder)
        """
        # 构建 args：只传非空的参数
        all_kwargs = {
            "query": query, "category": category, "text": text, "output": output,
            "speaker": speaker, "video": video, "draft_name": draft_name,
            "output_path": output_path, "resolution": resolution,
            "framerate": framerate, "url": url, "duration": duration,
        }
        args = {k: v for k, v in all_kwargs.items() if v not in ("", 0)}
        result = executor.execute(script_name, args)
        
        if result["success"]:
            output = result.get("output", "")
            if isinstance(output, dict):
                return json.dumps(output, ensure_ascii=False, indent=2)
            return str(output)
        else:
            return f"执行失败: {result.get('error', '未知错误')}"
    
    @tool
    def list_cli_scripts() -> str:
        """列出所有可用的 CLI 脚本及其用法"""
        return executor.list_available_scripts()
    
    return execute_cli_script, list_cli_scripts
