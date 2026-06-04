# layout_and_subtitle_ops — 布局与字幕模块需求文档

> **父文档**: [Agent 剪辑工具扩充 PRD](../Agent%20剪辑工具扩充需求与技术设计文档.md)
> **Phase**: 1 (纯 API 编排)
> **目标文件**: `tools/layout_ops.py` + `tools/subtitle_ops.py`

---

## 1. 模块归属与前置依赖

| 项目 | 说明 |
|------|------|
| **所属 Phase** | Phase 1 — 纯 API 编排层，0 外部依赖 |
| **对应 Python 文件** | `tools/layout_ops.py` (分屏效果) + `tools/subtitle_ops.py` (多语言字幕) |
| **注册 Handler** | `layout_ops` / `subtitle_ops` |
| **核心依赖** | `pyJianYingDraft` (vendored), `jy_wrapper.JyProject`, `ClipSettings`, `TextStyle`, `TextBackground` |
| **运行时环境** | Python ≥ 3.10，无需 Docker/GPU |
| **关联模块** | 字幕模块的 `subtitle_pairs` 数据结构可能由外部 ASR 或 LLM 生成后传入 |
| **草稿模式** | `apply_split_screen` 使用 `overwrite=True` 创建新草稿；`add_dual_subtitles` 使用 `overwrite=False` 加载已有草稿追加字幕 |

### 注册的 Agent Tools

| Tool 名称 | 类别 | Handler | 对应功能 ID |
|-----------|------|---------|-------------|
| `apply_split_screen` | write | `layout_ops` | V-04 分屏效果 |
| `add_dual_subtitles` | write | `subtitle_ops` | TX-06 多语言字幕 |

---

## 2. 接口定义与 Agent Tool 封装

### 2.1 apply_split_screen

```python
def apply_split_screen(
    project_name: str,
    video_paths: list[str],
    template: str = "2x2",
    duration: str = "10s",
    fill_mode: str = "loop",
) -> dict:
```

**入参说明**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 剪映草稿名称 |
| `video_paths` | `list[str]` | 是 | — | N 段视频绝对路径列表，数量必须与模板格数匹配 |
| `template` | `str` | 否 | `"2x2"` | 布局模板 ID |
| `duration` | `str` | 否 | `"10s"` | 分屏持续时长 |
| `fill_mode` | `str` | 否 | `"loop"` | 视频时长不足时的填充策略：`"loop"`(循环拼接) / `"freeze"`(尾帧定格，依赖 Phase 3) / `"stretch"`(轻微减速拉伸，通过 VideoSegment(speed=...) 实现，超出范围自动降级为 loop) |

**模板参数表**:

| template | 格数 | 布局描述 | 每格 scale | 每格 transform (x, y) |
|----------|------|---------|-----------|----------------------|
| `"2H"` | 2 | 左右并排 | `(0.5, 1.0)` | `(-0.5, 0)` / `(0.5, 0)` |
| `"2V"` | 2 | 上下并排 | `(1.0, 0.5)` | `(0, 0.5)` / `(0, -0.5)` |
| `"2x2"` | 4 | 四宫格 | `(0.5, 0.5)` | `(-0.5, 0.5)` / `(0.5, 0.5)` / `(-0.5, -0.5)` / `(0.5, -0.5)` |
| `"3x3"` | 9 | 九宫格 | `(0.333, 0.333)` | 3×3 均匀分布 |
| `"1+2"` | 3 | 左大右两小 | 左: `(0.6, 1.0)`, 右上/右下: `(0.4, 0.5)` | 左 `(-0.33, 0)`, 右上 `(0.5, 0.5)`, 右下 `(0.5, -0.5)` |

**标准化出参**:

```python
# 成功
{
    "ok": True,
    "template": "2x2",
    "layout": [
        {"track": 0, "scale": (0.5, 0.5), "pos": (-0.5, 0.5)},
        {"track": 1, "scale": (0.5, 0.5), "pos": (0.5, 0.5)},
        {"track": 2, "scale": (0.5, 0.5), "pos": (-0.5, -0.5)},
        {"track": 3, "scale": (0.5, 0.5), "pos": (0.5, -0.5)},
    ],
    "segment_ids": ["abc123", "def456", "ghi789", "jkl012"],
    "duration": "10s"
}

# 失败 — 视频数量与模板不匹配
{"ok": False, "reason": "video_count_mismatch",
 "detail": "template '2x2' requires 4 videos, got 3"}

# 失败 — 轨道数量超限
{"ok": False, "reason": "track_limit_exceeded",
 "detail": "template '3x3' requires 9 tracks, max is 10. Consider using nested PIP."}
```

### 2.2 add_dual_subtitles

```python
def add_dual_subtitles(
    project_name: str,
    subtitle_pairs: list[dict],
    primary_lang: str = "zh",
    secondary_lang: str = "en",
) -> dict:
```

**入参说明**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 剪映草稿名称 |
| `subtitle_pairs` | `list[dict]` | 是 | — | 字幕对列表，见下方结构 |
| `primary_lang` | `str` | 否 | `"zh"` | 主字幕语言标签（影响默认字号和样式） |
| `secondary_lang` | `str` | 否 | `"en"` | 副字幕语言标签 |

