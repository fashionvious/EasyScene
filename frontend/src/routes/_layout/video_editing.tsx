import { createFileRoute, Outlet, useParams, useNavigate } from "@tanstack/react-router"
import { useQuery } from "@tanstack/react-query"
import { FolderOpen, Loader2, Film } from "lucide-react"

const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

interface ScriptListItem {
  id: string
  script_name: string
  status: number
  create_time: string
}

async function getScriptList() {
  const token = localStorage.getItem("access_token")
  if (!token) throw new Error("用户未登录")

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/scripts`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })
  if (!response.ok) {
    throw new Error("获取剧本列表失败")
  }
  return response.json()
}

const STATUS_LABELS: Record<number, string> = {
  0: "草稿",
  1: "角色已生成",
  2: "分镜已生成",
  3: "视频已生成",
  4: "已废弃",
}

const STATUS_COLORS: Record<number, string> = {
  0: "bg-gray-400/60 text-white",
  1: "bg-blue-400/60 text-white",
  2: "bg-amber-400/60 text-white",
  3: "bg-emerald-400/60 text-white",
  4: "bg-red-400/60 text-white",
}

export const Route = createFileRoute("/_layout/video_editing")({
  component: VideoEditingLayout,
})

function VideoEditingLayout() {
  const params = useParams({ strict: false })
  const scriptId = params?.scriptId

  // 如果有 scriptId，说明匹配到了子路由，渲染子路由内容
  if (scriptId) {
    return <Outlet />
  }

  // 否则渲染剧本列表页
  return <VideoEditingList />
}

function VideoEditingList() {
  const navigate = useNavigate()
  const { data: scriptListData, isLoading, error } = useQuery({
    queryKey: ["scriptList"],
    queryFn: getScriptList,
    staleTime: 0,
    refetchOnMount: true,
  })

  const scripts: ScriptListItem[] = scriptListData?.scripts || []

  return (
    <div className="flex flex-col gap-8 p-6 md:p-10 min-h-full">
      {/* 标题区域 */}
      <div className="flex flex-col gap-2">
        <h1 className="text-4xl font-black tracking-tight flex items-center gap-3">
          <Film className="h-9 w-9 text-primary" />
          剪辑工厂
        </h1>
        <p className="text-body-semibold text-muted-foreground">
          选择你要剪辑的剧本吧
        </p>
      </div>

      {/* 剧本列表区域 */}
      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : error ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="rounded-full bg-destructive/10 p-4 mb-4">
            <Film className="h-8 w-8 text-destructive" />
          </div>
          <p className="text-muted-foreground">加载剧本列表失败，请稍后重试</p>
        </div>
      ) : scripts.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="rounded-full bg-muted p-4 mb-4">
            <FolderOpen className="h-8 w-8 text-muted-foreground" />
          </div>
          <h3 className="heading-feature mb-1">暂无剧本</h3>
          <p className="text-muted-foreground">
            先去文生视频页面创建一个剧本吧
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {scripts.map((script) => (
            <button
              key={script.id}
              type="button"
              onClick={() =>
                navigate({
                  to: "/video_editing/$scriptId",
                  params: { scriptId: script.id },
                })
              }
              className="group relative flex flex-col items-center justify-center gap-3 p-6
                rounded-[20px]
                bg-white/40 dark:bg-[#1a1b18]/40
                backdrop-blur-xl
                border border-white/50 dark:border-white/10
                shadow-[0_4px_30px_rgba(0,0,0,0.08)]
                hover:shadow-[0_8px_40px_rgba(0,0,0,0.15)]
                hover:bg-white/60 dark:hover:bg-[#1a1b18]/60
                hover:scale-[1.03]
                active:scale-[0.97]
                transition-all duration-200 ease-out
                cursor-pointer text-left"
            >
              {/* 文件夹图标 */}
              <div className="flex items-center justify-center w-14 h-14 rounded-[14px] bg-primary/20 group-hover:bg-primary/30 transition-colors duration-200">
                <FolderOpen className="h-7 w-7 text-primary" />
              </div>

              {/* 剧本名称 */}
              <span className="heading-feature text-center line-clamp-2 w-full">
                {script.script_name}
              </span>

              {/* 状态标签 */}
              <span
                className={`inline-flex items-center rounded-full px-3 py-0.5 text-small font-semibold ${STATUS_COLORS[script.status] ?? "bg-gray-400/60 text-white"}`}
              >
                {STATUS_LABELS[script.status] ?? "未知"}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
