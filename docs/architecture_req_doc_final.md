# 自动剪辑 Agent 架构升级需求文档 (PRD Final v2.0)

> 基于 Claude Code 源码审计 x JianYing Editor Agent 实际代码库的交叉分析
> 版本: 2.0 | 日期: 2026-06-01
>
> **修订说明**: 本版本基于对 `backend/app/agent/skills_agent/` 和 `backend/jianying-editor-skill/` 的逐文件扫描，修正了 v1.0 中与实际代码状态不符的假设，重新校准了优先级和实施范围。

---

## 一、现状诊断：与实际代码对齐的瓶颈清单

### 1.1 代码库的现实状况（已实现的 6 个需求）

以下能力已通过 req_01~req_06 实现，**不应**在本 PRD 中重复规划：

| 已实现 | 文件 | 核心能力 |
|--------|------|---------|
| req_01 | `timeout_config.py`, `media_normalizer.py`, `cli_executor.py`, `python_executor.py` | `truncate_output()` 字节级截断、`subprocess.run(timeout=...)` 全覆盖 |
| req_02 | `retry_config.py`, `cli_executor.py`, `python_executor.py`, `universal_tts.py`, `media_normalizer.py` | tenacity 指数退避重试、RETRYABLE_RETURN_CODES 白名单、fallback 降级 |
| req_03 | `token_utils.py`, `jianying_agent.py` | `estimate_tokens()`/`calculate_context_budget()`、`_generate_skills_prompt(token_budget=...)` 分级截断、`_skills_prompt_cache` 缓存、`GUIDE_TEMPLATE` 固定文本纳入预算 |
| req_04 | `python_executor.py` | `summarize_output()` 智能摘要、`save_full_output()` 日志持久化、条件性 temp_file 清理 |
| req_05 | `observability.py`, `jianying_agent.py`, `cli_executor.py` | Langfuse v3 懒初始化优雅降级、`CallbackHandler` 自动埋点、手动 span（`trace_id` 参数） |
| req_06 | `process_utils.py`, `media_normalizer.py` | `run_with_timeout()` Popen+communicate、`_kill_process_tree()` 跨平台（taskkill/killpg） |

### 1.2 关键基础设施已就绪

**Agent 与 AI 层**:
- **jy_wrapper.py** → 封装了完整的 `vendor/pyJianYingDraft/` 库（~30 个 .py 文件），提供 `JyProject` 高级 API
- **dashscope==1.25.15** → qwen3.6 系列模型调用
- **langgraph==1.2.2, langchain==1.3.2** → 已有生产级 Agent 框架
- **langgraph-checkpoint-postgres==3.0.5** → Postgres checkpoint 已在依赖中，**一行替换 InMemorySaver**

**数据与任务层**（本次升级可复用的现有基础设施）:
- **PostgreSQL**（SQLModel/sqlmodel）→ 已有 User/Conversation/Item 表 + `crud.py` Session 模式，可直接新增 `edit_plan`/`edit_step` 表
- **Redis**（`redis.asyncio`）→ 已有 `VideoProjectManager`（`ProjectGlobalState` + `TaskExecutionState` + 7 阶段生命周期管理），模式可直接复用到编辑任务状态跟踪
- **Celery**（redis broker）→ 已有 `celery_app` + `generate_video_workflow_task` 等异步任务模板，重量步骤（FFmpeg/TTS/导出）可直接包装为 Celery Task
- **FastAPI** → 已有 text2video API 路由 + WebSocket 基础

**桌面自动化**:
- **uiautomation==2.0.20** → Windows GUI 自动化控制剪映 App（固有脆弱性，详见 2.1）

**前端**:
- **React + TanStack Router + TanStack Query** → 已有 `video_editing` 页面（剧本列表 + 状态标签），`text2video` 流程页面（含审核/修改/重试交互）

### 1.3 修正后的瓶颈清单

