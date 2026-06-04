# timeline_ops — 时间线编排模块需求文档

> **父文档**: [Agent 剪辑工具扩充 PRD](../Agent%20剪辑工具扩充需求与技术设计文档.md)
> **Phase**: 1 (纯 API 编排)
> **目标文件**: `backend/app/agent/skills_agent/tools/timeline_ops.py`

---

## 1. 模块归属与前置依赖

| 项目 | 说明 |
|------|------|
| **所属 Phase** | Phase 1 — 纯 API 编排层，0 外部依赖 |
| **对应 Python 文件** | `tools/timeline_ops.py` |
| **注册 Handler** | `timeline_ops` (tool_registry.py) |
| **核心依赖** | `pyJianYingDraft` (vendored), `jy_wrapper.JyProject` |
| **运行时环境** | Python ≥ 3.10，无需 Docker/GPU |
| **关联模块** | 本模块的函数可能被 `transition_ops`、`layout_ops` 等模块调用以编排多片段时间线 |
| **草稿模式** | `apply_jcut` 使用 `overwrite=True` 创建新草稿；`apply_lcut` 使用 `overwrite=True`；`reorder_segments` 使用 `overwrite=False` 加载已有草稿 |

### 注册的 Agent Tools

| Tool 名称 | 类别 | 对应功能 ID |
|-----------|------|-------------|
| `apply_jcut` | write | T-01 J-Cut 声音先行 |
| `apply_lcut` | write | T-02 L-Cut 画面先行 |
| `reorder_segments` | write | T-05 片段重排 |

---

## 2. 接口定义与 Agent Tool 封装

### 2.1 apply_jcut

```python
def apply_jcut(
    project_name: str,
    video_path: str,
    audio_path: str,
    audio_lead_us: int = 1_500_000,
    video_start: str = "0s",
    video_duration: str = "10s",
) -> dict:
```

**入参说明**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 剪映草稿名称 |
| `video_path` | `str` | 是 | — | 视频文件绝对路径 |
| `audio_path` | `str` | 是 | — | 音频文件绝对路径（可与 video_path 相同，表示使用视频自带音轨） |
| `audio_lead_us` | `int` | 否 | `1_500_000` | 音频提前量，微秒。1,500,000 = 1.5 秒 |
| `video_start` | `str` | 否 | `"0s"` | 视频素材起始时间，如 `"5s"` 表示从视频第 5 秒开始截取 |
| `video_duration` | `str` | 否 | `"10s"` | 视频片段持续时长 |

**标准化出参**:

```python
# 成功
{"ok": True, "video_segment_id": "abc123", "audio_segment_id": "def456",
 "video_actual_start_us": 1500000, "audio_start_us": 0}

# 失败 — 音频提前量非法
{"ok": False, "reason": "invalid_audio_lead", "detail": "audio_lead_us must be positive, got -500000"}

# 失败 — 文件不存在
{"ok": False, "reason": "file_not_found", "detail": "Video not found: /path/to/video.mp4"}
```

### 2.2 apply_lcut

```python
def apply_lcut(
    project_name: str,
    video_path: str,
    audio_path: str,
    audio_tail_us: int = 1_500_000,
    video_start: str = "0s",
    video_duration: str = "10s",
) -> dict:
```

**入参说明**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 剪映草稿名称 |
| `video_path` | `str` | 是 | — | 视频文件绝对路径 |
| `audio_path` | `str` | 是 | — | 音频文件绝对路径 |
| `audio_tail_us` | `int` | 否 | `1_500_000` | 音频尾部延长量，微秒 |
| `video_start` | `str` | 否 | `"0s"` | 同 apply_jcut |
| `video_duration` | `str` | 否 | `"10s"` | 同 apply_jcut |

**标准化出参**:

```python
# 成功
{"ok": True, "video_segment_id": "abc123", "audio_segment_id": "def456",
 "audio_actual_duration_us": 11500000}

# 失败 — 音频源文件时长不足
{"ok": False, "reason": "audio_source_too_short",
 "required_us": 11500000, "available_us": 8000000, "truncated": True}
```

### 2.3 reorder_segments

```python
def reorder_segments(
    project_name: str,
    new_order: list[int],
) -> dict:
```

**入参说明**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 已有草稿名称（必须已存在） |
| `new_order` | `list[int]` | 是 | — | 新顺序索引列表，如 `[2, 0, 1, 3]` 表示原第 3 个片段移到首位 |

**标准化出参**:

```python
# 成功
{"ok": True, "reordered_count": 4, "original_order": [0, 1, 2, 3],
 "new_order": [2, 0, 1, 3]}

# 失败 — 索引越界
{"ok": False, "reason": "index_out_of_range",
 "detail": "index 5 out of range, only 4 segments available"}

# 失败 — 重复/缺失索引
{"ok": False, "reason": "invalid_order",
 "detail": "duplicate or missing indices in new_order"}
```

