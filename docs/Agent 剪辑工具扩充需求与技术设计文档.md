# Agent 剪辑工具扩充需求与技术设计文档 (PRD & Technical Spec)

> **版本**: v1.0
> **日期**: 2026-06-03
> **关联文档**: [能力清单](./ability_list.md) | [覆盖度报告](./Agent%20剪辑能力覆盖度与架构补齐报告.md)
> **目标**: 将 Agent 剪辑能力覆盖率从 44% (20/45) 提升至 100% (45/45)

---

## 1. 演进路线图 (Roadmap)

### 1.1 三阶段总览

```
Phase 1 ─── 纯 API 编排层           Phase 2 ─── draft.json 注入层        Phase 3 ─── 外部原子工具层
(0 外部依赖, 1-2 天)                (需剪映 JSON 协议知识, 3-5 天)        (需系统级依赖, 5-10 天)
                                                                           
J-Cut / L-Cut      ──────────────►  遮罩转场                   ────────►  定格帧 (FFmpeg)
片段重排           ──────────────►  颜色过渡转场 (纯色注入)    ────────►  BGM 卡点 (librosa)
分屏效果           ──────────────►  逐字高亮字幕               ────────►  降噪/人声增强 (noisereduce)
多语言字幕         ──────────────►  动态字幕条滑入             ────────►  绿幕抠像 (FFmpeg chromakey)
缩放/推拉转场      ──────────────►  BGM 音量闪避               ────────►  运动跟踪 (OpenCV CSRT)
                                   音频变速 (speed 注入)        ────────►  光流运镜分析 (OpenCV Farneback)
                                                                          曲线变速 (Speed Ramp)

覆盖率: 44% → 60%                  覆盖率: 60% → 76%                  覆盖率: 76% → 93% (+7% 远期)
```

### 1.2 各阶段核心目标

| 阶段 | 核心目标 | 新增能力数 | 预计工时 | 风险等级 |
|------|---------|-----------|---------|---------|
| **Phase 1** | 在不新增任何底层能力的前提下，通过 Agent 组合编排现有 API/CLI 实现 7 项高频剪辑操作 | 7 | 1-2 天 | 低 — 纯逻辑编排，无新协议依赖 |
| **Phase 2** | 深入 Draft JSON 协议层，通过结构化注入实现 7 项需要直接操作工程文件的高级能力 | 7 | 3-5 天 | 中 — 需熟悉剪映 JSON schema，版本升级可能导致节点路径变化 |
| **Phase 3** | 引入 FFmpeg / OpenCV / librosa 等工业级音视频处理库，补齐剪映 API 完全不支持的 8 项能力（另有 3 项需 ASR/LLM 编排，列为 Phase 3b 远期规划） | 8 (+3) | 5-10 天 | 高 — 需要服务器/Docker 环境配置，大文件 I/O，GPU 可选加速 |

### 1.3 技术栈依赖矩阵

| 依赖项 | 版本要求 | Phase | 用途 | 部署位置 |
|--------|---------|-------|------|---------|
| **Python** | ≥ 3.10 | P1-P3 | 运行环境 | 服务器 / Docker |
| **pyJianYingDraft** | vendored | P1-P3 | 核心草稿引擎 | 后端服务 |
| **FFmpeg** | ≥ 5.0 (需 libx264, libvpx) | P3 | 截帧、降噪、抠像、音频后处理 | Docker 镜像 |
| **librosa** | ≥ 0.10 | P3 | 节拍检测 (BGM 卡点) | Docker 镜像 |
| **soundfile** | ≥ 0.12 | P3 | 音频文件读写 | Docker 镜像 |
| **noisereduce** | ≥ 3.0 | P3 | 频谱降噪 | Docker 镜像 |
| **OpenCV (cv2)** | ≥ 4.8 (含 opencv-contrib) | P3 | 目标跟踪 (CSRT)、光流分析 (Farneback) | Docker 镜像 |
| **numpy** | ≥ 1.24 | P3 | 数值计算（OpenCV/librosa 依赖） | Docker 镜像 |

**Docker 镜像参考 (Dockerfile 关键层)**:

```dockerfile
FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    librosa>=0.10 \
    soundfile>=0.12 \
    noisereduce>=3.0 \
    opencv-python>=4.8 \
    opencv-contrib-python>=4.8 \
    numpy>=1.24
```

---

## 2. 核心场景需求优先级

### 2.1 场景聚焦：短剧混剪 & 剧情类短视频

面向当前高热度的短剧混剪和剧情短视频赛道，从 25 项待补齐能力中筛选出 **P0 级需求**，判断标准：

- 该能力在单条短剧视频中的出现频率
- 该能力对观众留存率（完播率）的影响
- 该能力在手动剪辑中的时间占比

| P0 编号 | 功能名称 | 原始 ID | 补齐方案 | 判定理由 |
|---------|---------|--------|---------|---------|
| **P0-01** | J-Cut 声音先行 | T-01 | Phase 1 API 编排 | 短剧开场必备：先闻其声制造悬念，几乎 100% 使用率 |
| **P0-02** | L-Cut 画面先行 | T-02 | Phase 1 API 编排 | 对话场景高频：声音延续到下一个镜头，保持叙事流畅 |
| **P0-03** | 多语言字幕 | TX-06 | Phase 1 API 编排 | 出海短剧刚需：中英/中日双语字幕是分发到 TikTok/YouTube 的标配 |
| **P0-04** | 画面定格 | T-07 | Phase 3 FFmpeg 截帧 | 短剧"名场面"标配：高潮帧定格 2-3 秒+特效文字卡点 |
| **P0-05** | BGM 卡点 | A-01 | Phase 3 librosa | 混剪核心：画面切换踩在音乐鼓点上，决定视频节奏感的 80% |
| **P0-06** | 曲线变速 | T-06 | Phase 3 FFmpeg 运动分析 | 慢动作强调情绪+快放过渡。固定倍速可用 draft.json 注入 speed 字段（见 3.2 apply_audio_speed），但曲线变速（Speed Ramp）需要逐帧分析视频内容后生成非线性的 speed curve，属于 Phase 3 能力 |
| **P0-07** | BGM 音量闪避 | A-03 | Phase 2 volume KF | 有人声时 BGM 自动降低，确保台词清晰可辨 |
| **P0-08** | 颜色过渡转场 | TR-05 | Phase 2 纯色注入 | 情绪转场：黑场过渡表示时间流逝，白场过渡表示回忆闪回 |

### 2.2 P0 需求的完整 Agent 工作流

