# Claudecode 源码架构审计报告 (v2.0)

## 一、关键机制的源码锚点 (Source Code Anchors)

针对 7 个核心设计模式，精确导航如下：

### 2.1 Agentic Loop (循环与退出)

- **主循环**: `query.ts:307` `while(true)` — 持续至 `query.ts:1728`
- **最大轮次限制**: `query.ts:1705-1708` — `if (maxTurns && nextTurnCount > maxTurns)`
- **MAX_OUTPUT_TOKENS_RECOVERY_LIMIT**: `query.ts:164` — `const MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3`
- **Token 预算**: `query.ts:291` `taskBudgetRemaining`, `query.ts:508-513` 压缩后更新
- **中断支持**: `query.ts:1223` 通过 `maxOutputTokensRecoveryCount` 恢复逻辑

### 2.2 子 Agent 调度

- **AgentTool 入口**: `tools/AgentTool/AgentTool.tsx`
- **标准子 Agent**: `tools/AgentTool/runAgent.ts` — 隔离上下文 + 独立 MCP
- **Fork 子 Agent**: `tools/AgentTool/forkSubagent.ts` — 继承父 Agent 完整上下文，与 coordinator 互斥
- **恢复 Agent**: `tools/AgentTool/resumeAgent.ts`
- **内存/快照**: `tools/AgentTool/agentMemory.ts`, `tools/AgentTool/agentMemorySnapshot.ts`
- **团队管理**: `tools/TeamCreateTool/`, `tools/TeamDeleteTool/`
- **Agent 间通信**: `tools/SendMessageTool/SendMessageTool.ts`
- **调度 Agent**: `tools/TaskCreateTool/`, `tools/TaskGetTool/`, `tools/TaskListTool/`, `tools/TaskOutputTool/`

### 2.3 工具执行健壮性

- **超时配置**: `utils/timeouts.ts` — `DEFAULT_TIMEOUT_MS = 120_000`, `MAX_TIMEOUT_MS = 600_000`
- **超时处理**: `utils/ShellCommand.ts:154` — `maxOutputBytes = MAX_TASK_OUTPUT_BYTES`
- **海量输出截断**: `utils/task/diskOutput.ts:30` — `MAX_TASK_OUTPUT_BYTES = 5 * 1024 * 1024 * 1024`（5GB 磁盘上限）
- **进程树终止**: 报告声称 "tree-kill"，实际通过 `utils/ShellCommand.ts` 内建的进程管理
- **后台任务自动降解**: `utils/ShellCommand.ts` — 输出过大时自动转为后台任务

### 2.4 上下文窗口管理

- **autoCompact**: `services/compact/autoCompact.ts:62-64` — `AUTOCOMPACT_BUFFER_TOKENS = 13_000`, `WARNING_THRESHOLD_BUFFER_TOKENS = 20_000`
- **reactiveCompact**: `services/compact/reactiveCompact.js` — 按需触发压缩，通过 `REACTIVE_COMPACT` feature flag
- **microCompact**: `services/compact/microCompact.ts` — 微压缩
- **sessionMemoryCompact**: `services/compact/sessionMemoryCompact.ts` — 会话记忆压缩
- **apiMicrocompact**: `services/compact/apiMicrocompact.ts` — API 层微压缩
- **contextCollapse**: `services/contextCollapse/` — 上下文折叠机制
- **postCompactCleanup**: `services/compact/postCompactCleanup.ts` — 压缩后清理
- **tokenCountWithEstimation**: `utils/tokens.ts` — `tokenCountWithEstimation()` 函数
- **maxOutputTokensForModel**: `services/api/claude.ts` — `getMaxOutputTokensForModel()`
- **MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES**: `services/compact/autoCompact.ts:70` — `= 3`

### 2.5 安全与权限控制

