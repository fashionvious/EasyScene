# 自动剪辑 Agent 架构升级需求文档 (PRD)

> 基于 Claude Code 源码架构审计 x JianYing Editor Agent 源码诊断的深度交叉分析
> 版本: 1.0 | 日期: 2026-06-01

---

## 一、架构升级愿景 (Executive Summary)

当前 JianYing Agent 是一个**基于 LangChain 的线性单轮 Agent**，具备基础的技能加载、CLI/Python 执行和媒体解析能力。但面对复杂的多步骤剪辑流（如：分镜解析 -> 素材匹配 -> 多轨道编排 -> 特效叠加 -> 字幕对齐 -> 导出），暴露出以下核心短板：

- **无状态机**：剪辑步骤是一次性脚本，无法暂停/回放/续跑
- **工具调用串行**：每个工具调用阻塞等待，无法并行处理独立的素材加载
- **上下文管理粗糙**：仅靠字符数估算 token，长分镜脚本容易溢出
- **错误恢复脆弱**：subprocess 失败即终止，无断点续跑能力
- **可观测性不足**：Langfuse 仅覆盖 LLM 调用，FFmpeg/TTS 等重计算节点缺乏细粒度追踪

借鉴 Claude Code 的 **6 层上下文压缩体系、生成器驱动的工具编排、Fork 子 Agent 模式、Feature Flag 动态加载、OpenTelemetry 全链路追踪**等架构思想，本 PRD 将指导 JianYing Agent 从能跑升级为生产级可编排的视频生成引擎。

---

## 二、现状诊断：源码级瓶颈清单

| # | 瓶颈 | 涉及文件 | 严重程度 |
|---|------|----------|----------|
| B1 | Agent 主循环依赖 LangChain create_agent，无显式状态机，无法中途暂停/恢复 | jianying_agent.py L10 | 高 |
| B2 | InMemorySaver 不支持持久化，进程崩溃 = 丢失全部进度 | jianying_agent.py L13 | 高 |
| B3 | 工具注册为静态 @tool 装饰器，无法动态加载/卸载 | jianying_agent.py L127-135 | 中 |
| B4 | CLI 脚本路由是 if/elif 硬编码，新增脚本需改源码 | cli_executor.py L159-205 | 中 |
| B5 | Token 估算用 len(text)//2，对英文/代码严重高估 | token_utils.py L16 | 中 |
| B6 | 技能注入是一次性全量，无分级压缩/懒加载 | jianying_agent.py L137-190 | 中 |
| B7 | 错误分类仅 3 层，无重试策略分级 | errors.py | 中 |
| B8 | Langfuse 手动 span 无 end/update，FFmpeg 耗时无法记录 | observability.py L66-81 | 中 |
| B9 | Python 执行器无并行能力，多段视频处理必须串行 | python_executor.py | 中 |
| B10 | 无分镜脚本解析层，LLM 直接生成完整 Python 代码，单步出错全盘重来 | 架构层缺失 | 高 |
| B11 | 输出截断仅 10KB，FFmpeg 详细日志丢失，调试困难 | timeout_config.py L13 | 低 |
| B12 | 无 Feature Flag 体系，新功能无法灰度上线 | 架构层缺失 | 低 |

---

## 三、核心模块需求拆解 (Modular Requirements)

---

### 模块 A：Agent 控制流与状态管理 (Agentic Loop & State Machine)

#### 当前痛点

- 依赖 LangChain 的 create_agent 黑盒循环，无法干预中间状态
- InMemorySaver 仅存活于进程生命周期，崩溃即丢失
- 剪辑任务（如导入素材 -> 粗剪 -> 加字幕 -> 加特效 -> 导出）是一整块 Python 代码，任一步骤失败需从头执行
- 无暂停/恢复能力：用户无法在粗剪完成后检查效果再决定是否继续

#### 借鉴机制

