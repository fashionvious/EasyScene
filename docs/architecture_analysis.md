# Hermes Agent 源码架构分析报告

## 第一部分：全局项目架构扫描 (Global Overview)

### 1. 整体项目架构

**技术栈**：TypeScript + React (Ink UI) + Bun 运行时

**核心框架**：这是一个 CLI-based AI Agent 框架（Hermes/Claude Code），采用以下架构模式：

| 层级 | 描述 |
|------|------|
| **入口层** | `main.tsx` - CLI 命令解析与初始化 |
| **REPL层** | `replLauncher.tsx` → `screens/REPL.tsx` - 交互式对话循环 |
| **查询引擎层** | `query.ts` - 核心 Agentic Loop（异步生成器模式） |
| **工具执行层** | `services/tools/toolOrchestration.ts` + `toolExecution.ts` |
| **工具实现层** | `tools/*` - BashTool, FileReadTool, AgentTool 等 |

**核心入口文件**：
- `/mnt/d/Java/hermes_documents/src/main.tsx` (803KB, 4683行) - CLI 入口
- `/mnt/d/Java/hermes_documents/src/query.ts` (68KB, 1729行) - 查询引擎核心
- `/mnt/d/Java/hermes_documents/src/tools/AgentTool/AgentTool.tsx` (233KB) - 多Agent工具

**数据流向**：
```
用户输入 → REPL → query() → LLM API → 解析响应
                                        ↓
                              工具调用 (tool_use)
                                        ↓
                        toolOrchestration → toolExecution → 具体Tool
                                        ↓
                              收集结果 → 下一轮query
```

### 2. 核心目录与文件

| 目录/文件 | 作用描述 |
|-----------|----------|
| `query.ts` | **核心Agentic循环** - 包含 `while(true)` 主循环，管理消息状态、上下文压缩、工具执行调度 |
| `tools/` | 所有工具实现：BashTool（命令执行）、FileReadTool、FileWriteTool、AgentTool（多Agent）、WebSearchTool等 |
| `tools/AgentTool/` | 多Agent调度系统：`AgentTool.tsx`、`runAgent.ts`（子Agent运行）、`forkSubagent.ts`（分叉子Agent） |
| `services/tools/` | 工具编排：`toolOrchestration.ts`（并发/串行调度）、`toolExecution.ts`（工具执行核心） |
| `services/compact/` | 上下文压缩：`autoCompact.ts`（自动压缩触发）、`compact.ts`（压缩实现） |
| `commands.ts` | CLI 命令注册表（754行，60+命令） |
| `coordinator/` | Coordinator 模式 - 多Agent协调器 |
| `utils/Shell.ts` | Shell 命令执行底层实现 |
| `utils/timeouts.ts` | 超时配置（默认2分钟，最大10分钟） |

### 3. 可用工具集 (Tools)

**已注册的核心工具**（来自 `tools.ts`）：

| 工具名 | 输入定义 | 输出定义 |
|--------|----------|----------|
| `BashTool` | `{command: string, timeout?: number, run_in_background?: boolean}` | `ExecResult {stdout, stderr, code}` |
| `FileReadTool` | `{path: string, offset?: number, limit?: number}` | 文件内容字符串 |
| `FileWriteTool` | `{path: string, content: string}` | 写入确认 |
| `FileEditTool` | `{path: string, old_string: string, new_string: string}` | 编辑确认 |
| `GlobTool` | `{pattern: string}` | 匹配的文件列表 |
| `GrepTool` | `{pattern: string, path?: string}` | 匹配结果 |
| `WebFetchTool` | `{url: string}` | 网页内容 |
| `WebSearchTool` | `{query: string}` | 搜索结果 |
| `AgentTool` | `{description: string, prompt: string, subagent_type?: string}` | 子Agent执行结果 |
| `TodoWriteTool` | `{todos: TodoItem[]}` | 任务列表更新 |
| `TaskCreateTool/GetTool/UpdateTool/ListTool` | 任务管理参数 | 任务状态 |

**工具执行并发控制**（`toolOrchestration.ts`）：
- 读取类工具（readonly）并发执行
- 写入类工具串行执行
- 最大并发数由 `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` 控制（默认10）

### 4. 可用指令 (Commands)

**核心命令**（来自 `commands.ts`）：
- `/compact` - 手动压缩上下文
- `/cost` - Token 消耗统计
- `/config` - 配置管理
- `/memory` - 记忆管理
- `/session` - 会话管理
- `/resume` - 恢复会话
- `/commit` - Git 提交
- `/commit-push-pr` - 提交并创建PR
- `/review` - 代码审查
- `/skills` - 技能管理
- `/mcp` - MCP 服务器管理
- `/tasks` - 任务管理

---

## 第二部分：核心机制与控制流提取

### 1. Agentic Loop (循环与退出机制)

**实现位置**：`query.ts` 第307行 `while (true)`

**循环维持机制**：
```typescript
// query.ts:307
while (true) {
  // 1. 准备阶段：获取消息、工具上下文
  const { messages, toolUseContext, turnCount } = state;
  
  // 2. 调用 LLM API
  yield { type: 'stream_request_start' };
  
  // 3. 处理响应：执行工具或返回结果
  
  // 4. 更新状态并继续下一轮
  state = { messages: [...], turnCount: nextTurnCount, ... };
}
```

