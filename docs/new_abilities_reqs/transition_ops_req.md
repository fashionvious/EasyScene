# transition_ops — 转场效果模块需求文档

> **父文档**: [Agent 剪辑工具扩充 PRD](../Agent%20剪辑工具扩充需求与技术设计文档.md)
> **Phase**: 1 (纯 API 编排)
> **目标文件**: `backend/app/agent/skills_agent/tools/transition_ops.py`

---

## 1. 模块归属与前置依赖

| 项目 | 说明 |
|------|------|
| **所属 Phase** | Phase 1 — 纯 API 编排层，0 外部依赖 |
| **对应 Python 文件** | `tools/transition_ops.py` |
| **注册 Handler** | `transition_ops` |
| **核心依赖** | `pyJianYingDraft` (vendored), `jy_wrapper.JyProject`, `KeyframeProperty` |
| **运行时环境** | Python ≥ 3.10，无需 Docker/GPU |
| **关联模块** | 可与 `timeline_ops` 组合：先编排时间线，再在片段衔接处添加转场 |

### 注册的 Agent Tools

| Tool 名称 | 类别 | 对应功能 ID |
|-----------|------|-------------|
| `apply_zoom_transition` | write | TR-04 缩放转场 |
| `apply_push_transition` | write | TR-07 推拉转场 |

---

## 2. 接口定义与 Agent Tool 封装

### 2.1 apply_zoom_transition

```python
def apply_zoom_transition(
    project_name: str,
    video_path_1: str,
    video_path_2: str,
    zoom_peak: float = 1.5,
    ramp_duration_us: int = 300_000,
) -> dict:
```

**入参说明**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 剪映草稿名称 |
| `video_path_1` | `str` | 是 | — | 前一段视频绝对路径 |
| `video_path_2` | `str` | 是 | — | 后一段视频绝对路径 |
| `zoom_peak` | `float` | 否 | `1.5` | 最大缩放倍数，必须 > 1.0 |
| `ramp_duration_us` | `int` | 否 | `300_000` | 过渡时长(微秒)，默认 0.3s |

**标准化出参**:

```python
# 成功
{
    "ok": True,
    "seg1_id": "abc123",
    "seg2_id": "def456",
    "seg1_end_scale": 1.5,
    "seg2_start_scale": 1.5,
    "ramp_duration_us": 300000
}

# 失败 — zoom_peak 非法
{"ok": False, "reason": "invalid_zoom_peak", "detail": "zoom_peak must be > 1.0, got 0.8"}
```

### 2.2 apply_push_transition

```python
def apply_push_transition(
    project_name: str,
    video_path_1: str,
    video_path_2: str,
    direction: str = "left",
    ramp_duration_us: int = 300_000,
) -> dict:
```

**入参说明**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 剪映草稿名称 |
| `video_path_1` | `str` | 是 | — | 前一段视频绝对路径 |
| `video_path_2` | `str` | 是 | — | 后一段视频绝对路径 |
| `direction` | `str` | 否 | `"left"` | 推出方向 |
| `ramp_duration_us` | `int` | 否 | `300_000` | 过渡时长(微秒) |

**方向参数映射**:

| direction | seg1 (推出) 终点 | seg2 (推入) 起点 | 使用的 KeyframeProperty |
|-----------|-----------------|-----------------|------------------------|
| `"left"` | position_x = -1.0 | position_x = +1.0 | `KFTypePositionX` |
| `"right"` | position_x = +1.0 | position_x = -1.0 | `KFTypePositionX` |
| `"up"` | position_y = +1.0 | position_y = -1.0 | `KFTypePositionY` |
| `"down"` | position_y = -1.0 | position_y = +1.0 | `KFTypePositionY` |

**标准化出参**:

```python
# 成功
{
    "ok": True,
    "direction": "left",
    "seg1_id": "abc123",
    "seg2_id": "def456",
    "seg1_end_pos": -1.0,
    "seg2_start_pos": 1.0,
    "ramp_duration_us": 300000
}

# 失败 — direction 非法
{
    "ok": False,
    "reason": "invalid_direction",
    "detail": "Unknown direction: 'diagonal'. Valid: left, right, up, down"
}
```

---

## 3. 核心执行逻辑与异常边界

### 3.1 apply_zoom_transition 执行流程

```
1. 校验 zoom_peak > 1.0，否则 ValueError
2. 校验 video_path_1 和 video_path_2 存在 (Path.exists())
3. project = JyProject(project_name, overwrite=True)
4. seg1 = project.add_media_safe(video_path_1, "0s", "5s", "Track1")
5. seg2 = project.add_media_safe(video_path_2, "5s", "5s", "Track2")
6. 获取片段在时间线上的时长 (⚠️ 使用 seg.target_timerange.duration，非 material_instance.duration):
   seg1_dur = seg1.target_timerange.duration  # 片段在轨道上的实际展示时长
7. 关键帧编排:
   seg1:
     - ramp 起点 = seg1_dur - ramp_duration_us
     - 如果 ramp_duration_us > seg1_dur → clamp 为 20%
     - add_keyframe(KeyframeProperty.uniform_scale, ramp_start_us, 1.0)
     - add_keyframe(KeyframeProperty.uniform_scale, seg1_dur, zoom_peak)
   seg2:
     - add_keyframe(KeyframeProperty.uniform_scale, 0, zoom_peak)           # 继承放大
     - add_keyframe(KeyframeProperty.uniform_scale, ramp_duration_us, 1.0)  # 缩回
8. project.save()
9. 返回结果
```

