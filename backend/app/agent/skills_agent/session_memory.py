"""
会话记忆（Session Memory）— 用户编辑偏好管理。

自动从分镜方案中提取偏好（分辨率/帧率/发音人/常用路径），
持久化到 PG user_preference 表，注入到 Agent system prompt 中。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class UserPreferenceSnapshot:
    """用户偏好的内存快照。"""
    preferred_resolution: str | None = None   # "1080p", "4K" 等
    preferred_fps: int | None = None          # 24/25/30/50/60
    preferred_speaker: str | None = None      # "zh_male_huoli" 等
    frequent_media_paths: list[str] = field(default_factory=list)
    last_project_name: str | None = None


class SessionMemory:
    """管理用户编辑偏好的提取、存储和注入。"""

    def __init__(self, user_id: str):
        self.user_id = user_id

    # ---- PG 读写 ----

    def load_preferences(self, session) -> UserPreferenceSnapshot:
        """从 PG 加载用户偏好。"""
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
                if pref.frequent_media_paths_json else []
            ),
            last_project_name=pref.last_project_name,
        )

    def save_preferences(
        self, session, snapshot: UserPreferenceSnapshot,
    ) -> None:
        """保存用户偏好到 PG。"""
        from app import crud
        from app.models import UserPreferenceUpdate

        paths = snapshot.frequent_media_paths or []
        update = UserPreferenceUpdate(
            preferred_resolution=snapshot.preferred_resolution,
            preferred_fps=snapshot.preferred_fps,
            preferred_speaker=snapshot.preferred_speaker,
            frequent_media_paths_json=json.dumps(paths[:5]) if paths else None,
            last_project_name=snapshot.last_project_name,
        )
        crud.update_user_preference(
            session=session, user_id=self.user_id, pref_in=update,
        )

    # ---- 偏好提取 ----

    def extract_from_plan(
        self, storyboard_json: dict,
    ) -> UserPreferenceSnapshot:
        """从分镜方案中自动提取偏好。"""
        snapshot = UserPreferenceSnapshot()

        config = storyboard_json.get("project_config", {})
        width = config.get("width", 0)
        if width >= 3840:
            snapshot.preferred_resolution = "4K"
        elif width >= 1920:
            snapshot.preferred_resolution = "1080p"
        elif width >= 1280:
            snapshot.preferred_resolution = "720p"

        if "fps" in config:
            snapshot.preferred_fps = config["fps"]

        snapshot.last_project_name = storyboard_json.get("project_name")

        for step in storyboard_json.get("steps", []):
            if step.get("speaker"):
                snapshot.preferred_speaker = step["speaker"]
            if step.get("file"):
                file_path = step["file"]
                if "\\" in file_path or "/" in file_path:
                    dir_path = os.path.dirname(file_path)
                    if dir_path and dir_path not in snapshot.frequent_media_paths:
                        snapshot.frequent_media_paths.append(dir_path)

        # Cap media paths at 5
        snapshot.frequent_media_paths = snapshot.frequent_media_paths[:5]

        return snapshot

    # ---- 偏好注入 ----

    def build_preference_prompt(self, session=None) -> str:
        """
        构建偏好注入提示（< 200 tokens）。

        新用户（无偏好记录）返回空字符串。
        session 为 None 时跳过 PG 查询，返回空。
        """
        if session is None:
            return ""

        try:
            prefs = self.load_preferences(session)
        except Exception:
            return ""

        lines: list[str] = []
        if prefs.preferred_resolution:
            lines.append(f"- 常用分辨率: {prefs.preferred_resolution}")
        if prefs.preferred_fps:
            lines.append(f"- 常用帧率: {prefs.preferred_fps}fps")
        if prefs.preferred_speaker:
            lines.append(f"- 常用发音人: {prefs.preferred_speaker}")
        if prefs.last_project_name:
            lines.append(f"- 最近项目: {prefs.last_project_name}")

        if not lines:
            return ""

        lines.insert(0, "\n## 用户编辑偏好（基于历史记录）\n")
        lines.append("\n> 用户未明确指定时，可默认使用以上偏好值。")
        return "\n".join(lines)
