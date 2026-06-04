# draft_injector — 草稿 JSON 注入模块需求文档

> **父文档**: [Agent 剪辑工具扩充 PRD](../Agent%20剪辑工具扩充需求与技术设计文档.md)
> **Phase**: 2 (draft.json 注入)
> **目标文件**: `backend/app/agent/skills_agent/tools/draft_injector.py`

---

## 1. 模块归属与前置依赖

| 项目 | 说明 |
|------|------|
| **所属 Phase** | Phase 2 — draft.json 注入层 |
| **对应 Python 文件** | `tools/draft_injector.py` |
| **注册 Handler** | `draft_injector` |
| **核心依赖** | `pyJianYingDraft` (vendored), `jy_wrapper.JyProject`, `json`, `uuid`, `os` |
| **运行时环境** | Python ≥ 3.10，无需 Docker/GPU |
| **关键约束** | **注入后必须调用 `JyProject.save()`** 以触发后处理管线。本模块所有 Tool 加载已有草稿 (`overwrite=False`)，如果草稿尚不由 Agent 创建则 `JyProject()` 会新建空草稿 -- 此时 segment_id 必然找不到，会返回 `segment_not_found` |
| **预计工时** | 3-5 天 |

### 注册的 Agent Tools

| Tool 名称 | 类别 | 对应功能 ID | 注入方向 |
|-----------|------|-------------|---------|
| `inject_mask_transition` | write | TR-02 遮罩转场 | mask 节点 + centerX 关键帧 |
| `inject_color_transition` | write | TR-05 颜色过渡转场 | color material + color segment |
| `add_karaoke_subtitle` | write | TX-02 逐字高亮字幕 | 逐字/逐词 TextSegment |
| `inject_subtitle_slide` | write | TX-07 动态字幕条滑入 | KFTypePositionX/Y 关键帧 |
| `apply_bgm_ducking` | write | A-03 BGM 音量闪避 | KFTypeVolume 关键帧 |
| `apply_audio_speed` | write | A-08 音频变速 | speed 字段 + source_timerange |
| `apply_speed_ramp` | write | T-06 曲线变速 | KFTypeSpeed keyframe_list (⚠️ 直接注入 JSON 节点，不通过 KeyframeProperty API) |

---

## 2. 接口定义与 Agent Tool 封装

### 2.1 inject_mask_transition

```python
def inject_mask_transition(
    project_name: str,
    segment_id: str,
    direction: str = "left_to_right",
    feather: float = 0.1,
) -> dict:
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 已有草稿名称 |
| `segment_id` | `str` | 是 | — | 目标 VideoSegment 全局 ID |
| `direction` | `str` | 否 | `"left_to_right"` | `"left_to_right"` / `"right_to_left"` / `"top_to_bottom"` / `"bottom_to_top"` |
| `feather` | `float` | 否 | `0.1` | 蒙版羽化 0.0-1.0，越界自动 clamp |

**出参**: `{"ok": True, "mask_id": "...", "keyframes_count": 2, "direction": "left_to_right"}`

### 2.2 inject_color_transition

```python
def inject_color_transition(
    project_name: str,
    seg1_id: str,
    seg2_id: str,
    color: str = "#000000",
    duration_us: int = 500_000,
) -> dict:
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 已有草稿名称 |
| `seg1_id` | `str` | 是 | — | 前段 VideoSegment ID |
| `seg2_id` | `str` | 是 | — | 后段 VideoSegment ID |
| `color` | `str` | 否 | `"#000000"` | 纯色值，正则 `^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$` |
| `duration_us` | `int` | 否 | `500_000` | 纯色片段持续时长(微秒) |

**出参**: `{"ok": True, "color_segment_id": "...", "color_material_id": "...", "transition_duration_us": 500000}`

### 2.3 add_karaoke_subtitle

```python
def add_karaoke_subtitle(
    project_name: str,
    text: str,
    char_timestamps: list[dict],
    track_name: str = "Karaoke",
    y_position: float = -0.75,
) -> dict:
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 已有草稿名称 |
| `text` | `str` | 是 | — | 完整字幕文本（用于日志） |
| `char_timestamps` | `list[dict]` | 是 | — | `[{"char": "你", "start_us": 0, "duration_us": 300000}, ...]` |
| `track_name` | `str` | 否 | `"Karaoke"` | 字幕轨道名 |
| `y_position` | `float` | 否 | `-0.75` | Y 轴位置 |

**出参**: `{"ok": True, "segment_count": 5, "total_chars": 5, "method": "per_char"}`

> **降级策略**: 当 `len(char_timestamps) > 50` 时自动降级为 `method: "per_word"`（每词一个 TextSegment）。

### 2.4 inject_subtitle_slide

```python
def inject_subtitle_slide(
    project_name: str,
    segment_id: str,
    slide_from: str = "right",
    slide_distance: float = 1.5,
    duration_us: int = 500_000,
) -> dict:
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 已有草稿名称 |
| `segment_id` | `str` | 是 | — | 目标 TextSegment ID |
| `slide_from` | `str` | 否 | `"right"` | 方向: `"left"` / `"right"` / `"top"` / `"bottom"` |
| `slide_distance` | `float` | 否 | `1.5` | 滑行距离(归一化坐标) |
| `duration_us` | `int` | 否 | `500_000` | 动画时长(微秒) |