以下是一条典型短剧混剪视频（60 秒）的端到端 Agent 流水线，串联所有 P0 功能：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     Agent 短剧混剪 60s 全自动流水线                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  [INPUT] 原始视频素材 + BGM 音频 + 中英文字幕文本 + 定格时间点             │
│                                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │ Step 1    │───►│ Step 2    │───►│ Step 3    │───►│ Step 4    │         │
│  │ BGM 卡点  │    │ 素材粗剪  │    │ 曲线变速  │    │ J/L-Cut   │         │
│  │ (P0-05)   │    │ + 定格    │    │ (P0-06)   │    │ (P0-01/02)│         │
│  │ librosa   │    │ (P0-04)   │    │ speed KF  │    │ 时间偏移  │         │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘          │
│                                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │ Step 5    │───►│ Step 6    │───►│ Step 7    │───►│ Step 8    │         │
│  │ 颜色转场  │    │ BGM 闪避  │    │ 多语言字幕│    │ 导出 MP4  │         │
│  │ (P0-08)   │    │ (P0-07)   │    │ (P0-03)   │    │ 1080P 30fps│        │
│  │ 纯色注入  │    │ volume KF │    │ 双轨对齐  │    │ auto_export│        │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘          │
│                                                                         │
│  [OUTPUT] 60s 混剪视频:                                                  │
│  - 画面精准踩在 BGM 鼓点上                                               │
│  - 高潮帧定格 + 慢动作                                                   │
│  - J-Cut 开场制造悬念                                                    │
│  - 台词段 BGM 自动降低                                                   │
│  - 中英双语字幕                                                          │
│  - 情绪转折处黑场过渡                                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 功能详细设计 (Feature Specifications)

### 3.1 Phase 1 功能规格 (纯 API 编排)

| 字段 | 说明 |
|------|------|
| **功能名称** | J-Cut 声音先行 |
| **原始 ID** | T-01 |
| **技术实现路径** | **API 组合**：视频片段整体后移 offset_us，音频从 0 开始，利用轨道独立性实现"音频早于视频"的感知效果。剪映不支持负时间戳，因此通过视频后移来等效实现。 |
| **Agent Tool 函数签名** | `def apply_jcut(project_name: str, video_path: str, audio_path: str, audio_lead_us: int = 1_500_000, video_start: str = "0s", video_duration: str = "10s") -> dict` |
| **入参说明** | `project_name`: 草稿名称；`video_path`: 视频文件绝对路径；`audio_path`: 音频文件绝对路径；`audio_lead_us`: 音频提前量(微秒)，默认 1.5s；`video_start`: 视频素材起始时间；`video_duration`: 视频片段持续时长 |
| **出参** | `{"ok": True, "video_segment_id": "...", "audio_segment_id": "...", "video_actual_start_us": 1500000, "audio_start_us": 0}` |
| **异常与边界** | (1) `audio_lead_us <= 0` → 抛出 `ValueError("J-Cut requires positive audio_lead_us")`；(2) 视频文件不存在 → `FileNotFoundError`；(3) 时间线溢出 → 草稿保存前校验 target_timerange 不超过 24 小时上限；(4) 兜底策略：保存草稿后调用 `audit_timeline` 自检 |
| **功能名称** | L-Cut 画面先行 |
| **原始 ID** | T-02 |
| **技术实现路径** | **API 组合**：Step 1: 视频 A 正常从 0 开始放置；Step 2: 音频 A 的 duration 设为 `video_duration + audio_tail_us`，使其尾部延伸到视频 A 结束之后；Step 3: 视频 B 的 start_time 设为 `video_A_duration`（与音频 A 尾部重叠），画面已在播放下一个镜头的画面，但上一个镜头的音频仍在延续。这要求 Agent 在创建多段视频时，自动编排前后片段与音频的重叠区间。 |
| **Agent Tool 函数签名** | `def apply_lcut(project_name: str, video_path: str, audio_path: str, audio_tail_us: int = 1_500_000, video_start: str = "0s", video_duration: str = "10s") -> dict` |
| **入参说明** | `audio_tail_us`: 音频尾部延长量(微秒)，默认 1.5s；其余同 J-Cut |
| **出参** | `{"ok": True, "video_segment_id": "...", "audio_segment_id": "...", "audio_actual_duration_us": 11500000}` |
| **异常与边界** | (1) `audio_tail_us <= 0` → `ValueError`；(2) 音频源文件时长不足 → 捕获 `IndexError` / segment 创建失败，返回 `{"ok": False, "reason": "audio_source_too_short", "required_us": ..., "available_us": ...}`；(3) 兜底：若音频源不足，自动截取可用部分并以 `"truncated": True` 标记 |
| **功能名称** | 片段重排 |
| **原始 ID** | T-05 |
| **技术实现路径** | **API 组合 + 草稿重建**：读取现有 draft_content.json → 提取所有视频片段的 metadata（material 引用、source_timerange、target_timerange）→ 按新顺序清空重建草稿。这是一种 idempotent 的重建策略，避免直接修改 segment 数组可能引发的 ID 冲突。 |
| **Agent Tool 函数签名** | `def reorder_segments(project_name: str, new_order: list[int]) -> dict` |
| **入参说明** | `project_name`: 已有草稿名称；`new_order`: 新顺序索引列表，如 `[2, 0, 1, 3]` 表示原第 3 个片段移到第 1 位 |
| **出参** | `{"ok": True, "reordered_count": 4, "original_order": [0, 1, 2, 3], "new_order": [2, 0, 1, 3]}` |
| **异常与边界** | (1) `new_order` 索引越界 → `IndexError("index 5 out of range, only 4 segments available")`；(2) `new_order` 去重后长度不等于原片段数 → `ValueError("duplicate or missing indices")`；(3) 草稿不存 → `DraftNotFound`；(4) 兜底：重建前自动备份原 draft_content.json 为 `draft_content.json.bak.{timestamp}` |
| **功能名称** | 分屏效果 |
| **原始 ID** | V-04 |
| **技术实现路径** | **API 组合**：Agent 预先计算 N 宫格布局的每格坐标（scale_x, scale_y, transform_x, transform_y），将 N 段视频分配到 N 条独立 VideoTrack，各片段通过 `ClipSettings` 设置位置和缩放。布局计算由模板参数驱动，Agent 只需传入模板 ID。 |
| **Agent Tool 函数签名** | `def apply_split_screen(project_name: str, video_paths: list[str], template: str = "2x2", duration: str = "10s") -> dict` |
| **入参说明** | `video_paths`: N 段视频绝对路径列表；`template`: 布局模板，`"2H"`(左右)、`"2V"`(上下)、`"2x2"`(四宫格)、`"3x3"`(九宫格)、`"1+2"`(左侧大右侧两小)；`duration`: 分屏持续时长 |
| **出参** | `{"ok": True, "template": "2x2", "layout": [{"track": 0, "scale": (0.5,0.5), "pos": (-0.5, 0.5)}, ...], "segment_ids": ["...", ...]}` |
| **异常与边界** | (1) `len(video_paths)` 与模板格数不匹配 → `ValueError(f"template {t} requires {n} videos, got {len(paths)}")`；(2) 某视频时长不足 → 自动 `loop` 或 `freeze` 填充（由 `fill_mode` 参数控制）；(3) 轨道数量上限 → 剪映最大轨道数约 10，超出后提示降级为画中画嵌套 |
| **功能名称** | 多语言字幕 |
| **原始 ID** | TX-06 |
| **技术实现路径** | **API 组合**：创建两条独立 Subtitles 轨道 (`Sub_Primary`, `Sub_Secondary`)，主字幕在 Y=-0.75 位置，副字幕在 Y=-0.85，使用更小字号和灰色以区分。两条轨道的 TextSegment 使用完全相同的 start_time 和 duration，确保逐句对齐。 |
| **Agent Tool 函数签名** | `def add_dual_subtitles(project_name: str, subtitle_pairs: list[dict], primary_lang: str = "zh", secondary_lang: str = "en") -> dict` |
| **入参说明** | `subtitle_pairs`: `[{"start_us": int, "duration_us": int, "primary_text": str, "secondary_text": str}, ...]`；`primary_lang/secondary_lang`: 语言标签，用于日志和样式选择 |
| **出参** | `{"ok": True, "pair_count": 45, "primary_track": "Sub_Primary", "secondary_track": "Sub_Secondary"}` |
| **异常与边界** | (1) `subtitle_pairs` 为空 → `ValueError`；(2) 某条字幕 duration 为 0 → 自动设为 `min_duration=500ms`；(3) 字幕文本为空字符串 → 跳过该条并记录 warning；(4) 两条轨道时间戳不一致 → 内部强制以 primary 的时间戳为准覆盖 secondary |
| **功能名称** | 缩放转场 |
| **原始 ID** | TR-04 |
| **技术实现路径** | **API 组合**：前一片段尾部最后 N 微秒添加 scale 关键帧（1.0 → 1.5），后一片段开头前 N 微秒添加 scale 关键帧（1.5 → 1.0）。两段关键帧在时间上无缝衔接，产生"放大→切换→缩小"的连续视觉流。 |
| **Agent Tool 函数签名** | `def apply_zoom_transition(project_name: str, video_path_1: str, video_path_2: str, zoom_peak: float = 1.5, ramp_duration_us: int = 300_000) -> dict` |
| **入参说明** | `zoom_peak`: 最大缩放倍数，默认 1.5；`ramp_duration_us`: 缩放过渡时长(微秒)，默认 0.3s |
| **出参** | `{"ok": True, "seg1_id": "...", "seg2_id": "...", "seg1_end_scale": 1.5, "seg2_start_scale": 1.5}` |
| **异常与边界** | (1) `zoom_peak <= 1.0` → `ValueError("zoom_peak must be > 1.0 for meaningful transition")`；(2) `ramp_duration_us` 超过片段时长 → 自动缩减为片段时长的 20%；(3) 关键帧数量爆炸 → 每片段最多 2 帧，后续如需微调由 Agent 自行追加 |
| **功能名称** | 推拉转场 |
| **原始 ID** | TR-07 |
| **技术实现路径** | **API 组合**：前一片段尾部添加 position_x 关键帧（中心 → 左侧屏幕外），后一片段开头添加 position_x 关键帧（右侧屏幕外 → 中心）。运动方向由 `direction` 参数控制。 |
| **Agent Tool 函数签名** | `def apply_push_transition(project_name: str, video_path_1: str, video_path_2: str, direction: str = "left", ramp_duration_us: int = 300_000) -> dict` |
| **入参说明** | `direction`: `"left"`(推左)、`"right"`(推右)、`"up"`(推上)、`"down"`(推下)；`ramp_duration_us`: 过渡时长 |
| **出参** | `{"ok": True, "direction": "left", "seg1_end_pos": -1.0, "seg2_start_pos": 1.0}` |
| **异常与边界** | (1) 非法 direction → `ValueError`；(2) 同 TR-04 的边界保护；(3) Agent 可组合调用：先调用缩放转场再调用推拉转场，实现复合效果 |

