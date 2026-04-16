"""
JianYing Editor Agent - AI 驱动的剪映自动化剪辑助手

这个包提供了：
1. Skill 解析器 - 自动读取 jianying-editor-skill 目录并拼装成 Skill 对象
2. CLI 脚本执行器 - 统一收敛所有一次性脚本，通过 script_name 动态路由
3. Python 代码执行器 - 执行 LLM 生成的 JyProject 编排代码
4. 媒体素材解析器 - 根据文件名自动查找视频/音频/图片文件
5. Agent 主文件 - 整合所有组件，提供完整的 AI 助手功能
"""

from .skill_parser import (
    Skill,
    SkillParser,
    load_jianying_skill
)

from .cli_executor import (
    SCRIPT_REGISTRY,
    CLIScriptExecutor,
    create_cli_executor_tool
)

from .python_executor import (
    PythonCodeExecutor,
    create_python_executor_tool,
    CODE_TEMPLATES,
    get_code_template
)

from .media_resolver import (
    MediaResolver,
    create_media_resolver_tool
)

from .jianying_agent import (
    JianYingSkillMiddleware,
    create_jianying_agent,
    run_jianying_agent
)

__all__ = [
    # Skill 解析器
    "Skill",
    "SkillParser",
    "load_jianying_skill",
    
    # CLI 执行器
    "SCRIPT_REGISTRY",
    "CLIScriptExecutor",
    "create_cli_executor_tool",
    
    # Python 执行器
    "PythonCodeExecutor",
    "create_python_executor_tool",
    "CODE_TEMPLATES",
    "get_code_template",
    
    # 媒体解析器
    "MediaResolver",
    "create_media_resolver_tool",
    
    # Agent
    "JianYingSkillMiddleware",
    "create_jianying_agent",
    "run_jianying_agent"
]
