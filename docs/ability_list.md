# EasyScene AI Agent - 剪映 API/CLI 能力清单

> 基于 jianying-editor-skill v1.5.0 + skills_agent 集成层
> 核心库: pyJianYingDraft (vendored)

---

## 一、项目/草稿生命周期

| 能力 | 说明 |
|---|---|
| 创建/加载剪映草稿 | 新建或打开已有项目，自定义分辨率（默认1920×1080） |
| 保存草稿 | 写入 JSON 变更，自动修补云端素材 ID，触发 UI 刷新 |
| 损坏草稿自动清理+重建 | 检测到缺失 JSON 文件时自动删除损坏目录并重建 |
| 自动释放项目锁 | PermissionError 时自动切换剪映页面以释放文件锁 |
| 加载已有草稿作为模板 | 通过 `duplicate_as_template` 基于已有草稿创建新项目 |
| 草稿列表/查看 | 列出所有本地草稿，查看草稿摘要或完整 JSON |
| 获取轨道时长 | 返回指定轨道的 timeline 结束位置（微秒精度） |
| 时间线审计 | 检测同一素材+同一源起始位置(source_start)重复超过 5 次的异常 |

---

## 二、素材导入与轨道管理

| 能力 | 说明 |
|---|---|
| 导入视频到轨道 | 自动识别扩展名，WEBM 自动转 MP4，自动解析分辨率 |
| 导入音频到轨道 | 自动查找非重叠轨道名，冲突时追加 `_1`、`_2` 后缀 |
| 导入云端素材 | 按 ID 或名称搜索云端数据库，自动下载后导入 |
| 导入云端音乐 | 智能云端音乐导入，本地缓存优先，fallback 到 mock + ID 注入 |
| 从指定偏移裁剪素材 | 裁取视频的特定片段放置到时间线指定位置 |
| 自动创建轨道 | 轨道不存在时自动创建 |
| 多轨道叠加 | 支持 VideoTrack、AudioTrack、BGM、Subtitles、EffectTrack 等 |

---

## 三、文字 / 字幕 / TTS（文本转语音）

| 能力 | 说明 |
|---|---|
| 添加基础文字 | 自定义字体、颜色、描边、背景、阴影、位置、动画 |
| 添加花字（云端特效文字）| 支持 `effectStyle` 花字样式 ID（TextEffect） |
| 添加文字气泡 | 气泡文字效果（TextBubble），支持 effect_id + resource_id |
| TTS 语音生成 | SAMI（字节跳动 WebSocket）和 Edge-TTS 双后端，自动 fallback |
| 旁白字幕自动对齐 | 按标点分词 → TTS 生成 → 字幕时长精确对齐音频 |
| 智能分句配音 | 长文本自动按标点分割为短句逐句配音 |
| 批量导出 SRT | 从草稿中提取字幕导出 SRT 字幕文件 |

---

## 四、特效与转场（视觉/人物/音频）

| 能力 | 说明 |
|---|---|
| 添加视频场景特效 | **639+** 种 VideoSceneEffect（复古DV、故障、赛博朋克、全息扫描、彩虹光、烟花、雪花等） |
| 添加人物特效 | **海量** VideoCharacterEffectType（BOOM！、大头、分身、火焰翅膀、机械姬、流光描边等） |
| 添加音频场景特效 | AudioSceneEffectType（8bit、低保真、合成器、回音、电话音、混响等） |
| 语音转歌曲 | SpeechToSongType 元数据支持 |
| 添加滤镜 | **500+** 种 FilterType（赛博朋克、富士蓝、青橙电影、日系奶油、黑白等） |
| 添加转场 | **437+** 种 TransitionType（叠化、模糊、雾化、翻页、立方体、故障、百叶窗等） |
| 文字入场动画 | **153** 种 TextIntro（复古打字机、卡拉OK、弹入、渐显等） |
| 文字出场动画 | TextOutro（渐隐、弹出等） |
| 文字循环动画 | TextLoopAnim（闪烁、跳动等） |
| 视频入场动画 | **156** 种 IntroType（动感放大、旋转开幕、渐显、抖动变焦等） |
| 视频出场动画 | **142** 种 OutroType（渐隐、旋转闭幕、放大、飘散等） |
| 同义词/模糊匹配 | 所有特效名称支持中英文同义词模糊解析（如 "glitch" → "故障"） |