**方向映射表**:

| slide_from | PropertyType | start_value | end_value |
|-----------|--------------|-------------|-----------|
| `"right"` | `KFTypePositionX` | `+slide_distance` | `0.0` |
| `"left"` | `KFTypePositionX` | `-slide_distance` | `0.0` |
| `"top"` | `KFTypePositionY` | `+slide_distance` | `0.0` |
| `"bottom"` | `KFTypePositionY` | `-slide_distance` | `0.0` |

### 2.5 apply_bgm_ducking

```python
def apply_bgm_ducking(
    project_name: str,
    voice_segments: list[dict],
    bgm_track_name: str = "BGM",
    duck_volume: float = 0.2,
    fade_us: int = 300_000,
) -> dict:
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 已有草稿名称 |
| `voice_segments` | `list[dict]` | 是 | — | `[{"start_us": int, "end_us": int}, ...]` |
| `bgm_track_name` | `str` | 否 | `"BGM"` | BGM 轨道名 |
| `duck_volume` | `float` | 否 | `0.2` | 闪避时音量 |
| `fade_us` | `int` | 否 | `300_000` | 渐变过渡时长(微秒) |

**出参**: `{"ok": True, "bgm_track": "BGM", "duck_windows": 5, "duck_volume": 0.2, "keyframes_generated": 20}`

### 2.6 apply_audio_speed

```python
def apply_audio_speed(
    project_name: str,
    track_name: str,
    segment_index: int,
    speed: float,
) -> dict:
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `project_name` | `str` | 是 | — | 已有草稿名称 |
| `track_name` | `str` | 是 | — | 音频轨道名 |
| `segment_index` | `int` | 是 | — | 片段在轨道中的索引(0-based) |
| `speed` | `float` | 是 | — | 倍速，2.0=2倍速，0.5=半速，范围 0.1-10.0 |

**出参**: `{"ok": True, "segment_id": "...", "speed": 2.0, "original_duration_us": 5000000, "new_target_duration_us": 2500000}`

---

## 3. 核心执行逻辑与异常边界

### 3.1 通用注入流程 (所有 6 个 Tool 共同遵循)

```
1. project = JyProject(project_name) — 加载已有草稿获得 root 路径
2. 备份: copy draft_content.json → draft_content.json.bak.{timestamp}
3. 读取 draft_content.json
4. 定位目标 tracks / segments 节点
5. 注入新节点 (mask / color_material / keyframes / speed 等)
6. 覆盖写入 draft_content.json
7. ⚠️ 必须调用 project.save() — 触发 _patch_cloud_material_ids + _force_activate_adjustments
8. 返回结果
```