- **Bash 权限入口**: `tools/BashTool/bashPermissions.ts:1-60`
- **AST 分析+分类器**: `utils/bash/ast.ts` — `parseForSecurityFromAst()`, `checkSemantics()`
- **Shell 命令解析**: `utils/bash/parser.ts` — `parseCommandRaw()`, `utils/bash/shellQuote.ts` — `tryParseShellCommand()`
- **分类器**: `utils/permissions/bashClassifier.ts` — `classifyBashCommand()`
- **权限规则**: `utils/permissions/PermissionRule.ts`, `utils/permissions/PermissionUpdateSchema.ts`
- **破坏性命令警告**: `tools/BashTool/destructiveCommandWarning.ts`
- **路径验证**: `tools/BashTool/pathValidation.ts`, `tools/BashTool/readOnlyValidation.ts`
- **沙箱**: `tools/BashTool/shouldUseSandbox.ts`
- **模式验证**: `tools/BashTool/modeValidation.ts`

### 2.6 可观测性埋点

- **事件日志**: `services/analytics/index.ts` — `logEvent()` 全局函数
- **Datadog**: `services/analytics/datadog.ts`
- **GrowthBook**: `services/analytics/growthbook.ts` — Feature Flag + A/B 测试
- **Session Tracing (OpenTelemetry)**: `utils/telemetry/sessionTracing.ts`
- **Perfetto Tracing**: `utils/telemetry/perfettoTracing.ts`
- **工具调用追踪**: `services/tools/toolExecution.ts:100` — 引用 `sessionTracing`
- **API 层日志**: `services/api/logging.ts:30` — 引用 `sessionTracing`
- **诊断追踪**: `services/diagnosticTracking.ts`

### 2.7 状态合并与冲突解决

- **工具编排**: `services/tools/toolOrchestration.ts:19-60`
- **并发工具执行**: `services/tools/toolOrchestration.ts:36-53` — `runToolsConcurrently()` + context modifier 收集
- **Streaming Tool Executor**: `services/tools/StreamingToolExecutor.ts`
- **最大并发**: `services/tools/toolOrchestration.ts:8-12` — `getMaxToolUseConcurrency()` 默认 10
- **方法签名**: `function* runTools()` 异步生成器模式

---

## 二、架构亮点拾遗 (Hidden Gems)

源码中包含报告完全遗漏的工程化精妙设计：

### 3.1 多层上下文压缩体系

报告仅提到 `autoCompact`，实际有 **6 层**压缩策略协同工作：

| 层级 | 文件 | 触发条件 |
|------|------|---------|
| reactiveCompact | `services/compact/reactiveCompact.js` | 实时 token 阈值 |
| autoCompact | `services/compact/autoCompact.ts` | 接近上下文窗口 13K buffer |
| microCompact | `services/compact/microCompact.ts` | 单轮内 token 过高 |
| sessionMemoryCompact | `services/compact/sessionMemoryCompact.ts` | 会话级记忆压缩 |
| apiMicrocompact | `services/compact/apiMicrocompact.ts` | API 层压缩 |
| contextCollapse | `services/contextCollapse/` | 上下文完全折叠（feature flag） |

### 3.2 AI 驱动的上下文巩固 (autoDream)

`services/autoDream/autoDream.ts` — Agent 在空闲时通过 LLM "做梦"来巩固和总结上下文，生成 `consolidationPrompt.ts`。这是一个在 AI Agent 系统中极少见的创新机制。

### 3.3 动态工具加载与 Feature Flag 体系

`tools.ts` 中大量工具通过 `feature('XXX')` 动态加载（GrowthBook 控制），而非硬编码注册表：
- `REACTIVE_COMPACT`, `CONTEXT_COLLAPSE`, `PROACTIVE`, `KAIROS`, `AGENT_TRIGGERS`, `MONITOR_TOOL`, `FORK_SUBAGENT`, `KAIROS_GITHUB_WEBHOOKS`, `KAIROS_PUSH_NOTIFICATION`

这是一个**生产级灰度发布系统**，报告将工具清单描述为静态注册表是对这一架构的严重错误描述。

### 3.4 Fork Subagent 的上下文继承

`tools/AgentTool/forkSubagent.ts` — 不是普通子 Agent 调度，而是 **fork**：子 Agent 可以**继承父 Agent 的完整对话上下文和系统提示**，这与传统的隔离子 Agent 模式形成互补。Fork 模式与 coordinator 模式互斥（`forkSubagent.ts:34`）。