### 3.2 Phase 2 功能规格 (draft.json 注入)

| 字段 | 说明 |
|------|------|
| **功能名称** | 遮罩转场 (Mask Wipe) |
| **原始 ID** | TR-02 |
| **技术实现路径** | **draft.json 注入**：为指定 VideoSegment 注入 `mask` 节点（线性蒙版 resource_id="636071"），并注入蒙版 `centerX` 的位置关键帧。关键帧 time_offset 从 0 到 segment.duration，values 从 -1.0 到 1.0（或反向），实现遮罩从左到右（或自定义方向）擦除的转场效果。 |
| **Agent Tool 函数签名** | `def inject_mask_transition(project_name: str, segment_id: str, direction: str = "left_to_right", feather: float = 0.1) -> dict` |
| **入参说明** | `project_name`: 草稿名称；`segment_id`: 目标 VideoSegment 的全局 ID；`direction`: `"left_to_right"` / `"right_to_left"` / `"top_to_bottom"` / `"bottom_to_top"`；`feather`: 蒙版羽化值 0.0-1.0 |
| **出参** | `{"ok": True, "mask_id": "...", "keyframes_count": 2, "direction": "left_to_right"}` |
| **异常与边界** | (1) `segment_id` 在 draft.json 中不存在 → `KeyError`，返回 `{"ok": False, "reason": "segment_not_found"}`；(2) 片段已存在 mask 节点 → 检测到已有 `"type": "mask"` 的 extra_material_ref → 抛出 `ValueError("segment already has a mask")`；(3) `feather` 越界 → clamp 到 [0.0, 1.0]；(4) 兜底: 注入后执行 JSON schema 校验，不合法的节点回滚到备份 |
| **功能名称** | 颜色过渡转场 |
| **原始 ID** | TR-05 |
| **技术实现路径** | **draft.json 注入**：在两段视频之间的缝隙注入一个纯色 material（type="color"），并创建对应的 video segment。同时为前后两个片段分别添加 fade_out / fade_in alpha 关键帧，实现 A → 纯色 → B 的三段式过渡。 |
| **Agent Tool 函数签名** | `def inject_color_transition(project_name: str, seg1_id: str, seg2_id: str, color: str = "#000000", duration_us: int = 500_000) -> dict` |
| **入参说明** | `seg1_id / seg2_id`: 前后两个 VideoSegment 的 ID；`color`: 纯色值 `#RRGGBB` 或 `#RRGGBBAA`；`duration_us`: 纯色片段持续时长(微秒) |
| **出参** | `{"ok": True, "color_segment_id": "...", "color_material_id": "...", "transition_duration_us": 500000}` |
| **异常与边界** | (1) seg1 和 seg2 之间没有间隙 → 自动将 seg2 向后偏移 duration_us；(2) 颜色格式非法 → `ValueError` 由正则 `^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$` 预校验；(3) color material 创建失败（JSON 节点冲突）→ 回滚所有修改；(4) 兜底：注入后 `project.save()` 全量保存 |
| **功能名称** | 逐字高亮字幕 (Karaoke) |
| **原始 ID** | TX-02 |
| **技术实现路径** | **draft.json 注入**：不采用"每字一个 TextSegment"的方案（会产生数百个 TextSegment 导致性能问题），改为：单条完整字幕 TextSegment + 按音节时间戳注入文字颜色渐变 keyframe 或逐字 char 级别的 material 数组。如果剪映 JSON 协议不支持逐字颜色，则降级为"每词一个 TextSegment"。 |
| **Agent Tool 函数签名** | `def add_karaoke_subtitle(project_name: str, text: str, char_timestamps: list[dict], track_name: str = "Karaoke", y_position: float = -0.75) -> dict` |
| **入参说明** | `text`: 完整字幕文本；`char_timestamps`: `[{"char": "你", "start_us": 0, "duration_us": 300000}, ...]`；`track_name`: 轨道名；`y_position`: Y 轴位置 |
| **出参** | `{"ok": True, "segment_count": 5, "total_chars": 5, "track_name": "Karaoke", "method": "per_char"}` |
| **异常与边界** | (1) `char_timestamps` 时间戳不连续 → 日志 warn 但继续执行；(2) 单字 duration=0 → 默认 200ms；(3) 超过 50 个字符 → 自动降级为每词模式（`method: "per_word"`），减少 segment 数量；(4) 兜底：若部分 char 创建失败，跳过并标记 `"failed_chars": ["..."]` |
| **功能名称** | 动态字幕条滑入 |
| **原始 ID** | TX-07 |
| **技术实现路径** | **draft.json 注入**：为 TextSegment 注入 `common_keyframes`，根据 `slide_from` 方向将距离转为正确的正负符号（`"left"` → start_value=-distance, `"right"` → start_value=+distance, `"top"` → KFTypePositionY start_value=+distance, `"bottom"` → KFTypePositionY start_value=-distance），首帧 time_offset=0 在屏幕外，末帧 time_offset=duration_us 在 values=[0.0]（目标位置）。不同方向使用不同的 property_type (`KFTypePositionX` 或 `KFTypePositionY`)。 |
| **Agent Tool 函数签名** | `def inject_subtitle_slide(project_name: str, segment_id: str, slide_from: str = "right", slide_distance: float = 1.5, duration_us: int = 500_000) -> dict` |
| **入参说明** | `segment_id`: TextSegment ID；`slide_from`: 滑入方向 `"left"` / `"right"` / `"top"` / `"bottom"`；`slide_distance`: 滑行距离(归一化坐标)；`duration_us`: 动画时长 |
| **出参** | `{"ok": True, "keyframe_list_id": "...", "keyframes": [{"time_offset": 0, "value": -1.5}, {"time_offset": 500000, "value": 0.0}]}` |
| **异常与边界** | (1) segment 不是 text 类型 → 读取 segment type 字段后 `ValueError`；(2) 已有 KFTypePositionX → 追加而非覆盖，双关键帧共存可能导致抖动，因此检测到后以 `"overwritten": True` 标记并替换；(3) `slide_distance` 过大导致字幕飞过目标 → 添加 ease-out 曲线 (curveType: "EaseOut") |
| **功能名称** | BGM 音量闪避 (Ducking) |
| **原始 ID** | A-03 |
| **技术实现路径** | **draft.json 注入**：读取 draft_content.json → 定位 BGM 轨道 → 为每个 BGM segment 注入 volume 关键帧。关键帧在 voice_segments 时间窗口内将 volume 降至 DUCK_VOLUME (0.2)，在窗口前后 FADE_US (0.3s) 内线性过渡。 |
| **Agent Tool 函数签名** | `def apply_bgm_ducking(project_name: str, voice_segments: list[dict], bgm_track_name: str = "BGM", duck_volume: float = 0.2, fade_us: int = 300_000) -> dict` |
| **入参说明** | `voice_segments`: `[{"start_us": int, "end_us": int}, ...]` 人声时间区间列表；`bgm_track_name`: BGM 轨道名；`duck_volume`: 闪避时音量 (0.0-1.0)；`fade_us`: 渐弱/渐强过渡时长 |
| **出参** | `{"ok": True, "bgm_track": "BGM", "duck_windows": 5, "duck_volume": 0.2, "keyframes_generated": 20}` |
| **异常与边界** | (1) BGM 轨道不存在 → `KeyError(f"track '{bgm_track_name}' not found")`；(2) voice_segments 时间窗口重叠 → 自动合并重叠区间；(3) BGM 时长短于人声区间 → 仅覆盖 BGM 存在的时间；(4) 兜底: 注入完成后校验 keyframe_list 的 time_offset 单调递增，避免播放时跳跃 |
| **功能名称** | 音频变速 |
| **原始 ID** | A-08 |
| **技术实现路径** | **draft.json 注入**：读取 draft_content.json → 找到目标 AudioSegment → 注入 `speed` 字段 → 同步调整 `source_timerange.duration = target_timerange.duration * speed`。注意：speed > 1.0 时 source 需要更多内容，须确保 source 时长足够。 |
| **Agent Tool 函数签名** | `def apply_audio_speed(project_name: str, track_name: str, segment_index: int, speed: float) -> dict` |
| **入参说明** | `track_name`: 音频轨道名；`segment_index`: 片段在轨道中的索引(0-based)；`speed`: 倍速，2.0 = 2倍速，0.5 = 半速 |
| **出参** | `{"ok": True, "segment_id": "...", "speed": 2.0, "original_duration_us": 5000000, "new_target_duration_us": 2500000}` |
| **异常与边界** | (1) `speed <= 0` → `ValueError`；(2) `segment_index` 越界 → `IndexError`；(3) source 时长不足 → 自动 clamp speed 到 source_duration / target_duration 的上限；(4) 变速后 target_timerange 变化可能影响后续片段位置 → 自动偏移同轨道后续 segment 的 start 时间；(5) 兜底: 修改前备份 JSON |

