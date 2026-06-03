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
try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    _POSTGRES_SAVER_AVAILABLE = True
except ImportError:
    AsyncPostgresSaver = None  # type: ignore[assignment]
    _POSTGRES_SAVER_AVAILABLE = False
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

try:
    from .token_utils import estimate_tokens, calculate_context_budget
    from .tool_registry import ToolSpec, ToolRegistry
except ImportError:
    from token_utils import estimate_tokens, calculate_context_budget
    from tool_registry import ToolSpec, ToolRegistry


# 固定文本模板（工具使用指南 + 工作流程），纳入 token 预算计算
GUIDE_TEMPLATE = """
## 工具使用指南

1. **resolve_media**: 根据文件名查找视频/音频/图片的完整路径（用户只需提供文件名，无需完整路径）
2. **list_media**: 列出所有可用的媒体文件
3. **load_skill**: 当需要详细了解某个技能时，使用此工具加载完整内容
4. **execute_cli_script**: 执行 CLI 脚本（如素材搜索、自动导出等）
5. **list_cli_scripts**: 列出所有可用的 CLI 脚本
6. **execute_jyproject_code**: 执行 JyProject 编排代码（用于复杂剪辑流）
7. **validate_jyproject_code**: 验证代码语法（不实际执行）
8. **submit_storyboard**: 提交分镜方案 JSON，系统自动分步执行

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

        # 生成并缓存 L0 路由摘要（技能目录，约 300 tokens，Agent 生命周期不变）
        self._route_summary_cache = self._build_route_summary()

    def _build_route_summary(self) -> str:
        """
        L0 — 始终注入的路由摘要（目标 < 500 tokens）。

        仅包含：技能名称 + 一句话描述 + 类别标记。
        让 LLM 知道"有哪些技能可用"，但不加载完整内容。
        """
        lines = ["## 可用技能目录\n"]
        categories = {
            "main": "主技能", "rule": "规则", "script": "脚本", "example": "示例",
        }

        for cat, label in categories.items():
            skills = self.parser.get_skills_by_category(cat)
            if not skills:
                continue
            lines.append(f"\n### {label}")
            max_show = 6  # per-category cap to keep L0 lean
            for s in skills[:max_show]:
                desc = s["description"][:50]
                lines.append(f"- `{s['name']}`: {desc}")
            if len(skills) > max_show:
                lines.append(
                    f"- ... 还有 {len(skills) - max_show} 个（使用 load_skill 查看）"
                )

        summary = "\n".join(lines)
        summary += "\n\n> 使用 `load_skill(name)` 获取任何技能的完整内容"
        return summary

    def _build_category_detail(self, category: str) -> str:
        """
        L1 — 按需注入的规则摘要（约 1500 tokens）。

        当 LLM 意图匹配某类别时，注入该类别的完整 rule 内容。
        对 rule 类注入前 2000 字符，对 script/example 仅注入描述。
        """
        skills = self.parser.get_skills_by_category(category)
        if not skills:
            return ""

        lines = [f"\n## {category} 类技能详情\n"]
        for s in skills:
            if category == "rule":
                content = s["content"][:2000]  # ~500-800 tokens
            else:
                content = s["description"]

            lines.append(f"### {s['name']}")
            lines.append(content)
            lines.append("")

        return "\n".join(lines)

    def _generate_skills_prompt(self, token_budget: int | None = None) -> str:
        # Keep for backward compatibility — delegates to L0/L1 system.
        # New callers should use _build_skills_addendum with category_hint instead.
        return self._route_summary_cache
    
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

        # 5. 分镜方案提交通具（Path B 入口）
        @tool
        def submit_storyboard(storyboard_json: str) -> str:
            """
            提交分镜方案 JSON，系统自动分步执行并实时显示进度。

            当你完成素材解析后，**必须调用此工具**提交最终方案。
            json 参数为完整的方案 JSON 字符串。

            格式：{"project_name":"...","project_config":{"width":1920,"height":1080},
                   "steps":[{"action":"import_media","file":"完整路径","start_time":"0s"},...]}

            示例调用：submit_storyboard('{"project_name":"test","steps":[{"action":"list_media"}]}')
            """
            import json as _json
            try:
                data = _json.loads(storyboard_json) if isinstance(storyboard_json, str) else storyboard_json
            except _json.JSONDecodeError as e:
                return f"JSON 解析失败: {e}"
            if not isinstance(data, dict) or "steps" not in data:
                return "错误: 方案必须包含 project_name 和 steps 字段"
            # Validation pass — task_id will be returned by the backend hook
            return _json.dumps({
                "status": "valid",
                "total_steps": len(data["steps"]),
                "message": "分镜方案校验通过，正在启动执行...",
            }, ensure_ascii=False)

        self.submit_storyboard_tool = submit_storyboard

        # 注册所有工具到声明式注册表
        self.registry = ToolRegistry()
        self.registry.register_many([
            ToolSpec(
                name="load_skill", handler="skill_parser",
                category="read", func=self.load_skill_tool,
                exec_mode="sync", concurrency_safe=True, timeout=10,
                description="加载技能的完整内容到 Agent 上下文中",
            ),
            ToolSpec(
                name="resolve_media", handler="media_resolver",
                category="read", func=self.resolve_media_tool,
                exec_mode="sync", concurrency_safe=True, timeout=30,
                retry_strategy="transient", max_retries=2,
                description="根据文件名查找视频/音频/图片文件的完整路径",
            ),
            ToolSpec(
                name="list_media", handler="media_resolver",
                category="read", func=self.list_media_tool,
                exec_mode="sync", concurrency_safe=True, timeout=30,
                description="列出所有可用的媒体文件",
            ),
            ToolSpec(
                name="execute_cli_script", handler="cli_executor",
                category="compute", func=self.execute_cli_tool,
                exec_mode="async", concurrency_safe=False, timeout=600,
                retry_strategy="recoverable", max_retries=2,
                description="执行 CLI 脚本（素材搜索、自动导出、TTS 等）",
            ),
            ToolSpec(
                name="list_cli_scripts", handler="cli_executor",
                category="read", func=self.list_cli_tool,
                exec_mode="sync", concurrency_safe=True, timeout=10,
                description="列出所有可用的 CLI 脚本",
            ),
            ToolSpec(
                name="execute_jyproject_code", handler="python_executor",
                category="write", func=self.execute_python_tool,
                exec_mode="sync", concurrency_safe=False, timeout=300,
                retry_strategy="transient", max_retries=1,
                description="执行 JyProject 编排代码（用于复杂剪辑流程）",
            ),
            ToolSpec(
                name="validate_jyproject_code", handler="python_executor",
                category="read", func=self.validate_python_tool,
                exec_mode="sync", concurrency_safe=True, timeout=10,
                description="验证 JyProject 代码语法（不实际执行）",
            ),
            ToolSpec(
                name="submit_storyboard", handler="storyboard_parser",
                category="write", func=self.submit_storyboard_tool,
                exec_mode="sync", concurrency_safe=False, timeout=10,
                description="提交分镜方案 JSON，系统自动分步执行并显示实时进度。编辑任务必须调用此工具完成。",
            ),
        ])
        self.tools = self.registry.get_all_tools()
    
    def _generate_skills_prompt(self, token_budget: int | None = None) -> str:
        """
        生成技能列表提示，支持 token 预算控制。

        优先级：main > rule > script > example
        每类技能在预算内尽量保留，超出预算时 example 类首先被省略。

        Args:
            token_budget: 允许使用的最大 token 数，None 表示不限制

        Returns:
            格式化的技能列表文本
        """
        if token_budget is None:
            token_budget = calculate_context_budget()

        parts: list[str] = []
        total_used = 0

        category_priority = [
            ("main", "主技能"),
            ("rule", "规则指南"),
            ("script", "CLI 脚本"),
            ("example", "示例代码"),
        ]

        for category, label in category_priority:
            cat_skills = self.parser.get_skills_by_category(category)
            if not cat_skills:
                continue

            cat_text = f"\n### {label}\n"
            cat_used = estimate_tokens(cat_text)

            skill_lines: list[str] = []
            for skill in cat_skills:
                line = f"- **{skill['name']}**: {skill['description']}\n"
                line_tokens = estimate_tokens(line)
                if total_used + cat_used + line_tokens > token_budget:
                    remaining = len(cat_skills) - len(skill_lines)
                    if remaining > 0:
                        skill_lines.append(
                            f"- ... 还有 {remaining} 个技能"
                            f"（使用 load_skill 查看）\n"
                        )
                    break
                skill_lines.append(line)
                cat_used += line_tokens

            if skill_lines:
                parts.append(cat_text + "".join(skill_lines))
                total_used += cat_used

        return "\n".join(parts)
    
    def _build_media_summary(
        self, available_files: list[dict], token_budget_remaining: int,
    ) -> str:
        """基于剩余 token 预算动态调整媒体文件列表数量"""
        if not available_files:
            return ""

        header = "\n### 可用媒体文件\n"
        used = estimate_tokens(header)

        # 每个文件行约 50 tokens（含文件名、类型、大小）
        per_file_tokens = 50
        max_show = min(
            len(available_files),
            max(1, (token_budget_remaining - used) // per_file_tokens),
        )

        media_lines: list[str] = []
        for f in available_files[:max_show]:
            line = f"- {f['name']} ({f['type']}, {f['size_mb']}MB)\n"
            media_lines.append(line)

        if len(available_files) > max_show:
            media_lines.append(
                f"- ... 还有 {len(available_files) - max_show} 个文件"
                f"（使用 list_media 查看）\n"
            )

        return header + "".join(media_lines)

    def _build_skills_addendum(
        self,
        token_budget: int | None = None,
        category_hint: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """
        构建分级技能附录（供 wrap_model_call / awrap_model_call 共享）。

        - L0（路由摘要）始终注入（~300 tokens）
        - L1（类别详情）仅在 category_hint 非空且有剩余预算时注入
        - L2（完整内容）通过 load_skill 工具按需加载

        将 GUIDE_TEMPLATE 等固定文本纳入 token 预算计算，
        避免实际注入量超出预期。
        """
        if token_budget is None:
            token_budget = calculate_context_budget()

        # L0 路由摘要（从缓存获取）
        route_summary = self._route_summary_cache
        guide_tokens = estimate_tokens(GUIDE_TEMPLATE)
        l0_tokens = estimate_tokens(route_summary)
        remaining = token_budget - guide_tokens - l0_tokens

        # L1 类别详情（按需，约 1500 tokens）
        category_detail = ""
        if category_hint and remaining > 500:
            detail = self._build_category_detail(category_hint)
            detail_tokens = estimate_tokens(detail)
            if detail_tokens <= remaining:
                category_detail = detail
                remaining -= detail_tokens

        # 媒体文件列表（每次动态生成，文件可能增删）
        media_summary = ""
        try:
            available_files = self.media_resolver.list_available()
            if available_files and remaining > 200:
                media_summary = self._build_media_summary(
                    available_files, remaining,
                )
        except Exception:
            pass

        # 用户偏好注入（有 session 时从 PG 加载）
        preference_prompt = ""
        if user_id:
            try:
                from .session_memory import SessionMemory
            except ImportError:
                from session_memory import SessionMemory
            memory = SessionMemory(user_id)
            preference_prompt = memory.build_preference_prompt()  # session=None → returns ""

        return (
            f"{route_summary}\n\n{category_detail}\n"
            f"{media_summary}\n{GUIDE_TEMPLATE}\n{preference_prompt}"
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """将技能描述注入到系统提示中，必要时压缩上下文。"""

        # autoCompact: 检查是否需要压缩消息历史
        if hasattr(request, "messages"):
            try:
                from .context_compactor import should_compact, compact_messages
            except ImportError:
                from context_compactor import should_compact, compact_messages

            if should_compact(request.messages):
                result = compact_messages(request.messages)
                if result.rounds_compacted > 0:
                    request = request.override(messages=result.compacted_content)

        skills_addendum = self._build_skills_addendum()
        new_content = list(request.system_message.content_blocks) + [
            {"type": "text", "text": skills_addendum},
        ]
        new_system_message = SystemMessage(content=new_content)
        modified_request = request.override(system_message=new_system_message)
        return handler(modified_request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """将技能描述注入到系统提示中（异步版本），必要时压缩上下文。"""

        if hasattr(request, "messages"):
            try:
                from .context_compactor import should_compact, compact_messages
            except ImportError:
                from context_compactor import should_compact, compact_messages

            if should_compact(request.messages):
                result = compact_messages(request.messages)
                if result.rounds_compacted > 0:
                    request = request.override(messages=result.compacted_content)

        skills_addendum = self._build_skills_addendum()
        new_content = list(request.system_message.content_blocks) + [
            {"type": "text", "text": skills_addendum},
        ]
        new_system_message = SystemMessage(content=new_content)
        modified_request = request.override(system_message=new_system_message)
        return await handler(modified_request)


def create_jianying_agent(
    skill_root: str,
    media_search_paths: list[str] = None,
    model_name: str = "glm-5.1",
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key: str = None,
    system_prompt: str = None,
    checkpointer=None,
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
        checkpointer: LangGraph checkpointer 实例。
                      默认 InMemorySaver()，生产环境传入 AsyncPostgresSaver(conn)。

    Returns:
        Agent 实例, middleware 实例
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
   - JyProject(name, width=1920, height=1080) 创建项目
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

请根据用户的需求，选择合适的工具完成任务。对于复杂任务，先生成代码并验证，再执行。

## 分镜方案提交（必须用工具）

任何文件操作任务，在解析完文件路径后，**必须调用 submit_storyboard 工具**提交方案：
```
submit_storyboard(storyboard_json='{"project_name":"我的视频","project_config":{"width":1920,"height":1080},"steps":[{"action":"import_media","file":"完整路径","start_time":"0s"},{"action":"export","draft_name":"项目名","resolution":"1080p"}]}')
```
可用 action: import_media, add_text, add_tts, add_audio, add_effect, add_transition,
add_subtitle, smart_rough_cut, export, asset_search, resolve_media, list_media

⚠️ **编辑任务必须用 submit_storyboard 提交！** 文件路径解析完成后，调用 submit_storyboard 工具提交 JSON 方案。
系统自动分步执行并显示实时进度。不要直接在文本中输出 JSON！

## 执行铁律（避免无效重试）

1. **代码错误不重试**：如果 execute_jyproject_code 报 AttributeError / TypeError / SyntaxError，
   说明代码本身有 bug，直接修正代码后重新执行。不要对同一段错误代码重复调用。

2. **CLI 异常不重试**：如果 execute_cli_script 返回"非预期异常"，说明脚本环境有问题，
   换一种方式实现（例如改用 execute_jyproject_code 内联调用 subprocess）。

3. **转场/特效查找**：使用 execute_cli_script("asset_search", query="模糊", category="transitions")
   来搜索 ID，不要直接调用 add_transition_simple 传中文名。
   JyProject 的 add_transition_simple 接受 transition_name 参数。
   如果报错 AttributeError: 'Transition'，说明库版本不支持该 API，
   改为不加转场，完成任务的其他部分。

4. **最多重试 2 次**：同一工具的同一参数失败 2 次后，改为替代方案，不要死循环。

5. **skill 名称中有反引号**：如 effects`（注意结尾的反引号），请直接复制使用。"""
    
    # 创建 Agent（默认 InMemorySaver，生产环境通过 checkpointer 参数注入持久化）
    if checkpointer is None:
        checkpointer = InMemorySaver()

    agent = create_agent(
        model,
        system_prompt=system_prompt,
        middleware=[middleware],
        checkpointer=checkpointer,
    )
    
    return agent, middleware


# 便捷函数
def run_jianying_agent(
    skill_root: str,
    user_message: str,
    thread_id: str = None,
    enable_observability: bool = False,
    **kwargs,
):
    """
    运行 JianYing Editor Agent 的便捷函数

    Args:
        skill_root: jianying-editor-skill 的根目录路径
        user_message: 用户消息
        thread_id: 对话线程 ID（如未提供，自动生成）
        enable_observability: 是否启用 Langfuse 可观测性埋点
        **kwargs: 其他参数传递给 create_jianying_agent

    Returns:
        Agent 响应结果
    """
    agent, middleware = create_jianying_agent(skill_root, **kwargs)

    if thread_id is None:
        thread_id = str(uuid.uuid4())

    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 5}

    # Langfuse callback 在 invoke 时通过 config["callbacks"] 传入
    if enable_observability:
        try:
            from .observability import create_langchain_callback
        except ImportError:
            from observability import create_langchain_callback

        callback = create_langchain_callback(
            trace_name="jianying_agent",
            tags=["easy-scene", "video-editing"],
        )
        if callback:
            config["callbacks"] = [callback]

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": user_message,
                }
            ]
        },
        config,
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
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 5}
    
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