| Claude Code 机制 | 源码锚点 | 映射到剪辑场景 |
|-----------------|----------|---------------|
| while(true) Agentic Loop + maxTurns | query.ts:307-1728 | 每个剪辑步骤 = 一个 turn，支持逐步推进 |
| taskBudgetRemaining Token 预算追踪 | query.ts:291,508-513 | 分镜脚本的 token 预算控制 |
| AbortSignal 外部中断 | query.ts:1223 | 用户可随时暂停剪辑流 |
| Checkpoint/Resume | tools/AgentTool/resumeAgent.ts | 断点续跑：从失败步骤恢复 |
| Fork Subagent（继承上下文） | tools/AgentTool/forkSubagent.ts | 并行处理多段视频片段后合并 |

#### 具体需求特性

**A-1: 剪辑步骤状态机 (EditStepStateMachine)**

```
状态流转: IDLE -> PLANNING -> EXECUTING(step_N) -> PAUSED -> EXECUTING(step_N+1) -> COMPLETED / FAILED
```

- 将剪辑流程建模为有序步骤列表 EditPlan.steps: list[EditStep]
- 每个 EditStep 包含: {id, name, tool, args, status, result, error, started_at, finished_at}
- 支持状态持久化到 SQLite（路径: ~/.jianying_agent/checkpoints/{task_id}.db）
- 状态快照序列化为 JSON，支持手动导出/导入

**A-2: 暂停/恢复/回放 (Pause / Resume / Replay)**

- 用户指令暂停 -> 当前步骤完成后进入 PAUSED 状态
- 用户指令继续 -> 从 PAUSED 状态恢复，跳过已完成步骤
- 用户指令从第3步重跑 -> 回退到指定步骤，清除后续步骤状态
- 恢复时自动重建执行上下文（JyProject 实例、已导入素材列表、轨道状态）

**A-3: 分镜解析器 (StoryboardParser)**

- 输入: LLM 生成的 JSON 分镜脚本（非 Python 代码）
- 结构: {"scenes": [{"duration": "5s", "media": "video.mp4", "text": "...", "effects": ["..."]}, ...]}
- 解析为 EditPlan 对象，每个 scene 拆分为独立的 EditStep
- 支持验证: 媒体文件是否存在、时长是否合理、特效名称是否合法

**A-4: Token 预算管理器 (TokenBudgetManager)**

- 替代 token_utils.py 的 len(text)//2 粗估
- 集成 tiktoken（cl100k_base）用于精确计数
- 实现分级预算:
  - 系统提示 + 技能注入: <=30% 总预算
  - 分镜脚本: <=40% 总预算
  - 工具输出: <=20% 总预算
  - 预留响应: >=10%
- 当分镜脚本超出预算时，自动触发微压缩：保留结构，省略冗余描述

#### 验收标准

- [ ] 剪辑任务可在任意步骤暂停，恢复后状态完全一致
- [ ] 进程崩溃后重启，可从 SQLite checkpoint 恢复最近一次执行
- [ ] 分镜 JSON 解析器能正确处理 20+ scene 的长脚本
- [ ] Token 预算误差 <= 5%（对比 tiktoken 精确计数）

---

### 模块 B：工具箱与系统交互 (Tool Registry & Execution)

#### 当前痛点

- 7 个工具通过 @tool 静态注册，新增工具需修改 jianying_agent.py
- CLI 脚本路由是 if/elif 硬编码，每新增一个脚本需改源码
- 工具调用完全串行，无法并行执行独立的素材加载
- 无工具执行超时的分级策略（FFmpeg 转码 vs 文件查找应有不同超时）
- Python 代码执行器每次创建临时文件，无代码缓存/复用

#### 借鉴机制

| Claude Code 机制 | 源码锚点 | 映射到剪辑场景 |
|-----------------|----------|---------------|
| Feature Flag 动态工具加载 | tools.ts + GrowthBook | 按任务类型动态加载/卸载工具 |
| runToolsConcurrently() 并发执行 | toolOrchestration.ts:36-53 | 并行加载视频+音频+图片素材 |
| isConcurrencySafe 分区 | toolOrchestration.ts L19-30 | 读取类工具并发，写入类工具串行 |
| StreamingToolExecutor 流式执行 | StreamingToolExecutor.ts | 实时返回 FFmpeg 处理进度 |
| 超时分级（120s/600s） | utils/timeouts.ts | FFmpeg 600s，文件查找 30s |
| 输出截断 -> 磁盘转储（5GB） | utils/task/diskOutput.ts | FFmpeg 完整日志写磁盘，摘要返回 LLM |
| 后台任务自动降解 | utils/ShellCommand.ts | 长时间导出自动转后台 |