### 3.3 Phase 2 关键架构约束：draft.json 注入后必须调用 JyProject.save()

**问题**：Phase 2 的所有 draft_injector 函数直接读写 `draft_content.json` 文件，但绕过了
`JyProject.save()` 的两个关键后处理步骤：

1. `_patch_cloud_material_ids()` — 为云端 BGM/音效注入 `music_id` 和 `type: "music"` 标记，缺失则云端音乐无法渲染
2. `_force_activate_adjustments()` — 强制激活色彩调整（brightness/contrast/saturation）关键帧，缺失则关键帧不生效

**强制规范**：每个 Phase 2 工具在写入 JSON 后，必须通过以下方式之一完成收尾：

```python
# 方案 A（推荐）: 重新加载 JyProject 实例并调用 save()
project = JyProject(project_name)  # 加载已有草稿
project.save()  # 触发 _patch_cloud_material_ids + _force_activate_adjustments

# 方案 B（性能优化）: 仅调用必要的后处理（需传入 drafts_root）
from utils.env_setup import get_default_drafts_root
root = get_default_drafts_root()
project._patch_cloud_material_ids()
project._force_activate_adjustments()
```

**适用工具**: `inject_mask_transition`, `inject_color_transition`, `add_karaoke_subtitle`,
`inject_subtitle_slide`, `apply_bgm_ducking`, `apply_audio_speed`

