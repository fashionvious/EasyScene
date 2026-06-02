# 需求 18：前端错误卡片（ErrorCard）+ 控制工具栏（ControlBar）

> 原 PRD 编号: P1-6 (F-2, F-3) | 优先级: P1

## 1. 依赖关系

- **前置依赖**：req_08（API 路由 — 消费 pause/resume/cancel/retry-step/skip-step 端点）、req_12（用户交互队列 — ErrorCard 的"待决策任务"数据源）
- **被谁依赖**：无

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前前端代码（[video_editing/$scriptId.tsx](../../../frontend/src/routes/_layout/video_editing/$scriptId.tsx)）：

- 已有成熟的聊天面板（`ChatPanel` 组件）——流式 SSE 输出、工具步骤渲染、历史会话管理
- 已有状态轮询模式（`refetchInterval` 用于剧本生成状态）
- 已有 React 组件库（`@/components/ui/button`, `Dialog`, `ScrollArea`, `Textarea`）
- 使用 TanStack Router + TanStack Query + Tailwind CSS

**缺失的前端组件**（需新增）：

1. **ErrorCard** — 步骤失败时的错误详情 + 操作按钮（重试/跳过/手动修复）
2. **ControlBar** — 编辑任务的全局控制（暂停/继续/取消 + 进度条）

### 代码库校验结论

- TanStack Query 的 `useQuery` + `refetchInterval` 模式可直接用于轮询 `/edit/{task_id}/progress`
- 现有的 `Dialog` 组件可用于 ErrorCard 的弹出确认框
- `ToolStepsView` 组件（现有，第 314-353 行）已展示工具步骤状态，可扩展为步骤进度条的基础
- `lucide-react` 图标库已安装（`Pause`, `Play`, `X`, `RefreshCw`, `SkipForward`, `AlertTriangle` 可用）

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `frontend/src/components/Edit/ErrorCard.tsx` | 错误卡片组件 |
| **新增** | `frontend/src/components/Edit/ControlBar.tsx` | 控制工具栏组件 |
| 修改 | `frontend/src/routes/_layout/video_editing/$scriptId.tsx` | 集成 ErrorCard + ControlBar |

### 核心技术细节

**ControlBar 组件**：

```typescript
// components/Edit/ControlBar.tsx

interface ControlBarProps {
  taskId: string
  status: string    // "running" | "paused" | "completed" | "failed"
  currentStep: number
  totalSteps: number
  progressPct: number
  currentStepName?: string
}

export function ControlBar({
  taskId, status, currentStep, totalSteps, progressPct, currentStepName,
}: ControlBarProps) {
  const queryClient = useQueryClient()
  const isRunning = status === "running"
  const isPaused = status === "paused"

  const pauseMutation = useMutation({
    mutationFn: () => fetch(`${API_BASE_URL}/api/v1/edit/${taskId}/pause`, {
      method: "POST", headers: getAuthHeaders(),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["editProgress", taskId] }),
  })

  const resumeMutation = useMutation({
    mutationFn: () => fetch(`${API_BASE_URL}/api/v1/edit/${taskId}/resume`, {
      method: "POST", headers: getAuthHeaders(),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["editProgress", taskId] }),
  })

  const cancelMutation = useMutation({
    mutationFn: () => fetch(`${API_BASE_URL}/api/v1/edit/${taskId}/cancel`, {
      method: "POST", headers: getAuthHeaders(),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["editProgress", taskId] }),
  })

  return (
    <div className="flex items-center gap-3 px-4 py-2.5 rounded-lg border border-border/50 bg-card">
      {/* 控制按钮 */}
      <div className="flex items-center gap-1.5">
        {isRunning && (
          <Button variant="outline" size="sm"
            onClick={() => pauseMutation.mutate()}
            disabled={pauseMutation.isPending}>
            <Pause className="h-4 w-4 mr-1" /> 暂停
          </Button>
        )}
        {isPaused && (
          <Button variant="outline" size="sm"
            onClick={() => resumeMutation.mutate()}
            disabled={resumeMutation.isPending}>
            <Play className="h-4 w-4 mr-1" /> 继续
          </Button>
        )}
        {(isRunning || isPaused) && (
          <Button variant="ghost" size="sm"
            onClick={() => cancelMutation.mutate()}
            disabled={cancelMutation.isPending}>
            <X className="h-4 w-4 mr-1" /> 取消
          </Button>
        )}
      </div>

      {/* 进度条 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-muted-foreground">
            Step {currentStep + 1}/{totalSteps}
            {currentStepName && ` — ${currentStepName}`}
          </span>
          <span className="text-xs font-mono text-muted-foreground">{progressPct}%</span>
        </div>
        <div className="h-2 rounded-full bg-muted overflow-hidden">
          <div
            className="h-full rounded-full bg-primary transition-all duration-500"
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>
    </div>
  )
}
```

