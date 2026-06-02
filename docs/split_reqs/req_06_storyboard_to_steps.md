# 需求 06：分镜→步骤转换器 (StoryboardToSteps)

> 原 PRD 编号: P0-2 (A-2) | 优先级: P0

## 1. 依赖关系

- **前置依赖**：req_02（ToolRegistry — 提供可用工具目录，用于校验 action 映射）、req_03（StepOrchestrator — StepSpec 格式定义）
- **被谁依赖**：req_08（API 路由 — `/edit/start` 端点调用转换器生成 EditPlan）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

**当前痛点（PRD B3）**：LLM 生成的 JyProject 代码是一次性完整 Python 脚本（参见 [python_executor.py:368](../../../backend/app/agent/skills_agent/python_executor.py#L368-L454) 的 `execute_jyproject_code` 工具），单步出错全盘重来。

```python
# 当前：LLM 直接生成 Python 代码
project = JyProject("我的项目")
project.add_media_safe("test01.mp4", "0s")
project.add_text_simple("标题", start_time="1s", duration="3s")
_trim_project_duration(project)
project.save()
```

**改造方向**：LLM 仍然生成业务逻辑，但不生成执行代码。改为生成结构化 JSON 分镜方案，由 StoryboardToSteps 转换为可独立执行的 StepSpec 列表。

**为什么不用 Python 代码而用 JSON**：
- Python 代码 = 黑盒，无法分步执行、独立重试、独立追踪
- JSON 方案 = 每步可独立执行、独立重试、前端可逐状态展示
- LLM 依然负责"业务逻辑"（哪些 clip 怎么排），但不负责"执行方式"

### 代码库校验结论

- 当前 7 个工具覆盖了剪辑全流程：素材解析（resolve_media）、媒体列表（list_media）、CLI 脚本执行（execute_cli_script）、JyProject 代码执行（execute_jyproject_code）等
- `media_resolver.py` 的 `resolve()` 和 `list_available()` 已提供素材验证能力
- 需要定义 JSON schema 将 LLM 输出映射到 7 个工具
- StoryboardToSteps 本身不执行 PyProject 代码，而是生成 StepSpec → 由 StepOrchestrator 调用 `execute_jyproject_code` 工具执行

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `backend/app/agent/skills_agent/storyboard_parser.py` | JSON schema 定义 + 解析器 |
| 修改 | `backend/app/agent/skills_agent/jianying_agent.py` | 新增 `parse_storyboard` 工具 + 修改 system prompt |

### 核心技术细节

**LLM 输出的 JSON schema**：

```json
{
    "project_name": "我的视频",
    "project_config": {
        "width": 1920,
        "height": 1080,
        "fps": 30
    },
    "steps": [
        {
            "action": "import_media",
            "file": "test01.mp4",
            "track": "main",
            "start_time": "0s"
        },
        {
            "action": "import_media",
            "file": "test02.mp4",
            "track": "main",
            "start_time": "5s"
        },
        {
            "action": "add_text",
            "text": "精彩内容",
            "start_time": "0s",
            "duration": "3s"
        },
        {
            "action": "add_tts",
            "text": "欢迎收看本期视频",
            "speaker": "zh_male_huoli",
            "start_time": "0s"
        },
        {
            "action": "add_effect",
            "effect_name": "变清晰2",
            "start_time": "0s",
            "duration": "5s"
        },
        {
            "action": "add_transition",
            "transition_name": "模糊",
            "duration": "0.5s"
        },
        {
            "action": "export",
            "draft_name": "我的视频",
            "resolution": "1080p",
            "fps": 30
        }
    ]
}
```

**解析器实现**：

```python
"""storyboard_parser.py — 分镜 JSON → EditPlan 转换器"""
import json
import uuid
from typing import Any

from step_orchestrator import StepSpec, EditPlan  # from req_03


# action → tool 映射表（基于 ToolRegistry 的 7 个工具）
ACTION_TO_TOOL: dict[str, str] = {
    "import_media":    "execute_jyproject_code",
    "add_text":        "execute_jyproject_code",
    "add_tts":         "execute_jyproject_code",
    "add_audio":       "execute_jyproject_code",
    "add_effect":      "execute_jyproject_code",
    "add_transition":  "execute_jyproject_code",
    "add_subtitle":    "execute_jyproject_code",
    "smart_rough_cut": "execute_cli_script",
    "export":          "execute_cli_script",
    "asset_search":    "execute_cli_script",
    "web_record":      "execute_cli_script",
    "resolve_media":   "resolve_media",
    "list_media":      "list_media",
}


class StoryboardParser:
    """将 LLM 生成的分镜 JSON 方案转换为 EditPlan + StepSpec 列表"""

    def parse(
        self,
        storyboard_json: str | dict,
        user_id: str,
        script_id: str,
    ) -> EditPlan:
        """主入口：解析分镜方案，返回可执行的 EditPlan"""
        if isinstance(storyboard_json, str):
            data = json.loads(storyboard_json)
        else:
            data = storyboard_json

        steps: list[StepSpec] = []
        for i, step_data in enumerate(data.get("steps", [])):
            action = step_data["action"]
            tool = ACTION_TO_TOOL.get(action)

            if tool is None:
                raise ValueError(
                    f"未知 action: '{action}'（步骤 {i + 1}）。"
                    f"可用: {list(ACTION_TO_TOOL.keys())}"
                )

            step = StepSpec(
                tool=tool,
                args={
                    "action": action,
                    **{k: v for k, v in step_data.items() if k != "action"},
                    "project_name": data.get("project_name", "未命名项目"),
                    "project_config": data.get("project_config", {}),
                    "step_index": i,
                },
            )
            steps.append(step)

        return EditPlan(
            task_id=str(uuid.uuid4()),
            user_id=user_id,
            script_id=script_id,
            steps=steps,
            project_state={"project_name": data.get("project_name", "")},
        )

    def validate(self, storyboard_json: str | dict) -> list[str]:
        """预校验：检查 action 的合法性，返回错误列表（空列表 = 通过）"""
        data = (
            json.loads(storyboard_json)
            if isinstance(storyboard_json, str)
            else storyboard_json
        )
        errors: list[str] = []

        steps = data.get("steps", [])
        if not steps:
            errors.append("分镜方案中 steps 为空，至少需要一个步骤")
            return errors

        for i, step in enumerate(steps):
            action = step.get("action", "")
            if not action:
                errors.append(f"步骤 {i + 1}: 缺少 action 字段")
            elif action not in ACTION_TO_TOOL:
                errors.append(
                    f"步骤 {i + 1}: 未知 action '{action}'，"
                    f"可用: {list(ACTION_TO_TOOL.keys())}"
                )

            # 特定 action 的参数校验
            if action == "import_media" and "file" not in step:
                errors.append(f"步骤 {i + 1} (import_media): 缺少 file 参数")
            if action == "add_tts" and "text" not in step:
                errors.append(f"步骤 {i + 1} (add_tts): 缺少 text 参数")

        return errors
```

**System prompt 更新**（在 `jianying_agent.py` 的 `system_prompt` 中追加）：

```text
## 分镜方案格式

当你需要生成复杂剪辑方案时，请输出如下 JSON 格式的分镜方案（而不是 Python 代码）：

```json
{
    "project_name": "项目名称",
    "steps": [
        {"action": "import_media", "file": "文件名", "start_time": "0s"},
        {"action": "add_text", "text": "标题文本", "start_time": "0s", "duration": "3s"},
        ...
    ]
}
```

可用的 action 类型：import_media, add_text, add_tts, add_audio, add_effect,
add_transition, add_subtitle, smart_rough_cut, export, asset_search, web_record
```

### 容错与边界

- `parse()` 遇到未知 action 抛 `ValueError`（由 StepOrchestrator 捕获并持久化）
- `validate()` 返回非致命错误列表，前端可在生成 EditPlan 前展示给用户
- `ACTION_TO_TOOL` 映射表可扩展：新增 action 只需添加一行映射
- 不改变现有的 `execute_jyproject_code` 工具——StoryboardToSteps 生成的 StepSpec 最终仍通过该工具执行

## 4. 验收标准 (DoD)

- [ ] `parse()` 正确将标准分镜 JSON 转换为 EditPlan（steps 列表非空、每个 StepSpec.tool 正确映射）
- [ ] `validate()` 对合法 JSON 返回空错误列表
- [ ] `validate()` 对缺失 action 的步骤返回对应错误
- [ ] `validate()` 对未知 action 返回错误信息包含可用 action 列表
- [ ] `validate()` 对 import_media 缺 file 参数返回错误
- [ ] 7 个现有工具操作均有 `ACTION_TO_TOOL` 映射项
- [ ] LLM system prompt 更新后，Agent 能理解并输出 JSON 分镜方案（而非 Python 代码）
