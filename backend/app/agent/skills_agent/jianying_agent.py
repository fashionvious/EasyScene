"""
JianYing Editor Agent - 整合 Skill 加载、CLI 执行器和 Python 执行器
"""
import os
import uuid
from typing import Callable, Awaitable
from pathlib import Path

from langchain.tools import tool
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, ModelResponse, AgentMiddleware
from langchain.messages import SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langchain_openai import ChatOpenAI

# 支持直接运行和模块导入
try:
    # 尝试相对导入（作为包的一部分）
    from .skill_parser import SkillParser, Skill
    from .cli_executor import create_cli_executor_tool
    from .python_executor import create_python_executor_tool
    from .media_resolver import create_media_resolver_tool, MediaResolver
except ImportError:
    # 如果失败，使用绝对导入（直接运行）
    from skill_parser import SkillParser, Skill
    from cli_executor import create_cli_executor_tool
    from python_executor import create_python_executor_tool
    from media_resolver import create_media_resolver_tool, MediaResolver


class JianYingSkillMiddleware(AgentMiddleware):
    """
    JianYing Editor Skill 中间件
    
    负责将技能描述注入到系统提示中，并提供技能加载工具
    """
    
    def __init__(self, skill_root: str, media_search_paths: list[str] = None):
        """
        初始化中间件
        
        Args:
            skill_root: jianying-editor-skill 的根目录路径
            media_search_paths: 媒体文件搜索路径列表（绝对路径）
        """
        self.skill_root = Path(skill_root)
        self.parser = SkillParser(skill_root)
        self.skills = self.parser.parse_all()
        self.media_search_paths = media_search_paths or []
        
        # 创建工具
        self._create_tools()
        
        # 生成技能提示
        self._generate_skills_prompt()
    
    def _create_tools(self) -> None:
        """创建所有工具"""
        # 1. 技能加载工具
        @tool
        def load_skill(skill_name: str) -> str:
            """
            加载技能的完整内容到 Agent 上下文中。
            
            当需要详细了解如何处理特定类型的请求时使用此工具。
            这将提供全面的说明、策略和指南。
            
            Args:
                skill_name: 技能名称（如 "jianying-editor", "rule_setup", "script_asset_search"）
            """
            skill = self.parser.get_skill(skill_name)
            
            if skill:
                return f"已加载技能: {skill_name}\n\n{skill['content']}"
            
            available = ", ".join(self.parser.get_skill_names())
            return f"未找到技能 '{skill_name}'。可用技能: {available}"
        
        self.load_skill_tool = load_skill
        
        # 2. CLI 执行器工具
        scripts_dir = self.skill_root / "scripts"
        self.execute_cli_tool, self.list_cli_tool = create_cli_executor_tool(
            str(scripts_dir)
        )
        
        # 3. Python 执行器工具
        self.execute_python_tool, self.validate_python_tool = create_python_executor_tool(
            str(self.skill_root)
        )
        
        # 4. 媒体素材解析工具
        self.resolve_media_tool, self.list_media_tool, self.media_resolver = \
            create_media_resolver_tool(extra_paths=self.media_search_paths)
        
        # 注册所有工具
        self.tools = [
            self.load_skill_tool,
            self.resolve_media_tool,
            self.list_media_tool,
            self.execute_cli_tool,
            self.list_cli_tool,
            self.execute_python_tool,
            self.validate_python_tool
        ]
    
    def _generate_skills_prompt(self) -> None:
        """生成技能列表提示"""
        skills_list = []
        
        # 按分类组织技能
        categories = {
            "main": "主技能",
            "rule": "规则指南",
            "script": "CLI 脚本",
            "example": "示例代码"
        }
        
        for category, label in categories.items():
            category_skills = self.parser.get_skills_by_category(category)
            if category_skills:
                skills_list.append(f"\n### {label}")
                for skill in category_skills:
                    skills_list.append(f"- **{skill['name']}**: {skill['description']}")
        
        self.skills_prompt = "\n".join(skills_list)
    
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """
        将技能描述注入到系统提示中
        """
        # 构建技能附录
        # 生成可用媒体文件摘要
        media_summary = ""
        try:
            available_files = self.media_resolver.list_available()
            if available_files:
                media_lines = ["\n### 可用媒体文件"]
                for f in available_files[:10]:  # 最多显示 10 个
                    media_lines.append(f"- {f['name']} ({f['type']}, {f['size_mb']}MB)")
                if len(available_files) > 10:
                    media_lines.append(f"- ... 还有 {len(available_files) - 10} 个文件（使用 list_media 查看）")
                media_summary = "\n".join(media_lines)
        except Exception:
            pass
        
        skills_addendum = f"""
## 可用技能

{self.skills_prompt}

{media_summary}

## 工具使用指南

1. **resolve_media**: 根据文件名查找视频/音频/图片的完整路径（用户只需提供文件名，无需完整路径）
2. **list_media**: 列出所有可用的媒体文件
3. **load_skill**: 当需要详细了解某个技能时，使用此工具加载完整内容
4. **execute_cli_script**: 执行 CLI 脚本（如素材搜索、自动导出等）
5. **list_cli_scripts**: 列出所有可用的 CLI 脚本
6. **execute_jyproject_code**: 执行 JyProject 编排代码（用于复杂剪辑流）
7. **validate_jyproject_code**: 验证代码语法（不实际执行）

## 工作流程

1. **当用户提到视频/音频/图片文件时，先用 `resolve_media` 解析文件名获取完整路径**
   - 用户说 "test01.mp4" -> 调用 resolve_media("test01.mp4") -> 得到完整路径
   - 用户说 "test" -> 调用 resolve_media("test") -> 模糊匹配
   - 用户给完整路径 -> 调用 resolve_media 验证文件是否存在
2. 使用 `load_skill("jianying-editor")` 了解整体能力
3. 根据任务类型选择合适的规则（如 `load_skill("rule_media")`）
4. 对于简单任务，使用 CLI 脚本（如 `execute_cli_script`）
5. 对于复杂编排，生成 JyProject 代码并使用 `execute_jyproject_code`
"""
        
        # 追加到系统消息
        new_content = list(request.system_message.content_blocks) + [
            {"type": "text", "text": skills_addendum}
        ]
        new_system_message = SystemMessage(content=new_content)
        
        # 创建修改后的请求
        modified_request = request.override(system_message=new_system_message)
        return handler(modified_request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """
        将技能描述注入到系统提示中（异步版本）
        
        逻辑与 wrap_model_call 完全相同，只是 handler 是异步的。
        """
        # 构建技能附录
        # 生成可用媒体文件摘要
        media_summary = ""
        try:
            available_files = self.media_resolver.list_available()
            if available_files:
                media_lines = ["\n### 可用媒体文件"]
                for f in available_files[:10]:  # 最多显示 10 个
                    media_lines.append(f"- {f['name']} ({f['type']}, {f['size_mb']}MB)")
                if len(available_files) > 10:
                    media_lines.append(f"- ... 还有 {len(available_files) - 10} 个文件（使用 list_media 查看）")
                media_summary = "\n".join(media_lines)
        except Exception:
            pass

        skills_addendum = f"""
## 可用技能

{self.skills_prompt}

{media_summary}

## 工具使用指南

1. **resolve_media**: 根据文件名查找视频/音频/图片的完整路径（用户只需提供文件名，无需完整路径）
2. **list_media**: 列出所有可用的媒体文件
3. **load_skill**: 当需要详细了解某个技能时，使用此工具加载完整内容
4. **execute_cli_script**: 执行 CLI 脚本（如素材搜索、自动导出等）
5. **list_cli_scripts**: 列出所有可用的 CLI 脚本
6. **execute_jyproject_code**: 执行 JyProject 编排代码（用于复杂剪辑流）
7. **validate_jyproject_code**: 验证代码语法（不实际执行）

## 工作流程

1. **当用户提到视频/音频/图片文件时，先用 `resolve_media` 解析文件名获取完整路径**
   - 用户说 "test01.mp4" -> 调用 resolve_media("test01.mp4") -> 得到完整路径
   - 用户说 "test" -> 调用 resolve_media("test") -> 模糊匹配
   - 用户给完整路径 -> 调用 resolve_media 验证文件是否存在
2. 使用 `load_skill("jianying-editor")` 了解整体能力
3. 根据任务类型选择合适的规则（如 `load_skill("rule_media")`）
4. 对于简单任务，使用 CLI 脚本（如 `execute_cli_script`）
5. 对于复杂编排，生成 JyProject 代码并使用 `execute_jyproject_code`
"""

        # 追加到系统消息
        new_content = list(request.system_message.content_blocks) + [
            {"type": "text", "text": skills_addendum}
        ]
        new_system_message = SystemMessage(content=new_content)

        # 创建修改后的请求
        modified_request = request.override(system_message=new_system_message)
        return await handler(modified_request)


def create_jianying_agent(
    skill_root: str,
    media_search_paths: list[str] = None,
    model_name: str = "qwen3.6-plus",
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key: str = None,
    system_prompt: str = None
):
    """
    创建 JianYing Editor Agent
    
    Args:
        skill_root: jianying-editor-skill 的根目录路径
        media_search_paths: 媒体文件搜索路径列表（绝对路径），用户只需输入文件名即可
        model_name: 模型名称
        base_url: API 基础 URL
        api_key: API 密钥（如未提供，从环境变量读取）
        system_prompt: 自定义系统提示
        
    Returns:
        Agent 实例
    """
    # 获取 API 密钥
    if api_key is None:
        api_key = os.getenv("DASHSCOPE_API_KEY")
    
    # 创建模型
    model = ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key
    )
    
    # 创建中间件
    middleware = JianYingSkillMiddleware(skill_root, media_search_paths=media_search_paths)
    
    # 默认系统提示
    if system_prompt is None:
        system_prompt = """你是一个专业的视频剪辑助手，帮助用户使用剪映（JianYing）进行自动化视频编辑。

你可以：
1. 创建和编辑视频项目
2. 添加素材、字幕、特效
3. 应用转场和滤镜
4. 生成解说视频
5. 自动导出视频

## 关键规则

1. **文件路径**：当用户提到视频/音频/图片文件时，先用 resolve_media 工具解析文件名获取完整路径，用户只需提供文件名即可。

2. **JyProject 代码**：使用 execute_jyproject_code 时，代码中不要写任何 import 语句！JyProject 和 os 已自动导入。直接写业务逻辑即可。

   正确示例：
   ```
   project = JyProject("我的项目")
   project.add_media_safe("D:/video/test.mp4", "0s")
   project.add_text_simple("标题", start_time="1s", duration="3s")
   _trim_project_duration(project)  # 防黑屏：必须在 save() 前调用！
   project.save()
   ```

   错误示例（不要这样写）：
   ```
   from jianying_editor import JyProject  # ❌ 不要写 import
   import jy_wrapper                       # ❌ 不要写 import
   project.save()                          # ❌ 缺少 _trim_project_duration，会黑屏
   ```

3. **防黑屏规则**：在 project.save() 前必须调用 _trim_project_duration(project)！它会自动将项目总时长裁剪为所有片段的最大结束时间，避免视频播完后黑屏继续播放。

4. **JyProject API 速查**：
   - JyProject(name, width=1920, height=1080, fps=30) 创建项目
   - project.add_media_safe(path, start_time, duration, track_name) 添加媒体
   - project.add_text_simple(text, start_time, duration) 添加文本
   - project.add_audio_safe(path, start_time, track_name) 添加音频
   - project.add_cloud_music(query, start_time) 添加云端音乐
   - project.add_tts_intelligent(text, speaker, start_time) TTS语音
   - project.add_narrated_subtitles(text, speaker, start_time) 旁白+字幕
   - project.add_effect_simple(effect_name, start_time, duration) 特效
   - project.add_transition_simple(transition_name, duration) 转场
   - project.save() 保存（必须调用！）
   - 时间格式："0s", "1s", "3s" 或微秒整数

请根据用户的需求，选择合适的工具完成任务。对于复杂任务，先生成代码并验证，再执行。"""
    
    # 创建 Agent
    agent = create_agent(
        model,
        system_prompt=system_prompt,
        middleware=[middleware],
        checkpointer=InMemorySaver()
    )
    
    return agent, middleware