**退出条件**（第1704-1711行）：
```typescript
// 最大轮次限制
if (maxTurns && nextTurnCount > maxTurns) {
  yield createAttachmentMessage({
    type: 'max_turns_reached',
    maxTurns,
    turnCount: nextTurnCount,
  });
  return { reason: 'max_turns', turnCount: nextTurnCount };
}
```

**重试限制**：
- `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3` - 输出token超限重试次数
- `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3` - 连续压缩失败次数上限
- AbortSignal 中断支持 - 外部可中断循环

**死循环防护**：
- 通过 `maxTurns` 参数限制最大轮次
- 通过 `taskBudget` 限制总token消耗
- 通过 abortController 支持外部中断

### 2. 子Agent调度与上下文隔离

**实现位置**：`tools/AgentTool/runAgent.ts`、`tools/AgentTool/forkSubagent.ts`

**派生子任务机制**：
```typescript
// AgentTool.tsx 使用 runAgent() 启动子Agent
export async function* runAgent(params) {
  // 1. 创建子Agent上下文（隔离的消息历史）
  const subagentContext = createSubagentContext(params);
  
  // 2. 调用 query() 执行子任务
  const result = yield* query({
    messages: params.messages,
    systemPrompt: buildAgentSystemPrompt(agentDefinition),
    toolUseContext: createIsolatedToolContext(),
    ...
  });
  
  return result;
}
```

**上下文隔离**：
- 子Agent 拥有独立的消息历史
- 独立的 MCP 服务器连接（`initializeAgentMcpServers`）
- 独立的工作目录（支持 worktree 隔离模式）

**结果过滤**：
- 子Agent 结果通过 `extractPartialResult()` 提取关键信息
- 支持 `TaskOutput` 工具输出截断（`MAX_TASK_OUTPUT_BYTES`）

### 3. 工具执行的健壮性

**超时机制**（`utils/timeouts.ts` + `BashTool.tsx`）：

```typescript
// 默认超时配置
const DEFAULT_TIMEOUT_MS = 120_000;  // 2分钟
const MAX_TIMEOUT_MS = 600_000;      // 10分钟

// BashTool 使用
const timeoutMs = timeout || getDefaultTimeoutMs();
```

**超时处理**（`ShellCommand.ts`）：
- 支持 `onTimeout` 回调
- 超时后可自动转为后台任务（`auto-background`）
- 使用 `tree-kill` 终止进程树

**海量日志截断**（`utils/toolResultStorage.ts`）：
```typescript
// 工具结果大小限制
MAX_TASK_OUTPUT_BYTES = ...;        // 文件存储阈值
MAX_TASK_OUTPUT_BYTES_DISPLAY = ...; // 显示截断阈值

// 大结果处理
if (output.length > MAX_TASK_OUTPUT_BYTES) {
  // 1. 写入磁盘文件
  // 2. 返回文件路径引用
  return buildLargeToolResultMessage(filePath);
}
```

**实现位置**：
- `tools/BashTool/BashTool.tsx` - 超时配置
- `utils/Shell.ts` - 执行超时（`commandTimeout`）
- `utils/ShellCommand.ts` - `SIZE_WATCHDOG_INTERVAL_MS = 5000` 磁盘大小监控

### 4. 上下文窗口管理

**实现位置**：`services/compact/autoCompact.ts`

**压缩触发机制**（水位线规则）：
```typescript
// autoCompact.ts:62-64
export const AUTOCOMPACT_BUFFER_TOKENS = 13_000;
export const WARNING_THRESHOLD_BUFFER_TOKENS = 20_000;
export const ERROR_THRESHOLD_BUFFER_TOKENS = 20_000;

// 计算压缩阈值
export function getAutoCompactThreshold(model: string): number {
  const effectiveContextWindow = getEffectiveContextWindowSize(model);
  return effectiveContextWindow - AUTOCOMPACT_BUFFER_TOKENS;
}
```

**Token 使用监控**：
```typescript
// query.ts 使用 tokenCountWithEstimation() 计算当前token
// 当接近阈值时触发 compactConversation()
```

**压缩流程**：
1. `calculateTokenWarningState()` 检查是否超过阈值
2. 触发 `compactConversation()` 生成摘要
3. 替换历史消息为摘要消息
4. 继续循环

### 5. 安全与权限控制

**实现位置**：`tools/BashTool/bashPermissions.ts`、`utils/permissions/`

**高危操作定义**：
```typescript
// bashPermissions.ts 通过分类器检测危险命令
export function bashToolHasPermission(
  input: BashToolInput,
  context: ToolPermissionContext
): PermissionResult {
  // 1. 解析命令 AST
  const ast = parseForSecurity(command);
  
  // 2. 检查语义（破坏性操作检测）
  checkSemantics(ast);
  
  // 3. 分类器判断（allow/ask/deny）
  return classifyBashCommand(command, context);
}
```

