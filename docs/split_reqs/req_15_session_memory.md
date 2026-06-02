# 需求 15：会话记忆（用户偏好 PG 存储）

> 原 PRD 编号: P1-8 (C-4) | 优先级: P1

## 1. 依赖关系

- **前置依赖**：req_04（PG 数据模型 — `user_preference` 表定义）、req_05（CRUD 函数 — `get_or_create_user_preference`/`update_user_preference`)
- **被谁依赖**：无（独立于后续需求）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库完全无用户偏好存储。每次对话中，用户重复指定相同的分辨率、帧率、TTS 发音人：

```
用户: "导出为 1080p 30fps"
用户: "还是用 1080p 30fps，这次文本不同"
用户: "zh_male_huoli 这个发音人，导出 1080p 30fps"
```

**PRD 设计的偏好数据结构**：

```json
{
    "preferred_resolution": "1080p",
    "preferred_fps": 30,
    "preferred_speaker": "zh_male_huoli",
    "frequent_media_paths": ["D:/videos/", "D:/素材/"],
    "last_project_name": "我的视频"
}
```

### 代码库校验结论

- `user_preference` 表已在 req_04 定义（SQLModel，`user_id` 唯一约束）
- CRUD 函数 `get_or_create_user_preference` / `update_user_preference` 已在 req_05 实现
- 偏好注入到 system prompt 在 `jianying_agent.py` 的 `_build_skills_addendum()` 中实现
- 偏好自动提取逻辑轻量（从最近的工具调用和 LLM 输出中解析）
- **不需要 autoDream**（剪辑场景下无长空闲期）

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `backend/app/agent/skills_agent/session_memory.py` | 偏好提取 + 注入逻辑 |
| 修改 | `backend/app/agent/skills_agent/jianying_agent.py` | 在 system prompt 尾部注入偏好 |
| 修改 | `backend/app/api/routes/edit.py` | 任务结束后触发偏好提取 |

### 核心技术细节

```python
"""session_memory.py — 用户偏好管理"""
import json
import logging
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class UserPreferenceSnapshot:
    preferred_resolution: str | None = None   # "1080p"
    preferred_fps: int | None = None          # 30
    preferred_speaker: str | None = None      # "zh_male_huoli"
    frequent_media_paths: list[str] | None = None
    last_project_name: str | None = None


class SessionMemory:
    """管理用户编辑偏好的提取、存储和注入"""

    def __init__(self, user_id: str):
        self.user_id = user_id

    def load_preferences(self, session) -> UserPreferenceSnapshot:
        """从 PG 加载用户偏好"""
        from app import crud
        pref = crud.get_or_create_user_preference(
            session=session, user_id=self.user_id,
        )
        return UserPreferenceSnapshot(
            preferred_resolution=pref.preferred_resolution,
            preferred_fps=pref.preferred_fps,
            preferred_speaker=pref.preferred_speaker,
            frequent_media_paths=(
                json.loads(pref.frequent_media_paths_json)
                if pref.frequent_media_paths_json else None
            ),
            last_project_name=pref.last_project_name,
        )

    def save_preferences(self, session, snapshot: UserPreferenceSnapshot) -> None:
        """保存用户偏好到 PG"""
        from app import crud
        from app.models import UserPreferenceUpdate

        update = UserPreferenceUpdate(
            preferred_resolution=snapshot.preferred_resolution,
            preferred_fps=snapshot.preferred_fps,
            preferred_speaker=snapshot.preferred_speaker,
            frequent_media_paths_json=(
                json.dumps(snapshot.frequent_media_paths)
                if snapshot.frequent_media_paths else None
            ),
            last_project_name=snapshot.last_project_name,
        )
        crud.update_user_preference(
            session=session, user_id=self.user_id, pref_in=update,
        )

    def extract_from_plan(self, storyboard_json: dict) -> UserPreferenceSnapshot:
        """从分镜方案中自动提取偏好"""
        snapshot = UserPreferenceSnapshot()

        config = storyboard_json.get("project_config", {})
        if "width" in config and config["width"] >= 1920:
            snapshot.preferred_resolution = "1080p"
        elif "width" in config and config["width"] >= 1280:
            snapshot.preferred_resolution = "720p"

        if "fps" in config:
            snapshot.preferred_fps = config["fps"]

        snapshot.last_project_name = storyboard_json.get("project_name")

        # 从步骤中提取常用路径和发音人
        for step in storyboard_json.get("steps", []):
            if step.get("speaker"):
                snapshot.preferred_speaker = step["speaker"]
            if step.get("file") and "\\" in step["file"]:
                # 提取目录路径
                import os
                dir_path = os.path.dirname(step["file"])
                if dir_path:
                    if snapshot.frequent_media_paths is None:
                        snapshot.frequent_media_paths = []
                    if dir_path not in snapshot.frequent_media_paths:
                        snapshot.frequent_media_paths.append(dir_path)

        return snapshot

    def build_preference_prompt(self, session) -> str:
        """
        构建偏好注入提示（< 200 tokens）。

        每次会话开始时注入到 system prompt 尾部。
        """
        prefs = self.load_preferences(session)
        lines = ["\n## 用户编辑偏好（基于历史记录）\n"]

        has_any = False
        if prefs.preferred_resolution:
            lines.append(f"- 常用分辨率: {prefs.preferred_resolution}")
            has_any = True
        if prefs.preferred_fps:
            lines.append(f"- 常用帧率: {prefs.preferred_fps}fps")
            has_any = True
        if prefs.preferred_speaker:
            lines.append(f"- 常用发音人: {prefs.preferred_speaker}")
            has_any = True
        if prefs.last_project_name:
            lines.append(f"- 最近项目: {prefs.last_project_name}")
            has_any = True

        if not has_any:
            return ""

        lines.append("\n> 用户未明确指定时，可默认使用以上偏好值。")
        return "\n".join(lines)
```

**集成到 jianying_agent.py**：

```python
# _build_skills_addendum 尾部追加偏好

def _build_skills_addendum(self, token_budget=None, user_id=None) -> str:
    # ... 现有 L0/L1/GUIDE_TEMPLATE 逻辑 ...

    # 注入用户偏好（若可用）
    preference_prompt = ""
    if user_id:
        from session_memory import SessionMemory
        memory = SessionMemory(user_id)
        preference_prompt = memory.build_preference_prompt(session)

    return (
        f"{route_summary}\n\n{category_detail}\n"
        f"{media_summary}\n{GUIDE_TEMPLATE}\n{preference_prompt}"
    )
```

### 容错与边界

- `build_preference_prompt()` 对新用户（无偏好记录）返回空字符串
- `extract_from_plan()` 的所有字段均为可选，缺失时不抛异常
- 偏好提示 < 200 tokens（通过 `estimate_tokens()` 验证）
- `frequent_media_paths` 最多保留 5 个路径（超出部分自动裁剪）
- PG 查询失败时静默降级（不注入偏好，不影响主流程）

## 4. 验收标准 (DoD)

- [ ] 新用户首次使用时 `build_preference_prompt()` 返回空字符串
- [ ] 用户保存偏好后 `build_preference_prompt()` 返回包含分辨率和帧率的提示
- [ ] `extract_from_plan()` 从分镜 JSON 中正确提取分辨率、帧率、发音人
- [ ] 偏好提示 token 数 < 200
- [ ] `frequent_media_paths` 去重且最多保留 5 条
- [ ] PG 查询失败时不影响 Agent 正常对话
- [ ] 偏好跨会话持久化（重启后仍可读取）