| # | 瓶颈 | 涉及文件 | 严重程度 | 修正说明 |
|---|------|----------|----------|---------|
| B1 | Agent 主循环依赖 LangChain `create_agent`，无显式编排层 | `jianying_agent.py:255-349` | 高 | 原 B1 正确 |
| B2 | InMemorySaver 用于开发，生产需切换持久化（Postgres checkpoint 已在依赖中） | `jianying_agent.py:346` | 高 | **修正**: 项目已有 `langgraph-checkpoint-postgres`，无需从零实现 SQLite |
| B3 | JyProject 代码由 LLM 一次性生成完整 Python 脚本，单步出错全盘重来 | `python_executor.py` | 高 | 原 B10，提升优先级 |
| B4 | 工具注册为静态 `@tool` 装饰器 + 硬编码 `tools = [...]` 列表 | `jianying_agent.py:127-135` | 中 | 原 B3 |
| B5 | CLI 脚本路由是 `if/elif` 硬编码在 `_build_cmd()` 方法（35 行条件分支） | `cli_executor.py:153-220` | 中 | 原 B4 |
| B6 | Token 估算 `len(text)//2` 对英文/代码方向性偏差（中文低估、英文高估），需模型适配 | `token_utils.py:16` | 中 | **修正**: 原 PRD 建议 tiktoken cl100k_base，但项目使用 qwen 模型，tiktoken 的 OpenAI 编码不适用。应使用 dashscope 的 Tokenizer 或混合估算 |
| B7 | errors.py 有 4 类（JyError/UserInputError/InfraError/DataError），但重试策略依赖 `retry_config.py` 的异常类型判断，未与错误分类体系打通 | `errors.py`, `retry_config.py` | 中 | **修正**: 原 PRD 称"仅 3 层"，实际为 4 类；问题不在于分类数，而在于 retry 和 errors 未关联 |
| B8 | Langfuse 手动 span 创建后仅在 `cli_executor.py` 中调用了 `.end()`，`python_executor.py` 和 `media_normalizer.py` 的 subprocess 调用未被覆盖 | 多个文件 | 中 | **修正**: span 管理在 cli_executor 中已基本正确，遗漏范围在 python_executor 和 FFmpeg |
| B9 | 无 Feature Flag 体系 | 架构层缺失 | 低 | 维持 P2 |
| B10 | 分镜 → JyProject 代码生成无中间表示层，LLM 直接输出 Python 代码 | 架构层缺失 | 中 | 比原优先级降低——当前场景下 LLM 生成代码已可工作 |

---

## 二、核心挑战：视频剪辑场景的特殊约束

在制定架构方案前，必须正视以下**领域特有约束**，这些约束在原始 PRD 中被低估：

### 2.1 uiautomation 的固有脆弱性

```
当前：剪映 App 通过 uiautomation 控制（Windows GUI 自动化）
约束：GUI 自动化本质不可靠（窗口焦点丢失、控件树变化、剪映版本更新）
影响：任何依赖 app 状态的"断点续跑"机制，如果 app 崩溃则无法恢复状态
```

### 2.2 FFmpeg 的 GPU/内存资源竞争

```
当前：media_normalizer.py 和 smart_rough_cut.py 调用 FFmpeg（CPU 编码 libx264）
约束：并发 FFmpeg 进程会耗尽 CPU/内存，Windows 上 GPU 编码（h264_nvenc）不稳定
影响：ConcurrentToolOrchestrator 的 FFmpeg 并发需要硬性上限（建议 2），而非通用并发数
```

### 2.3 JyProject 的文件系统耦合

```
当前：JyProject 通过 jy_wrapper → pyJianYingDraft 直接写 JSON 草稿文件到磁盘
约束：草稿文件是原子单位——不可拆分执行，save() 后剪映 App 才能识别
影响：EditStep 粒度的设计不能小于一个完整的 JyProject.save() 周期
```

---

## 三、模块需求（修订版）

---

### 模块 A：编排层与执行控制 (Orchestration Layer)

#### A-1: 分步编排器 (StepOrchestrator) — 替代 EditStepStateMachine

> **v1.0 原方案**: 完整的 EditStepStateMachine（7 状态 FSM）
> **v2.0 简化**: 保留线性执行+checkpoint，取消复杂 FSM。视频剪辑 90% 场景是线性流程。

```
执行模型:
  PLANNING → EXECUTING(step_1) → EXECUTING(step_2) → ... → COMPLETED
                ↑ 失败从这里恢复                          ↓
              PAUSED (在步骤间暂停)

每个 step 完成后自动保存 checkpoint → PostgreSQL (edit_step 表) + Redis (实时状态)
恢复时: 读取 checkpoint → 跳过已完成步骤 → 从失败步骤继续
```

**实现要点**:
- 定义 `EditPlan` dataclass: `{task_id, steps: list[StepSpec], current_index: int, project_state: dict}`
- 每个 `StepSpec`: `{id, tool, args, status(pending|running|done|failed), result, started_at, finished_at}`
- **Checkpoint 持久化到 PostgreSQL**（复用现有 SQLModel Session 模式）:
  - 新增 `edit_task` 表: `{id, user_id, script_id, status, current_step, total_steps, project_state_json}`
  - 新增 `edit_step` 表: `{id, task_id, step_index, tool_name, args_json, status, result_json, error, started_at, finished_at, retry_count}`