**`subtitle_pairs` 元素结构**:

```python
{
    "start_us": 0,           # int, 开始时间(微秒)
    "duration_us": 2000000,  # int, 持续时长(微秒)
    "primary_text": "你好世界",   # str, 主字幕文本
    "secondary_text": "Hello World", # str, 副字幕文本
}
```

**标准化出参**:

```python
# 成功
{"ok": True, "pair_count": 45, "primary_track": "Sub_Primary",
 "secondary_track": "Sub_Secondary", "skipped_pairs": 0}

# 部分成功（有空字幕被跳过）
{"ok": True, "pair_count": 45, "skipped_pairs": 2,
 "skipped_details": [{"index": 5, "reason": "empty_text"}, {"index": 12, "reason": "empty_text"}]}

# 失败 — 空输入
{"ok": False, "reason": "empty_input", "detail": "subtitle_pairs must not be empty"}
```

---

## 3. 核心执行逻辑与异常边界

### 3.1 apply_split_screen 执行流程

```
1. 查找模板配置: LAYOUT_TEMPLATES[template]
2. 校验 len(video_paths) == 模板格数
3. 校验轨道数 <= 10 (剪映上限)，超限抛出 track_limit_exceeded
4. project = JyProject(project_name, overwrite=True)
5. for i, (video_path, layout_cell) in enumerate(zip(video_paths, template_cells)):
     a. track_name = f"SplitTrack_{i}"
     b. seg = project.add_media_safe(video_path, "0s", duration, track_name)
     c. seg.clip_settings = ClipSettings(
            scale_x=layout_cell.scale_x, scale_y=layout_cell.scale_y,
            transform_x=layout_cell.transform_x, transform_y=layout_cell.transform_y,
        )
     d. 如果视频时长不足: fill_mode 处理 (loop/freeze/stretch)
6. project.save()
7. 返回布局信息 + segment_ids
```

### 3.2 add_dual_subtitles 执行流程

```
1. 校验 subtitle_pairs 非空
2. project = JyProject(project_name) — 加载已有草稿
3. 为每种语言预设样式:
   - primary: size=4.0, color=(1,1,1), transform_y=-0.75, 带背景底板
   - secondary: size=3.0, color=(0.7,0.7,0.7), transform_y=-0.85, 半透明背景
4. for each pair:
     a. 如果 primary_text 为空字符串 → 跳过并记录
     b. 如果 duration_us == 0 → 设为 min_duration (500ms)
     c. primary_start_time = pair["start_us"], secondary 强制与之相同
     d. project.add_text_simple(primary_text, start_us, duration_us, "Sub_Primary", style=primary_style)
     e. project.add_text_simple(secondary_text, start_us, duration_us, "Sub_Secondary", style=secondary_style)
5. project.save()
6. 返回统计信息
```

### 3.3 异常分类与兜底策略

| 异常场景 | 检测方式 | 策略 |
|---------|---------|------|
| `len(video_paths)` != 模板格数 | 入参校验 | `{"ok": False, "reason": "video_count_mismatch"}` |
| 模板 ID 非法 | 查表失败 | `{"ok": False, "reason": "unknown_template", "available": ["2H","2V","2x2","3x3","1+2"]}` |
| 轨道数超限 | 模板格数 > 10 | `{"ok": False, "reason": "track_limit_exceeded"}` 并提示降级为画中画 |
| 某视频时长不足 | segment 创建后检测实际 duration | 按 fill_mode: `loop` → 多次 add_clip 循环拼接; `freeze` → 截取尾帧并延长 (Phase 3 extract_freeze_frame 跨模块调用); `stretch` → 通过 `VideoSegment(speed=...)` 轻微减速(≤0.75x)拉伸至目标时长，超出减速范围则降级为 `loop` |
| `subtitle_pairs` 为空 | 入参校验 | `{"ok": False, "reason": "empty_input"}` |
| 某字幕 duration=0 | 逐条校验 | 自动设为 `min_duration=500ms`，不中断 |
| 某字幕文本为空 | 逐条校验 | 跳过该条，记录 `skipped_details`，不中断 |
| 两条轨道时间戳不一致 | 内部强制覆盖 | 以 primary 的 start_us 为准，覆盖 secondary |

---

## 4. 可观测性设计 (Observability)

### 4.1 LangFuse Trace 埋点

使用 **LangFuse SDK v3.x 原生 Trace API**。严禁使用 `langfuse.decorators`。

