"""
StoryboardParser — 将 LLM 生成的分镜 JSON 转换为 StepOrchestrator 可执行的 EditPlan。

LLM 仍然负责业务逻辑（哪些 clip 怎么排），但不生成 Python 执行代码。
结构化 JSON 方案 → 每步可独立执行、独立重试、前端可逐状态展示。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

try:
    from .step_orchestrator import StepSpec, EditPlan
except ImportError:
    from step_orchestrator import StepSpec, EditPlan


# ============================================================================
# action → tool 映射表（基于 ToolRegistry 的 7 个工具）
# ============================================================================

ACTION_TO_TOOL: dict[str, str] = {
    # JyProject 代码执行类（通过 execute_jyproject_code 工具）
    "import_media":     "execute_jyproject_code",
    "add_text":         "execute_jyproject_code",
    "add_tts":          "execute_jyproject_code",
    "add_audio":        "execute_jyproject_code",
    "add_effect":       "execute_jyproject_code",
    "add_transition":   "execute_jyproject_code",
    "add_subtitle":     "execute_jyproject_code",
    # CLI 脚本类（通过 execute_cli_script 工具）
    "smart_rough_cut":  "execute_cli_script",
    "export":           "execute_cli_script",
    "asset_search":     "execute_cli_script",
    "web_record":       "execute_cli_script",
    # 媒体解析类（直接工具）
    "resolve_media":    "resolve_media",
    "list_media":       "list_media",
}


# 可用 action 列表（用于 system prompt）
AVAILABLE_ACTIONS = list(ACTION_TO_TOOL.keys())


# ============================================================================
# Parser
# ============================================================================

class StoryboardParser:
    """将 LLM 生成的分镜 JSON 转换为 EditPlan + StepSpec 列表。"""

    def parse(
        self,
        storyboard_json: str | dict[str, Any],
        user_id: str,
        script_id: str,
    ) -> EditPlan:
        """
        解析分镜方案，返回可执行的 EditPlan。

        Raises:
            ValueError: 遇到未知 action 时抛出（由 StepOrchestrator 捕获并持久化）
        """
        if isinstance(storyboard_json, str):
            data = json.loads(storyboard_json)
        else:
            data = storyboard_json

        project_name = data.get("project_name", "未命名项目")
        project_config = data.get("project_config", {})

        steps: list[StepSpec] = []
        for i, step_data in enumerate(data.get("steps", [])):
            action = step_data.get("action", "")
            tool = ACTION_TO_TOOL.get(action)

            if tool is None:
                raise ValueError(
                    f"未知 action: '{action}'（步骤 {i + 1}）。"
                    f"可用: {AVAILABLE_ACTIONS}"
                )

            step = StepSpec(
                tool=tool,
                args={
                    "action": action,
                    **{k: v for k, v in step_data.items() if k != "action"},
                    "project_name": project_name,
                    "project_config": project_config,
                    "step_index": i,
                },
            )
            steps.append(step)

        return EditPlan(
            task_id=str(uuid.uuid4()),
            user_id=user_id,
            script_id=script_id,
            steps=steps,
            project_state={"project_name": project_name},
        )

    def validate(self, storyboard_json: str | dict[str, Any]) -> list[str]:
        """
        预校验：检查 action 合法性和必填参数。

        Returns:
            错误列表（空列表 = 校验通过）。
            前端可在生成 EditPlan 前展示这些错误给用户。
        """
        if isinstance(storyboard_json, str):
            try:
                data = json.loads(storyboard_json)
            except json.JSONDecodeError as e:
                return [f"JSON 解析失败: {e}"]
        else:
            data = storyboard_json

        errors: list[str] = []
        steps = data.get("steps", [])

        if not steps:
            errors.append("分镜方案中 steps 为空，至少需要一个步骤")
            return errors

        if not isinstance(steps, list):
            errors.append("steps 必须是数组")
            return errors

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                errors.append(f"步骤 {i + 1}: 必须是 JSON 对象")
                continue

            action = step.get("action", "")
            if not action:
                errors.append(f"步骤 {i + 1}: 缺少 action 字段")
            elif action not in ACTION_TO_TOOL:
                errors.append(
                    f"步骤 {i + 1}: 未知 action '{action}'，"
                    f"可用: {AVAILABLE_ACTIONS}"
                )

            # 特定 action 的参数校验
            if action == "import_media" and "file" not in step:
                errors.append(
                    f"步骤 {i + 1} (import_media): 缺少 file 参数"
                )
            if action == "add_tts" and "text" not in step:
                errors.append(
                    f"步骤 {i + 1} (add_tts): 缺少 text 参数"
                )
            if action == "add_text" and "text" not in step:
                errors.append(
                    f"步骤 {i + 1} (add_text): 缺少 text 参数"
                )
            if action == "export" and "draft_name" not in step:
                errors.append(
                    f"步骤 {i + 1} (export): 缺少 draft_name 参数"
                )

        return errors
