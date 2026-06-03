import { Pause, Play, X } from "lucide-react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"

const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem("access_token")
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

export interface ControlBarProps {
  taskId: string
  status: string
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
    mutationFn: () =>
      fetch(`${API_BASE_URL}/api/v1/edit/${taskId}/pause`, {
        method: "POST", headers: getAuthHeaders(),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["editProgress", taskId] }),
  })

  const resumeMutation = useMutation({
    mutationFn: () =>
      fetch(`${API_BASE_URL}/api/v1/edit/${taskId}/resume`, {
        method: "POST", headers: getAuthHeaders(),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["editProgress", taskId] }),
  })

  const cancelMutation = useMutation({
    mutationFn: () =>
      fetch(`${API_BASE_URL}/api/v1/edit/${taskId}/cancel`, {
        method: "POST", headers: getAuthHeaders(),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["editProgress", taskId] }),
  })

  return (
    <div className="flex items-center gap-3 px-4 py-2.5 rounded-lg border border-border/50 bg-card">
      <div className="flex items-center gap-1.5">
        {isRunning && (
          <Button
            variant="outline" size="sm"
            onClick={() => pauseMutation.mutate()}
            disabled={pauseMutation.isPending}
          >
            <Pause className="h-4 w-4 mr-1" /> 暂停
          </Button>
        )}
        {isPaused && (
          <Button
            variant="outline" size="sm"
            onClick={() => resumeMutation.mutate()}
            disabled={resumeMutation.isPending}
          >
            <Play className="h-4 w-4 mr-1" /> 继续
          </Button>
        )}
        {(isRunning || isPaused) && (
          <Button
            variant="ghost" size="sm"
            onClick={() => {
              if (window.confirm("确定取消？已完成步骤不会被删除")) {
                cancelMutation.mutate()
              }
            }}
            disabled={cancelMutation.isPending}
          >
            <X className="h-4 w-4 mr-1" /> 取消
          </Button>
        )}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-muted-foreground">
            Step {currentStep + 1}/{totalSteps}
            {currentStepName && ` — ${currentStepName}`}
          </span>
          <span className="text-xs font-mono text-muted-foreground">
            {progressPct}%
          </span>
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