### 3.5 Agent 团队管理与通信

`TeamCreateTool`, `TeamDeleteTool`, `SendMessageTool` — 允许多个 Agent 组成团队并相互发送消息，实现 Agent Swarm（`utils/swarm/`）模式。报告完全遗漏了这些机制。

### 3.6 LSP 集成

`services/lsp/` — 完整的语言服务器协议客户端实现（`LSPClient.ts`, `LSPServerManager.ts`, `LSPDiagnosticRegistry.ts`），允许 Agent 直接与 IDE 的 LSP 通信获取诊断信息。

---

## 三、最终修订版架构文档 (v2.0)

### 4.1 整体项目架构

**技术栈**：TypeScript + React (Ink UI) + Bun 运行时

**核心框架**：CLI-based AI Agent 框架（Hermes/Claude Code），采用以下架构模式：

| 层级 | 描述 | 关键文件 |
|------|------|---------|
| **入口层** | CLI 命令解析与初始化 | `main.tsx` |
| **REPL层** | 交互式对话循环 | `replLauncher.tsx` → `screens/REPL.tsx` |
| **查询引擎层** | 核心 Agentic Loop（`async function*` 生成器模式） | `query.ts`（~1729 行，`while(true)` at L307） |
| **工具执行层** | 并发/串行调度 + 状态合并 | `services/tools/toolOrchestration.ts` + `toolExecution.ts` + `StreamingToolExecutor.ts` |
| **工具实现层** | 40+ 工具（含 feature-flag 条件加载） | `tools/*` (BashTool, AgentTool, FileReadTool, ToolSearchTool, LSPTool 等) |
| **压缩层** | 6 层压缩策略协同 | `services/compact/*` + `services/contextCollapse/` + `services/autoDream/` |

**数据流向**：
```
用户输入 → REPL → query() → LLM API → 解析响应
                                        ↓
                              工具调用 (tool_use)
                                        ↓
                        toolOrchestration → toolExecution → 具体Tool
                                        ↓
                              收集结果 → 下一轮 query
        [autoCompact/reactiveCompact/contextCollapse 在上述流程中持续监测]
```

### 4.2 核心目录与文件

| 目录/文件 | 作用描述 |
|-----------|----------|
| `query.ts` | **核心Agentic循环** — `while(true)` at L307-1728，包含消息状态管理、token 预算追踪、上下文压缩调度、工具执行调度 |
| `tools.ts` | **工具注册表** — 静态导入 + feature-flag 条件加载，40+ 工具 |
| `tools/AgentTool/` | 多Agent调度：`AgentTool.tsx`、`runAgent.ts`（隔离子Agent）、`forkSubagent.ts`（上下文继承 fork）、`resumeAgent.ts`（恢复） |
| `services/tools/` | 工具编排：`toolOrchestration.ts`（`function* runTools()` 生成器 + 并发/串行调度 + context modifier 收集）、`toolExecution.ts`、`StreamingToolExecutor.ts` |
| `services/compact/` | 上下文压缩：`autoCompact.ts`（自动触发）、`reactiveCompact.js`（按需触发）、`microCompact.ts`（微压缩）、`sessionMemoryCompact.ts`（记忆压缩）、`compact.ts`（压缩实现）、`postCompactCleanup.ts` |
| `services/autoDream/` | AI 驱动的上下文巩固 |
| `services/contextCollapse/` | 上下文折叠机制 |
| `services/analytics/` | 可观测性：`index.ts`（logEvent）、`growthbook.ts`（Feature Flag）、`datadog.ts` |
| `services/api/` | API 层：`claude.ts`（LLM 调用 + token 管理）、`withRetry.ts`（重试）、`errors.ts` |
| `services/lsp/` | LSP 集成：`LSPClient.ts`、`LSPServerManager.ts` |
| `commands.ts` | CLI 命令注册表 — **80+ 命令**（含 `/compact`, `/cost`, `/config`, `/memory`, `/session`, `/resume`, `/commit`, `/review`, `/skills`, `/mcp`, `/tasks`, `/ide`, `/init`, `/login`, `/doctor`, `/keybindings`, `/status`, `/context`, `/teleport` 等） |
| `coordinator/` | Coordinator 模式 — 多 Agent 协调器（与 fork 模式互斥） |
| `utils/Shell.ts` | Shell 命令执行底层实现 |
| `utils/ShellCommand.ts` | 命令执行 + 输出截断 + 后台任务自动降解 |
| `utils/timeouts.ts` | 超时配置：默认 120s / 最大 600s |
| `utils/tokens.ts` | Token 计数（`tokenCountWithEstimation`） |
| `utils/task/diskOutput.ts` | 大输出磁盘转储：`MAX_TASK_OUTPUT_BYTES = 5GB` |
| `utils/bash/` | Bash AST 解析 + 安全检查（`ast.ts`, `parser.ts`） |
| `utils/permissions/` | 权限系统（`bashClassifier.ts`, `PermissionRule.ts`） |
| `utils/telemetry/` | 追踪（`sessionTracing.ts` — OpenTelemetry, `perfettoTracing.ts` — Perfetto） |
| `utils/swarm/` | Agent Swarm 支持（`spawnInProcess.ts`, `inProcessRunner.ts`） |