#### 具体需求特性

**B-1: 声明式工具注册表 (DeclarativeToolRegistry)**

```python
# tools/registry.py
TOOL_REGISTRY = {
    "resolve_media": {
        "handler": "media_resolver.resolve",
        "description": "根据文件名查找媒体完整路径",
        "category": "read",
        "timeout": 30,
        "concurrency_safe": True,
    },
    "execute_ffmpeg": {
        "handler": "ffmpeg_ops.execute",
        "description": "执行 FFmpeg 命令",
        "category": "compute",
        "timeout": 600,
        "concurrency_safe": False,
    },
    "add_clip": {
        "handler": "jyproject.add_clip",
        "description": "添加视频片段到轨道",
        "category": "write",
        "timeout": 10,
        "concurrency_safe": False,
    },
}
```

- 基于配置文件（YAML/Python dict）声明工具元数据
- 支持运行时 register_tool() / unregister_tool() 动态扩展
- 工具分类: read（可并发） / write（必须串行） / compute（可并发但限流）

**B-2: 并发工具编排器 (ConcurrentToolOrchestrator)**

- 参考 Claude Code 的 partitionToolCalls() 模式
- 自动将一批工具调用按 concurrency_safe 分区:
  - 所有 read 类工具 -> asyncio.gather() 并发执行
  - 所有 write 类工具 -> 逐个串行执行，每次执行后更新上下文
- 最大并发数可配置（默认 5，FFmpeg 资源密集需限制）

**B-3: 分级超时与输出管理 (TieredTimeout & OutputManagement)**

```python
TIMEOUT_TIERS = {
    "file_ops": 30,
    "api_calls": 60,
    "ffmpeg_light": 120,
    "ffmpeg_heavy": 600,
    "export": 900,
}
```

- 输出超过 10KB 时，完整日志写入 ~/.jianying_agent/logs/{task_id}/{step_id}.log
- 返回给 LLM 的是结构化摘要: {success, summary, full_log_path, metrics}
- FFmpeg 进度解析: 从 stderr 提取 time=00:01:23.45 实现进度回调

**B-4: CLI 脚本自动发现 (AutoDiscoverCLI)**

