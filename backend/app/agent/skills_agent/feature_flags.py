"""Feature Flag 体系 — 编辑 Agent 功能开关统一管理。

零依赖：仅使用 os.getenv + 标准库 dataclass。
Flag 不持久化到数据库——环境变量/配置文件是唯一真相源。
"""
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FeatureFlag:
    """单个功能开关。

    frozen=True 防止运行时意外修改 Flag 定义。
    通过 .get() 读取当前值，通过 __bool__ 支持布尔 Flag 的简洁判断。
    """

    key: str
    default: Any
    description: str = ""

    def get(self) -> Any:
        """从环境变量读取，未设置时返回默认值。"""
        env_val = os.getenv(self.key, "").strip()
        if not env_val:
            return self.default

        # 根据默认值类型推断并转换
        if isinstance(self.default, bool):
            return env_val.lower() in ("true", "1", "yes", "on")
        if isinstance(self.default, int):
            try:
                return int(env_val)
            except ValueError:
                return self.default
        if isinstance(self.default, float):
            try:
                return float(env_val)
            except ValueError:
                return self.default
        return env_val

    def __bool__(self) -> bool:
        """布尔 Flag 可直接用于 if 判断。"""
        return bool(self.get())

    def __repr__(self) -> str:
        return f"FeatureFlag({self.key}={self.get()!r}, default={self.default!r})"


class EditFeatureFlags:
    """编辑 Agent 功能开关注册表。

    用法:
        from feature_flags import flags

        if flags.CB_FFMPEG_ENABLED:           # __bool__ 自动求值
            breaker.call(ffmpeg_func, ...)

        mode = flags.EDIT_ASYNC_MODE.get()    # 非布尔 Flag 显式调用 .get()
    """

    # ---- 断路器 ----
    CB_FFMPEG_ENABLED = FeatureFlag(
        "CB_FFMPEG_ENABLED", False,
        "FFmpeg 断路器启用（默认关闭）",
    )
    CB_UI_ENABLED = FeatureFlag(
        "CB_UI_ENABLED", False,
        "uiautomation 断路器启用（默认关闭）",
    )

    # ---- 执行模式 ----
    EDIT_ASYNC_MODE = FeatureFlag(
        "EDIT_ASYNC_MODE", "sync",
        "编辑步骤异步模式: 'celery' | 'sync'（默认 sync）",
    )
    CONCURRENT_EXEC_ENABLED = FeatureFlag(
        "CONCURRENT_EXEC_ENABLED", False,
        "步骤并发执行启用（默认关闭，Phase 1 验证后开启）",
    )

    # ---- 上下文管理 ----
    CONTEXT_COMPACT_ENABLED = FeatureFlag(
        "CONTEXT_COMPACT_ENABLED", True,
        "上下文 autoCompact 启用（默认开启）",
    )
    USE_STORYBOARD_MODE = FeatureFlag(
        "USE_STORYBOARD_MODE", False,
        "StoryboardToSteps 模式（默认关闭，使用传统 JyProject 代码生成）",
    )

    # ---- 可观测性 ----
    LANGFUSE_MANUAL_SPAN_ENABLED = FeatureFlag(
        "LANGFUSE_MANUAL_SPAN_ENABLED", True,
        "Langfuse 手动 span 启用（默认开启）",
    )

    # ---- 调试 ----
    DEBUG_KEEP_TEMP_FILES = FeatureFlag(
        "DEBUG_KEEP_TEMP_FILES", False,
        "保留临时 Python 文件供调试（默认关闭）",
    )
    DEBUG_LOG_TOOL_ARGS = FeatureFlag(
        "DEBUG_LOG_TOOL_ARGS", False,
        "记录完整工具参数到日志（默认关闭，含用户数据风险）",
    )

    @classmethod
    def dump_all(cls) -> dict[str, dict[str, Any]]:
        """导出所有 Flag 当前值，供 doctor 命令和调试使用。"""
        result: dict[str, dict[str, Any]] = {}
        for attr_name in dir(cls):
            if attr_name.startswith("_"):
                continue
            val = getattr(cls, attr_name)
            if isinstance(val, FeatureFlag):
                result[attr_name] = {
                    "value": val.get(),
                    "default": val.default,
                    "description": val.description,
                }
        return result


# 全局单例
flags = EditFeatureFlags()