### 3.2 apply_push_transition 执行流程

```
1. 校验 direction in ["left", "right", "up", "down"]
2. 查表确定 property_type (KFTypePositionX 或 KFTypePositionY)
3. 查表确定 seg1 终点值和 seg2 起点值
4. project = JyProject(project_name, overwrite=True)
5. seg1 = project.add_media_safe(video_path_1, "0s", "5s", "Track1")
6. seg2 = project.add_media_safe(video_path_2, "5s", "5s", "Track2")
7. 关键帧编排 (以 direction="left" 为例):
   seg1:
     - ramp_start_us = seg1.duration - ramp_duration_us (clamp 同 zoom)
     - add_keyframe(KeyframeProperty.position_x, ramp_start_us, 0.0)
     - add_keyframe(KeyframeProperty.position_x, seg1.duration_us, -1.0)
   seg2:
     - add_keyframe(KeyframeProperty.position_x, 0, 1.0)           # 从右侧屏幕外
     - add_keyframe(KeyframeProperty.position_x, ramp_duration_us, 0.0)  # 滑到中心
8. project.save()
9. 返回结果
```

### 3.3 关键帧使用规范

> **重要**: `add_keyframe` 方法签名要求第一个参数为 `KeyframeProperty` **枚举值**，
> 不能传入字符串。错误的写法 `add_keyframe("uniform_scale", ...)` 将导致运行时异常。

```python
from pyJianYingDraft import KeyframeProperty

# 正确
seg.add_keyframe(KeyframeProperty.uniform_scale, time_us, value)
seg.add_keyframe(KeyframeProperty.position_x, time_us, value)
seg.add_keyframe(KeyframeProperty.position_y, time_us, value)

# 错误 — 字符串参数
seg.add_keyframe("uniform_scale", time_us, value)  # ❌ 运行时异常
```

### 3.4 异常分类与兜底策略

| 异常场景 | 检测方式 | 策略 |
|---------|---------|------|
| `zoom_peak <= 1.0` | 入参校验 | `{"ok": False, "reason": "invalid_zoom_peak"}` |
| `direction` 不在 `{"left","right","up","down"}` | 入参校验 | `{"ok": False, "reason": "invalid_direction", "available": [...]}` |
| `ramp_duration_us` > 片段时长 | 执行中 clamp | 自动缩减为 `min(ramp, duration * 0.2)`，记录 `"ramp_clamped": True` |
| 视频文件不存在 | `Path(video_path).exists()` | `{"ok": False, "reason": "file_not_found"}` |
| Agent 组合调用 (缩放+推拉) | 两次调用同一片段组的 keyframe | 不冲突，每种 property_type 独立持有自己的关键帧列表 |

---

## 4. 可观测性设计 (Observability)

### 4.1 LangFuse Trace 埋点