- 替代 SCRIPT_REGISTRY 硬编码
- 自动扫描 scripts/*.py，从 docstring + argparse 注解提取元数据
- 生成统一的 JSON Schema 描述，供 LLM 选择调用
- 新增脚本只需遵循约定（docstring + argparse），无需改注册表代码

#### 验收标准

- [ ] 新增工具只需在 registry 配置中添加条目，无需修改 Agent 核心代码
- [ ] 3 个独立素材文件的并发加载时间 < 串行加载的 50%
- [ ] FFmpeg 超时后正确终止进程树（Windows: taskkill /T /F，Unix: killpg）
- [ ] 新增 CLI 脚本放入 scripts/ 目录后自动被发现，无需重启 Agent

---

### 模块 C：上下文与记忆管理 (Context & Memory)

#### 当前痛点

- Token 估算 len(text)//2 对英文/代码严重高估（英文实际 token 约 4 字符/token）
- 技能注入是一次性全量加载，35 个技能的描述文本直接拼入系统提示
- 无上下文压缩机制，长对话（多轮分镜修改）会逐渐耗尽上下文窗口
- 无会话记忆：用户说上次的配色方案时 Agent 无法回忆
- 分镜脚本修改后，旧版本仍在上下文中占用 token

#### 借鉴机制

| Claude Code 机制 | 源码锚点 | 映射到剪辑场景 |
|-----------------|----------|---------------|
| 6 层上下文压缩体系 | services/compact/* | 分镜迭代时的上下文优化 |
| autoCompact（接近窗口时触发） | autoCompact.ts:62-64 | 长分镜对话自动压缩 |
| microCompact（单轮内压缩） | microCompact.ts | FFmpeg 详细日志在下一轮被压缩 |
| sessionMemoryCompact（会话记忆） | sessionMemoryCompact.ts | 用户偏好（配色、字体）长效记忆 |
| contextCollapse（上下文折叠） | services/contextCollapse/ | 旧分镜版本折叠为摘要 |
| autoDream（空闲时巩固） | services/autoDream/autoDream.ts | 空闲时总结剪辑偏好 |
| Feature Flag 控制压缩策略 | GrowthBook | 灰度上线新的压缩策略 |

#### 具体需求特性

**C-1: 精确 Token 计数器 (PreciseTokenCounter)**

```python
import tiktoken

_encoder = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    return len(_encoder.encode(text))

def calculate_context_budget(model_max: int = 131072) -> ContextBudget:
    return ContextBudget(
        system_prompt=int(model_max * 0.15),
        skills_injection=int(model_max * 0.15),
        storyboard=int(model_max * 0.40),
        tool_outputs=int(model_max * 0.20),
        reserved_response=int(model_max * 0.10),
    )
```

**C-2: 分级技能注入 (TieredSkillInjection)**

- **Level 0 - 路由索引**（始终注入）: 技能名称 + 一句话描述，约 500 tokens
- **Level 1 - 规则摘要**（按需注入）: 当 LLM 选择某技能时，注入该技能的核心规则摘要，约 2000 tokens
- **Level 2 - 完整内容**（工具加载）: 通过 load_skill 工具按需加载完整内容
- 预估: Level 0 常驻 ~500 tokens vs 当前全量 ~15000 tokens，**节省 97% 上下文**

**C-3: 三层上下文压缩 (ThreeLayerCompression)**

```
Layer 1: reactiveCompact -- 当 token 使用 > 80% 预算时触发
  -> 将旧的工具输出替换为结构化摘要

Layer 2: autoCompact -- 当 token 使用 > 90% 预算时触发
  -> 将旧的对话轮次压缩为要点列表

Layer 3: contextCollapse -- 当 token 使用 > 95% 预算时触发
  -> 将旧分镜版本折叠为 版本N: 一句话摘要
```

- 每层压缩后更新 token_budget_remaining
- 压缩操作本身消耗 token，需预留 5% buffer

**C-4: 会话记忆管理器 (SessionMemoryManager)**

```python
class SessionMemory:
    # 跨会话的长效记忆
    user_preferences: dict      # 字体、配色、分辨率偏好
    project_context: dict       # 当前项目名称、素材路径、草稿状态
    editing_patterns: list      # 用户常用的剪辑模式（如总是加慢动作）
    error_learnings: list       # 历史错误及解决方案
```

- 持久化到 ~/.jianying_agent/memory/{user_id}.json
- 每次会话开始时注入摘要到系统提示（<= 1000 tokens）
- 会话结束时自动更新（增量写入）

**C-5: autoDream 空闲巩固 (IdleConsolidation)**

- 当 Agent 空闲（无新任务 5 分钟）时，触发 LLM 自动总结当前会话
- 生成结构化摘要: {任务进展, 关键决策, 待办事项, 用户偏好变化}
- 存入 SessionMemory，下次会话自动注入

#### 验收标准

- [ ] Token 计数误差 <= 3%（对比 tiktoken 基准）
- [ ] 35 个技能的 Level 0 注入 <= 600 tokens
- [ ] 长对话（50+ 轮）不触发上下文溢出错误
- [ ] 会话记忆在进程重启后正确恢复
- [ ] autoDream 在空闲 5 分钟后自动触发

---

### 模块 D：容错恢复与全链路追踪 (Error Handling & Observability)

#### 当前痛点

- 错误分类仅 3 层，无法区分可重试和不可重试错误
- Langfuse 手动 span 只有 create_manual_span，没有 end_span / update_span，FFmpeg 耗时无法记录
- FFmpeg 失败时，stderr 输出被截断到 10KB，关键错误信息可能丢失
- 无断路器：当剪映 App 崩溃时，Agent 持续重试直到超时
- 无指标聚合：无法回答过去 10 次导出的平均耗时这类问题
- 进程树终止不一致：Windows taskkill vs Unix killpg 行为不同

#### 借鉴机制

| Claude Code 机制 | 源码锚点 | 映射到剪辑场景 |
|-----------------|----------|---------------|
| OpenTelemetry sessionTracing | utils/telemetry/sessionTracing.ts | 全链路追踪每个剪辑步骤 |
| Perfetto Tracing | utils/telemetry/perfettoTracing.ts | 可视化时间线（Chrome DevTools 格式） |
| logEvent() 全局埋点 | services/analytics/index.ts | 统一事件日志 |
| withRetry.ts 指数退避重试 | services/api/withRetry.ts | FFmpeg/TTS 调用级重试 |
| MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES=3 | autoCompact.ts:70 | 连续失败断路器 |
| 5GB 磁盘转储 | utils/task/diskOutput.ts | FFmpeg 完整日志持久化 |
| 进程树终止 | utils/ShellCommand.ts | 跨平台进程清理 |

#### 具体需求特性

**D-1: 五级错误分类与重试策略 (ErrorClassification)**

```python
class ErrorLevel(Enum):
    TRANSIENT = "transient"      # 网络抖动、临时文件锁 -> 立即重试 3 次
    RECOVERABLE = "recoverable"  # 剪映占用项目、素材路径变更 -> 等待后重试
    DEGRADED = "degraded"        # TTS 失败、特效不支持 -> 降级执行（跳过该步骤）
    FATAL = "fatal"              # 磁盘满、剪映崩溃 -> 终止并报告
    USER = "user"                # 参数错误、素材不存在 -> 暂停等待用户输入

RETRY_STRATEGIES = {
    ErrorLevel.TRANSIENT:    {"max_retries": 3, "wait": "exponential(1s, 30s)"},
    ErrorLevel.RECOVERABLE:  {"max_retries": 2, "wait": "fixed(5s)", "pre_action": "release_lock"},
    ErrorLevel.DEGRADED:     {"max_retries": 0, "fallback": "skip_step"},
    ErrorLevel.FATAL:        {"max_retries": 0, "action": "abort_and_notify"},
    ErrorLevel.USER:         {"max_retries": 0, "action": "pause_and_ask"},
}
```

**D-2: 断路器模式 (CircuitBreaker)**

```python
class EditCircuitBreaker:
    # 当同一类型错误连续出现 N 次时，断路器打开，阻止后续调用。
    #
    # 适用场景:
    # - 剪映 App 崩溃 -> 连续 3 次 PermissionError -> 断路，提示用户重启剪映
    # - TTS API 故障 -> 连续 3 次 TimeoutError -> 断路，提示切换后端
    # - 磁盘空间不足 -> 连续 2 次 OSError -> 断路，提示清理空间
    failure_threshold: int = 3
    recovery_timeout: int = 60  # 秒
    state: str = "closed"  # closed | open | half_open
```

**D-3: 全链路追踪集成 (FullChainTracing)**

```python
class EditTracer:
    # 基于 Langfuse SDK v2 的全链路追踪。
    #
    # Trace 层级:
    # trace(task_id)
    # +-- span(plan_generation)        # LLM 生成分镜
    # |   +-- generation(qwen3.6)      # LLM 调用
    # |   +-- span(token_budget)       # Token 消耗
    # +-- span(step_1_import_media)
    # |   +-- span(resolve_media)      # 路径解析
    # |   +-- span(add_video_safe)     # 写入轨道
    # +-- span(step_2_add_subtitles)
    # |   +-- generation(qwen3.6)      # 字幕文本生成
    # |   +-- span(tts_synthesis)      # TTS 合成
    # |   +-- span(add_narrated_subs)  # 字幕写入
    # +-- span(step_3_export)
    # |   +-- span(ffmpeg_export)      # FFmpeg 导出
    # +-- span(quality_check)          # 质检
```

- 每个 span 记录: {name, input, output, duration_ms, status, metadata}
- FFmpeg 进度通过 stderr 解析实时更新 span metadata
- 导出 Perfetto JSON 格式，可在 Chrome DevTools chrome://tracing 中可视化

**D-4: 指标聚合与健康检查 (MetricsAggregation)**

```python
class EditMetrics:
    # 剪辑任务指标聚合
    task_duration_ms: Histogram       # 任务总耗时分布
    step_duration_ms: dict            # 各步骤耗时分布
    error_rate: Counter               # 错误率（按错误类型分）
    token_usage: Histogram            # Token 消耗分布
    ffmpeg_success_rate: Counter      # FFmpeg 成功率
    tts_latency_ms: Histogram         # TTS 延迟分布
```

- 存储: SQLite ~/.jianying_agent/metrics.db
- 查询: 支持过去 N 次任务的平均耗时、错误率最高的步骤等
- 健康检查端点: jianying-agent doctor 命令，检查:
  - Python 环境、pyJianYingDraft 版本
  - 剪映 App 是否运行
  - 磁盘空间
  - Langfuse 连接状态
  - 最近 5 次任务的成功率

**D-5: 结构化执行日志 (StructuredExecutionLog)**

```python
@dataclass
class StepExecutionLog:
    step_id: str
    tool_name: str
    input_args: dict
    output_summary: str          # <= 500 chars，返回给 LLM
    full_log_path: str           # 完整日志文件路径
    stdout_bytes: int
    stderr_bytes: int
    duration_ms: int
    exit_code: int
    retry_count: int
    error_level: str | None
    langfuse_trace_id: str | None
    langfuse_span_id: str | None
```

- 每个步骤生成一条结构化日志
- 完整 stdout/stderr 写入磁盘（无截断），LLM 只看摘要
- 支持 jianying-agent logs --task-id=xxx --step=3 查看详细日志

#### 验收标准

- [ ] FFmpeg 超时后，进程树被正确终止（Windows + Unix 双平台验证）
- [ ] 断路器在连续 3 次同类错误后触发，60 秒后自动半开
- [ ] Langfuse 面板可展示完整的 trace 层级（LLM -> 工具 -> subprocess）
- [ ] jianying-agent doctor 输出所有健康检查项
- [ ] 500MB 的 FFmpeg 日志完整写入磁盘，返回给 LLM 的摘要 <= 500 chars

---

## 四、实施路线图 (Implementation Roadmap)

### P0 -- 核心基础架构（第 1-3 周）

> **目标**: 建立状态机 + 持久化 + 精确 token 管理，解决崩溃即丢失的根本问题

| 编号 | 需求 | 模块 | 工作量 | 依赖 |
|------|------|------|--------|------|
| P0-1 | EditStepStateMachine 状态机 | A-1 | 3d | 无 |
| P0-2 | SQLite Checkpoint 持久化 | A-1 | 2d | P0-1 |
| P0-3 | StoryboardParser 分镜解析器 | A-3 | 2d | P0-1 |
| P0-4 | PreciseTokenCounter (tiktoken) | C-1 | 1d | 无 |
| P0-5 | TieredSkillInjection 分级注入 | C-2 | 2d | P0-4 |
| P0-6 | DeclarativeToolRegistry 声明式注册 | B-1 | 2d | 无 |
| P0-7 | ErrorClassification 五级错误分类 | D-1 | 1d | 无 |

**里程碑**: Agent 能解析分镜 JSON -> 按步骤执行 -> 崩溃后从 SQLite 恢复

### P1 -- 关键性能提升（第 4-6 周）

> **目标**: 并发执行 + 上下文压缩 + 全链路追踪，解决慢和看不见的问题

| 编号 | 需求 | 模块 | 工作量 | 依赖 |
|------|------|------|--------|------|
| P1-1 | ConcurrentToolOrchestrator 并发编排 | B-2 | 3d | P0-6 |
| P1-2 | 三层上下文压缩 (reactive/auto/collapse) | C-3 | 3d | P0-4 |
| P1-3 | FullChainTracing Langfuse 集成 | D-3 | 2d | 无 |
| P1-4 | CircuitBreaker 断路器 | D-2 | 1d | P0-7 |
| P1-5 | 分级超时与输出管理 | B-3 | 2d | P0-6 |
| P1-6 | StructuredExecutionLog 结构化日志 | D-5 | 2d | P1-3 |
| P1-7 | AutoDiscoverCLI 脚本自动发现 | B-4 | 1d | P0-6 |

**里程碑**: Agent 能并行加载素材、长对话不溢出、Langfuse 可查看完整 trace

### P2 -- 体验与扩展（第 7-10 周）

> **目标**: 暂停恢复 + 会话记忆 + 指标聚合，解决不好用的问题

| 编号 | 需求 | 模块 | 工作量 | 依赖 |
|------|------|------|--------|------|
| P2-1 | Pause/Resume/Replay 暂停恢复 | A-2 | 3d | P0-1, P0-2 |
| P2-2 | SessionMemoryManager 会话记忆 | C-4 | 2d | 无 |
| P2-3 | autoDream 空闲巩固 | C-5 | 2d | C-4 |
| P2-4 | MetricsAggregation 指标聚合 | D-4 | 2d | P1-3 |
| P2-5 | Fork SubAgent 并行分镜处理 | A-4 | 3d | P0-1, P1-1 |
| P2-6 | Perfetto 可视化导出 | D-3 | 1d | P1-3 |
| P2-7 | Feature Flag 体系 | B-1 | 1d | P0-6 |
| P2-8 | jianying-agent doctor 健康检查 | D-4 | 1d | P2-4 |

**里程碑**: Agent 支持暂停/恢复、记住用户偏好、可导出 Perfetto 时间线

---

## 五、架构对比总览

```
+-----------------------------------------------------------------------+
|                  当前架构 (LangChain Linear Agent)                      |
|                                                                       |
|  用户输入 -> LangChain Agent -> @tool 调用 -> subprocess.run -> 返回   |
|                       ^                                               |
|              InMemorySaver (内存)                                      |
|              Langfuse (仅 LLM 层)                                     |
|              token_utils (len//2)                                     |
+-----------------------------------------------------------------------+
|                            升级为                                      |
+-----------------------------------------------------------------------+
|                  目标架构 (EditStep Orchestrator)                       |
|                                                                       |
|  用户输入 -> StoryboardParser -> EditPlan -> EditStepStateMachine      |
|                                              |                        |
|                                    ConcurrentToolOrchestrator         |
|                                    +-------+-------+                  |
|                              read并发    write串行    compute限流      |
|                                    +-------+-------+                  |
|                                              |                        |
|                                    StructuredExecutionLog              |
|                                    +-- Langfuse Trace                 |
|                                    +-- SQLite Checkpoint              |
|                                    +-- SessionMemory                  |
|                                    +-- MetricsAggregation             |
|                                              |                        |
|                              三层上下文压缩 -> TokenBudgetManager       |
|                                              |                        |
|                              断路器 + 五级错误分类 -> 恢复/降级/终止    |
+-----------------------------------------------------------------------+
```

---

## 六、附录：源码级改造清单

| 文件 | 改造内容 | 对应需求 |
|------|----------|----------|
| jianying_agent.py | 替换 create_agent 为 EditStepStateMachine | A-1 |
| jianying_agent.py | 替换 InMemorySaver 为 SQLite Checkpoint | A-1 |
| token_utils.py | 替换 len(text)//2 为 tiktoken 精确计数 | C-1 |
| cli_executor.py | 替换 SCRIPT_REGISTRY 为自动发现 | B-4 |
| cli_executor.py | 替换 if/elif 路由为声明式 registry | B-1 |
| python_executor.py | 添加并行执行支持 (asyncio.subprocess) | B-2 |
| observability.py | 扩展 Langfuse span 生命周期管理 | D-3 |
| errors.py | 扩展为五级错误分类 + 重试策略 | D-1 |
| timeout_config.py | 分级超时配置 + 磁盘转储 | B-3 |
| skill_parser.py | 支持分级注入（Level 0/1/2） | C-2 |
| media_resolver.py | 添加 concurrency_safe 标记 | B-2 |
| process_utils.py | 统一进程树终止 + 断路器集成 | D-2 |
| 新增 edit_state_machine.py | 状态机核心实现 | A-1 |
| 新增 storyboard_parser.py | 分镜 JSON 解析 | A-3 |
| 新增 tool_orchestrator.py | 并发工具编排 | B-2 |
| 新增 context_compressor.py | 三层上下文压缩 | C-3 |
| 新增 session_memory.py | 会话记忆管理 | C-4 |
| 新增 circuit_breaker.py | 断路器模式 | D-2 |
| 新增 edit_metrics.py | 指标聚合 | D-4 |
| 新增 edit_tracer.py | 全链路追踪封装 | D-3 |

---

*文档结束。本文档基于 JianYing Agent 源码（skills_agent/ + jianying-editor-skill/）与 Claude Code 架构审计报告（architecture_audit_report.md）的深度交叉分析生成。*