- **Agent 自身的 LangGraph checkpoint** 切换到 `PostgresSaver`（`jianying_agent.py:346` 一行替换），进程崩溃后对话历史可恢复
- Checkpoint 中包含 `project_state` 快照（素材列表、轨道布局），用于恢复时重建 JyProject 上下文

#### A-2: 分镜→步骤转换器 (StoryboardToSteps)

将 LLM 生成的剪辑方案（非 Python 代码）转为 StepSpec 列表：

```python
# 输入: LLM 生成的结构化方案
{
    "project_name": "我的视频",
    "steps": [
        {"action": "import_media", "file": "test01.mp4", "track": "main"},
        {"action": "add_text", "text": "标题", "start": "0s", "duration": "3s"},
        {"action": "add_tts", "text": "旁白文本", "speaker": "zh_male"},
        {"action": "export", "resolution": "1080p", "fps": 30}
    ]
}

# 输出: EditPlan 对象
# 每个 action 映射到具体的 tool + args
# 验证: 素材是否存在、参数是否合法
```

**为何不生成 Python 代码**: 
- Python 代码 = 黑盒，无法分步执行
- 结构化 JSON 方案 = 每步可独立执行、独立重试、独立追踪
- LLM 仍然生成业务逻辑（哪些 clip 怎么排），但不生成执行代码

#### A-3: 暂停/恢复 (Pause/Resume)

- **暂停**: 当前步骤完成后进入 PAUSED，保存 checkpoint
- **恢复**: 读取 checkpoint，跳过 done 步骤，从 current_index 继续
- **回退到第 N 步**: 清除 step_N 之后的 done 状态，重置 current_index = N
- **恢复时重建上下文**: 从 checkpoint 的 `project_state` + 前 N 步的 result 重建 JyProject 实例

**注意**: 不保证跨 app 崩溃的恢复。如果剪映 App 崩溃，需要重启 app + 重建草稿。这是 uiautomation 的物理限制，不是代码问题。

#### A-4: JyProject 上下文管理器 (ProjectContext)

封装 JyProject 实例的生命周期：

```python
class ProjectContext:
    """管理 JyProject 实例的创建、状态追踪和恢复"""
    project: JyProject
    imported_media: list[str]     # 已导入的素材路径
    track_layout: dict            # 当前轨道布局
    current_duration_us: int      # 当前项目总时长（微秒）

    def snapshot(self) -> dict:   # 序列化为 checkpoint
    def restore(self, data: dict): # 从 checkpoint 恢复
```

---

### 模块 B：工具系统升级 (Tool System)

#### B-1: 声明式工具注册表 (ToolRegistry)

> 当前 7 个工具够用。重点是规范化注册方式，方便未来扩展。

```python
TOOL_REGISTRY: dict[str, ToolSpec] = {
    "resolve_media": ToolSpec(
        handler="media_resolver",
        category="read",          # read | write | compute
        concurrency_safe=True,
        timeout=30,
        retry_strategy="transient",  # 关联 B-7 的错误分类
    ),
    "execute_ffmpeg": ToolSpec(
        handler="media_normalizer",
        category="compute",
        concurrency_safe=False,
        timeout=600,
        max_concurrent=2,          # FFmpeg 特殊限制
        retry_strategy="recoverable",
    ),
    # ... 其他工具 ...
}
```

#### B-2: 并发编排（渐进式）

> **v2.0 降级**: 当前阶段不需要完整的 `ConcurrentToolOrchestrator`。先用最简单的方案验证价值。

**Phase 1 (P0)**: 素材导入步骤（resolve_media + FFmpeg normalize）并发执行
- 仅对 `category="read"` + `concurrency_safe=True` 的工具启用 `asyncio.gather`
- 最大并发 = 3（避免 FFmpeg 资源竞争）
- 其他步骤保持串行

**Phase 2 (P1)**: 当 Phase 1 验证稳定后，扩展到全部 read 类工具

#### B-3: CLI 脚本自动发现

> 当前 `SCRIPT_REGISTRY` 硬编码 10 个脚本。未来扩展时改。

```python
def discover_scripts(scripts_dir: Path) -> dict:
    """扫描 scripts/*.py，从 docstring 提取 description，从 argparse 提取参数"""
    # 替代 SCRIPT_REGISTRY 硬编码
    # 新增脚本只需遵循约定（有 docstring + argparse），无需改 cli_executor.py
```

---

### 模块 C：Token 管理与上下文 (Token & Context)

#### C-1: 模型适配的 Token 计数