---

## 五、关键帧动画

| 能力 | 说明 |
|---|---|
| 缩放关键帧 | `uniform_scale` / `scale_x` / `scale_y` |
| 位置关键帧 | `position_x` / `position_y`（画中画移动） |
| 旋转关键帧 | `rotation` |
| 透明度关键帧 | `alpha`（淡入淡出） |
| 色彩关键帧 | `saturation`、`contrast`、`brightness` |
| 音量关键帧 | `volume` |
| 自动激活调整关键帧 | 保存时自动修补 JSON 使关键帧生效 |

---

## 六、蒙版与画中画

| 能力 | 说明 |
|---|---|
| 线性蒙版 | MaskType.线性 |
| 圆形蒙版 | MaskType.圆形 |
| 爱心蒙版 | MaskType.爱心 |
| 镜面蒙版等 | 多种蒙版类型 |
| 画中画（PIP） | 多轨道叠加 + 位置/缩放关键帧 |
| ClipSettings 调节 | alpha、flip、rotation、scale、position、brightness、contrast、saturation、crop |
| 背景填充 | `add_background_filling("blur"\|"color")` 模糊/纯色背景，4 档模糊(0.0625/0.375/0.75/1.0) |
| 贴纸叠加 | `StickerSegment` 贴纸片段，支持 resource_id + ClipSettings |
| 音频淡入淡出 | `add_fade(in_duration, out_duration)` 为视频片段添加音频淡入淡出 |
| 变速控制 | 素材变速（speed control） |

---

## 七、智能缩放（Smart Zoom）

| 能力 | 说明 |
|---|---|
| 录制鼠标点击/移动事件 | 10 FPS 采样，>5px 阈值 |
| 根据事件自动添加缩放关键帧 | 点击位置自适应缩放，移动时摄像机跟随 |
| 按住时长自动延长 | 按住期间持续放大 |
| 自动缩放还原 | 事件结束后自动回到原始缩放级别 |
| 会话分组 | 同组连续事件归并为一个缩放片段 |

---

## 八、AI 驱动的智能编辑

| 能力 | 说明 |
|---|---|
| 智能粗剪 | AI（Gemini-3-Pro）分析视频 → 识别高光片段 → 自动生成带剪辑点+字幕的草稿 |
| 电影解说视频生成 | 加载 AI 生成的 storyboard JSON → 视频切片 → 字幕分割 → 双轨高光 → BGM/蒙版可选 |
| 视频转写+匹配 | AI 转写视频 → 匹配 B-roll 素材 → 自动组装 |
| AI 视频分析模板 | 提供 Gemini API 视频分析的标准 prompt 模板 |
| 元素全量重建 | 重建草稿中所有元素（all_elements_regen） |

---

## 九、HTML/Web 动画录制

| 能力 | 说明 |
|---|---|
| HTML → 视频 | Playwright Chromium 启动 → 录制 HTML/JS 动画 → 输出 WEBM/MP4 |
| 动画完成信号 | 等待 `window.animationFinished` JS 信号 |
| 支持 CDN 库 | GSAP、Three.js、Chart.js、D3.js、Lottie、p5.js 等 |
| 录制超时控制 | 默认最长 30 秒 |
| README → 教程视频 | 将 README.md 自动转为分步教程视频（Web录制+TTS+字幕） |

---

## 十、导出

| 能力 | 说明 |
|---|---|
| 无头导出为 MP4 | 通过 uiautomation 控制剪映自动导出 |
| 多分辨率 | 480P / 720P / 1080P / 2K / 4K / 8K |
| 多帧率 | 24 / 25 / 30 / 50 / 60 fps |
| 鲁棒导出 | 带失败重试机制的稳定导出流程 |

---

## 十一、屏幕录制（Windows GUI 工具）

| 能力 | 说明 |
|---|---|
| 全屏录制 | ffmpeg gdigrab 全屏捕获 |
| 系统音频捕获 | DirectShow 音频采集 |
| 鼠标/键盘事件捕获 | 归一化坐标 + 点击/按键事件记录 |
| 录制悬浮窗 UI | 迷你红点浮窗，点击停止 |
| 录制后自动生成草稿 | 自动调用 Smart Zoom 生成带缩放的草稿 |
| 窗口位置记忆 | recorder_config.json 保存 UI 位置 |

