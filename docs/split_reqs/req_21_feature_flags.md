# 需求 21：Feature Flag 体系

> 原 PRD 编号: P2-5 | 优先级: P2

## 1. 依赖关系

- **前置依赖**：无（独立于所有其他需求）
- **被谁依赖**：req_13（断路器 — 通过 Feature Flag 控制启用/关闭）、req_09（Celery — 可通过 Flag 控制是否异步分发）、req_14（autoCompact — 可通过 Flag 启用/禁用）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库无 Feature Flag 体系。所有功能启用/禁用通过以下方式：
- 代码中的硬编码常量（如 `STREAM_FLAG = True` 在 [$scriptId.tsx:105](../../../frontend/src/routes/_layout/video_editing/$scriptId.tsx#L105)）
- 环境变量（如 `LANGFUSE_SECRET_KEY` 控制可观测性启用）
- 配置文件中的开关（如 `celery_config.py` 的 `task_ignore_result`）

**PRD 降级策略**：不需要 LaunchDarkly 等外部服务。使用环境变量 + YAML/TOML 配置文件。

**需要 Feature Flag 的场景**：
- 断路器启用/关闭（`CB_UI_ENABLED`, `CB_FFMPEG_ENABLED`）
- Celery 异步分发模式（`EDIT_ASYNC_MODE`: `"celery"` | `"sync"`）
- autoCompact 编译开关（`CONTEXT_COMPACT_ENABLED`）
- StoryboardToSteps vs 传统 JyProject 代码生成（`USE_STORYBOARD_MODE`）
- 并发执行 vs 串行执行（`CONCURRENT_EXEC_ENABLED`）
- Langfuse 手动 span 覆盖（`LANGFUSE_MANUAL_SPAN_ENABLED`）

### 代码库校验结论

- 项目已有 `app/config.py`（settings 类），可直接在 settings 中新增 Flag 字段
- 无需引入第三方 Feature Flag 库——14 个 Flag 以内，环境变量/配置文件完全够用
- 每个 Flag 需要明确的默认值（保证 backward compatible）

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `backend/app/agent/skills_agent/feature_flags.py` | Feature Flag 统一管理模块 |
| 修改 | `backend/app/config.py`（或 settings 文件） | 新增 Flag 字段（可选） |

### 核心技术细节

```python
"""feature_flags.py — 编辑 Agent 功能开关统一管理"""
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FeatureFlag:
    """单个功能开关"""
    key: str
    default: Any
    description: str

    def get(self) -> Any:
        """从环境变量读取，环境变量为空时返回默认值"""
        env_val = os.getenv(self.key, "").strip()
        if not env_val:
            return self.default

        # 类型推断
        if isinstance(self.default, bool):
            return env_val.lower() in ("true", "1", "yes", "on")
        if isinstance(self.default, int):
            return int(env_val)
        if isinstance(self.default, float):
            return float(env_val)
        return env_val


class EditFeatureFlags:
    """
    编辑 Agent 功能开关注册表。

    用法：
        flags = EditFeatureFlags()
        if flags.CB_FFMPEG_ENABLED:
            breaker.call(ffmpeg_func, ...)
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
        "编辑步骤异步模式（'celery' | 'sync'，默认同步）",
    )
    CONCURRENT_EXEC_ENABLED = FeatureFlag(
        "CONCURRENT_EXEC_ENABLED", False,
        "步骤并发执行启用（默认关闭，Phase 1 验证后开启）",
    )

    # ---- 上下文 ----
    CONTEXT_COMPACT_ENABLED = FeatureFlag(
        "CONTEXT_COMPACT_ENABLED", True,
        "上下文 autoCompact 启用（默认开启）",
    )
    USE_STORYBOARD_MODE = FeatureFlag(
        "USE_STORYBOARD_MODE", False,
        "使用 StoryboardToSteps 模式（默认关闭，使用传统 JyProject 代码）",
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
        "记录完整工具参数到日志（默认关闭，PII 风险）",
    )

    @classmethod
    def dump_all(cls) -> dict[str, Any]:
        """导出所有 Flag 当前值（用于 /doctor 命令）"""
        result = {}
        for attr_name in dir(cls):
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
```

### 容错与边界

- 所有 Flag 必须有明确的默认值（环境变量未设置时使用）
- 布尔 Flag 支持多种 true 表示法（`true`, `1`, `yes`, `on`）
- `FeatureFlag` 使用 `frozen=True` 防止运行时修改（Flag 应只在启动时读取）
- `dump_all()` 返回便于调试的完整 FLag 状态（req_22 doctor 命令中使用）
- Flag 不持久化到数据库——环境变量/配置文件是唯一真相源

## 4. 验收标准 (DoD)

- [ ] `EditFeatureFlags.CB_FFMPEG_ENABLED.get()` 默认返回 `False`
- [ ] 设置环境变量 `CB_FFMPEG_ENABLED=true` 后返回 `True`
- [ ] 设置环境变量 `EDIT_ASYNC_MODE=celery` 后返回 `"celery"`
- [ ] 设置环境变量 `CONCURRENT_EXEC_ENABLED=1` 后返回 `True`
- [ ] `dump_all()` 返回包含所有 Flag 当前值和描述的完整字典
- [ ] 未设置环境变量的 Flag 返回默认值
- [ ] `FeatureFlag` dataclass 为 frozen，不可在运行时修改
