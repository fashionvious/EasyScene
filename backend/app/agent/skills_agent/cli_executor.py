"""
通用 CLI 脚本执行器 - 统一收敛所有一次性脚本，通过 script_name 动态路由
"""
import os
import subprocess
import json
from typing import Optional, Any
from pathlib import Path
from langchain.tools import tool


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
    
    def execute(
        self,
        script_name: str,
        args: dict[str, Any],
        timeout: int = 300
    ) -> dict[str, Any]:
        """
        执行指定的脚本
        
        Args:
            script_name: 脚本名称
            args: 脚本参数
            timeout: 超时时间（秒）
            
        Returns:
            执行结果，包含 success, output, error 等字段
        """
        # 检查脚本是否注册
        if script_name not in SCRIPT_REGISTRY:
            return {
                "success": False,
                "error": f"未知的脚本: {script_name}",
                "available_scripts": list(SCRIPT_REGISTRY.keys())
            }
        
        # 获取脚本信息
        script_info = SCRIPT_REGISTRY[script_name]
        script_path = self.scripts_dir / script_info["script"]
        
        # 检查脚本文件是否存在
        if not script_path.exists():
            return {
                "success": False,
                "error": f"脚本文件不存在: {script_path}"
            }
        
        # 构建命令
        cmd = ["python", str(script_path)]
        
        # 根据脚本类型添加参数
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
        
        # 执行命令
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.scripts_dir)
            )
            
            # 尝试解析 JSON 输出
            output = result.stdout.strip()
            try:
                parsed_output = json.loads(output)
            except json.JSONDecodeError:
                parsed_output = output
            
            return {
                "success": result.returncode == 0,
                "output": parsed_output,
                "raw_output": output,
                "error": result.stderr.strip() if result.returncode != 0 else None,
                "returncode": result.returncode
            }
            
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"脚本执行超时（{timeout}秒）"
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"执行失败: {str(e)}"
            }


# 创建 LangChain 工具
def create_cli_executor_tool(scripts_dir: str):
    """创建 CLI 执行器工具"""
    executor = CLIScriptExecutor(scripts_dir)
    
    @tool
    def execute_cli_script(script_name: str) -> str:
        """
        执行 jianying-editor-skill 中的 CLI 脚本。
        
        可用脚本:
        - asset_search: 搜索特效、转场、动画等素材
        - auto_exporter: 无头导出草稿为 MP4/SRT
        - draft_inspector: 检查草稿列表和详情
        - movie_commentary_builder: 从故事板生成解说视频
        - sync_jy_assets: 同步剪映 App 中的素材
        - api_validator: 环境诊断
        - smart_zoomer: 智能变焦
        - smart_rough_cut: 智能粗剪
        - universal_tts: TTS 语音合成
        - web_recorder: Web 录屏
        
        Args:
            script_name: 脚本名称（如 'asset_search', 'auto_exporter'）
        """
        result = executor.execute(script_name, {})
        
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