> **关键修正**: v1.0 建议 `tiktoken` cl100k_base。**项目使用 qwen3.6 模型（dashscope），tiktoken 不适用。**

```python
# 方案: 混合估算 + dashscope API（当可用时）
def count_tokens(text: str, model: str = "qwen3.6-plus") -> int:
    """
    - 优先使用 dashscope 的 Tokenizer.tokenizer() 精确计数
    - dashscope 不可用时 fallback 到混合估算:
      - 中文/CJK 字符: 1.2 chars/token
      - 英文/ASCII 字符: 3.5 chars/token
      - 混合使用 unicodedata.east_asian_width 判断
    """
```

**为什么不用 tiktoken**: dashscope 的 qwen tokenizer 行为与 OpenAI tokenizer 显著不同。用错误的 tokenizer 计数可能导致 20-40% 的偏差，比粗估更危险。

#### C-2: 分级技能注入（已部分实现）

> req_03 已实现了 token-budget-aware 截断。v2.0 在此基础上增加层级概念。

当前代码的 `_generate_skills_prompt(token_budget=...)` 已支持优先级截断（main > rule > script > example）。在此之上追加：

- **Level 0 — 路由摘要**（始终注入）: 仅技能名称 + 类别标记，约 300 tokens
- **Level 1 — 规则摘要**（按需注入）: 当 LLM 的意图匹配某类别时注入该类别的 rule 摘要，约 1500 tokens
- **Level 2 — 完整内容**（工具加载）: 通过现有的 `load_skill` 工具按需加载

**与当前代码的关系**: 当前的 `_skills_prompt_cache` 生成 Level 0+1 的混合体。改造为两级：`_route_summary` (L0) + `_category_detail(category)` (L1)。

#### C-3: 上下文压缩（轻量版）

> **v2.0 降级**: v1.0 提出 3 层压缩（reactive/auto/collapse）。对于剪辑场景，80% 的对话不超过 10 轮。**autoCompact 一层即可**。

- 当 `token_usage > 80%` 时触发
- 将旧轮次的工具输出替换为 `[步骤 3 已完成: 素材导入成功, 用时 12s]`
- 保留最近 3 轮的完整内容
- 基于现有的 `estimate_tokens()` + `calculate_context_budget()`，不需要新基础设施

#### C-4: 会话记忆（用户偏好）

```python
# ~/.jianying_agent/memory/preferences.json
{
    "last_project_name": "...",
    "preferred_resolution": "1080p",
    "preferred_fps": 30,
    "preferred_speaker": "zh_male_huoli",
    "frequent_media_paths": ["D:/videos/", "D:/素材/"]
}
```

- 每次会话开始注入到系统提示（< 200 tokens）
- 自动从用户消息和工具调用结果中提取偏好
- **不需要 autoDream**（剪辑场景下无长空闲期）

---

### 模块 D：错误处理与可观测性 (Error & Observability)

#### D-1: 错误分类与重试策略打通

> 当前 `errors.py` 有 4 类错误（JyError → UserInputError/InfraError/DataError），`retry_config.py` 有 RETRYABLE_EXCEPTIONS 元组。两者互不感知。

```python
# 将错误分类与重试策略关联
ERROR_RETRY_MAP = {
    UserInputError:   {"max_retries": 0, "action": "notify_user"},
    InfraError:       {"max_retries": 3, "wait": "exponential(1s, 30s)", "action": "retry"},
    DataError:        {"max_retries": 0, "action": "notify_user"},
    subprocess.TimeoutExpired: {"max_retries": 2, "action": "retry_or_kill_tree"},
}
```

**不需要 5 级分类**: 当前 4 级 + TimeoutExpired 已覆盖所有实际场景。TRANSIENT vs RECOVERABLE 的区分在实践中边界模糊。

#### D-2: 断路器（轻量版）

```python
class CircuitBreaker:
    """连续 N 次同类错误后暂停，M 秒后半开"""
    def __init__(self, failure_threshold=3, recovery_timeout=60):
        ...
```

- 仅用于 uiautomation 调用（控制剪映 App 的 GUI 操作）和 FFmpeg
- 不用于 LLM 调用和文件操作
- 默认关闭，config 中可启用

#### D-3: Langfuse 追踪补齐

> 当前 `cli_executor.py` 已通过手动 span 覆盖 subprocess 调用。补齐剩余覆盖：

- `python_executor.py` 的 `execute()`: 添加 `trace_id` 参数 + span 管理（参考 cli_executor 的模式）
- `media_normalizer.py` 的 `normalize_webm_for_jianying()`: 通过 `process_utils.run_with_timeout` 创建 span

