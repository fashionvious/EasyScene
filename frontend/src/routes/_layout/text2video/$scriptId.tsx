import { useState, useEffect } from "react"
import { createFileRoute } from "@tanstack/react-router"
import { useQuery, useMutation } from "@tanstack/react-query"
import {
  Video,
  Sparkles,
  Loader2,
  Check,
  AlertCircle,
  Users,
  Film,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

// 类型定义
interface CharacterInfo {
  id: string
  role_name: string
  role_desc: string
}

interface ShotScript {
  id: string
  shot_no: number
  total_script: string
}

interface ScriptStatusResponse {
  script_id: string
  script_name: string
  script_content: string
  status: number
  characters: CharacterInfo[]
  shot_scripts: ShotScript[]
  is_generating_characters: boolean
  is_generating_shots: boolean
}

interface UpdateCharactersResponse {
  message: string
  characters: CharacterInfo[]
}

interface UpdateShotsResponse {
  message: string
  shot_scripts: ShotScript[]
}

// API 基础 URL
const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

// 1. 获取剧本状态API
async function getScriptStatus(scriptId: string): Promise<ScriptStatusResponse> {
  const token = localStorage.getItem("access_token");
  if (!token) throw new Error("用户未登录");

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/script-status/${scriptId}`, {
    headers: {
      "Authorization": `Bearer ${token}` // 添加认证头部
    }
  })
  if (!response.ok) {
    throw new Error(`获取剧本状态失败: ${response.statusText}`)
  }
  return response.json()
}

// 2. 更新角色信息API
async function updateCharacters(
  scriptId: string,
  characters: CharacterInfo[],
): Promise<UpdateCharactersResponse> {
  const token = localStorage.getItem("access_token");
  if (!token) throw new Error("用户未登录");

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/update-characters/${scriptId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}` // 添加认证头部
    },
    body: JSON.stringify({ characters }),
  })
  if (!response.ok) {
    throw new Error(`更新角色信息失败: ${response.statusText}`)
  }
  return response.json()
}