---

## 十二、资产管理

| 能力 | 说明 |
|---|---|
| 云端音乐搜索/下载 | 按名称/ID 搜索，URL 安全验证，大小限制（默认512MB） |
| 本地 BGM 同步 | 从剪映 app 缓存同步收藏/播放过的 BGM |
| 云端音效搜索 | 搜索云端音效库 |
| 云端视频素材搜索 | 搜索云端视频素材库 |
| 花字样式库构建 | 扫描本地草稿构建花字样式索引 |
| 素材库构建 | 扫描所有草稿构建音乐/音效 CSV 索引 |
| 素材资源搜索 | 关键字跨数据库搜索（特效/转场/动画/TTS 发音人），双语同义词扩展 |

---

## 十三、TTS 发音人

| 能力 | 说明 |
|---|---|
| SAMI 后端 | 字节跳动 WebSocket TTS API，支持所有剪映发音人 ID |
| Edge-TTS 后端 | 微软 Edge TTS 作为 fallback |
| 自动鉴权 | 从剪映本地配置文件读取 device_id 和 iid |
| 指数退避重试 | SAMI 失败最多重试 3 次 |
| **168 个发音人** | 中文+多语言，带分类标签 |

---

## 十四、诊断与验证

| 能力 | 说明 |
|---|---|
| 环境诊断 | 检测 ffprobe、视频文件、草稿创建、素材导入、文字添加、保存全流程 |
| 代码语法验证 | 执行前预检查 JyProject 代码 |
| 仓库卫生检查 | 检测 `__pycache__`、`.pyc`、`cloud_cache` 文件是否被 git 追踪 |
| 数据 Schema 校验 | 验证所有 CSV 数据库文件列结构完整性 |

---

## 十五、Agent 集成层工具（LangChain Tools）

| 工具名 | 类别 | 说明 |
|---|---|---|
| `load_skill` | read | 按名称加载完整 skill 内容 |
| `resolve_media` | read | 将文件名解析为完整路径 |
| `list_media` | read | 列出所有可用素材文件 |
| `execute_cli_script` | compute | 执行 CLI 脚本（asset_search、auto_exporter 等 10 个） |
| `list_cli_scripts` | read | 列出所有可用 CLI 脚本 |
| `execute_jyproject_code` | write | 执行 JyProject Python 代码 |
| `validate_jyproject_code` | read | 验证 JyProject 代码语法 |
| `submit_storyboard` | write | 提交 storyboard JSON 进行批量执行 |

---

## 汇总统计

| 类别 | 数量 |
|---|---|
| Python API 方法 | 30+ |
| CLI 脚本 | 13 |
| GUI 工具 | 1 |
| CSV 数据库 | 11 |
| 视频场景特效 | 639+ |
| 人物特效 | 海量 |
| 音频场景特效 | 多种 |
| 滤镜 | 500+ |
| 转场 | 437+ |
| 文字入场动画 | 153 |
| 视频入场动画 | 156 |
| 视频出场动画 | 142 |
| TTS 发音人 | 168 |
| LangChain Agent 工具 | 8 |
| 参考规则文档 | 11 |
| 示例工作流 | 10 |

---

## 环境变量配置

| 变量 | 默认值 | 用途 |
|---|---|---|
| `JY_SKILL_ROOT` | 自动检测 | Skill 根路径 |
| `JY_LOG_LEVEL` | INFO | 日志级别 |
| `JY_CLOUD_MAX_MB` | 512 | 云端下载最大大小(MB) |
| `JY_TTS_INSECURE_SSL` | 0 | 禁用 TTS WebSocket TLS 验证 |
| `JY_PROJECTS_ROOT` | 自动检测 | 剪映项目根目录覆盖 |

---

## 平台限制

- **导出/屏幕录制**: 仅 Windows（需要剪映客户端 + uiautomation）
- **草稿生成**: 跨平台（仅操作 JSON，不依赖剪映客户端）
- **支持的剪映版本**: 5.x（模板模式）、6.x（导出），导出仅支持 v6 及以下
