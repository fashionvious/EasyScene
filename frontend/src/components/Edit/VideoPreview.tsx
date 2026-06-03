import { Loader2, Video } from "lucide-react"

const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

export interface VideoPreviewProps {
  previewPath: string | null
  stepName: string
  isLoading: boolean
}

export function VideoPreview({ previewPath, stepName, isLoading }: VideoPreviewProps) {
  if (!previewPath && !isLoading) return null

  const videoUrl = previewPath
    ? `${API_BASE_URL}/static/${encodeURIComponent(previewPath)}`
    : null

  return (
    <div className="rounded-lg border border-border/50 bg-card overflow-hidden">
      <div className="px-4 py-2 border-b border-border/50">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Video className="h-4 w-4 text-primary" />
          步骤预览: {stepName}
        </h3>
      </div>

      <div className="p-1">
        {isLoading ? (
          <div className="flex items-center justify-center aspect-video bg-muted/30">
            <div className="flex flex-col items-center gap-2">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              <span className="text-xs text-muted-foreground">正在生成预览...</span>
            </div>
          </div>
        ) : videoUrl ? (
          <video
            src={videoUrl}
            controls
            preload="metadata"
            className="w-full h-auto object-contain rounded"
          >
            您的浏览器不支持视频播放
          </video>
        ) : null}
      </div>
    </div>
  )
}