---

## 4. 外部原子工具集成方案 (Phase 3)

### 4.1 设计原则

Phase 3 的工具与 Phase 1/2 有本质区别：它们不是操作剪映 JSON，而是**处理原始音视频文件**，产出一个新文件后再导入剪映。这决定了以下架构原则：

1. **输入输出隔离**：外部工具操作独立的临时文件，不直接接触 draft.json
2. **幂等性**：同名输入 + 同参数 → 复用缓存结果（`__jycache__/` 目录）
3. **资源上限**：所有子进程受 timeout 和内存限制（默认 120s / 2GB）
4. **错误可观测**：每个子进程调用的 stdout/stderr 写入日志，供 LangFuse Trace 采样

### 4.2 子进程安全调用规范

所有外部命令行工具（FFmpeg、OpenCV 脚本）统一通过 `SubprocessExecutor` 封装调用：

```python
import subprocess
import resource
import os
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class SubprocessResult:
    """子进程执行结果"""
    returncode: int
    stdout: str
    stderr: str
    output_path: Optional[str] = None
    cache_hit: bool = False

@dataclass
class SubprocessConfig:
    """子进程执行约束"""
    timeout_seconds: int = 120
    memory_limit_mb: int = 2048
    cache_dir: str = "__jycache__"

class SubprocessExecutor:
    """安全的子进程执行器，供 Agent tool 调用"""

    def __init__(self, config: SubprocessConfig = SubprocessConfig()):
        self.config = config
        os.makedirs(config.cache_dir, exist_ok=True)

    def _get_cache_path(self, input_path: str, params: dict) -> str:
        """基于输入文件 hash + 参数生成缓存路径"""
        import hashlib, json
        key = hashlib.md5(
            (input_path + json.dumps(params, sort_keys=True)).encode()
        ).hexdigest()
        return os.path.join(self.config.cache_dir, key)

    def run(
        self,
        cmd: list[str],
        output_path: Optional[str] = None,
        cache_params: Optional[dict] = None,
        timeout: Optional[int] = None,
    ) -> SubprocessResult:
        """
        安全执行子进程命令。

        Args:
            cmd: 命令行参数列表，如 ["ffmpeg", "-i", "in.mp4", "out.mp4"]
            output_path: 预期输出文件路径（用于缓存校验）
            cache_params: 缓存键参数（用于复用相同转换）
            timeout: 超时秒数，覆盖 config 默认值

        Returns:
            SubprocessResult

        Raises:
            subprocess.TimeoutExpired: 子进程超时
            RuntimeError: 返回码非零
        """
        # 检查缓存
        if output_path and cache_params:
            cached = self._get_cache_path(cache_params.get("input", ""), cache_params)
            if os.path.exists(cached):
                # 将缓存文件复制到用户期望的输出路径
                import shutil
                shutil.copy2(cached, output_path)
                return SubprocessResult(0, "", "", output_path, cache_hit=True)

        t = timeout or self.config.timeout_seconds

        def set_limits():
            limit = self.config.memory_limit_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=t,
            preexec_fn=set_limits if os.name != "nt" else None,
        )

        result = SubprocessResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            output_path=output_path,
        )

        if proc.returncode != 0:
            raise RuntimeError(
                f"Subprocess failed (exit {proc.returncode}): {' '.join(cmd)}\n"
                f"stderr: {proc.stderr[-500:]}"
            )

        # 写入缓存
        if output_path and os.path.exists(output_path) and cache_params:
            cached = self._get_cache_path(cache_params.get("input", ""), cache_params)
            import shutil
            shutil.copy2(output_path, cached)

        return result

# 全局单例
_executor = SubprocessExecutor()
```

### 4.3 各外部工具 Agent Tool 定义

---

#### 4.3.1 定格帧 (Freeze Frame)

```python
import os
import uuid
from pathlib import Path
from typing import Optional

def extract_freeze_frame(
    video_path: str,
    timestamp_s: float,
    output_dir: str = "__jycache__",
    duration_s: float = 3.0,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> dict:
    """
    从视频指定时间戳截取单帧，生成长度为 duration_s 的静态图片视频。

    实现：FFmpeg -vframes 1 + loop + t

    Args:
        video_path: 源视频绝对路径
        timestamp_s: 截帧时间点（秒）
        output_dir: 输出目录
        duration_s: 定格持续时长（秒）
        width/height: 输出分辨率，None 则保持原分辨率

    Returns:
        {"ok": True, "output_path": "...", "timestamp_s": 5.0, "duration_s": 3.0}

    Raises:
        FileNotFoundError: 源视频不存在
        RuntimeError: FFmpeg 执行失败
        ValueError: timestamp_s 超过视频总时长
    """
    if not Path(video_path).exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    # 先探测视频时长
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", video_path
    ]
    probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
    import json
    info = json.loads(probe.stdout)
    video_duration = float(info["format"]["duration"])

    if timestamp_s > video_duration:
        raise ValueError(
            f"timestamp_s ({timestamp_s}s) exceeds video duration ({video_duration}s)"
        )

    output_name = (
        f"freeze_{Path(video_path).stem}_{int(timestamp_s*1000)}ms_{int(duration_s)}s.mp4"
    )
    output_path = os.path.join(output_dir, output_name)
    frame_path = os.path.join(output_dir, f"_freeze_frame_{uuid.uuid4().hex}.png")

    # Step 1: 截取单帧为 PNG 图片
    cmd1 = [
        "ffmpeg", "-y",
        "-ss", str(timestamp_s),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        frame_path,
    ]
    _executor.run(cmd1, output_path=frame_path)

    # Step 2: 将图片转为指定时长的视频 (loop + trim 两步法)
    vf_parts = [f"loop=-1:1:0,trim=duration={duration_s},setpts=N/FRAME_RATE/TB"]
    if width and height:
        vf_parts.insert(0, f"scale={width}:{height}")

    cmd2 = [
        "ffmpeg", "-y",
        "-i", frame_path,
        "-vf", ",".join(vf_parts),
        "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-an",
        output_path,
    ]

    result = _executor.run(
        cmd2,
        output_path=output_path,
        cache_params={"input": video_path, "ts": timestamp_s, "dur": duration_s, "w": width, "h": height},
    )

    # 清理临时帧文件
    try:
        os.remove(frame_path)
    except OSError:
        pass

    return {
        "ok": True,
        "output_path": output_path,
        "timestamp_s": timestamp_s,
        "duration_s": duration_s,
        "cache_hit": result.cache_hit,
    }
```

---

#### 4.3.2 BGM 卡点 (Beat Detection)

