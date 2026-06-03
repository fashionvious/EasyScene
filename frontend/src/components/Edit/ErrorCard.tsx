import { AlertTriangle, RefreshCw, SkipForward } from "lucide-react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"

const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem("access_token")
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

export interface ErrorCardProps {
  taskId: string
  stepIndex: number
  stepName: string
  error: string
  onDismiss: () => void
}

export function ErrorCard({
  taskId, stepIndex, stepName, error, onDismiss,
}: ErrorCardProps) {
  const queryClient = useQueryClient()

  const retryMutation = useMutation({
    mutationFn: () =>
      fetch(
        `${API_BASE_URL}/api/v1/edit/${taskId}/retry-step?step_index=${stepIndex}`,
        { method: "POST", headers: getAuthHeaders() },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["editProgress", taskId] })
      onDismiss()
    },
  })

  const skipMutation = useMutation({
    mutationFn: () =>
      fetch(
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
            步骤 {stepIndex + 1} &ldquo;{stepName}&rdquo; 失败
          </p>
          <p className="text-xs text-muted-foreground mt-1 line-clamp-3">
            {error}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="outline" size="sm"
          onClick={() => retryMutation.mutate()}
          disabled={retryMutation.isPending}
        >
          <RefreshCw className="h-3.5 w-3.5 mr-1" /> 重试此步骤
        </Button>
        <Button
          variant="ghost" size="sm"
          onClick={() => {
            if (window.confirm("确定跳过此步骤？")) {
              skipMutation.mutate()
            }
          }}
          disabled={skipMutation.isPending}
        >
          <SkipForward className="h-3.5 w-3.5 mr-1" /> 跳过
        </Button>
      </div>
    </div>
  )
}