**不需要 Perfetto 导出**: Langfuse 已提供足够的 trace 可视化。Perfetto 为 Chrome DevTools 格式，对剪辑场景无额外价值。

#### D-4: 指标聚合

- 存储: JSON 文件（不是 SQLite——对于 Agent 级指标，JSON 足够且更易查看）
- 指标: 任务成功率、各步骤平均耗时、错误类型分布、Token 消耗趋势
- 命令: `python -m skills_agent.metrics --last 10`

---

### 模块 E：Celery + Redis 异步执行 (Async Execution Pipeline)

项目已有的 `celery_app`（Redis broker）+ `VideoProjectManager`（Redis 状态管理）已在 text2video 流程中验证了完整的异步管线。本模块将其复用到 Agent 编辑流程。

#### E-1: 步骤分级执行策略

```
轻量步骤（Agent 同步）          重量步骤（Celery 异步）
─────────────────────────      ─────────────────────────
resolve_media    (30s)         FFmpeg normalize  (120-600s)
add_text_simple  (<1s)         TTS synthesis     (30-120s)
add_media_safe   (<1s)         auto_exporter     (60-900s)
load_skill       (<1s)         smart_rough_cut   (180-600s)
JyProject.save() (<1s)         云端素材下载        (不定)
```

**实现要点**:
- `StepSpec.exec_mode`: `"sync"` | `"async"` — 声明步骤的执行方式
- 异步步骤包装为 Celery Task，Agent 通过 `task_id` 轮询或等待回调
- 复用现有 `TaskExecutionState` 模型跟踪 Celery 任务状态
- 同步步骤由 Agent 直接调用 `@tool` 函数

#### E-2: Redis 实时步骤状态

复用现有 `VideoProjectManager` 的模式，新增 `EditTaskState`:

```python
class EditTaskState(BaseModel):
    """编辑任务实时状态（Redis Hash）"""
    task_id: str
    user_id: str
    script_id: str
    status: str          # planning | running | paused | completed | failed
    current_step: int    # 当前执行到第几步
    total_steps: int     # 总步骤数
    step_name: str       # 当前步骤名称
    step_status: str     # pending | running | done | failed
    progress_pct: float  # 当前步骤进度百分比
    celery_task_id: str | None  # 关联的 Celery 任务 ID
    error_message: str | None
    updated_at: float
```

- 每个步骤状态变更 → 写入 Redis Hash → FastAPI 通过轮询或 WebSocket 推送给前端
- Redis TTL 72 小时（任务完成后自动过期清理）
- 与 `edit_step` PostgreSQL 表互补：PG 存永久记录，Redis 存实时状态

#### E-3: 用户交互队列

当 Agent 步骤失败需要用户决策时：

```
步骤失败 → Agent 判断 ErrorLevel → USER/FATAL
  ├── USER: 暂停执行，Redis Set user_pending:{user_id} ← task_id
  │         前端轮询发现 → 弹出确认框（重试/跳过/手动修复）
  └── FATAL: 终止执行，记录错误日志，通知前端
```

- 复用现有 `ProjectStatus.WAITING_REVIEW` 模式
- 前端通过 `GET /api/v1/edit/pending` 查询待处理任务

---

### 模块 F：前端配合改造 (Frontend Coordination)

当前 `video_editing/$scriptId.tsx` 仅显示 4 个粗粒度阶段（草稿 → 角色 → 分镜 → 视频）。本模块定义与 Agent 升级配套的前端改造。

#### F-1: 步骤进度指示器 (StepProgressBar)

横向步骤条，替代当前的简单状态标签：

```
 ┌──────────────────────────────────────────────────┐
 │  ● 导入素材  ● 粗剪  ◐ 加字幕  ○ 加特效  ○ 导出  │
 │   ✅ 3/3     ✅ 1/1    ⏳ 中...   待执行   待执行  │
 └──────────────────────────────────────────────────┘
```

- 数据来源: `GET /api/v1/edit/{task_id}/progress`（从 Redis `EditTaskState` 读取）
- 轮询间隔: 1s（使用 TanStack Query `refetchInterval`）
- 每个步骤显示: 图标（✅/❌/⏳/○）+ 步骤名 + 子任务数（如 "3/3 素材"）

#### F-2: 错误处理交互 (ErrorCard)

```
 ┌──────────────────────────────────────────┐
 │ ⚠️ 步骤 3 "添加字幕" 失败                    │
 │                                          │
 │ 错误: TTS 合成超时（120s）                  │
 │                                          │
 │ [重试此步骤]  [跳过此步骤]  [手动输入字幕文本]  │
 └──────────────────────────────────────────┘
```

