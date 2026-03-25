import { createFileRoute, Outlet, useParams, Link } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { Video, FileText, Loader2 } from "lucide-react"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"

// API 基础 URL
const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

// 剧本列表项类型
interface ScriptListItem {
  id: string
  script_name: string
  status: number
  create_time: string
}

// 获取剧本列表
async function getScriptList() {
  const token = localStorage.getItem("access_token")
  if (!token) throw new Error("用户未登录")

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/scripts`, {
    headers: {
      "Authorization": `Bearer ${token}`
    }
  })
  if (!response.ok) {
    throw new Error(`获取剧本列表失败`)
  }
  return response.json()
}

// 获取剧本基本信息
async function getScriptInfo(scriptId: string) {
  const token = localStorage.getItem("access_token")
  if (!token) throw new Error("用户未登录")

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/script-status/${scriptId}`, {
    headers: {
      "Authorization": `Bearer ${token}`
    }
  })
  if (!response.ok) {
    throw new Error(`获取剧本信息失败`)
  }
  return response.json()
}

export const Route = createFileRoute("/_layout/text2video")({
  component: Text2VideoLayout,
})

function Text2VideoLayout() {
  const params = useParams({ strict: false })
  const scriptId = params?.scriptId

  // 获取剧本列表
  const { data: scriptListData, isLoading: isLoadingList } = useQuery({
    queryKey: ["scriptList"],
    queryFn: getScriptList,
    // 确保每次进入页面都重新获取最新数据
    staleTime: 0,
    refetchOnMount: true,
  })

  // 获取当前剧本信息（如果选中了某个剧本）
  const { data: scriptInfo } = useQuery({
    queryKey: ["scriptInfo", scriptId],
    queryFn: () => getScriptInfo(scriptId),
    enabled: !!scriptId,
    // 确保每次切换剧本都重新获取
    staleTime: 0,
    refetchOnMount: true,
  })

  const scripts = scriptListData?.scripts || []

  return (
    <div className="flex h-full">
      {/* 子侧边栏 - 显示剧本列表 */}
      <div className="w-32 border-r bg-muted/10 flex flex-col">
        <div className="p-4 border-b">
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <Video className="h-4 w-4 text-purple-500" />
            剧本列表
          </h2>
        </div>
        <ScrollArea className="flex-1">
          <div className="p-2">
            {isLoadingList ? (
              <div className="flex items-center justify-center p-3">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              </div>
            ) : scripts.length > 0 ? (
              scripts.map((script: ScriptListItem) => (
                <Link
                  key={script.id}
                  to="/text2video/$scriptId"
                  params={{ scriptId: script.id }}
                  className={cn(
                    "flex items-center gap-2 p-3 rounded-lg text-sm mb-1",
                    scriptId === script.id
                      ? "bg-purple-500/10 text-purple-700 dark:text-purple-300"
                      : "hover:bg-muted/50 text-muted-foreground hover:text-foreground"
                  )}
                >
                  <FileText className="h-4 w-4 flex-shrink-0" />
                  <span className="truncate">
                    {script.script_name}
                  </span>
                </Link>
              ))
            ) : (
              <div className="p-3 text-sm text-muted-foreground">
                暂无剧本
              </div>
            )}
          </div>
        </ScrollArea>
      </div>

      {/* 主内容区域 */}
      <div className="flex-1 overflow-auto">
        <Outlet />
      </div>
    </div>
  )
}
