# JianYing Editor Agent

AI 驱动的剪映自动化剪辑助手，基于 LangChain 和 jianying-editor-skill 构建。

## 功能特性

### 1. Skill 解析器 (`skill_parser.py`)

自动读取 `jianying-editor-skill` 目录并拼装成 Skill 对象：

- 解析主技能文件 `SKILL.md`
- 解析 `rules/` 目录下的规则文件
- 解析 `scripts/` 目录下的脚本
- 解析 `examples/` 目录下的示例

```python
from skills_agent import SkillParser

parser = SkillParser("/path/to/jianying-editor-skill")
skills = parser.parse_all()

# 获取特定技能
skill = parser.get_skill("jianying-editor")

# 按分类获取
rules = parser.get_skills_by_category("rule")
```

### 2. CLI 脚本执行器 (`cli_executor.py`)

统一收敛所有一次性脚本，通过 `script_name` 动态路由：

**可用脚本：**
- `asset_search` - 搜索特效、转场、动画等素材
- `auto_exporter` - 无头导出草稿为 MP4/SRT
- `draft_inspector` - 检查草稿列表和详情
- `movie_commentary_builder` - 从故事板生成解说视频
- `sync_jy_assets` - 同步剪映 App 中的素材
- `api_validator` - 环境诊断
- `smart_zoomer` - 智能变焦
- `smart_rough_cut` - 智能粗剪
- `universal_tts` - TTS 语音合成
- `web_recorder` - Web 录屏

```python
from skills_agent import CLIScriptExecutor

executor = CLIScriptExecutor("/path/to/scripts")

# 执行脚本
result = executor.execute("asset_search", {
    "query": "复古",
    "category": "filters"
})
```

### 3. Python 代码执行器 (`python_executor.py`)

执行 LLM 根据 `rules/` 规范生成的 JyProject 编排代码：

```python
from skills_agent import PythonCodeExecutor

executor = PythonCodeExecutor("/path/to/jianying-editor-skill")

# 执行代码
code = '''
project = JyProject("My Video")
project.add_media_safe("video.mp4", "0s")
project.add_text_simple("标题", start_time="1s", duration="3s")
project.save()
'''

result = executor.execute(code)
```

### 4. Agent 主文件 (`jianying_agent.py`)

整合所有组件，提供完整的 AI 助手功能：

```python
from skills_agent import create_jianying_agent, run_jianying_agent

# 方式 1: 创建 Agent 并手动调用
agent, middleware = create_jianying_agent(
    skill_root="/path/to/jianying-editor-skill",
    model_name="qwen-max"
)

result = agent.invoke({
    "messages": [{"role": "user", "content": "帮我搜索复古滤镜"}]
}, config={"configurable": {"thread_id": "test"}})

# 方式 2: 便捷函数
result = run_jianying_agent(
    skill_root="/path/to/jianying-editor-skill",
    user_message="帮我创建一个视频项目"
)
```

## 工具说明

Agent 提供以下工具：

1. **load_skill** - 加载技能的完整内容
2. **execute_cli_script** - 执行 CLI 脚本
3. **list_cli_scripts** - 列出所有可用的 CLI 脚本
4. **execute_jyproject_code** - 执行 JyProject 编排代码
5. **validate_jyproject_code** - 验证代码语法

## 工作流程

1. 首先使用 `load_skill("jianying-editor")` 了解整体能力
2. 根据任务类型选择合适的规则（如 `load_skill("rule_media")`）
3. 对于简单任务，使用 CLI 脚本（如 `execute_cli_script`）
4. 对于复杂编排，生成 JyProject 代码并使用 `execute_jyproject_code`

## 示例用法

### 搜索素材

```python
result = run_jianying_agent(
    skill_root,
    "帮我搜索一下有哪些复古风格的滤镜"
)
```

### 创建视频项目

```python
result = run_jianying_agent(
    skill_root,
    "创建一个名为'我的Vlog'的视频项目，添加一段视频素材和背景音乐"
)
```

### 添加字幕

```python
result = run_jianying_agent(
    skill_root,
    "在视频的1秒到3秒位置添加字幕'欢迎观看'"
)
```

### 导出视频

```python
result = run_jianying_agent(
    skill_root,
    "将项目'我的Vlog'导出为1080p 60fps的MP4文件"
)
```

## 配置

### 环境变量

- `DASHSCOPE_API_KEY` - 阿里云 DashScope API 密钥
- `JY_SKILL_ROOT` - jianying-editor-skill 根目录（可选）

### 模型配置

默认使用 `qwen-max` 模型，可以通过参数自定义：

```python
agent, middleware = create_jianying_agent(
    skill_root,
    model_name="qwen-plus",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="your-api-key"
)
```

## 目录结构

```
skills_agent/
├── __init__.py           # 包导出
├── skill_parser.py       # Skill 解析器
├── cli_executor.py       # CLI 脚本执行器
├── python_executor.py    # Python 代码执行器
├── jianying_agent.py     # Agent 主文件
└── README.md             # 说明文档
```

## 依赖

- langchain
- langchain-openai
- langgraph
- pydantic

## 注意事项

1. 确保 `jianying-editor-skill` 目录存在且包含必要的文件
2. 执行代码前会自动注入环境初始化代码
3. 所有代码执行都有超时保护（默认 300 秒）
4. 临时文件会在执行后自动清理