### 4.3 可用工具集 (Tools)

**已注册的核心工具**（来自 `tools.ts`，按类别分组）：

#### 始终加载（核心工具）
| 工具名 | 文件 |
|--------|------|
| `AgentTool` | `tools/AgentTool/AgentTool.tsx` |
| `BashTool` | `tools/BashTool/BashTool.tsx` |
| `FileReadTool` | `tools/FileReadTool/FileReadTool.tsx` |
| `FileWriteTool` | `tools/FileWriteTool/FileWriteTool.tsx` |
| `FileEditTool` | `tools/FileEditTool/FileEditTool.tsx` |
| `GlobTool` | `tools/GlobTool/GlobTool.tsx` |
| `GrepTool` | `tools/GrepTool/GrepTool.tsx` |
| `WebFetchTool` | `tools/WebFetchTool/WebFetchTool.tsx` |
| `WebSearchTool` | `tools/WebSearchTool/WebSearchTool.tsx` |
| `TodoWriteTool` | `tools/TodoWriteTool/TodoWriteTool.tsx` |
| `TaskOutputTool` | `tools/TaskOutputTool/TaskOutputTool.tsx` |
| `TaskCreateTool` | `tools/TaskCreateTool/TaskCreateTool.tsx` |
| `TaskGetTool` | `tools/TaskGetTool/TaskGetTool.tsx` |
| `TaskListTool` | `tools/TaskListTool/TaskListTool.tsx` |
| `TaskUpdateTool` | `tools/TaskUpdateTool/TaskUpdateTool.tsx` |
| `TaskStopTool` | `tools/TaskStopTool/TaskStopTool.tsx` |
| `EnterPlanModeTool` | `tools/EnterPlanModeTool/EnterPlanModeTool.tsx` |
| `ExitPlanModeV2Tool` | `tools/ExitPlanModeTool/ExitPlanModeV2Tool.tsx` |
| `EnterWorktreeTool` | `tools/EnterWorktreeTool/EnterWorktreeTool.tsx` |
| `ExitWorktreeTool` | `tools/ExitWorktreeTool/ExitWorktreeTool.tsx` |
| `SkillTool` | `tools/SkillTool/SkillTool.tsx` |
| `AskUserQuestionTool` | `tools/AskUserQuestionTool/AskUserQuestionTool.tsx` |
| `BriefTool` | `tools/BriefTool/BriefTool.tsx` |
| `NotebookEditTool` | `tools/NotebookEditTool/NotebookEditTool.tsx` |
| `LSPTool` | `tools/LSPTool/LSPTool.tsx` |
| `ListMcpResourcesTool` | `tools/ListMcpResourcesTool/ListMcpResourcesTool.tsx` |
| `ReadMcpResourceTool` | `tools/ReadMcpResourceTool/ReadMcpResourceTool.tsx` |
| `ToolSearchTool` | `tools/ToolSearchTool/ToolSearchTool.tsx` |
| `SyntheticOutputTool` | `tools/SyntheticOutputTool/SyntheticOutputTool.tsx` |
| `SendMessageTool` | `tools/SendMessageTool/SendMessageTool.tsx` (lazy) |
| `TeamCreateTool` | `tools/TeamCreateTool/TeamCreateTool.tsx` (lazy) |
| `TeamDeleteTool` | `tools/TeamDeleteTool/TeamDeleteTool.tsx` (lazy) |
| `TungstenTool` | `tools/TungstenTool/TungstenTool.tsx` |