- 数据来源: Redis `EditTaskState.error_message` + `step_status=failed`
- 三个操作映射到 API: `POST /edit/{task_id}/retry-step`, `/skip-step`, `/manual-fix`
- 手动修复模式下，前端展示文本框让用户输入替代内容

#### F-3: 控制工具栏 (ControlBar)

```
 [⏸ 暂停]  [▶ 继续]  [⏹ 取消]    进度: ████████░░ 78%    Step 6/8
```

- 调用 `POST /edit/{task_id}/pause` / `/resume` / `/cancel`
- 暂停在当前步骤完成后生效（不中断正在执行的 FFmpeg/导出）
- 取消时弹出确认框（"确定取消？已完成步骤不会被删除"）

#### F-4: 视频预览 (VideoPreview)

- 步骤产出可预览的视频片段时（如 `smart_rough_cut` 结束），内嵌 `<video>` 播放器
- 数据来源: Redis `EditTaskState.metadata.preview_path`
- WebSocket 推送预览就绪事件，前端即时展示

#### F-5: 新增 API 路由

| 端点 | 方法 | 数据来源 | 说明 |
|------|------|---------|------|
| `/api/v1/edit/start` | POST | Agent | 发起编辑任务，Agent 开始生成 EditPlan |
| `/api/v1/edit/{task_id}/progress` | GET | Redis | 轮询实时进度 |
| `/api/v1/edit/{task_id}/steps` | GET | PostgreSQL | 获取步骤列表及状态 |
| `/api/v1/edit/{task_id}/step/{step_id}/log` | GET | 磁盘文件 | 获取某步骤的完整执行日志 |
| `/api/v1/edit/{task_id}/pause` | POST | Agent | 暂停 |
| `/api/v1/edit/{task_id}/resume` | POST | Agent | 继续 |
| `/api/v1/edit/{task_id}/cancel` | POST | Agent | 取消 |
| `/api/v1/edit/{task_id}/retry-step` | POST | Agent | 重试某步骤 |
| `/api/v1/edit/{task_id}/skip-step` | POST | Agent | 跳过某步骤 |
| `/api/v1/edit/pending` | GET | Redis | 查询当前用户待决策的任务 |
| `/ws/edit/{task_id}` | WebSocket | Redis PubSub | 实时推送进度变更 |

---

## 四、实施路线图（修订版）

### P0 — 核心编排与持久化（第 1-3 周）

> **目标**: 分步执行 + PG checkpoint + Redis 状态 + 前端基础进度

| 编号 | 需求 | 模块 | 工作量 | 说明 |
|------|------|------|--------|------|
| P0-1 | StepOrchestrator + edit_task/edit_step PG 表 + PostgresSaver | A-1 | 3d | 线性执行 + PG 持久化，1 行替换 InMemorySaver |
| P0-2 | StoryboardToSteps 分镜转换器 | A-2 | 3d | LLM → JSON → EditPlan，关键创新点 |
| P0-3 | ProjectContext JyProject 封装 | A-4 | 2d | 状态快照/恢复 + `_trim_project_duration` 集成 |
| P0-4 | ToolRegistry 声明式注册 | B-1 | 2d | 规范化现有 7 个工具，定义 ToolSpec + exec_mode |
| P0-5 | 模型适配的 Token 计数 | C-1 | 1d | dashscope Tokenizer + fallback 混合估算 |
| P0-6 | Level 0/1 分级技能注入 | C-2 | 1d | 在现有 `_skills_prompt_cache` 基础上分层 |
| P0-7 | Redis EditTaskState + 轻量/重量步骤分级 | E-1, E-2 | 2d | 复用 VideoProjectManager 模式，sync/async 分发 |
| P0-8 | 前端步骤进度条 + API 路由骨架 | F-1, F-5 | 2d | TanStack Query 轮询 + 基础步骤 UI |

**里程碑**: Agent 接受分镜 JSON → 分步执行 → PG 持久化 → 前端实时展示进度

### P1 — 异步执行与错误恢复（第 4-6 周）

> **目标**: Celery 异步重量步骤 + 错误恢复 + 上下文压缩