---

## 3. 核心执行逻辑与异常边界

### 3.1 apply_jcut 执行流程

```
1. 校验 audio_lead_us > 0，否则 ValueError
2. 校验 video_path 和 audio_path 存在，否则 FileNotFoundError
3. safe_tim(video_duration) → duration_us
4. ⚠️ 校验音频源时长: 用 ffprobe 或 get_duration_ffprobe_cached() 获取音频实际时长
   若 audio_source_duration_us < duration_us + audio_lead_us:
     返回 {"ok": False, "reason": "audio_source_too_short",
            "required_us": duration_us + audio_lead_us,
            "available_us": audio_source_duration_us}
5. video_actual_start_us = audio_lead_us (视频后移实现"音频先行")
6. project.add_audio_safe(audio_path, start_time="0s", duration=duration_us + audio_lead_us)
7. project.add_media_safe(video_path, start_time=video_actual_start_us, duration=duration_us,
                          source_start=video_start)
8. project.save() → 触发后处理管线
9. project.audit_timeline() → 自检
10. 返回结果
```

### 3.2 apply_lcut 执行流程

```
1. 校验 audio_tail_us > 0，否则 ValueError
2. 校验 video_path 和 audio_path 存在
3. ⚠️ 校验音频源时长: 若 audio_source_duration_us < duration_us + audio_tail_us:
     返回 {"ok": False, "reason": "audio_source_too_short", "truncated": True}
4. Step 1: 视频 A 从 0 开始放置，duration = video_duration
5. Step 2: 音频 A 的 duration = video_duration + audio_tail_us（尾部延伸到视频结束后）
6. project.save()
7. 返回结果
```

> **重要**: 单片段 `apply_lcut` 仅实现"音频比视频长"的时间线布局。完整的多片段 L-Cut（音频跨越视频切割点，下一个画面在音频延续期间开始）需要 Agent 在 ReAct 循环中协调多次调用：
> 1. 调用 `apply_lcut` 为视频 A 创建延长音频
> 2. 调用 `apply_jcut` (或直接用 `add_media_safe`) 为视频 B 设置 start_time = 视频 A 的结束时间
> 3. 这样视频 B 的画面在音频 A 的尾部播放期间已经开始，实现真正的 L-Cut 效果
> 
> 这是 Agent 编排层的职责，不由单次 Tool 调用完成。

### 3.3 reorder_segments 执行流程

```
1. project = JyProject(project_name) — 加载已有草稿
2. 备份 draft_content.json → draft_content.json.bak.{timestamp}
3. 读取 draft_content.json，提取所有 video tracks 的 segments metadata:
   - material_id, source_timerange (start, duration), target_timerange
   - 记录原始轨道名和顺序
4. 校验 new_order:
   - 长度 == 原 segments 数量
   - 去重后长度 == 原数量 (无重复/缺失)
   - 所有索引 < 原数量
5. 按 new_order 重新排列 segments 列表
6. 覆盖写入 draft_content.json
7. project.save() — 触发 _patch_cloud_material_ids + _force_activate_adjustments
8. 返回结果
```

### 3.4 异常分类与兜底策略

| 异常场景 | 检测方式 | 策略 |
|---------|---------|------|
| `audio_lead_us <= 0` 或 `audio_tail_us <= 0` | 入参校验 | `ValueError` → 返回 `{"ok": False, "reason": "invalid_param"}` |
| 视频/音频文件不存在 | `Path(video_path).exists()` | `FileNotFoundError` → `{"ok": False, "reason": "file_not_found"}` |
| 音频源文件时长不足 | 捕获 segment 创建时的 duration 校验失败 | `{"ok": False, "reason": "audio_source_too_short", "truncated": True}` |
| new_order 索引越界 | 读取 segments 列表后比对长度 | `{"ok": False, "reason": "index_out_of_range"}` |
| new_order 重复/缺失 | `len(set(new_order)) != len(original_segments)` | `{"ok": False, "reason": "invalid_order"}` |
| 草稿不存在 | `DraftFolder.has_draft()` | `{"ok": False, "reason": "draft_not_found"}` |
| 时间线溢出 (>24h) | save() 前检查累计 duration | `{"ok": False, "reason": "timeline_overflow"}` |

---

## 4. 可观测性设计 (Observability)

### 4.1 LangFuse Trace 埋点

使用 **LangFuse SDK v3.x 原生 Trace API** (`langfuse.trace()` + `trace.span()`)。禁止使用已废弃的 `langfuse.decorators` 模块。