#### Feature Flag 条件加载
| 工具 | Feature Flag |
|------|-------------|
| `CronCreateTool/CronDeleteTool/CronListTool` | `AGENT_TRIGGERS` |
| `MonitorTool` | `MONITOR_TOOL` |
| `PushNotificationTool` | `KAIROS` / `KAIROS_PUSH_NOTIFICATION` |
| `SleepTool` | `PROACTIVE` / `KAIROS` |
| `REPLTool` | `USER_TYPE === 'ant'` |
| `SendUserFileTool` | `KAIROS` |
| `SubscribePRTool` | `KAIROS_GITHUB_WEBHOOKS` |

**工具执行并发控制**（`services/tools/toolOrchestration.ts`）：
- 读取类工具并发执行，写入类工具串行执行
- 最大并发数由 `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` 环境变量控制（默认 10）
- context modifier 收集后按顺序应用到当前上下文

### 4.4 可用指令 (Commands)

**核心命令**（来自 `commands.ts`，共 80+）：

基础操作：`/clear`, `/color`, `/help`, `/init`, `/login`, `/logout`, `/doctor`, `/status`, `/keybindings`, `/rename`, `/config`

会话管理：`/compact`, `/resume`, `/session`, `/context`, `/ctx_viz`, `/cost`, `/memory`

代码协作：`/commit`, `/commit-push-pr`, `/review`, `/diff`, `/pr_comments`, `/copy`

AI 增强：`/skills`, `/tasks`, `/mcp`, `/ide`, `/init-verifiers`, `/release-notes`, `/agents-platform`

高级功能：`/teleport`（远程会话传输）, `/fork`, `/team`, `/btw`, `/good-claude`, `/share`

AI 理解：`/issue`（反馈）, `/feedback`, `/desktop`, `/mobile`, `/install-github-app`, `/install-slack-app`, `/onboarding`

### 4.5 核心机制详述

#### 4.5.1 Agentic Loop (`query.ts`)

- **主循环**: `while(true)` at L307，退出 at L1728
- **最大轮次**: `maxTurns` 参数，在 L1705-1708 检查
- **输出 Token 恢复上限**: `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3`（L164）
- **Token 预算管理**: `taskBudget` 参数，`taskBudgetRemaining` 在每次压缩后更新（L508-513, L1138-1143）
- **中断支持**: `AbortSignal` + abortController 外部分中断

#### 4.5.2 子 Agent 调度 (`tools/AgentTool/`)

- **标准隔离模式** (`runAgent.ts`): 独立消息历史、独立 MCP、独立工作目录
- **Fork 模式** (`forkSubagent.ts`): 子 Agent 继承父 Agent 完整上下文 + 系统提示，与 coordinator 互斥
- **恢复模式** (`resumeAgent.ts`): 恢复中断的 Agent
- **团队模式**: `TeamCreateTool`/`TeamDeleteTool` + `SendMessageTool` 支持多 Agent 协作

#### 4.5.3 工具执行健壮性

- **超时**: 默认 120s / 最大 600s (`utils/timeouts.ts`)，支持环境变量 `BASH_DEFAULT_TIMEOUT_MS`/`BASH_MAX_TIMEOUT_MS`
- **输出截断**: 超过 5GB (`MAX_TASK_OUTPUT_BYTES`) 写入磁盘 + 返回引用路径 (`utils/task/diskOutput.ts`)
- **后台自动降解**: `utils/ShellCommand.ts` — 长时间命令自动转后台任务
- **重试**: `services/api/withRetry.ts` — API 调用级重试