```python
from langfuse import Langfuse

langfuse = Langfuse(
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
)


def apply_split_screen(
    project_name: str,
    video_paths: list[str],
    template: str = "2x2",
    duration: str = "10s",
    fill_mode: str = "loop",
) -> dict:
    trace = langfuse.trace(
        name="apply_split_screen",
        metadata={
            "tool": "apply_split_screen",
            "phase": "1",
            "handler": "layout_ops",
            "capability_id": "V-04",
            "template": template,
        },
        input={
            "project_name": project_name,
            "template": template,
            "duration": duration,
            "video_count": len(video_paths),
            "fill_mode": fill_mode,
        },
    )

    try:
        # 模板校验 span
        tmpl_span = trace.span(
            name="apply_split_screen.template_validation",
            input={"template": template, "video_count": len(video_paths)},
        )

        layout = LAYOUT_TEMPLATES.get(template)
        if layout is None:
            tmpl_span.update(level="ERROR", status_message=f"Unknown template: {template}")
            tmpl_span.end()
            return {"ok": False, "reason": "unknown_template",
                    "available": list(LAYOUT_TEMPLATES.keys())}

        if len(video_paths) != layout.cell_count:
            tmpl_span.update(level="ERROR", status_message="Video count mismatch")
            tmpl_span.end()
            return {"ok": False, "reason": "video_count_mismatch",
                    "detail": f"template '{template}' requires {layout.cell_count} videos, got {len(video_paths)}"}

        tmpl_span.update(output={"valid": True})
        tmpl_span.end()

        # 执行 span
        exec_span = trace.span(
            name="apply_split_screen.execution",
            input={"template": template, "duration": duration},
        )

        project = JyProject(project_name, overwrite=True)
        segment_ids = []

        for i, (vpath, cell) in enumerate(zip(video_paths, layout.cells)):
            seg = project.add_media_safe(vpath, "0s", duration, f"SplitTrack_{i}")
            seg.clip_settings = ClipSettings(
                scale_x=cell.sx, scale_y=cell.sy,
                transform_x=cell.tx, transform_y=cell.ty,
            )
            segment_ids.append(getattr(seg, "segment_id", "unknown"))

        project.save()

        result = {
            "ok": True,
            "template": template,
            "layout": [c.to_dict() for c in layout.cells],
            "segment_ids": segment_ids,
            "duration": duration,
        }

        exec_span.update(output=result)
        exec_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        trace.update(level="ERROR", status_message=str(e))
        return {"ok": False, "reason": type(e).__name__, "detail": str(e)}


def add_dual_subtitles(
    project_name: str,
    subtitle_pairs: list[dict],
    primary_lang: str = "zh",
    secondary_lang: str = "en",
) -> dict:
    """Trace 规范同 apply_split_screen，metadata.capability_id = 'TX-06'"""
    trace = langfuse.trace(
        name="add_dual_subtitles",
        metadata={
            "tool": "add_dual_subtitles",
            "phase": "1",
            "handler": "subtitle_ops",
            "capability_id": "TX-06",
        },
        input={
            "project_name": project_name,
            "pair_count": len(subtitle_pairs),
            "primary_lang": primary_lang,
            "secondary_lang": secondary_lang,
        },
    )

    try:
        # ... 校验与执行逻辑 ...

        result = {
            "ok": True,
            "pair_count": len(subtitle_pairs),
            "primary_track": "Sub_Primary",
            "secondary_track": "Sub_Secondary",
            "skipped_pairs": skipped_count,
        }

        trace.update(output=result)
        return result

    except Exception as e:
        trace.update(level="ERROR", status_message=str(e))
        return {"ok": False, "reason": type(e).__name__, "detail": str(e)}
```

### 4.2 关键指标埋点

| 指标 | Span 名称 | 记录内容 |
|------|----------|---------|
| 分屏模板校验耗时 | `apply_split_screen.template_validation` | template ID, video_count |
| 分屏执行耗时 | `apply_split_screen.execution` | 各视频导入耗时 |
| 双字幕生成耗时 | `add_dual_subtitles.execution` | pair_count, 每条字幕的创建耗时分布 |
| 字幕跳过率 | `add_dual_subtitles.skipped` | skipped_pairs 数量及原因分布 |

---

## 5. 模块验收标准 (DoD)

### layout_ops

- [ ] `apply_split_screen` 支持全部 5 种模板 (`2H`, `2V`, `2x2`, `3x3`, `1+2`)
- [ ] 每个模板的布局坐标在 1920×1080 画布上视觉正确（手动抽查截图）
- [ ] 单元测试: 视频数量与模板不匹配 → 返回 `video_count_mismatch`
- [ ] 单元测试: 非法模板 → 返回 `unknown_template` 并给出可选列表
- [ ] 单元测试: 时长不足的视频按 fill_mode 正确填充
- [ ] Agent 调用后 LangFuse Trace 包含 `template_validation` + `execution` 两个 Span

### subtitle_ops

- [ ] `add_dual_subtitles` 创建两条独立轨道 (`Sub_Primary`, `Sub_Secondary`)
- [ ] 两条轨道的字幕时间严格对齐（误差 < 1ms）
- [ ] 主字幕在 Y=-0.75，副字幕在 Y=-0.85，字号/颜色有区分
- [ ] 单元测试: 空 subtitle_pairs → `empty_input`
- [ ] 单元测试: duration=0 → 自动补偿为 500ms
- [ ] 单元测试: 空文本 → 跳过并记录 skipped_details
- [ ] Agent 调用后 LangFuse Trace 记录 pair_count + skipped_pairs