```python
def detect_beats(
    audio_path: str,
    sr: int = 22050,
    tightness: float = 1.0,
) -> dict:
    """
    检测音频节拍时间点，用于 BGM 卡点编辑。

    实现：librosa.beat.beat_track + 动态规划后处理。

    Args:
        audio_path: 音频文件绝对路径
        sr: 采样率（librosa 默认 22050）
        tightness: 节拍密度系数，>1.0 增加检测到的节拍数量，<1.0 减少

    Returns:
        {
            "ok": True,
            "bpm": 128.0,
            "beat_times": [0.0, 0.468, 0.936, 1.404, ...],
            "beat_frames": [0, 10, 20, 30, ...],
            "total_beats": 128,
            "duration_s": 60.0
        }
    """
    import librosa

    y, sr_actual = librosa.load(audio_path, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr_actual, tightness=tightness)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr_actual).tolist()

    return {
        "ok": True,
        "bpm": float(tempo) if not hasattr(tempo, '__len__') else float(tempo[0]),
        "beat_times": beat_times,
        "beat_frames": beat_frames.tolist(),
        "total_beats": len(beat_times),
        "duration_s": len(y) / sr_actual,
    }


def apply_beat_sync_cut(
    project_name: str,
    video_path: str,
    bpm: float,
    beat_times: list[float],
    beats_per_clip: int = 2,
) -> dict:
    """
    按节拍时间点分割视频并生成卡点草稿。

    每个视频片段的起点对齐一个节拍，持续 beats_per_clip 个节拍的时长。

    Args:
        project_name: 草稿名称
        video_path: 源视频路径
        bpm: BPM 值
        beat_times: 节拍时间点列表
        beats_per_clip: 每个片段覆盖的节拍数

    Returns:
        {"ok": True, "clip_count": 30, "total_duration_s": 60.0}
    """
    from jy_wrapper import JyProject

    project = JyProject(project_name, overwrite=True)
    clip_count = 0

    for i in range(0, len(beat_times) - beats_per_clip, beats_per_clip):
        start = beat_times[i]
        end = beat_times[min(i + beats_per_clip, len(beat_times) - 1)]
        clip_dur = end - start
        if clip_dur < 0.1:
            continue

        seg = project.add_clip(
            media_path=video_path,
            source_start=f"{start}s",
            duration=f"{clip_dur}s",
        )
        # 每个片段添加轻微的缩放入场
        # 注意: add_keyframe 第一个参数为 KeyframeProperty 枚举，非字符串
        from pyJianYingDraft import KeyframeProperty
        seg.add_keyframe(KeyframeProperty.uniform_scale, 0, 1.05)
        seg.add_keyframe(KeyframeProperty.uniform_scale, 100_000, 1.0)
        clip_count += 1

    project.save()
    return {
        "ok": True,
        "clip_count": clip_count,
        "total_duration_s": beat_times[-1] if beat_times else 0,
    }
```

---

#### 4.3.3 人声增强 + 降噪

```python
def denoise_audio(
    input_path: str,
    output_path: Optional[str] = None,
    method: str = "noisereduce",
    highpass_hz: int = 80,
    lowpass_hz: int = 8000,
) -> dict:
    """
    对音频文件进行降噪和人声增强处理。

    两种方案：
    - "noisereduce": Python 频谱减法，精准但慢（适合 <= 5 分钟）
    - "ffmpeg": FFmpeg 高通+低通+动态降噪，快速但粗糙（适合长音频）

    Args:
        input_path: 原始音频路径
        output_path: 输出路径，None 则自动生成
        method: "noisereduce" 或 "ffmpeg"
        highpass_hz: 高通截止频率
        lowpass_hz: 低通截止频率

    Returns:
        {"ok": True, "output_path": "...", "method": "noisereduce"}
    """
    if output_path is None:
        base = os.path.splitext(input_path)[0]
        output_path = f"{base}_denoised.wav"

    if method == "noisereduce":
        import noisereduce as nr
        import soundfile as sf

        data, rate = sf.read(input_path)
        # 单声道处理以加速
        if data.ndim > 1:
            data_mono = data.mean(axis=1)
        else:
            data_mono = data

        reduced = nr.reduce_noise(
            y=data_mono,
            sr=rate,
            prop_decrease=0.9,
            n_fft=2048,
            hop_length=512,
        )

        sf.write(output_path, reduced, rate)

    elif method == "ffmpeg":
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-af", (
                f"highpass=f={highpass_hz},"
                f"lowpass=f={lowpass_hz},"
                f"afftdn=nr=10:nf=-25,"
                f"loudnorm=I=-16:LRA=11:TP=-1.5"
            ),
            output_path,
        ]
        _executor.run(cmd, output_path=output_path)

    else:
        raise ValueError(f"Unknown method: {method}, use 'noisereduce' or 'ffmpeg'")

    return {
        "ok": True,
        "output_path": output_path,
        "method": method,
    }
```

---

#### 4.3.4 绿幕抠像 (Chroma Key)

```python
def chroma_key_remove(
    input_path: str,
    output_path: Optional[str] = None,
    color_hex: str = "00FF00",
    similarity: float = 0.3,
    blend: float = 0.1,
) -> dict:
    """
    使用 FFmpeg chromakey 滤镜去除绿幕背景。

    注意：剪映不支持带 alpha 通道的 MP4。
    因此输出使用 libvpx-vp9 编码的 WEBM 以保留透明度，
    或输出带黑色背景的 MP4（当 blend=0 时）。

    Args:
        input_path: 绿幕素材路径
        output_path: 输出路径，None 则自动生成
        color_hex: 要抠除的颜色 (RRGGBB)
        similarity: 颜色相似度阈值 (0.01-1.0)
        blend: 边缘混合强度 (0.0-1.0)

    Returns:
        {"ok": True, "output_path": "...", "format": "webm"}
    """
    if output_path is None:
        base = os.path.splitext(input_path)[0]
        output_path = f"{base}_keyed.webm"

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", (
            f"chromakey=0x{color_hex}:{similarity}:{blend}"
        ),
        "-c:v", "libvpx-vp9",
        "-pix_fmt", "yuva420p",
        "-auto-alt-ref", "0",
        output_path,
    ]

    _executor.run(cmd, output_path=output_path)

    return {
        "ok": True,
        "output_path": output_path,
        "format": "webm",
        "color": f"#{color_hex}",
        "similarity": similarity,
    }
```

---

#### 4.3.5 目标跟踪 (Object Tracking)

