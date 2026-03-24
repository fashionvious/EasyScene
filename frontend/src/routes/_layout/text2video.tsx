import { useState } from "react"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { useMutation } from "@tanstack/react-query"
import {
  Video,
  Sparkles,
  Loader2,
  Check,
  AlertCircle,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Outlet } from "@tanstack/react-router"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"

// 类型定义
interface CreateScriptResponse {
  script_id: string
  script_name: string
  message: string
}

// API 基础 URL
const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

// 创建剧本API
async function createScript(
  scriptName: string,
  scriptContent: string,
): Promise<CreateScriptResponse> {
  // 1. 获取 Token (通常存储在 localStorage 中，键名可能是 'access_token' 或 'token')
  const token = localStorage.getItem("access_token"); 

  // 如果没有 token，可以提前抛出错误或重定向到登录页
  if (!token) {
    throw new Error("用户未登录，请先登录");
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/create-script`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      // 2. 添加 Authorization 头部
      "Authorization": `Bearer ${token}`,
    },
    body: JSON.stringify({ script_name: scriptName, script_content: scriptContent }),
  })

  if (!response.ok) {
    // 尝试解析后端返回的错误信息
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `创建剧本失败: ${response.statusText}`);
  }

  return response.json()
}

export const Route = createFileRoute("/_layout/text2video")({
  component: () => <Outlet />, // 仅仅作为容器，渲染子路由
})

function Text2Video() {
  const navigate = useNavigate()
  
  // 初始状态
  const [scriptName, setScriptName] = useState("")
  const [scriptContent, setScriptContent] = useState("")
  
  // 错误提示
  const [error, setError] = useState<string | null>(null)
  
  // 字数限制
  const SCRIPT_NAME_MIN = 1
  const SCRIPT_NAME_MAX = 30
  const SCRIPT_CONTENT_MIN = 10
  const SCRIPT_CONTENT_MAX = 500
  
  // 创建剧本
  const createScriptMutation = useMutation({
    mutationFn: () => createScript(scriptName, scriptContent),
    onSuccess: (data) => {
      // 跳转到子页面，显示等待生成状态
      navigate({
        to: "/text2video/$scriptId",
        params: { scriptId: data.script_id },
      })
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "创建剧本失败")
    },
  })
  
  // 处理确定按钮点击
  const handleSubmit = () => {
    // 验证剧本名
    if (!scriptName.trim()) {
      setError("请输入剧本名")
      return
    }
    if (scriptName.length < SCRIPT_NAME_MIN || scriptName.length > SCRIPT_NAME_MAX) {
      setError(`剧本名长度需在${SCRIPT_NAME_MIN}-${SCRIPT_NAME_MAX}字之间`)
      return
    }
    
    // 验证剧本内容
    if (!scriptContent.trim()) {
      setError("请输入剧本内容")
      return
    }
    if (scriptContent.length < SCRIPT_CONTENT_MIN || scriptContent.length > SCRIPT_CONTENT_MAX) {
      setError(`剧本内容长度需在${SCRIPT_CONTENT_MIN}-${SCRIPT_CONTENT_MAX}字之间`)
      return
    }
    
    setError(null)
    createScriptMutation.mutate()
  }

  return (
    <div className="flex flex-col gap-6">
      {/* 页面标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Video className="h-6 w-6 text-purple-500" />
            文生视频
          </h1>
          <p className="text-muted-foreground">
            输入你想要生成的剧本内容吧！
          </p>
        </div>
      </div>

      {/* 输入区域 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">剧本信息</CardTitle>
          <CardDescription>
            输入剧本名称和内容，AI将为你生成角色信息和分镜头脚本
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* 剧本名输入 */}
          <div className="space-y-2">
            <Label htmlFor="script-name">剧本名</Label>
            <Input
              id="script-name"
              placeholder="请输入剧本名（1-30字）"
              value={scriptName}
              onChange={(e) => setScriptName(e.target.value)}
              maxLength={SCRIPT_NAME_MAX}
              disabled={createScriptMutation.isPending}
            />
            <p className="text-xs text-muted-foreground">
              {scriptName.length} / {SCRIPT_NAME_MAX} 字
            </p>
          </div>
          
          {/* 剧本内容输入 */}
          <div className="space-y-2">
            <Label htmlFor="script-content">剧本内容</Label>
            <Textarea
              id="script-content"
              placeholder="请输入剧本内容（10-500字）"
              value={scriptContent}
              onChange={(e) => setScriptContent(e.target.value)}
              maxLength={SCRIPT_CONTENT_MAX}
              className="min-h-[200px]"
              disabled={createScriptMutation.isPending}
            />
            <p className="text-xs text-muted-foreground">
              {scriptContent.length} / {SCRIPT_CONTENT_MAX} 字
            </p>
          </div>
          
          {/* 确定按钮 */}
          <Button
            onClick={handleSubmit}
            disabled={createScriptMutation.isPending}
            className="w-full"
          >
            {createScriptMutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                正在创建...
              </>
            ) : (
              <>
                <Check className="h-4 w-4 mr-2" />
                确定
              </>
            )}
          </Button>
        </CardContent>
      </Card>

      {/* 错误提示 */}
      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* 空状态 */}
      <div className="flex flex-col items-center justify-center text-center py-12">
        <div className="rounded-full bg-muted p-4 mb-4">
          <Video className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold">开始创作视频</h3>
        <p className="text-muted-foreground">
          输入剧本信息，AI将为你生成角色信息和分镜头脚本
        </p>
      </div>
    </div>
  )
}