#### 4.5.4 上下文窗口管理（6 层体系）

1. **autoCompact** (`services/compact/autoCompact.ts`): BUFFER=13K, WARNING=20K, MAX_CONSECUTIVE_FAILURES=3
2. **reactiveCompact** (`services/compact/reactiveCompact.js`): Feature flag `REACTIVE_COMPACT` 控制
3. **microCompact** (`services/compact/microCompact.ts`): 单轮内 token 压缩
4. **sessionMemoryCompact** (`services/compact/sessionMemoryCompact.ts`): 会话记忆压缩
5. **apiMicrocompact** (`services/compact/apiMicrocompact.ts`): API 层微压缩
6. **contextCollapse** (`services/contextCollapse/`): 上下文完全折叠

**压缩流程**：
1. `calculateTokenWarningState()` 检查 token 阈值
2. 触发 `compactConversation()` 生成摘要
3. `buildPostCompactMessages()` 替换历史消息
4. `runPostCompactCleanup()` 清理

#### 4.5.5 安全与权限控制 (`tools/BashTool/bashPermissions.ts`)

- **AST 分析**: `utils/bash/ast.ts` — `parseForSecurityFromAst()` + `checkSemantics()`
- **命令解析**: `utils/bash/parser.ts` — `parseCommandRaw()`
- **分类器**: `utils/permissions/bashClassifier.ts` — `classifyBashCommand()` 返回 allow/ask/deny
- **权限规则**: `utils/permissions/PermissionRule.ts` + `PermissionUpdateSchema.ts`
- **破坏性命令警告**: `tools/BashTool/destructiveCommandWarning.ts`
- **沙箱**: `tools/BashTool/shouldUseSandbox.ts`
- **人类确认**: `CanUseToolFn` 回调 + `permissionMode` 配置

#### 4.5.6 可观测性埋点

- **事件日志**: `services/analytics/index.ts` — `logEvent()`
- **OpenTelemetry**: `utils/telemetry/sessionTracing.ts`
- **Perfetto**: `utils/telemetry/perfettoTracing.ts`
- **Feature Flags**: `services/analytics/growthbook.ts` — GrowthBook A/B 测试
- **Datadog**: `services/analytics/datadog.ts`
- **诊断**: `services/diagnosticTracking.ts`
- **追踪范围**: 工具调用结果/错误, Token 消耗, 查询链深度, 压缩事件

#### 4.5.7 状态合并与冲突解决 (`services/tools/toolOrchestration.ts`)

- **生成器模式**: `async function* runTools()` — 流式 yield 结果
- **分区并发**: `partitionToolCalls()` 区分 `isConcurrencySafe` 批次
- **并发处理**: 读取类工具 `runToolsConcurrently()` 并发执行，context modifier 收集后顺序应用（L31-60）
- **串行处理**: 写入类工具 `runToolsSerially()` 逐个执行并更新上下文

---

## 四、Python + LangGraph 复刻建议（更新）

基于修正后的源码分析，修正原报告的迁移建议：

| 原报告建议 | 修正 |
|-----------|------|
| "先实现基础 query loop" | 应同时参考 6 层压缩体系 — token 管理是核心 |
| "添加工具执行框架（串行）" | 生成器模式是关键，需 `async def` generator + `yield` |
| "实现上下文压缩" | 不是单层 autoCompact，是 6 层协同 — 应优先实现 autoCompact + microCompact |
| "添加并发工具执行" | context modifier 收集+顺序应用是精髓 |
| "实现多 Agent 调度" | fork 模式（继承上下文）与 coordinator 模式（协调调度）是两种互补范式 |
| "添加权限系统" | AST 级别的 bash 解析需要 tree-sitter Python 绑定 |

**新增关键发现**：
- **autoDream 机制**：可作为独立组件实现，核心是 LLM 驱动的上下文巩固
- **GrowthBook/Feature Flag**：应引入配置驱动的功能开关体系