| 编号 | 需求 | 模块 | 工作量 | 说明 |
|------|------|------|--------|------|
| P1-1 | Celery 重量步骤包装（FFmpeg/TTS/导出） | E-1 | 2d | 复用 celery_app + generate_video_workflow_task 模板 |
| P1-2 | 素材导入并发（Phase 1） | B-2 | 2d | 仅 read 类工具并发，最大 3 |
| P1-3 | 上下文 autoCompact（单层） | C-3 | 2d | 80% 阈值触发 |
| P1-4 | 错误分类与重试打通 | D-1 | 2d | errors.py ↔ retry_config.py ↔ ToolRegistry |
| P1-5 | 断路器（uiautomation + FFmpeg） | D-2 | 1d | 仅关键路径 |
| P1-6 | 前端错误卡片 + 控制工具栏（暂停/继续/取消） | F-2, F-3 | 2d | 复用 text2video 审核交互模式 |
| P1-7 | 用户交互队列（Redis pending） | E-3 | 1d | 复用 WAITING_REVIEW 模式 |
| P1-8 | 会话记忆（用户偏好 → PG 表） | C-4 | 1d | user_preference SQLModel 表 |

**里程碑**: 重量步骤异步不阻塞、长对话不溢出、前端可暂停/重试/跳过

### P2 — 生产就绪（第 7-9 周）

> **目标**: 追踪 + 指标 + 预览 + Feature Flag

| 编号 | 需求 | 模块 | 工作量 | 说明 |
|------|------|------|--------|------|
| P2-1 | Langfuse 追踪补齐（python_executor + FFmpeg） | D-3 | 1d | 补齐 span 覆盖 |
| P2-2 | 前端视频预览 + WebSocket 推送 | F-4 | 1d | 步骤产出可预览时即时展示 |
| P2-3 | CLI 脚本自动发现 | B-3 | 1d | 替代 hardcoded SCRIPT_REGISTRY |
| P2-4 | 指标聚合 + metrics 命令 | D-4 | 1d | PG 查询 edit_step 统计 |
| P2-5 | Feature Flag 体系 | B-1 | 1d | 环境变量 + 配置文件 |
| P2-6 | jianying-agent doctor 健康检查 | D-4 | 1d | Python/FFmpeg/剪映/Redis/PG/磁盘空间 |

**里程碑**: 可观测性完善、视频预览、生产环境可运维

### 未来考虑（降级到 Optional）

| 原需求 | 降级原因 |
|--------|---------|
| autoDream 空闲巩固 | 剪辑 Agent 按需运行，无长期空闲期 |
| Perfetto 可视化导出 | Langfuse trace 已足够 |
| SQLite checkpoint（v1.0） | JSON 文件 + Postgres（已有）更务实 |
| Fork SubAgent 并行分镜 | 场景匹配度低；单视频线性编辑为主 |
| ConcurrentToolOrchestrator 完整版 | Phase 1 并发验证后再扩展 |

---

## 五、架构对比

```
当前架构 (post req_01~06):
══════════════════════════════════════════
  用户 → LangChain Agent
           ├── 7 个 @tool
           ├── InMemorySaver
           ├── tenacity 重试
           ├── token-budget 技能注入
           ├── Langfuse callback + manual span
           └── Popen + communicate 进程管理

目标架构 (post P0+P1):
══════════════════════════════════════════
  用户 → LLM 生成分镜 JSON
           ↓
       StoryboardToSteps
           ↓
       StepOrchestrator ──→ PostgreSQL (edit_task/edit_step + PostgresSaver)
           │                      ↑
           ├── 轻量步骤 (sync) ────┤ Agent @tool 直接执行
           │                      │
           └── 重量步骤 (async) ──→ Celery Worker ──→ Redis ──→ 前端轮询
             (FFmpeg/TTS/Export)        │
                                        ↓
                                   PostgreSQL (edit_step 状态)
                                        ↑
                                   FastAPI ──→ React 前端
                                     │            ├── 步骤进度条
                                     │            ├── 错误卡片 (重试/跳过)
                                     │            ├── 暂停/继续/取消
                                     │            └── 视频预览
                                     │
                                   Redis ──→ WebSocket 实时推送
                                     │
                                   Langfuse ──→ 全链路 Trace
```

**数据流总结**:
- **PostgreSQL**: 永久数据——用户、剧本、`edit_task`、`edit_step`、用户偏好
- **Redis**: 实时数据——当前步骤进度、Celery 任务状态、待决策队列（TTL 72h）
- **Celery**: 异步执行——FFmpeg 转码、TTS、导出等长时任务
- **Langfuse**: 可观测性——LLM 调用 + 工具调用 + subprocess 全链路追踪

---

## 六、源码级改造清单（修订版 v3.0）

### 后端改造