// 3. 更新分镜头脚本API
async function updateShots(
  scriptId: string,
  shotScripts: ShotScript[],
): Promise<UpdateShotsResponse> {
  const token = localStorage.getItem("access_token");
  if (!token) throw new Error("用户未登录");

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/update-shots/${scriptId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}` // 添加认证头部
    },
    body: JSON.stringify({ shot_scripts: shotScripts }),
  })
  if (!response.ok) {
    throw new Error(`更新分镜头脚本失败: ${response.statusText}`)
  }
  return response.json()
}

export const Route = createFileRoute("/_layout/text2video/$scriptId")({
  component: ScriptDetail,
  head: () => ({
    meta: [
      {
        title: "剧本详情 - 文生视频 - FastAPI Cloud",
      },
    ],
  }),
})

function ScriptDetail() {
  const { scriptId } = Route.useParams()
  
  // 状态
  const [editableCharacters, setEditableCharacters] = useState<CharacterInfo[]>([])
  const [editableShots, setEditableShots] = useState<ShotScript[]>([])
  const [error, setError] = useState<string | null>(null)
  
 // 获取剧本状态
 const { data: scriptData, isLoading, refetch } = useQuery({
  queryKey: ["scriptStatus", scriptId],
  queryFn: () => getScriptStatus(scriptId),
  // 修复：TanStack Query v5 中，回调参数是 query 对象
  refetchInterval: (query) => {
    const data = query.state.data;
    // 如果正在生成，每2秒轮询一次
    if (data?.is_generating_characters || data?.is_generating_shots) {
      return 2000
    }
    return false
  },
})
  
  // 当数据加载完成时，初始化可编辑状态
  useEffect(() => {
    if (scriptData) {
      if (scriptData.characters.length > 0 && editableCharacters.length === 0) {
        setEditableCharacters(scriptData.characters)
      }
      if (scriptData.shot_scripts.length > 0 && editableShots.length === 0) {
        setEditableShots(scriptData.shot_scripts)
      }
    }
  }, [scriptData])
  
  // 更新角色信息
  const updateCharactersMutation = useMutation({
    mutationFn: () => updateCharacters(scriptId, editableCharacters),
    onSuccess: () => {
      refetch()
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "更新角色信息失败")
    },
  })
  
  // 更新分镜头脚本
  const updateShotsMutation = useMutation({
    mutationFn: () => updateShots(scriptId, editableShots),
    onSuccess: () => {
      refetch()
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "更新分镜头脚本失败")
    },
  })

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <Loader2 className="h-8 w-8 animate-spin text-purple-500" />
        <p className="mt-4 text-muted-foreground">加载中...</p>
      </div>
    )
  }

  if (!scriptData) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>未找到剧本信息</AlertDescription>
      </Alert>
    )
  }

  const hasCharacters = scriptData.characters.length > 0
  const hasShots = scriptData.shot_scripts.length > 0
  const isGeneratingCharacters = scriptData.is_generating_characters
  const isGeneratingShots = scriptData.is_generating_shots

  return (
    <div className="flex flex-col gap-6">
      {/* 页面标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Video className="h-6 w-6 text-purple-500" />
            {scriptData.script_name}
          </h1>
          <p className="text-muted-foreground">
            剧本详情和生成进度
          </p>
        </div>
      </div>

      {/* 错误提示 */}
      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* 角色信息卡片 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Users className="h-5 w-5 text-purple-500" />
            角色信息
          </CardTitle>
          <CardDescription>
            AI根据剧本内容提取的主要角色信息
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isGeneratingCharacters ? (
            <div className="flex flex-col items-center justify-center py-8">
              <div className="relative">
                <div className="w-12 h-12 border-4 border-purple-500/30 rounded-full" />
                <div className="absolute top-0 left-0 w-12 h-12 border-4 border-purple-500 border-t-transparent rounded-full animate-spin" />
              </div>
              <div className="mt-4 text-center">
                <p className="text-sm font-medium">正在生成角色信息</p>
                <p className="text-xs text-muted-foreground mt-1">
                  AI正在分析剧本内容，提取主要角色...
                </p>
              </div>
            </div>
          ) : hasCharacters ? (
            <div className="space-y-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[100px]">角色</TableHead>
                    <TableHead className="w-[150px]">名字</TableHead>
                    <TableHead>角色描述</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {editableCharacters.map((char, index) => (
                    <TableRow key={char.id || index}>
                      <TableCell>角色{index + 1}</TableCell>
                      <TableCell>
                        <Input
                          value={char.role_name}
                          onChange={(e) => {
                            const newChars = [...editableCharacters]
                            newChars[index].role_name = e.target.value
                            setEditableCharacters(newChars)
                          }}
                          disabled={updateCharactersMutation.isPending}
                        />
                      </TableCell>
                      <TableCell>
                        <Textarea
                          value={char.role_desc}
                          onChange={(e) => {
                            const newChars = [...editableCharacters]
                            newChars[index].role_desc = e.target.value
                            setEditableCharacters(newChars)
                          }}
                          disabled={updateCharactersMutation.isPending}
                          className="min-h-[60px]"
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Button
                onClick={() => updateCharactersMutation.mutate()}
                disabled={updateCharactersMutation.isPending}
                className="w-full"
              >
                {updateCharactersMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                    正在保存...
                  </>
                ) : (
                  <>
                    <Check className="h-4 w-4 mr-2" />
                    确定角色信息
                  </>
                )}
              </Button>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
              <Users className="h-8 w-8 mb-2" />
              <p>等待生成角色信息</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 分镜头脚本卡片 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Film className="h-5 w-5 text-purple-500" />
            分镜头脚本
          </CardTitle>
          <CardDescription>
            AI根据剧本内容生成的分镜头脚本
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isGeneratingShots ? (
            <div className="flex flex-col items-center justify-center py-8">
              <div className="relative">
                <div className="w-12 h-12 border-4 border-purple-500/30 rounded-full" />
                <div className="absolute top-0 left-0 w-12 h-12 border-4 border-purple-500 border-t-transparent rounded-full animate-spin" />
              </div>
              <div className="mt-4 text-center">
                <p className="text-sm font-medium">正在生成分镜头脚本</p>
                <p className="text-xs text-muted-foreground mt-1">
                  AI正在分析剧本内容，生成分镜头脚本...
                </p>
              </div>
            </div>
          ) : hasShots ? (
            <div className="space-y-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[100px]">分镜</TableHead>
                    <TableHead>分镜内容</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {editableShots.map((shot, index) => (
                    <TableRow key={shot.id || index}>
                      <TableCell>分镜{shot.shot_no}</TableCell>
                      <TableCell>
                        <Textarea
                          value={shot.total_script}
                          onChange={(e) => {
                            const newShots = [...editableShots]
                            newShots[index].total_script = e.target.value
                            setEditableShots(newShots)
                          }}
                          disabled={updateShotsMutation.isPending}
                          className="min-h-[80px]"
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Button
                onClick={() => updateShotsMutation.mutate()}
                disabled={updateShotsMutation.isPending}
                className="w-full"
              >
                {updateShotsMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                    正在保存...
                  </>
                ) : (
                  <>
                    <Check className="h-4 w-4 mr-2" />
                    确定分镜头脚本
                  </>
                )}
              </Button>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
              <Film className="h-8 w-8 mb-2" />
              <p>等待生成分镜头脚本</p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