```python
def track_object(
    video_path: str,
    roi: tuple[float, float, float, float],
    sample_fps: int = 5,
    tracker_type: str = "CSRT",
) -> dict:
    """
    跟踪视频中的目标物体，返回归一化轨迹坐标。

    使用 OpenCV CSRT 跟踪器（精度最高，速度较慢），
    或 KCF 跟踪器（速度快，精度较低）。

    Args:
        video_path: 视频文件路径
        roi: 初始目标区域 (x, y, w, h)，像素坐标
        sample_fps: 采样帧率（降低以减少输出数据量）
        tracker_type: "CSRT" 或 "KCF"

    Returns:
        {
            "ok": True,
            "trajectory": [
                {"frame": 0,   "time_us": 0,        "cx": 0.5, "cy": 0.3, "w": 0.1, "h": 0.1},
                {"frame": 5,   "time_us": 166667,    "cx": 0.52, "cy": 0.31, "w": 0.1, "h": 0.1},
                ...
            ],
            "tracker": "CSRT",
            "fps": 30.0,
            "total_frames": 300
        }
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)

    # 初始化跟踪器
    if tracker_type == "CSRT":
        tracker = cv2.TrackerCSRT_create()
    elif tracker_type == "KCF":
        tracker = cv2.TrackerKCF_create()
    else:
        cap.release()
        raise ValueError(f"Unknown tracker: {tracker_type}")

    ret, frame = cap.read()
    if not ret:
        cap.release()
        raise RuntimeError("Failed to read first frame")

    roi_int = tuple(map(int, roi))  # (x, y, w, h)
    tracker.init(frame, roi_int)

    trajectory = []
    sample_interval = max(1, int(fps / sample_fps))

    for i in range(0, total_frames, sample_interval):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if not ret:
            break

        success, box = tracker.update(frame)
        if success:
            t_us = int(i / fps * 1_000_000)
            trajectory.append({
                "frame": i,
                "time_us": t_us,
                "cx": (box[0] + box[2] / 2) / width,
                "cy": (box[1] + box[3] / 2) / height,
                "w": box[2] / width,
                "h": box[3] / height,
            })

    cap.release()

    return {
        "ok": True,
        "trajectory": trajectory,
        "tracker": tracker_type,
        "fps": fps,
        "total_frames": total_frames,
    }


def apply_text_tracking(
    project_name: str,
    segment_id: str,
    trajectory: list[dict],
) -> dict:
    """
    将跟踪轨迹注入为 TextSegment 的位置关键帧。

    Args:
        project_name: 草稿名称
        segment_id: 目标 TextSegment ID
        trajectory: track_object() 返回的 trajectory 列表

    Returns:
        {"ok": True, "keyframes_applied": 60}
    """
    import json, uuid
    from jy_wrapper import JyProject

    # 通过 JyProject 实例获取正确的草稿路径（而非直接读环境变量）
    project = JyProject(project_name)
    content_path = os.path.join(project.root, project.name, "draft_content.json")

    with open(content_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    applied = 0
    for track in data.get("tracks", []):
        for seg in track.get("segments", []):
            if seg.get("id") == segment_id:
                pos_x_kf = []
                pos_y_kf = []

                for pt in trajectory:
                    pos_x_kf.append({
                        "id": uuid.uuid4().hex,
                        "time_offset": pt["time_us"],
                        "values": [pt["cx"] * 2 - 1],  # 归一化 → 剪映坐标
                        "curveType": "Line",
                    })
                    pos_y_kf.append({
                        "id": uuid.uuid4().hex,
                        "time_offset": pt["time_us"],
                        "values": [1 - pt["cy"] * 2],
                        "curveType": "Line",
                    })

                seg.setdefault("common_keyframes", []).extend([
                    {
                        "id": uuid.uuid4().hex,
                        "property_type": "KFTypePositionX",
                        "material_id": seg.get("material_id", ""),
                        "keyframe_list": pos_x_kf,
                    },
                    {
                        "id": uuid.uuid4().hex,
                        "property_type": "KFTypePositionY",
                        "material_id": seg.get("material_id", ""),
                        "keyframe_list": pos_y_kf,
                    },
                ])
                applied = len(trajectory)
                break

    with open(content_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    return {"ok": True, "keyframes_applied": applied}
```

---

#### 4.3.6 光流运镜分析 (Optical Flow)

```python
def analyze_motion(
    video_path: str,
    sample_fps: int = 2,
    head_seconds: float = 2.0,
    tail_seconds: float = 2.0,
) -> dict:
    """
    分析视频片段的运动方向和幅度，用于无缝运镜拼接。

    使用 OpenCV Farneback 光流算法，计算画面主导运动方向。

    Args:
        video_path: 视频文件路径
        sample_fps: 光流采样帧率
        head_seconds: 分析开头几秒（用于判断"入点"运动方向）
        tail_seconds: 分析结尾几秒（用于判断"出点"运动方向）

    Returns:
        {
            "ok": True,
            "head_motion": {"direction": "right", "magnitude": 3.5},
            "tail_motion": {"direction": "left",  "magnitude": 2.8},
            "recommendation": "compatible"  # head 和 tail 方向是否适合拼接
        }
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    video_dur = total_frames / fps

    sample_interval = max(1, int(fps / sample_fps))

    def _analyze_segment(start_frame: int, end_frame: int) -> dict:
        prev_gray = None
        motions = []

        for fi in range(start_frame, min(end_frame, total_frames), sample_interval):
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ret, frame = cap.read()
            if not ret:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # 缩放到 320px 宽以加速
            scale = 320 / gray.shape[1]
            gray_small = cv2.resize(gray, (320, int(gray.shape[0] * scale)))

            if prev_gray is not None:
                flow = cv2.calcOpticalFlowFarneback(
                    prev_gray, gray_small, None,
                    0.5, 3, 15, 3, 5, 1.2, 0
                )
                motions.append({
                    "dx": float(flow[..., 0].mean()),
                    "dy": float(flow[..., 1].mean()),
                })

            prev_gray = gray_small

        if not motions:
            return {"direction": "static", "magnitude": 0.0}

        avg_dx = sum(m["dx"] for m in motions) / len(motions)
        avg_dy = sum(m["dy"] for m in motions) / len(motions)
        mag = np.sqrt(avg_dx**2 + avg_dy**2)

        if mag < 1.0:
            direction = "static"
        elif abs(avg_dx) > abs(avg_dy):
            direction = "right" if avg_dx > 0 else "left"
        else:
            direction = "down" if avg_dy > 0 else "up"

        return {"direction": direction, "magnitude": round(mag, 2)}

    head_frames = int(head_seconds * fps)
    tail_frames = max(0, total_frames - int(tail_seconds * fps))

    head_motion = _analyze_segment(0, head_frames)
    tail_motion = _analyze_segment(tail_frames, total_frames)

    cap.release()

    # 判断兼容性：相同方向 → 可拼接，相反方向 → 不推荐
    compatible = (
        head_motion["direction"] == "static"
        or tail_motion["direction"] == "static"
        or head_motion["direction"] == tail_motion["direction"]
    )

    return {
        "ok": True,
        "head_motion": head_motion,
        "tail_motion": tail_motion,
        "recommendation": "compatible" if compatible else "incompatible",
    }
```

---

#### 4.3.7 曲线变速 (Speed Ramp)