| 文件 | 改造内容 | 对应需求 | 优先级 |
|------|----------|----------|--------|
| `jianying_agent.py` | `InMemorySaver()` → `PostgresSaver`（1 行）；`run_jianying_agent()` → StepOrchestrator 驱动 | A-1 | P0 |
| `token_utils.py` | 替换 `len//2` 为 dashscope 混合估算 | C-1 | P0 |
| `skill_parser.py` / `jianying_agent.py` | 分级注入 L0/L1/L2 | C-2 | P0 |
| `cli_executor.py` | `_build_cmd()` → 自动发现 + ToolRegistry；exec_mode 标记 | B-1, B-3, E-1 | P0/P1 |
| `python_executor.py` | 添加 `trace_id` span 管理 | D-3 | P2 |
| `errors.py` / `retry_config.py` | 打通错误分类与重试策略 | D-1 | P1 |
| `observability.py` | 补齐 span end/update 辅助函数 | D-3 | P1 |
| `app/agent/utils/redis.py` | 新增 `EditTaskState` 模型 + `EditTaskManager`（复用 `VideoProjectManager` 模式） | E-2 | P0 |
| `app/agent/generateVideo/celery_config.py` | 复用 celery_app，新增编辑步骤 Task 定义 | E-1 | P1 |
| `app/models.py` | 新增 `EditTask` / `EditStep` / `UserPreference` SQLModel 表 | A-1, C-4 | P0 |
| `app/crud.py` | 新增 edit_task / edit_step / user_preference CRUD 函数 | A-1, C-4 | P0 |

**新增后端模块**:

| 文件 | 功能 | 对应需求 | 优先级 |
|------|------|----------|--------|
| `step_orchestrator.py` | 线性执行 + PG checkpoint + Celery 任务分发 | A-1, E-1 | P0 |
| `storyboard_parser.py` | 分镜 JSON → EditPlan | A-2 | P0 |
| `project_context.py` | JyProject 生命周期管理 + 状态快照 | A-4 | P0 |
| `tool_registry.py` | 声明式 ToolSpec + exec_mode 分类 | B-1 | P0 |
| `circuit_breaker.py` | 断路器（uiautomation + FFmpeg） | D-2 | P1 |
| `session_memory.py` | 用户偏好管理（PG 存储） | C-4 | P1 |

### 前端改造

| 文件 | 改造内容 | 对应需求 | 优先级 |
|------|----------|----------|--------|
| `video_editing/$scriptId.tsx` | 新增步骤进度条 + 错误卡片 + 控制工具栏 + 视频预览 | F-1~F-4 | P0/P1/P2 |
| **新增** `components/Edit/StepProgressBar.tsx` | 横向步骤指示器组件 | F-1 | P0 |
| **新增** `components/Edit/ErrorCard.tsx` | 错误详情 + 操作按钮（重试/跳过/手动修复） | F-2 | P1 |
| **新增** `components/Edit/ControlBar.tsx` | 暂停/继续/取消控制栏 | F-3 | P1 |
| **新增** `components/Edit/VideoPreview.tsx` | 步骤输出视频预览 | F-4 | P2 |
| **新增** `routes/_layout/video_editing/edit.$taskId.tsx` | 编辑任务详情页（含实时轮询） | F-5 | P0 |

### 新增 API 路由

| 端点 | 文件位置 |
|------|---------|
| `POST /api/v1/edit/start` | `app/api/routes/edit.py`（新增） |
| `GET /api/v1/edit/{task_id}/progress` | 同上 |
| `GET /api/v1/edit/{task_id}/steps` | 同上 |
| `POST /api/v1/edit/{task_id}/pause` | 同上 |
| `POST /api/v1/edit/{task_id}/resume` | 同上 |
| `POST /api/v1/edit/{task_id}/cancel` | 同上 |
| `POST /api/v1/edit/{task_id}/retry-step` | 同上 |
| `POST /api/v1/edit/{task_id}/skip-step` | 同上 |
| `GET /api/v1/edit/pending` | 同上 |
| `WS /ws/edit/{task_id}` | 同上 |

### 不再新增的模块（对比 v1.0 减少 6 个）

- ~~edit_state_machine.py~~（简化为 step_orchestrator）
- ~~context_compressor.py~~（单层 autoCompact 集成到 orchestrator）
- ~~edit_metrics.py~~（指标从 PG edit_step 表查询）
- ~~edit_tracer.py~~（Langfuse 集成到 observability.py）
- ~~concurrent_tool_executor.py~~（Phase 1 并发集成到 orchestrator）
- ~~perfetto_exporter.py~~（不实现）

---

*文档结束。v3.0 整合了 PostgreSQL/Redis/Celery 现有基础设施 + 前端配合改造。新增模块从 v1.0 的 14 个减少到 8 个（后端 6 + 前端 4 组件），P0 从 7 项聚焦到 8 项（含基础设施），整体工期 9 周。*
