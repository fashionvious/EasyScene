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
  X,
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
  three_view_image_path?: string | null
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

interface SingleCharacterResponse {
  id: string
  role_name: string
  role_desc: string
  message: string
}

interface UpdateShotsResponse {
  message: string
  shot_scripts: ShotScript[]
}

// API 基础 URL
const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

// 辅助函数：将本地文件路径转换为 HTTP URL
function convertImagePathToUrl(imagePath: string | null | undefined): string | null {
  if (!imagePath) return null

  // 从完整路径中提取文件名
  // 例如：D:\...\generated_images\叶玉华_three_view_20260401_095133.png -> 叶玉华_three_view_20260401_095133.png
  const fileName = imagePath.split(/[/\\]/).pop()

  if (fileName) {
    return `${API_BASE_URL}/static/images/${fileName}`
  }

  return null
}

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

// 2. 更新单个角色信息API
async function updateSingleCharacter(
  characterId: string,
  roleName: string,
  roleDesc: string,
): Promise<SingleCharacterResponse> {
  const token = localStorage.getItem("access_token");
  if (!token) throw new Error("用户未登录");

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/character/${characterId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`
    },
    body: JSON.stringify({ 
      role_name: roleName,
      role_desc: roleDesc 
    }),
  })
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.detail || `更新角色信息失败: ${response.statusText}`)
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

// 图片预览模态框组件
function ImagePreviewModal({
  imagePath,
  onClose
}: {
  imagePath: string
  onClose: () => void
}) {
  const imageUrl = convertImagePathToUrl(imagePath)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80"
      onClick={onClose}
    >
      <div className="relative max-w-4xl max-h-[90vh] p-4">
        <button
          onClick={onClose}
          className="absolute top-2 right-2 z-10 rounded-full bg-white/10 p-2 hover:bg-white/20"
        >
          <X className="h-6 w-6 text-white" />
        </button>
        {imageUrl ? (
          <img
            src={imageUrl}
            alt="角色四视图"
            className="max-w-full max-h-[85vh] object-contain rounded-lg"
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <div className="text-white text-center p-8">
            <p>图片路径无效</p>
          </div>
        )}
      </div>
    </div>
  )
}

function ScriptDetail() {
  const { scriptId } = Route.useParams()
  
  // 状态
  const [editableCharacters, setEditableCharacters] = useState<CharacterInfo[]>([])
  const [editableShots, setEditableShots] = useState<ShotScript[]>([])
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [updatingCharacterId, setUpdatingCharacterId] = useState<string | null>(null)
  const [previewImage, setPreviewImage] = useState<string | null>(null)
  
 // 获取剧本状态
 const { data: scriptData, isLoading, refetch } = useQuery({
  queryKey: ["scriptStatus", scriptId],
  queryFn: () => getScriptStatus(scriptId),
  // 修复：TanStack Query v5 中，回调参数是 query 对象
  refetchInterval: (query) => {
    const data = query.state.data;
    // 如果正在生成角色或分镜，每2秒轮询一次
    if (data?.is_generating_characters || data?.is_generating_shots) {
      return 2000
    }
    // 如果有角色信息，但某些角色还没有四视图，也持续轮询
    if (data?.characters && data.characters.length > 0) {
      const hasMissingThreeView = data.characters.some(
        (char: CharacterInfo) => !char.three_view_image_path
      )
      if (hasMissingThreeView) {
        return 3000 // 每3秒轮询一次检查四视图生成状态
      }
    }
    // 默认情况下，每10秒轮询一次，确保数据是最新的
    return 10000
  },
  // 确保每次切换剧本时都重新获取数据
  staleTime: 0,
  refetchOnMount: true,
})
  
  // 当数据加载完成时，初始化可编辑状态
  // 修复：每次 scriptData 变化时都更新编辑状态
  useEffect(() => {
    if (scriptData) {
      // 直接使用最新数据，不再判断 editableCharacters.length === 0
      setEditableCharacters(scriptData.characters)
      setEditableShots(scriptData.shot_scripts)
    }
  }, [scriptData?.script_id, scriptData?.characters, scriptData?.shot_scripts])
  
  // 保存单个角色信息
  const handleSaveCharacter = async (character: CharacterInfo) => {
    if (!character.id) {
      setError("角色ID不存在")
      return
    }
    
    setUpdatingCharacterId(character.id)
    setError(null)
    setSuccessMessage(null)
    
    try {
      await updateSingleCharacter(character.id, character.role_name, character.role_desc)
      setSuccessMessage("已提交，正在生成四视图...")
       // 不需要手动刷新，轮询会自动获取最新数据
       // 10秒后清除成功消息
      setTimeout(() => setSuccessMessage(null), 10000)
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败")
    } finally {
      setUpdatingCharacterId(null)
    }
  }
  
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
    <>
      {/* 图片预览模态框 */}
      {previewImage && (
        <ImagePreviewModal 
          imagePath={previewImage} 
          onClose={() => setPreviewImage(null)} 
        />
      )}
      
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

        {/* 成功提示 */}
        {successMessage && (
          <Alert className="border-green-500 bg-green-50 text-green-900">
            <Check className="h-4 w-4" />
            <AlertDescription>{successMessage}</AlertDescription>
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
            {hasCharacters ? (
              <div className="space-y-4">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[100px]">角色</TableHead>
                      <TableHead className="w-[150px]">名字</TableHead>
                      <TableHead>角色描述</TableHead>
                      <TableHead className="w-[100px]">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {editableCharacters.map((char, index) => {
                      const isUpdating = updatingCharacterId === char.id 
                      const hasThreeView = !!convertImagePathToUrl(char.three_view_image_path)
                      return (
                        <TableRow key={char.id || index}>
                          <TableCell>
                            <div className="flex items-center gap-2">
                              {/* 四视图缩略图 */}
                                {hasThreeView ? (
                                <img
                                  src={convertImagePathToUrl(char.three_view_image_path)!}
                                  alt={`${char.role_name}四视图`}
                                  className="w-16 h-16 object-cover rounded cursor-pointer hover:opacity-80 transition-opacity"
                                  onClick={() => setPreviewImage(char.three_view_image_path!)}
                                />
                              ) : (
                                <div className="w-16 h-16 bg-gray-100 rounded flex items-center justify-center">
                                  <Users className="h-6 w-6 text-gray-400" />
                                </div>
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            <Input
                              value={char.role_name}
                              onChange={(e) => {
                                const newChars = [...editableCharacters]
                                newChars[index].role_name = e.target.value
                                setEditableCharacters(newChars)
                              }}
                              disabled={isUpdating}
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
                              disabled={isUpdating}
                              className="min-h-[60px]"
                            />
                          </TableCell>
                          <TableCell>
                            <Button
                              size="sm"
                              onClick={() => handleSaveCharacter(char)}
                              disabled={isUpdating || !char.id}
                            >
                              {isUpdating ? (
                                <>
                                  <Loader2 className="h-4 w-4 animate-spin mr-1" />
                                  保存中
                                </>
                              ) : (
                                <>
                                  <Check className="h-4 w-4 mr-1" />
                                  提交
                                </>
                              )}
                            </Button>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
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
    </>
  )
}