```python
def apply_speed_ramp(
    project_name: str,
    segment_id: str,
    speed_curve: list[dict],
) -> dict:
    """
    为视频片段注入曲线变速（非固定倍速）。

    剪映 draft.json 中的 speed curve 通过 source_timerange 与
    target_timerange 的映射关系实现。每个 curve point 定义了一个
    映射对：(source_time, target_time, speed)。

    Args:
        project_name: 草稿名称
        segment_id: 目标 VideoSegment ID
        speed_curve: 速度曲线定义列表
            [
                {"target_time_us": 0,        "speed": 0.5},   # 慢动作
                {"target_time_us": 1000000,  "speed": 1.0},   # 正常
                {"target_time_us": 1500000,  "speed": 2.0},   # 加速
                {"target_time_us": 2000000,  "speed": 1.0},   # 恢复正常
            ]

    Returns:
        {"ok": True, "curve_points": 4, "duration_us": 2000000}

    使用限制:
        - speed 范围: 0.1 ~ 10.0 (剪映客户端限制)
        - 需要配合 FFmpeg 分析视频内容以确定最佳变速点（如高潮段慢放）
        - 注入后需调用 JyProject.save() 确保 draft.json 完整性
    """
    import json, uuid, os

    from jy_wrapper import JyProject
    project = JyProject(project_name)
    content_path = os.path.join(project.root, project.name, "draft_content.json")

    with open(content_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for track in data.get("tracks", []):
        for seg in track.get("segments", []):
            if seg.get("id") == segment_id:
                # 构建 speed curve 节点
                curve_id = uuid.uuid4().hex
                seg["speed"] = speed_curve[-1]["speed"]  # 名义速度
                seg.setdefault("common_keyframes", []).append({
                    "id": curve_id,
                    "property_type": "KFTypeSpeed",
                    "material_id": seg.get("material_id", ""),
                    "keyframe_list": [
                        {
                            "id": uuid.uuid4().hex,
                            "time_offset": pt["target_time_us"],
                            "values": [pt["speed"]],
                            "curveType": "Line",
                        }
                        for pt in speed_curve
                    ],
                })
                break

    with open(content_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    project.save()  # 触发后处理管线
    return {
        "ok": True,
        "curve_points": len(speed_curve),
        "duration_us": speed_curve[-1]["target_time_us"],
    }
```

---

### 4.4 Phase 3b — 远期规划 (依赖 ASR/LLM 编排)

以下 3 项能力需要 ASR 语音识别 + LLM 语义匹配，超出了纯音视频处理的范畴，
列为 Phase 3b 远期规划：

| 能力 ID | 名称 | 核心技术栈 | 预估工时 |
|---------|------|-----------|---------|
| T-09 | 多机位对齐 | Whisper ASR + 音频波形交叉相关 | 5 天 |
| T-10 | B-Roll 智能叠加 | ASR 转写 → LLM 语义匹配 → 素材库检索 | 7 天 |
| V-02/V-09 | 蒙版打码 + 贴纸跟踪 | Phase 2 mask 注入 + Phase 3 OpenCV 跟踪 | 已部分覆盖 |

---

## 附录

### A. Agent Tool 注册清单

所有新增工具需在 `tool_registry.py` 中注册，供 LangChain Agent 调用：

```python
# 新增 Tool 注册项 (tool_registry.py 扩充)

PHASE1_TOOLS = [
    ToolDef(name="apply_jcut",              handler="timeline_ops",   category="write"),
    ToolDef(name="apply_lcut",              handler="timeline_ops",   category="write"),
    ToolDef(name="reorder_segments",        handler="timeline_ops",   category="write"),
    ToolDef(name="apply_split_screen",      handler="layout_ops",     category="write"),
    ToolDef(name="add_dual_subtitles",      handler="subtitle_ops",   category="write"),
    ToolDef(name="apply_zoom_transition",   handler="transition_ops", category="write"),
    ToolDef(name="apply_push_transition",   handler="transition_ops", category="write"),
]

PHASE2_TOOLS = [
    ToolDef(name="inject_mask_transition",  handler="draft_injector", category="write"),
    ToolDef(name="inject_color_transition", handler="draft_injector", category="write"),
    ToolDef(name="add_karaoke_subtitle",    handler="draft_injector", category="write"),
    ToolDef(name="inject_subtitle_slide",   handler="draft_injector", category="write"),
    ToolDef(name="apply_bgm_ducking",       handler="draft_injector", category="write"),
    ToolDef(name="apply_audio_speed",       handler="draft_injector", category="write"),
]

PHASE3_TOOLS = [
    ToolDef(name="extract_freeze_frame",    handler="external_tools", category="write"),
    ToolDef(name="detect_beats",            handler="external_tools", category="read"),
    ToolDef(name="apply_beat_sync_cut",     handler="external_tools", category="write"),
    ToolDef(name="denoise_audio",           handler="external_tools", category="write"),
    ToolDef(name="chroma_key_remove",       handler="external_tools", category="write"),
    ToolDef(name="track_object",            handler="external_tools", category="read"),
    ToolDef(name="apply_text_tracking",     handler="external_tools", category="write"),
    ToolDef(name="analyze_motion",          handler="external_tools", category="read"),
    ToolDef(name="apply_speed_ramp",        handler="external_tools", category="write"),
]

# Phase 3b 远期规划 (依赖 ASR + LLM 语义匹配，暂不注册)
PHASE3B_DEFERRED = {
    "T-09_multicam_sync":  "多机位对齐 — Whisper ASR + 音频波形交叉相关",
    "T-10_broll_overlay":  "B-Roll 智能叠加 — ASR 转写 → LLM 语义匹配 → 素材库检索",
}
```

### B. 验收标准

| 阶段 | 验收条件 |
|------|---------|
| Phase 1 | 每个 Tool 的单元测试覆盖 happy path + 3 个边界 case；Agent ReAct 循环中能正确调用并返回 `{"ok": True}` |
| Phase 2 | draft.json 注入前/后的 JSON schema 校验通过；注入节点在剪映客户端中正常渲染不报错；修改前后有 .bak 备份 |
| Phase 3 | FFmpeg/librosa/OpenCV 在 Docker 中可用；子进程超时/内存限制生效；缓存命中率 > 60%；大文件（>500MB）不 OOM；Phase 3b 远期 3 项待 ASR/LLM 基础设施就绪后启动 |

### C. 文件结构规划

```
backend/app/agent/skills_agent/
├── tools/
│   ├── __init__.py
│   ├── timeline_ops.py        # Phase 1: J-Cut, L-Cut, reorder
│   ├── layout_ops.py          # Phase 1: split_screen
│   ├── subtitle_ops.py        # Phase 1: dual_subtitles
│   ├── transition_ops.py      # Phase 1: zoom/push transitions
│   ├── draft_injector.py      # Phase 2: mask, color, karaoke, slide, ducking, audio_speed
│   ├── external_tools.py      # Phase 3: freeze_frame, beats, denoise, chromakey, tracking, motion, speed_ramp
│   ├── subprocess_executor.py # Phase 3: 公共子进程安全执行器
│   └── phase3b_planning.md    # Phase 3b 远期: 多机位对齐, B-Roll 智能叠加 (ASR+LLM)
```