**人类确认机制**：
- 权限检查返回 `{ result: false, message: "需要确认" }` 时挂起执行
- 通过 `CanUseToolFn` 回调等待用户确认
- 支持 `permissionMode` 配置（auto/plan/deny）

**危险命令警告**（`destructiveCommandWarning.ts`）：
- `git reset --hard`、`rm -rf` 等命令触发警告
- 通过正则和 AST 分析识别

### 6. 可观测性埋点

**实现位置**：`services/analytics/`、`utils/telemetry/`

**埋点机制**：
```typescript
// 全局事件日志
import { logEvent } from 'src/services/analytics/index.js';

logEvent('tengu_tool_use_error', {
  toolName: sanitizedToolName,
  error: 'No such tool available',
  queryChainId: toolUseContext.queryTracking?.chainId,
  ...
});
```

**追踪组件**：
- `sessionTracing.ts` - OpenTelemetry 集成
- `perfettoTracing.ts` - Perfetto 追踪
- GrowthBook - 功能开关和 A/B 测试

**记录内容**：
- 工具调用结果和错误
- Token 消耗统计
- 查询链深度
- 压缩事件

### 7. 状态合并与冲突解决

**实现位置**：`services/tools/toolOrchestration.ts`

**并发工具执行状态合并**：
```typescript
// toolOrchestration.ts:19-82
export async function* runTools(...) {
  for (const { isConcurrencySafe, blocks } of partitionToolCalls(...)) {
    if (isConcurrencySafe) {
      // 并发执行读取类工具
      const queuedContextModifiers = {};
      for await (const update of runToolsConcurrently(...)) {
        // 收集上下文修改器
        if (update.contextModifier) {
          queuedContextModifiers[toolUseID].push(modifyContext);
        }
      }
      // 按顺序应用上下文修改
      for (const block of blocks) {
        for (const modifier of queuedContextModifiers[block.id]) {
          currentContext = modifier(currentContext);
        }
      }
    } else {
      // 串行执行写入类工具
      for await (const update of runToolsSerially(...)) {
        // 逐个更新上下文
      }
    }
  }
}
```

**冲突解决策略**：
- 读取类操作：并发执行，结果合并
- 写入类操作：串行执行，避免冲突
- 上下文修改：收集后按顺序应用

**源码中未发现明显实现的机制**：
- 并行 Agent 返回结果的 diff/merge 逻辑
- 复杂的冲突解决策略（如三方合并）

---

## 第三部分：借鉴与迁移建议

### Python + LangGraph 复刻建议

**实现难度最高、最需注意的陷阱**：

1. **异步生成器模式的 Agentic Loop**（难度：★★★★★）
   - 原代码大量使用 `async function*` 和 `yield*` 实现流式响应
   - Python 中需使用 `async generator` + `yield` 模拟
   - **陷阱**：生成器的生命周期管理、异常传播、提前终止处理

2. **工具执行的并发/串行调度**（难度：★★★★☆）
   - 需要实现 `partitionToolCalls` 的 readonly 检测逻辑
   - 并发执行时的上下文修改器收集和顺序应用
   - **陷阱**：Python asyncio 的任务取消、异常处理

3. **上下文压缩与 Token 管理**（难度：★★★★☆）
   - `autoCompact` 需要精确的 token 计算
   - 压缩边界的维护（`compactBoundary`）
   - **陷阱**：token 计算的准确性、压缩后消息历史的一致性

4. **多 Agent 隔离与通信**（难度：★★★★☆）
   - 子 Agent 的上下文隔离
   - Worktree 模式的文件系统隔离
   - **陷阱**：Python 的进程隔离不如 Node.js 成熟

5. **权限系统的 AST 分析**（难度：★★★☆☆）
   - Bash 命令的安全分析需要 tree-sitter
   - Python 可用 `tree-sitter` 绑定，但生态不如 JS 成熟

### 推荐的实现路径

```
1. 先实现基础 query loop（简化版，无流式）
2. 添加工具执行框架（串行模式）
3. 实现上下文压缩（基于 token 估算）
4. 添加并发工具执行
5. 实现多 Agent 调度
6. 最后添加权限系统
```

### 关键设计决策

| 原代码方案 | Python 替代方案 |
|------------|-----------------|
| `async function*` 流式生成器 | `asyncio.Queue` + `async generator` |
| `AbortController` 中断 | `asyncio.Event` + `task.cancel()` |
| `ChildProcess.spawn` 执行 | `asyncio.create_subprocess_exec` |
| `tree-kill` 进程组终止 | `os.killpg()` 信号组 |
| `tree-sitter` Bash 解析 | `tree-sitter-python` 绑定 |
| `Ink` React CLI UI | `rich` / `textual` |

---

## 总结

Hermes Agent 的核心设计亮点：
1. **生成器模式的 Agentic Loop** - 流式响应 + 状态管理
2. **分区并发执行** - 读写分离的工具调度
3. **多层压缩机制** - snip + microcompact + autocompact
4. **灵活的多 Agent 系统** - 支持 fork、worktree、远程执行

最大的技术挑战在于将 TypeScript 的异步生成器模式迁移到 Python，以及确保并发执行时的状态一致性。
