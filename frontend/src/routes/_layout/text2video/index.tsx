// src/routes/_layout/text2video/index.tsx
import { useState } from "react"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { useMutation } from "@tanstack/react-query"
import {
  Video,
  Loader2,
  Check,
  AlertCircle,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"

// ... (复制之前 text2video.tsx 中的类型定义和 API 函数) ...
interface CreateScriptResponse {
  script_id: string
  script_name: string
  message: string
}

const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

async function createScript(
  scriptName: string,
  scriptContent: string,
): Promise<CreateScriptResponse> {
  const token = localStorage.getItem("access_token"); 
  if (!token) throw new Error("用户未登录，请先登录");

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/create-script`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`,
    },
    body: JSON.stringify({ script_name: scriptName, script_content: scriptContent }),
  })

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `创建剧本失败: ${response.statusText}`);
  }
  return response.json()
}

// 注意：路由路径改为 "/_layout/text2video/"
export const Route = createFileRoute("/_layout/text2video/")({
  component: Text2VideoIndex,
  head: () => ({ meta: [{ title: "文生视频 - FastAPI Cloud" }] }),
})

function Text2VideoIndex() {
  const navigate = useNavigate()
  const [scriptName, setScriptName] = useState("")
  const [scriptContent, setScriptContent] = useState("")
  const [error, setError] = useState<string | null>(null)
  
  const SCRIPT_NAME_MIN = 1
  const SCRIPT_NAME_MAX = 30
  const SCRIPT_CONTENT_MIN = 10
  const SCRIPT_CONTENT_MAX = 1000
  
  const createScriptMutation = useMutation({
    mutationFn: () => createScript(scriptName, scriptContent),
    onSuccess: (data) => {
      navigate({
        to: "/text2video/$scriptId",
        params: { scriptId: data.script_id },
      })
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "创建剧本失败")
    },
  })
  
  const handleSubmit = () => {
    // ... (复制之前的验证逻辑) ...
    if (!scriptName.trim()) { setError("请输入剧本名"); return }
    if (scriptName.length > SCRIPT_NAME_MAX) { setError(`剧本名长度需在${SCRIPT_NAME_MIN}-${SCRIPT_NAME_MAX}字之间`); return }
    if (!scriptContent.trim()) { setError("请输入剧本内容"); return }
    if (scriptContent.length < SCRIPT_CONTENT_MIN || scriptContent.length > SCRIPT_CONTENT_MAX) { setError(`剧本内容长度需在${SCRIPT_CONTENT_MIN}-${SCRIPT_CONTENT_MAX}字之间`); return }
    
    setError(null)
    createScriptMutation.mutate()
  }

  // ... (return 语句复制之前的 JSX 内容) ...
  return (
    <div className="flex flex-col gap-6 p-6 md:p-8">
      {/* 页面标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Video className="h-6 w-6 text-purple-500" />
            文生视频
          </h1>
          <p className="text-muted-foreground">输入你想要生成的剧本内容吧！</p>
        </div>
      </div>
      {/* 输入区域 Card ... */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">剧本信息</CardTitle>
          <CardDescription>输入剧本名称和内容，AI将为你生成角色信息和分镜头脚本</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="script-name">剧本名</Label>
            <Input id="script-name" placeholder="请输入剧本名（1-30字）" value={scriptName} onChange={(e) => setScriptName(e.target.value)} maxLength={SCRIPT_NAME_MAX} disabled={createScriptMutation.isPending} />
            <p className="text-xs text-muted-foreground">{scriptName.length} / {SCRIPT_NAME_MAX} 字</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="script-content">剧本内容</Label>
            <Textarea id="script-content" placeholder="请输入剧本内容（10-1000字）" value={scriptContent} onChange={(e) => setScriptContent(e.target.value)} maxLength={SCRIPT_CONTENT_MAX} className="min-h-[200px]" disabled={createScriptMutation.isPending} />
            <p className="text-xs text-muted-foreground">{scriptContent.length} / {SCRIPT_CONTENT_MAX} 字</p>
          </div>
          <Button onClick={handleSubmit} disabled={createScriptMutation.isPending} className="w-full">
            {createScriptMutation.isPending ? (<><Loader2 className="h-4 w-4 animate-spin mr-2" />正在创建...</>) : (<><Check className="h-4 w-4 mr-2" />确定</>)}
          </Button>
        </CardContent>
      </Card>
      {error && (<Alert variant="destructive"><AlertCircle className="h-4 w-4" /><AlertDescription>{error}</AlertDescription></Alert>)}
      <div className="flex flex-col items-center justify-center text-center py-12">
        <div className="rounded-full bg-muted p-4 mb-4"><Video className="h-8 w-8 text-muted-foreground" /></div>
        <h3 className="text-lg font-semibold">开始创作视频</h3>
        <p className="text-muted-foreground">输入剧本信息，AI将为你生成角色信息和分镜头脚本</p>
      </div>
    </div>
  )
}