> **⚠️ 架构底线**: 跳过 Step 7 将导致云端音乐引用失效、色彩关键帧不生效。
> 参见[父文档 3.3 节](../Agent%20剪辑工具扩充需求与技术设计文档.md#33-phase-2-关键架构约束draftjson-注入后必须调用-jyprojectsave)。

### 3.2 各 Tool 的执行差异

| Tool | 注入目标 | 定位方式 | 关键节点 |
|------|---------|---------|---------|
| `inject_mask_transition` | VideoSegment 的 `mask` 字段 + `extra_material_refs` | 遍历 tracks → 匹配 segment_id | mask_id, centerX keyframes |
| `inject_color_transition` | `materials.videos[]` + 新 track segment | 匹配 seg1_id/seg2_id，在二者之间插入 | ⚠️ color material 结构为推测性设计，需在剪映 5.x/6.x 中验证 JSON schema |
| `add_karaoke_subtitle` | 多条 TextSegment 到指定 track | 创建新 track 或追加到已有 track | 逐个 char/word 创建 TextSegment |
| `inject_subtitle_slide` | TextSegment 的 `common_keyframes` | 匹配 segment_id | KFTypePositionX/Y keyframe_list |
| `apply_bgm_ducking` | BGM track 所有 segment 的 `common_keyframes` | 匹配 track.name == bgm_track_name | KFTypeVolume keyframe_list |
| `apply_audio_speed` | AudioSegment 的 `speed` + `source_timerange` | 匹配 track.name + segment_index | speed 值, source_timerange.duration 调整 |
| `apply_speed_ramp` | VideoSegment 的 `common_keyframes` (KFTypeSpeed) | 匹配 segment_id | ⚠️ pyJianYingDraft 的 KeyframeProperty 枚举无 speed 属性，需直接注入 JSON 节点，归入 Phase 2 |

### 3.3 异常分类与兜底策略

| 异常场景 | 检测方式 | 策略 |
|---------|---------|------|
| `segment_id` 在 JSON 中不存在 | 遍历所有 tracks 未匹配 | `{"ok": False, "reason": "segment_not_found"}` |
| 片段已存在 mask 节点 | 检查 extra_material_refs 中是否有 `"type": "mask"` | `{"ok": False, "reason": "mask_already_exists"}` |
| `feather` 越界 | 入参校验 | clamp 到 [0.0, 1.0]，记录 `"feather_clamped": True` |
| `color` 格式非法 | 正则 `^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$` | `{"ok": False, "reason": "invalid_color_format"}` |
| seg1/seg2 之间无间隙 | target_timerange 比对 | 自动将 seg2 及后续 segment 向后偏移 duration_us |
| color material 创建 ID 冲突 | 随机 uuid 碰撞 (概率极低) | 重试 3 次，仍失败则返回 error |
| `char_timestamps` 超过 50 字符 | 入参校验 | 降级为 `method: "per_word"`，按空格/标点分组 |
| `slide_from` 非法 | 查表失败 | `{"ok": False, "reason": "invalid_slide_direction"}` |
| TextSegment 已有同名 KF | 检测 `common_keyframes[].property_type` | 替换旧值并标记 `"overwritten": True` |
| BGM 轨道不存在 | 遍历 tracks 未匹配 name | `{"ok": False, "reason": "track_not_found", "track": bgm_track_name}` |
| voice_segments 时间窗口重叠 | 遍历排序后比对 | 自动合并重叠区间 |
| `speed` 越界 (≤0 或 >10) | 入参校验 | `{"ok": False, "reason": "invalid_speed"}` |
| source_timerange 时长不足 | `source_duration < target_duration * speed` | 自动 clamp speed 到 `source_duration / target_duration` |
| JSON 写入后节点非法 | Schema 校验 | 回滚到 `.bak.{timestamp}` 备份 |
| 草稿不存在 | `JyProject()` 加载失败 | `DraftNotFound` |

---

## 4. 可观测性设计 (Observability)

### 4.1 LangFuse Trace 埋点

```python
import json
import os
import uuid
import shutil
from datetime import datetime
from langfuse import Langfuse

langfuse = Langfuse(
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
)


def _backup_and_load(project_name: str) -> tuple["JyProject", dict]:
    """通用: 备份 JSON 并加载，返回 (project, data)"""
    from jy_wrapper import JyProject

    project = JyProject(project_name)
    content_path = os.path.join(project.root, project.name, "draft_content.json")

    # 备份
    bak_path = f"{content_path}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(content_path, bak_path)

    with open(content_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return project, data, content_path, bak_path


def _save_and_finalize(project: "JyProject", data: dict, content_path: str) -> None:
    """通用: 写入 JSON + 调用 save() 后处理管线"""
    with open(content_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # ⚠️ 必须调用: 触发 _patch_cloud_material_ids + _force_activate_adjustments
    project.save()


def inject_mask_transition(
    project_name: str,
    segment_id: str,
    direction: str = "left_to_right",
    feather: float = 0.1,
) -> dict:
    trace = langfuse.trace(
        name="inject_mask_transition",
        metadata={
            "tool": "inject_mask_transition",
            "phase": "2",
            "handler": "draft_injector",
            "capability_id": "TR-02",
            "injection_type": "mask",
        },
        input={
            "project_name": project_name,
            "segment_id": segment_id,
            "direction": direction,
            "feather": feather,
        },
    )

    try:
        # 校验 + 准备 span
        prep_span = trace.span(
            name="inject_mask_transition.prepare",
            input={"segment_id": segment_id},
        )

        feather = max(0.0, min(1.0, feather))
        project, data, content_path, bak_path = _backup_and_load(project_name)

        # 查找目标 segment
        target_seg = None
        for track in data.get("tracks", []):
            for seg in track.get("segments", []):
                if seg.get("id") == segment_id:
                    target_seg = seg
                    break
            if target_seg:
                break

        if target_seg is None:
            prep_span.update(level="ERROR", status_message="segment_not_found")
            prep_span.end()
            return {"ok": False, "reason": "segment_not_found",
                    "detail": f"segment_id '{segment_id}' not found in draft"}

        # 检查已有 mask
        for ref in target_seg.get("extra_material_refs", []):
            if isinstance(ref, dict) and ref.get("type") == "mask":
                prep_span.update(level="WARNING", status_message="mask_already_exists")
                prep_span.end()
                return {"ok": False, "reason": "mask_already_exists"}

        prep_span.update(output={"segment_found": True})
        prep_span.end()

        # 注入 span
        inject_span = trace.span(
            name="inject_mask_transition.inject",
            input={"direction": direction, "feather": feather},
        )

        mask_id = uuid.uuid4().hex
        dur = target_seg["target_timerange"]["duration"]

        mask_node = {
            "id": mask_id,
            "name": "线性",
            "type": "mask",
            "resource_type": "mask_type",
            "resource_id": "636071",
            "platform": "all",
            "position_info": "",
            "config": {
                "centerX": 0.0, "centerY": 0.0,
                "width": 1.0, "height": 1.0,
                "rotation": 0.0, "feather": feather,
                "invert": False, "roundCorner": 0.0,
                "aspectRatio": 1.0,
            },
        }

        # 方向 → centerX 关键帧映射
        DIR_KF_MAP = {
            "left_to_right":  (-1.0, 1.0),
            "right_to_left":  (1.0, -1.0),
            "top_to_bottom":  (1.0, -1.0),  # centerY
            "bottom_to_top":  (-1.0, 1.0),  # centerY
        }
        start_val, end_val = DIR_KF_MAP.get(direction, (-1.0, 1.0))

        target_seg.setdefault("extra_material_refs", []).append(mask_id)
        target_seg["mask"] = mask_node
        # 注入蒙版关键帧到 common_keyframes
        target_seg.setdefault("common_keyframes", []).append({
            "id": uuid.uuid4().hex,
            "property_type": "KFTypeMaskCenterX",
            "material_id": mask_id,
            "keyframe_list": [
                {"id": uuid.uuid4().hex, "time_offset": 0, "values": [start_val], "curveType": "Line"},
                {"id": uuid.uuid4().hex, "time_offset": dur, "values": [end_val], "curveType": "Line"},
            ],
        })

        _save_and_finalize(project, data, content_path)

        result = {"ok": True, "mask_id": mask_id, "keyframes_count": 2, "direction": direction}

        inject_span.update(output=result)
        inject_span.end()
        trace.update(output=result)
        return result

    except Exception as e:
        # 回滚
        if 'bak_path' in locals() and os.path.exists(bak_path):
            shutil.copy2(bak_path, content_path)
        trace.update(level="ERROR", status_message=f"{type(e).__name__}: {str(e)}")
        return {"ok": False, "reason": type(e).__name__, "detail": str(e)}


# inject_color_transition, add_karaoke_subtitle, inject_subtitle_slide,
# apply_bgm_ducking, apply_audio_speed 均遵循相同的 Trace 埋点模式：
#   1. langfuse.trace(name=func_name, metadata={...}, input={...})
#   2. trace.span(name=f"{func_name}.prepare")
#   3. trace.span(name=f"{func_name}.inject")
#   4. 异常时回滚到 .bak 并 trace.update(level="ERROR")
```

### 4.2 注入安全指标

| 指标 | Span 名称 | 记录内容 |
|------|----------|---------|
| JSON 备份大小 | `{tool}.prepare` | `bak_size_bytes` |
| 注入耗时 | `{tool}.inject` | 定位耗时 + 写入耗时 |
| 节点是否已存在 (冲突) | `{tool}.prepare` | `"mask_already_exists"`, `"kf_overwritten"` 等 |
| 回滚次数 | 异常分支 | `"rollback": True`, `bak_path` |

---

## 5. 模块验收标准 (DoD)

- [ ] 6 个 Tool 在 `tool_registry.py` 中正确注册
- [ ] **每个 Tool 的注入操作后必然调用 `JyProject.save()`**（code review 红线）
- [ ] **注入前必然创建 `.bak.{timestamp}` 备份**
- [ ] **异常时自动回滚到备份文件**
- [ ] `inject_mask_transition`: 支持 4 方向；已有 mask 时返回 `mask_already_exists`
- [ ] `inject_color_transition`: seg1/seg2 无间隙时自动偏移；color 格式校验；⚠️ 纯色 material 的 JSON 结构需在剪映 5.x/6.x 中实测确认后才视为正式通过
- [ ] `add_karaoke_subtitle`: >50 字符自动降级为 `per_word`；空 char 跳过
- [ ] `inject_subtitle_slide`: 4 方向正确映射 property_type + 正负号
- [ ] `apply_bgm_ducking`: 重叠区间合并；BGM 轨道不存在时返回 `track_not_found`
- [ ] `apply_audio_speed`: speed 越界拒绝；source 不足时自动 clamp
- [ ] 注入后的 draft_content.json 在剪映客户端中正常渲染不报错
- [ ] 单元测试覆盖率 ≥ 80%
- [ ] LangFuse Trace 记录注入前后的 JSON 节点变化量 (delta)