```python
from langfuse import Langfuse
from pyJianYingDraft import KeyframeProperty

langfuse = Langfuse(
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
)

VALID_DIRECTIONS = {"left", "right", "up", "down"}


def apply_zoom_transition(
    project_name: str,
    video_path_1: str,
    video_path_2: str,
    zoom_peak: float = 1.5,
    ramp_duration_us: int = 300_000,
) -> dict:
    trace = langfuse.trace(
        name="apply_zoom_transition",
        metadata={
            "tool": "apply_zoom_transition",
            "phase": "1",
            "handler": "transition_ops",
            "capability_id": "TR-04",
        },
        input={
            "project_name": project_name,
            "zoom_peak": zoom_peak,
            "ramp_duration_us": ramp_duration_us,
        },
    )

    try:
        # 校验 span
        val_span = trace.span(
            name="apply_zoom_transition.validation",
            input={"zoom_peak": zoom_peak},
        )

        if zoom_peak <= 1.0:
            val_span.update(level="ERROR", status_message=f"Invalid zoom_peak: {zoom_peak}")
            val_span.end()
            return {"ok": False, "reason": "invalid_zoom_peak",
                    "detail": f"zoom_peak must be > 1.0, got {zoom_peak}"}

        val_span.update(output={"valid": True})
        val_span.end()

        # 执行 span
        exec_span = trace.span(
            name="apply_zoom_transition.execution",
            input={"ramp_duration_us": ramp_duration_us},
        )

        project = JyProject(project_name, overwrite=True)
        seg1 = project.add_media_safe(video_path_1, "0s", "5s", "Track1")
        seg2 = project.add_media_safe(video_path_2, "5s", "5s", "Track2")

        # ramp 保护 — 使用 target_timerange.duration (时间线上实际展示时长)
        seg1_dur = seg1.target_timerange.duration
        actual_ramp = min(ramp_duration_us, int(seg1_dur * 0.2))
        ramp_clamped = actual_ramp != ramp_duration_us

        seg1.add_keyframe(KeyframeProperty.uniform_scale, seg1_dur - actual_ramp, 1.0)
        seg1.add_keyframe(KeyframeProperty.uniform_scale, seg1_dur, zoom_peak)
        seg2.add_keyframe(KeyframeProperty.uniform_scale, 0, zoom_peak)
        seg2.add_keyframe(KeyframeProperty.uniform_scale, actual_ramp, 1.0)

        project.save()

        result = {
            "ok": True,
            "seg1_id": getattr(seg1, "segment_id", "unknown"),
            "seg2_id": getattr(seg2, "segment_id", "unknown"),
            "seg1_end_scale": zoom_peak,
            "seg2_start_scale": zoom_peak,
            "ramp_duration_us": actual_ramp,
            "ramp_clamped": ramp_clamped,
        }

        exec_span.update(output=result)
        exec_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        trace.update(level="ERROR", status_message=str(e))
        return {"ok": False, "reason": type(e).__name__, "detail": str(e)}


def apply_push_transition(
    project_name: str,
    video_path_1: str,
    video_path_2: str,
    direction: str = "left",
    ramp_duration_us: int = 300_000,
) -> dict:
    """Trace 规范同 apply_zoom_transition，metadata.capability_id = 'TR-07'"""

    trace = langfuse.trace(
        name="apply_push_transition",
        metadata={
            "tool": "apply_push_transition",
            "phase": "1",
            "handler": "transition_ops",
            "capability_id": "TR-07",
        },
        input={
            "project_name": project_name,
            "direction": direction,
            "ramp_duration_us": ramp_duration_us,
        },
    )

    try:
        if direction not in VALID_DIRECTIONS:
            return {"ok": False, "reason": "invalid_direction",
                    "detail": f"Unknown direction: '{direction}'. Valid: {sorted(VALID_DIRECTIONS)}"}

        # 方向 → property + start/end 值 映射
        DIRECTION_MAP = {
            "left":  (KeyframeProperty.position_x, -1.0, +1.0),
            "right": (KeyframeProperty.position_x, +1.0, -1.0),
            "up":    (KeyframeProperty.position_y, +1.0, -1.0),
            "down":  (KeyframeProperty.position_y, -1.0, +1.0),
        }

        keyframe_prop, seg1_end_val, seg2_start_val = DIRECTION_MAP[direction]

        exec_span = trace.span(
            name="apply_push_transition.execution",
            input={"direction": direction, "ramp_duration_us": ramp_duration_us},
        )

        project = JyProject(project_name, overwrite=True)
        seg1 = project.add_media_safe(video_path_1, "0s", "5s", "Track1")
        seg2 = project.add_media_safe(video_path_2, "5s", "5s", "Track2")

        seg1_dur = seg1.target_timerange.duration
        actual_ramp = min(ramp_duration_us, int(seg1_dur * 0.2))

        seg1.add_keyframe(keyframe_prop, seg1_dur - actual_ramp, 0.0)
        seg1.add_keyframe(keyframe_prop, seg1_dur, seg1_end_val)
        seg2.add_keyframe(keyframe_prop, 0, seg2_start_val)
        seg2.add_keyframe(keyframe_prop, actual_ramp, 0.0)

        project.save()

        result = {
            "ok": True,
            "direction": direction,
            "seg1_id": getattr(seg1, "segment_id", "unknown"),
            "seg2_id": getattr(seg2, "segment_id", "unknown"),
            "seg1_end_pos": seg1_end_val,
            "seg2_start_pos": seg2_start_val,
            "ramp_duration_us": actual_ramp,
        }

        exec_span.update(output=result)
        exec_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        trace.update(level="ERROR", status_message=str(e))
        return {"ok": False, "reason": type(e).__name__, "detail": str(e)}
```

### 4.2 关键指标埋点

| 指标 | Span 名称 | 记录内容 |
|------|----------|---------|
| 缩放转场执行耗时 | `apply_zoom_transition.execution` | ramp 有未被 clamp |
| 推拉转场执行耗时 | `apply_push_transition.execution` | direction, actual_ramp |
| 复合转场次数 | 同一次 Agent 调用中出现两个 transition tool 调用 | 标记 `"composite": True` |

---

## 5. 模块验收标准 (DoD)

- [ ] 两个 Tool 在 `tool_registry.py` 中正确注册
- [ ] `apply_zoom_transition`: 单元测试 happy path + 3 边界 (zoom_peak=0.5、ramp 过长、文件不存在)
- [ ] `apply_push_transition`: 单元测试 happy path + 3 边界 (非法 direction、ramp 过长、文件不存在)
- [ ] 关键帧使用 `KeyframeProperty` 枚举，禁止字符串入参 (code review 强制检查)
- [ ] ramp_duration_us 超过片段时长的 20% 时自动 clamp，并在出参中标记 `ramp_clamped: True`
- [ ] LangFuse Trace 包含 validation + execution 两个 Span
- [ ] 复合调用 (缩放+推拉) 不产生关键帧冲突
- [ ] Agent ReAct 循环中能正确调用并返回 `{"ok": True}`