**ErrorCard 组件**：

```typescript
// components/Edit/ErrorCard.tsx

interface ErrorCardProps {
  taskId: string
  stepIndex: number
  stepName: string
  error: string
  onDismiss: () => void
}

export function ErrorCard({ taskId, stepIndex, stepName, error, onDismiss }: ErrorCardProps) {
  const queryClient = useQueryClient()

  const retryMutation = useMutation({
    mutationFn: () => fetch(
      `${API_BASE_URL}/api/v1/edit/${taskId}/retry-step?step_index=${stepIndex}`,
      { method: "POST", headers: getAuthHeaders() },
    ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["editProgress", taskId] })
      onDismiss()
    },
  })

  const skipMutation = useMutation({
    mutationFn: () => fetch(
      `${API_BASE_URL}/api/v1/edit/${taskId}/skip-step?step_index=${stepIndex}`,
      { method: "POST", headers: getAuthHeaders() },
    ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["editProgress", taskId] })
      onDismiss()
    },
  })

  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 space-y-3">
      <div className="flex items-start gap-3">
        <AlertTriangle className="h-5 w-5 text-amber-500 shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-amber-600 dark:text-amber-400">
            步骤 {stepIndex + 1} "{stepName}" 失败
          </p>
          <p className="text-xs text-muted-foreground mt-1 line-clamp-3">{error}</p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm"
          onClick={() => retryMutation.mutate()}
          disabled={retryMutation.isPending}>
          <RefreshCw className="h-3.5 w-3.5 mr-1" /> 重试此步骤
        </Button>
        <Button variant="ghost" size="sm"
          onClick={() => skipMutation.mutate()}
          disabled={skipMutation.isPending}>
          <SkipForward className="h-3.5 w-3.5 mr-1" /> 跳过
        </Button>
      </div>
    </div>
  )
}
```

### 容错与边界

- ControlBar 的 `pause` 仅在当前步骤完成后生效（后端行为，前端按钮即时响应）
- ErrorCard 的 `skip-step` 需确认（可使用 `Dialog` 弹出二次确认）
- ControlBar 在 `status=completed` 或 `status=failed` 时不显示操作按钮
- 所有 API 调用使用 `getAuthHeaders()` 携带 JWT token
- TanStack Query 的 `invalidateQueries` 确保操作后即时刷新数据

## 4. 验收标准 (DoD)

- [ ] ControlBar 在 `status=running` 时显示暂停按钮，点击后调用 `POST /edit/{task_id}/pause`
- [ ] ControlBar 在 `status=paused` 时显示继续按钮，点击后调用 `POST /edit/{task_id}/resume`
- [ ] ControlBar 的进度条正确显示 `progressPct` 百分比
- [ ] ErrorCard 显示失败步骤名称 + 错误信息
- [ ] ErrorCard 的"重试"按钮调用 `POST /edit/{task_id}/retry-step?step_index=N`
- [ ] ErrorCard 的"跳过"按钮调用 `POST /edit/{task_id}/skip-step?step_index=N`
- [ ] 操作后自动刷新进度数据（`invalidateQueries`）
- [ ] 取消操作时弹出确认框