# 便捷函数
def run_jianying_agent(
    skill_root: str,
    user_message: str,
    thread_id: str = None,
    **kwargs
):
    """
    运行 JianYing Editor Agent 的便捷函数
    
    Args:
        skill_root: jianying-editor-skill 的根目录路径
        user_message: 用户消息
        thread_id: 对话线程 ID（如未提供，自动生成）
        **kwargs: 其他参数传递给 create_jianying_agent
        
    Returns:
        Agent 响应结果
    """
    # 创建 Agent
    agent, middleware = create_jianying_agent(skill_root, **kwargs)
    
    # 生成线程 ID
    if thread_id is None:
        thread_id = str(uuid.uuid4())
    
    # 配置
    config = {"configurable": {"thread_id": thread_id}}
    
    # 调用 Agent
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": user_message
                }
            ]
        },
        config
    )
    
    return result


if __name__ == "__main__":
    # 示例用法
    import sys
    
    # 获取 skill_root
    if len(sys.argv) > 1:
        skill_root = sys.argv[1]
    else:
        # 自动探测 skill_root 路径
        # 当前文件位于 backend/app/agent/skills_agent/
        # jianying-editor-skill 可能位于:
        #   - backend/jianying-editor-skill/
        #   - 项目根目录/jianying-editor-skill/
        current_dir = os.path.dirname(os.path.abspath(__file__))
        env_root = os.getenv("JY_SKILL_ROOT", "").strip()
        candidates = [
            env_root,
            os.path.join(current_dir, "..", "..", "..", "jianying-editor-skill"),  # backend/
            os.path.join(current_dir, "..", "..", "..", "..", "jianying-editor-skill"),  # 项目根
        ]
        skill_root = None
        for p in candidates:
            if p and os.path.exists(os.path.join(p, "SKILL.md")):
                skill_root = os.path.abspath(p)
                break
        if not skill_root:
            # 回退到 backend/ 下的路径
            skill_root = os.path.abspath(
                os.path.join(current_dir, "..", "..", "..", "jianying-editor-skill")
            )
    
    print(f"Skill Root: {skill_root}")
    
    # 创建 Agent
    agent, middleware = create_jianying_agent(skill_root)
    
    # 对话线程
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    
    # 测试请求（现在只需文件名，无需完整路径）
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "将test01.mp4、test02.mp4、test03.mp4按顺序剪辑到一起成为一个视频"
                }
            ]
        },
        config
    )
    
    # 打印结果
    for message in result["messages"]:
        if hasattr(message, 'pretty_print'):
            message.pretty_print()
        else:
            print(f"{message.type}: {message.content}")