```python
from langfuse import Langfuse

langfuse = Langfuse(
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
)


def apply_jcut(
    project_name: str,
    video_path: str,
    audio_path: str,
    audio_lead_us: int = 1_500_000,
    video_start: str = "0s",
    video_duration: str = "10s",
) -> dict:
    # 创建 Trace（一次 Tool 调用 = 一个 Trace）
    trace = langfuse.trace(
        name="apply_jcut",
        metadata={
            "tool": "apply_jcut",
            "phase": "1",
            "handler": "timeline_ops",
            "capability_id": "T-01",
        },
        input={
            "project_name": project_name,
            "video_path": video_path,
            "audio_path": audio_path,
            "audio_lead_us": audio_lead_us,
        },
    )

    # 创建 Span 追踪具体执行阶段
    validation_span = trace.span(
        name="apply_jcut.validation",
        input={"audio_lead_us": audio_lead_us, "video_path": video_path},
    )

    try:
        # --- 校验阶段 ---
        if audio_lead_us <= 0:
            validation_span.update(
                level="ERROR",
                status_message=f"Invalid audio_lead_us: {audio_lead_us}",
            )
            validation_span.end()
            return {"ok": False, "reason": "invalid_audio_lead",
                    "detail": "audio_lead_us must be positive"}

        if not Path(video_path).exists():
            validation_span.update(
                level="ERROR",
                status_message=f"File not found: {video_path}",
            )
            validation_span.end()
            return {"ok": False, "reason": "file_not_found",
                    "detail": f"Video not found: {video_path}"}

        validation_span.update(output={"valid": True})
        validation_span.end()

        # --- 执行阶段 ---
        exec_span = trace.span(
            name="apply_jcut.execution",
            input={"video_duration": video_duration, "video_start": video_start},
        )

        project = JyProject(project_name, overwrite=True)
        duration_us = safe_tim(video_duration)
        video_actual_start_us = audio_lead_us

        project.add_audio_safe(audio_path, "0s", duration_us + audio_lead_us)
        seg = project.add_media_safe(
            video_path,
            start_time=video_actual_start_us,
            duration=duration_us,
            source_start=video_start,
        )
        project.save()

        result = {
            "ok": True,
            "video_segment_id": getattr(seg, "segment_id", "unknown"),
            "video_actual_start_us": video_actual_start_us,
            "audio_start_us": 0,
        }

        exec_span.update(output=result)
        exec_span.end()

        trace.update(output=result)
        return result

    except Exception as e:
        exec_span.update(level="ERROR", status_message=str(e))
        exec_span.end()
        trace.update(level="ERROR", status_message=str(e))
        return {"ok": False, "reason": type(e).__name__, "detail": str(e)}


def apply_lcut(...) -> dict:
    """同 apply_jcut 的 Trace 埋点模式，metadata.capability_id = 'T-02'"""
    ...


def reorder_segments(project_name: str, new_order: list[int]) -> dict:
    """同 apply_jcut 的 Trace 埋点模式，metadata.capability_id = 'T-05'"""
    ...
```

### 4.2 Trace 字段规范

| 字段 | 必填 | 说明 |
|------|------|------|
| `trace.name` | 是 | 函数名，如 `"apply_jcut"` |
| `trace.metadata.tool` | 是 | Agent Tool 注册名 |
| `trace.metadata.phase` | 是 | `"1"` |
| `trace.metadata.handler` | 是 | `"timeline_ops"` |
| `trace.metadata.capability_id` | 是 | 原始需求 ID，如 `"T-01"` |
| `trace.input` | 是 | 除文件内容外的所有入参 |
| `trace.output` | 是 | 完整的出参 dict |
| `span.name` | 是 | 阶段名，如 `"apply_jcut.validation"` |
| `span.level` | 异常时 | `"ERROR"` / `"WARNING"` |

---

## 5. 模块验收标准 (DoD)

- [ ] 三个 Tool 在 `tool_registry.py` 中正确注册，LangChain Agent 可调用
- [ ] `apply_jcut`: 单元测试覆盖 happy path + 3 个边界 case (负偏移量、文件不存在、时间线溢出)
- [ ] `apply_lcut`: 单元测试覆盖 happy path + 3 个边界 case (负偏移量、音频源不足、文件不存在)。多片段 L-Cut 编排逻辑属于 Agent ReAct 层的集成测试，不在此 Tool 的单元测试范围内
- [ ] `reorder_segments`: 单元测试覆盖 happy path + 3 个边界 case (索引越界、重复索引、草稿不存在)
- [ ] 每次调用自动生成 LangFuse Trace (含至少 2 个 Span: validation + execution)
- [ ] 异常时 Trace 标记为 ERROR 级别，且错误信息包含 `reason` 字段供 Agent 决策
- [ ] `reorder_segments` 执行前自动创建 `.bak.{timestamp}` 备份
