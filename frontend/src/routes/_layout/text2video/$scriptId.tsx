import { useState, useEffect, useRef } from "react"
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
  AlertTriangle,
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip"

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
  scene_group: number  // 场景组号
  scene_name: string   // 场景名称
  shot_group: number   // 分镜头组号
  grid_image_path?: string | null  // 九宫格图片路径
  first_frame_image_path?: string | null  // 首帧图路径
  video_path?: string | null  // 视频路径
}

interface SceneBackground {
  scene_group: number
  scene_name: string
  background_image_path?: string | null
}

interface ScriptStatusResponse {
  script_id: string
  script_name: string
  script_content: string
  status: number
  characters: CharacterInfo[]
  shot_scripts: ShotScript[]
  scene_backgrounds: SceneBackground[]
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

interface UpdateSingleShotResponse {
  id: string
  shot_no: number
  total_script: string
  message: string
}

interface ConfirmThreeViewResponse {
  id: string
  role_name: string
  role_desc: string
  three_view_image_path: string
  message: string
}

// API 基础 URL
const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

// 辅助函数：将本地文件路径转换为 HTTP URL
// 修改：支持按 script_id 区分的路径
// 例如：D:\...\{script_id}\generated_images\叶玉华_three_view_20260401_095133.png
// 转換為：http://127.0.0.1:8000/static/{script_id}/generated_images/叶玉华_three_view_20260401_095133.png
function convertImagePathToUrl(imagePath: string | null | undefined, scriptId?: string): string | null {
  if (!imagePath) return null

  // 从完整路径中提取 script_id 和文件名
  // 路径格式：D:\...\{script_id}\generated_images\{filename}
  const pathParts = imagePath.split(/[/\\]/)
  
  // 查找 generated_images 目录的位置
  const generatedImagesIndex = pathParts.findIndex(part => part === "generated_images")
  
  if (generatedImagesIndex > 0) {
    // script_id 应该在 generated_images 的前一个位置
    const extractedScriptId = pathParts[generatedImagesIndex - 1]
    const fileName = pathParts[pathParts.length - 1]
    
    if (extractedScriptId && fileName) {
      return `${API_BASE_URL}/static/${extractedScriptId}/generated_images/${fileName}`
    }
  }
  
  // 兜底：如果路径格式不匹配，尝试使用传入的 scriptId
  const fileName = pathParts[pathParts.length - 1]
  if (fileName && scriptId) {
    return `${API_BASE_URL}/static/${scriptId}/generated_images/${fileName}`
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

// 3.5 更新单个分镜头脚本API
async function updateSingleShot(
  shotId: string,
  totalScript: string,
): Promise<UpdateSingleShotResponse> {
  const token = localStorage.getItem("access_token");
  if (!token) throw new Error("用户未登录");

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/shot/${shotId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`
    },
    body: JSON.stringify({ total_script: totalScript }),
  })
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.detail || `更新分镜头脚本失败: ${response.statusText}`)
  }
  return response.json()
}

// 4. 确认角色四视图API
async function confirmCharacterThreeView(
  characterId: string,
): Promise<ConfirmThreeViewResponse> {
  const token = localStorage.getItem("access_token");
  if (!token) throw new Error("用户未登录");

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/confirm-character-three-view`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`
    },
    body: JSON.stringify({ character_id: characterId }),
  })
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.detail || `确认角色四视图失败: ${response.statusText}`)
  }
  return response.json()
}

// 5. 生成场景背景图API
interface GenerateBackgroundResponse {
  success: boolean
  script_id: string
  scene_group: number
  scene_name: string
  background_image_path: string
  message: string
}

async function generateSceneBackground(
  scriptId: string,
  sceneGroupNo: number,
  sceneName: string,
  shotScripts: ShotScript[],
): Promise<GenerateBackgroundResponse> {
  const token = localStorage.getItem("access_token");
  if (!token) throw new Error("用户未登录");

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/generate-background/${scriptId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`
    },
    body: JSON.stringify({
      scene_group_no: sceneGroupNo,
      scene_name: sceneName,
      shot_scripts: shotScripts,
    }),
  })
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.detail || `生成背景图失败: ${response.statusText}`)
  }
  return response.json()
}

// 6. 生成九宫格图片API
interface GenerateGridImageResponse {
  success: boolean
  script_id: string
  shot_no: number
  grid_image_path: string
  message: string
}

async function generateGridImage(
  scriptId: string,
  shotNo: number,
  shotScriptText: string,
  sceneGroupNo: number,
): Promise<GenerateGridImageResponse> {
  const token = localStorage.getItem("access_token");
  if (!token) throw new Error("用户未登录");

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/generate-grid-image/${scriptId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`
    },
    body: JSON.stringify({
      shot_no: shotNo,
      shot_script_text: shotScriptText,
      scene_group_no: sceneGroupNo,
    }),
  })
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.detail || `生成九宫格图片失败: ${response.statusText}`)
  }
  return response.json()
}

// 6.5 生成首帧图API
interface GenerateFirstFrameImageResponse {
  success: boolean
  script_id: string
  shot_no: number
  first_frame_image_path: string
  message: string
}

async function generateFirstFrameImage(
  scriptId: string,
  shotNo: number,
  shotScriptText: string,
  sceneGroupNo: number,
  scriptName: string,
): Promise<GenerateFirstFrameImageResponse> {
  const token = localStorage.getItem("access_token");
  if (!token) throw new Error("用户未登录");

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/generate-first-frame-image/${scriptId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`
    },
    body: JSON.stringify({
      shot_no: shotNo,
      shot_script_text: shotScriptText,
      scene_group_no: sceneGroupNo,
      script_name: scriptName,
    }),
  })
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.detail || `生成首帧图失败: ${response.statusText}`)
  }
  return response.json()
}

// 7. 生成视频API
interface GenerateVideoResponse {
  success: boolean
  script_id: string
  shot_no: number
  video_path: string
  message: string
}

async function generateVideo(
  scriptId: string,
  shotNo: number,
  shotlistText: string,
): Promise<GenerateVideoResponse> {
  const token = localStorage.getItem("access_token");
  if (!token) throw new Error("用户未登录");

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/generate-video/${scriptId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`
    },
    body: JSON.stringify({
      shot_no: shotNo,
      shotlist_text: shotlistText,
    }),
  })
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.detail || `生成视频失败: ${response.statusText}`)
  }
  return response.json()
}

// 7.5 基于首帧图生成视频API（seedance模型）
interface GenerateVideoFromFirstFrameResponse {
  success: boolean
  script_id: string
  shot_no: number
  video_path: string
  message: string
}

async function generateVideoFromFirstFrame(
  scriptId: string,
  shotNo: number,
  shotlistText: string,
): Promise<GenerateVideoFromFirstFrameResponse> {
  const token = localStorage.getItem("access_token");
  if (!token) throw new Error("用户未登录");

  const response = await fetch(`${API_BASE_URL}/api/v1/text2video/generate-video-from-first-frame/${scriptId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`
    },
    body: JSON.stringify({
      shot_no: shotNo,
      shotlist_text: shotlistText,
    }),
  })
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.detail || `生成视频失败: ${response.statusText}`)
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
  scriptId,
  onClose
}: {
  imagePath: string
  scriptId: string
  onClose: () => void
}) {
  const imageUrl = convertImagePathToUrl(imagePath, scriptId)

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
  const [originalCharacters, setOriginalCharacters] = useState<CharacterInfo[]>([]) // 原始角色数据
  const [originalShots, setOriginalShots] = useState<ShotScript[]>([]) // 原始分镜数据
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [updatingCharacterId, setUpdatingCharacterId] = useState<string | null>(null)
  const [confirmingCharacterId, setConfirmingCharacterId] = useState<string | null>(null)
  const [previewImage, setPreviewImage] = useState<string | null>(null)
  const [generatingBackgroundScene, setGeneratingBackgroundScene] = useState<number | null>(null) // 正在生成背景图的场景组号
  const [sceneBackgrounds, setSceneBackgrounds] = useState<Map<number, string>>(new Map()) // 场景组号 -> 背景图路径
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false) // 确认对话框是否打开
  const [pendingSceneGroup, setPendingSceneGroup] = useState<{ sceneGroupNo: number, sceneName: string, sceneShots: ShotScript[] } | null>(null) // 待确认的场景组信息
  const [generatingGridShot, setGeneratingGridShot] = useState<number | null>(null) // 正在生成九宫格图片的分镜号
  const [generatingVideoShot, setGeneratingVideoShot] = useState<number | null>(null) // 正在生成视频的分镜号
  const [generatingThreeViewCharacterIds, setGeneratingThreeViewCharacterIds] = useState<Set<string>>(new Set()) // 正在生成四视图的角色ID集合
  const [videoConfirmDialogOpen, setVideoConfirmDialogOpen] = useState(false) // 视频生成确认对话框是否打开
  const [pendingVideoShot, setPendingVideoShot] = useState<{ shotNo: number; shotId: string; totalScript: string } | null>(null) // 待确认生成视频的分镜信息

  // 使用 ref 跟踪是否应该接受远程更新
  const shouldAcceptRemoteUpdateRef = useRef(true)
  const lastSubmittedDataRef = useRef<{ characters: CharacterInfo[], shots: ShotScript[] } | null>(null)
  
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
    // 如果有分镜正在生成视频（video_path为空但首帧图已存在），持续轮询
    if (data?.shot_scripts && data.shot_scripts.length > 0) {
      const hasGeneratingVideo = data.shot_scripts.some(
        (shot: ShotScript) => !shot.video_path && shot.first_frame_image_path
      )
      if (hasGeneratingVideo) {
        return 5000 // 每5秒轮询一次检查视频生成状态
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
  // 修复：只有在非编辑状态下才更新本地数据
  useEffect(() => {
    if (scriptData) {
      // 场景背景图数据和四视图防呆状态始终同步（不受编辑锁定影响）
      if (scriptData.scene_backgrounds && scriptData.scene_backgrounds.length > 0) {
        const bgMap = new Map<number, string>()
        for (const bg of scriptData.scene_backgrounds) {
          if (bg.background_image_path) {
            bgMap.set(bg.scene_group, bg.background_image_path)
          }
        }
        setSceneBackgrounds(bgMap)
      }

      // 检查正在生成四视图的角色是否已完成，如果完成则从防呆集合中移除
      setGeneratingThreeViewCharacterIds(prev => {
        if (prev.size === 0) return prev
        const next = new Set(prev)
        for (const charId of prev) {
          const char = scriptData.characters.find(c => c.id === charId)
          if (char && char.three_view_image_path) {
            next.delete(charId)
          }
        }
        return next.size === prev.size ? prev : next
      })

      // 检查是否应该接受远程更新
      if (!shouldAcceptRemoteUpdateRef.current) {
        console.log('[DEBUG] 用户正在编辑，拒绝远程数据覆盖')
        return
      }

      console.log('[DEBUG] 接受远程数据更新')
      // 更新原始数据和编辑数据
      setOriginalCharacters(scriptData.characters)
      setOriginalShots(scriptData.shot_scripts)
      setEditableCharacters(scriptData.characters)
      setEditableShots(scriptData.shot_scripts)
    }
  }, [scriptData?.script_id, scriptData?.characters, scriptData?.shot_scripts, scriptData?.scene_backgrounds])
  
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

      // 提交成功后，标记该角色正在生成四视图（防呆）
      setGeneratingThreeViewCharacterIds(prev => new Set(prev).add(character.id))

      // 提交成功后，更新原始数据，解除编辑锁定
      setOriginalCharacters([...editableCharacters])
      shouldAcceptRemoteUpdateRef.current = true // 解锁远程更新

      // 不需要手动刷新，轮询会自动获取最新数据
      // 10秒后清除成功消息
      setTimeout(() => setSuccessMessage(null), 10000)
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败")
    } finally {
      setUpdatingCharacterId(null)
    }
  }

  // 确认角色四视图
  const handleConfirmThreeView = async (character: CharacterInfo) => {
    if (!character.id) {
      setError("角色ID不存在")
      return
    }

    if (!character.three_view_image_path) {
      setError("角色还没有生成四视图")
      return
    }

    setConfirmingCharacterId(character.id)
    setError(null)
    setSuccessMessage(null)

    try {
      const result = await confirmCharacterThreeView(character.id)
      setSuccessMessage(result.message || "四视图已确认")

      // 确认成功后，更新原始数据，解除编辑锁定
      setOriginalCharacters([...editableCharacters])
      shouldAcceptRemoteUpdateRef.current = true // 解锁远程更新

      // 刷新数据
      refetch()
      // 5秒后清除成功消息
      setTimeout(() => setSuccessMessage(null), 5000)
    } catch (err) {
      setError(err instanceof Error ? err.message : "确认失败")
    } finally {
      setConfirmingCharacterId(null)
    }
  }

  // 更新分镜头脚本
  const updateShotsMutation = useMutation({
    mutationFn: () => updateShots(scriptId, editableShots),
    onSuccess: () => {
      // 提交成功后，更新原始数据，解除编辑锁定
      setOriginalShots([...editableShots])
      shouldAcceptRemoteUpdateRef.current = true // 解锁远程更新
      refetch()
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "更新分镜头脚本失败")
    },
  })

  // 生成场景背景图
  const handleGenerateBackground = async (sceneGroupNo: number, sceneName: string, sceneShots: ShotScript[]) => {
    setGeneratingBackgroundScene(sceneGroupNo)
    setError(null)
    setSuccessMessage(null)

    try {
      const result = await generateSceneBackground(scriptId, sceneGroupNo, sceneName, sceneShots)
      setSuccessMessage(result.message || `场景组${sceneGroupNo}背景图生成任务已启动`)

      // 10秒后清除成功消息
      setTimeout(() => setSuccessMessage(null), 10000)
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成背景图失败")
    } finally {
      setGeneratingBackgroundScene(null)
    }
  }

  // 打开确认对话框
  const openConfirmDialog = (sceneGroupNo: number, sceneName: string, sceneShots: ShotScript[]) => {
    setPendingSceneGroup({ sceneGroupNo, sceneName, sceneShots })
    setConfirmDialogOpen(true)
  }

  // 确认提交
  const handleConfirmSubmit = () => {
    if (pendingSceneGroup) {
      handleGenerateBackground(
        pendingSceneGroup.sceneGroupNo,
        pendingSceneGroup.sceneName,
        pendingSceneGroup.sceneShots
      )
    }
    setConfirmDialogOpen(false)
    setPendingSceneGroup(null)
  }

  // 取消提交
  const handleCancelSubmit = () => {
    setConfirmDialogOpen(false)
    setPendingSceneGroup(null)
  }

  // 打开视频生成确认对话框
  const openVideoConfirmDialog = (shotNo: number, shotId: string, totalScript: string) => {
    setPendingVideoShot({ shotNo, shotId, totalScript })
    setVideoConfirmDialogOpen(true)
  }

  // 确认生成视频
  const handleConfirmVideoGenerate = async () => {
    if (!pendingVideoShot) return
    const { shotNo, totalScript } = pendingVideoShot
    setVideoConfirmDialogOpen(false)
    setPendingVideoShot(null)

    setGeneratingVideoShot(shotNo)
    setError(null)
    setSuccessMessage(null)
    try {
      const result = await generateVideoFromFirstFrame(scriptId, shotNo, totalScript)
      setSuccessMessage(result.message || `分镜${shotNo}视频生成任务已启动`)
      setOriginalShots([...editableShots])
      shouldAcceptRemoteUpdateRef.current = true
      setTimeout(() => setSuccessMessage(null), 10000)
    } catch (err) {
      setError(err instanceof Error ? err.message : "视频生成失败")
    } finally {
      setGeneratingVideoShot(null)
    }
  }

  // 取消生成视频
  const handleCancelVideoGenerate = () => {
    setVideoConfirmDialogOpen(false)
    setPendingVideoShot(null)
  }

  // 生成九宫格图片
  const handleGenerateGridImage = async (shotNo: number, shotScriptText: string, sceneGroupNo: number) => {
    setGeneratingGridShot(shotNo)
    setError(null)
    setSuccessMessage(null)

    try {
      const result = await generateGridImage(scriptId, shotNo, shotScriptText, sceneGroupNo)
      setSuccessMessage(result.message || `分镜${shotNo}九宫格图片生成任务已启动`)

      // 10秒后清除成功消息
      setTimeout(() => setSuccessMessage(null), 10000)
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成九宫格图片失败")
    } finally {
      setGeneratingGridShot(null)
    }
  }

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
          scriptId={scriptId}
          onClose={() => setPreviewImage(null)} 
        />
      )}

      {/* 确认对话框 */}
      <Dialog open={confirmDialogOpen} onOpenChange={setConfirmDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-yellow-500" />
              确认提交
            </DialogTitle>
            <DialogDescription className="text-base pt-2">
              此操作会根据当前场景组中的场景描述生成背景参考图，请确定相关信息无误。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="sm:justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={handleCancelSubmit}
            >
              取消
            </Button>
            <Button
              type="button"
              onClick={handleConfirmSubmit}
            >
              确定
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 视频生成确认对话框 */}
      <Dialog open={videoConfirmDialogOpen} onOpenChange={setVideoConfirmDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-yellow-500" />
              确认生成视频
            </DialogTitle>
            <DialogDescription className="text-base pt-2">
              生成视频会消耗大量token，请确定分镜内容。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="sm:justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={handleCancelVideoGenerate}
            >
              取消
            </Button>
            <Button
              type="button"
              onClick={handleConfirmVideoGenerate}
            >
              确定
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      
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
                      <TableHead className="w-[160px]">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {editableCharacters.map((char, index) => {
                      const isUpdating = updatingCharacterId === char.id
                      const isConfirming = confirmingCharacterId === char.id
                      const hasThreeView = !!convertImagePathToUrl(char.three_view_image_path, scriptId)
                      return (
                        <TableRow key={char.id || index}>
                          <TableCell>
                            <div className="flex items-center gap-2">
                              {/* 四视图缩略图 */}
                                {hasThreeView ? (
                                <img
                                  src={convertImagePathToUrl(char.three_view_image_path, scriptId)!}
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
                                // 用户开始输入，立即锁定远程更新
                                shouldAcceptRemoteUpdateRef.current = false
                                const newChars = [...editableCharacters]
                                newChars[index].role_name = e.target.value
                                setEditableCharacters(newChars)
                                // 用户修改了角色信息，允许重新提交
                                setGeneratingThreeViewCharacterIds(prev => {
                                  if (!prev.has(char.id)) return prev
                                  const next = new Set(prev)
                                  next.delete(char.id)
                                  return next
                                })
                              }}
                              disabled={isUpdating || isConfirming || generatingThreeViewCharacterIds.has(char.id)}
                            />
                          </TableCell>
                          <TableCell>
                            <Textarea
                              value={char.role_desc}
                              onChange={(e) => {
                                // 用户开始输入，立即锁定远程更新
                                shouldAcceptRemoteUpdateRef.current = false
                                const newChars = [...editableCharacters]
                                newChars[index].role_desc = e.target.value
                                setEditableCharacters(newChars)
                                // 用户修改了角色信息，允许重新提交
                                setGeneratingThreeViewCharacterIds(prev => {
                                  if (!prev.has(char.id)) return prev
                                  const next = new Set(prev)
                                  next.delete(char.id)
                                  return next
                                })
                              }}
                              disabled={isUpdating || isConfirming || generatingThreeViewCharacterIds.has(char.id)}
                              className="min-h-[60px]"
                            />
                          </TableCell>
                          <TableCell>
                            <div className="flex gap-2">
                              <Button
                                size="sm"
                                onClick={() => handleSaveCharacter(char)}
                                disabled={isUpdating || isConfirming || !char.id || generatingThreeViewCharacterIds.has(char.id)}
                              >
                                {isUpdating || generatingThreeViewCharacterIds.has(char.id) ? (
                                  <>
                                    <Loader2 className="h-4 w-4 animate-spin mr-1" />
                                    生成中
                                  </>
                                ) : (
                                  <>
                                    <Check className="h-4 w-4 mr-1" />
                                    提交
                                  </>
                                )}
                              </Button>
                            </div>
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
              <div className="space-y-6">
                {/* 按场景分组显示 */}
                {Array.from(new Set(editableShots.map(shot => shot.scene_group))).map(sceneGroupNo => {
                  const sceneShots = editableShots.filter(shot => shot.scene_group === sceneGroupNo)
                  const sceneName = sceneShots[0]?.scene_name || "默认场景"
                  const isGeneratingBackground = generatingBackgroundScene === sceneGroupNo
                  const backgroundImagePath = sceneBackgrounds.get(sceneGroupNo)
                  
                  return (
                    <div key={sceneGroupNo} className="space-y-4">
                      {/* 场景组标题和提交按钮 */}
                      <div className="flex items-center justify-between bg-muted/50 px-4 py-2 rounded-lg">
                        <div className="flex items-center gap-2">
                          <Film className="h-4 w-4 text-purple-500" />
                          <span className="font-semibold">场景组{sceneGroupNo}：{sceneName}</span>
                        </div>
                        <Button
                          size="sm"
                          onClick={() => openConfirmDialog(sceneGroupNo, sceneName, sceneShots)}
                          disabled={isGeneratingBackground}
                        >
                          {isGeneratingBackground ? (
                            <>
                              <Loader2 className="h-4 w-4 animate-spin mr-1" />
                              生成中...
                            </>
                          ) : (
                            <>
                              <Sparkles className="h-4 w-4 mr-1" />
                              提交场景组
                            </>
                          )}
                        </Button>
                      </div>

                      {/* 背景图显示 */}
                      {backgroundImagePath && (
                        <div className="inline-block">
                          <img
                            src={convertImagePathToUrl(backgroundImagePath, scriptId)!}
                            alt={`场景组${sceneGroupNo}背景图`}
                            className="h-16 rounded cursor-pointer hover:opacity-80 transition-opacity"
                            onClick={() => setPreviewImage(backgroundImagePath)}
                          />
                        </div>
                      )}
                      
                      {/* 按分镜头组显示 */}
                      {Array.from(new Set(sceneShots.map(shot => shot.shot_group))).map(shotGroupNo => {
                        const groupShots = sceneShots.filter(shot => shot.shot_group === shotGroupNo)

                        return (
                          <div key={`${sceneGroupNo}-${shotGroupNo}`} className="border rounded-lg p-4 space-y-3">
                            {/* 分镜内容 */}
                            {groupShots.map((shot, index) => {
                              const shotIndex = editableShots.findIndex(s => s.id === shot.id)
                              const isGeneratingGrid = generatingGridShot === shot.shot_no
                              const hasFirstFrameImage = !!convertImagePathToUrl(shot.first_frame_image_path, scriptId)
                              return (
                                <div key={shot.id || index} className="space-y-2">
                                  <div className="flex items-start gap-3">
                                    <div className="flex flex-col items-center min-w-[60px] pt-2">
                                      <span className="text-sm font-medium">
                                        分镜{shot.shot_no}
                                      </span>
                                      {/* 首帧图预览 */}
                                      {hasFirstFrameImage ? (
                                        <img
                                          src={convertImagePathToUrl(shot.first_frame_image_path, scriptId)!}
                                          alt={`分镜${shot.shot_no}首帧图`}
                                          className="w-12 h-8 object-cover rounded cursor-pointer hover:opacity-80 transition-opacity mt-1"
                                          onClick={() => setPreviewImage(shot.first_frame_image_path!)}
                                        />
                                      ) : null}
                                    </div>
                                    <Textarea
                                      value={shot.total_script}
                                      onChange={(e) => {
                                        // 用户开始输入，立即锁定远程更新
                                        shouldAcceptRemoteUpdateRef.current = false
                                        const newShots = [...editableShots]
                                        newShots[shotIndex].total_script = e.target.value
                                        setEditableShots(newShots)
                                      }}
                                      disabled={updateShotsMutation.isPending}
                                      className="min-h-[80px] flex-1"
                                    />
                                  </div>
                                  {/* 提交和确定按钮 */}
                                  <div className="flex justify-end gap-2 items-center">
                                    {(() => {
                                      // 防呆检查：所有角色四视图是否生成完 + 当前场景组背景图是否生成完
                                      const allCharactersHaveThreeView = editableCharacters.length > 0 && editableCharacters.every(c => !!c.three_view_image_path)
                                      const hasSceneBackground = !!sceneBackgrounds.get(sceneGroupNo)
                                      const canSubmitShot = allCharactersHaveThreeView && hasSceneBackground
                                      const shotSubmitDisabled = isGeneratingGrid || updateShotsMutation.isPending || !canSubmitShot
                                      // 生成具体的缺失提示
                                      const missingHints: string[] = []
                                      if (!allCharactersHaveThreeView) missingHints.push("角色四视图")
                                      if (!hasSceneBackground) missingHints.push("场景背景图")
                                      const missingText = missingHints.length > 0 ? `${missingHints.join("、")}缺失` : ""
                                      return (
                                        <Tooltip>
                                          <TooltipTrigger asChild>
                                            <span className="inline-flex">
                                              <Button
                                                size="sm"
                                                onClick={async () => {
                                                  // 先更新数据库中的total_script，再生成首帧图
                                                  setGeneratingGridShot(shot.shot_no)
                                                  setError(null)
                                                  setSuccessMessage(null)
                                                  try {
                                                    await updateSingleShot(shot.id, shot.total_script)
                                                    const result = await generateFirstFrameImage(scriptId, shot.shot_no, shot.total_script, sceneGroupNo, scriptData.script_name)
                                                    setSuccessMessage(result.message || `分镜${shot.shot_no}已提交，首帧图生成任务已启动`)
                                                    // 更新原始数据，解除编辑锁定
                                                    setOriginalShots([...editableShots])
                                                    shouldAcceptRemoteUpdateRef.current = true
                                                    setTimeout(() => setSuccessMessage(null), 10000)
                                                  } catch (err) {
                                                    setError(err instanceof Error ? err.message : "提交失败")
                                                  } finally {
                                                    setGeneratingGridShot(null)
                                                  }
                                                }}
                                                disabled={shotSubmitDisabled}
                                              >
                                                {isGeneratingGrid ? (
                                                  <>
                                                    <Loader2 className="h-4 w-4 animate-spin mr-1" />
                                                    生成中
                                                  </>
                                                ) : (
                                                  <>
                                                    <Sparkles className="h-4 w-4 mr-1" />
                                                    提交
                                                  </>
                                                )}
                                              </Button>
                                            </span>
                                          </TooltipTrigger>
                                          <TooltipContent>
                                            {canSubmitShot ? "提交生成首帧图" : missingText}
                                          </TooltipContent>
                                        </Tooltip>
                                      )
                                    })()}
                                    <Button
                                      size="sm"
                                      variant="outline"
                                      onClick={() => openVideoConfirmDialog(shot.shot_no, shot.id, shot.total_script)}
                                      disabled={!hasFirstFrameImage || generatingVideoShot === shot.shot_no}
                                    >
                                      {generatingVideoShot === shot.shot_no ? (
                                        <>
                                          <Loader2 className="h-4 w-4 animate-spin mr-1" />
                                          生成视频中
                                        </>
                                      ) : (
                                        <>
                                          <Check className="h-4 w-4 mr-1" />
                                          确定
                                        </>
                                      )}
                                    </Button>
                                    {/* 查看视频超链接 */}
                                    {(() => {
                                      const videoUrl = convertImagePathToUrl(shot.video_path, scriptId)
                                      const hasVideo = !!videoUrl
                                      return (
                                        <a
                                          href={hasVideo ? videoUrl! : undefined}
                                          target={hasVideo ? "_blank" : undefined}
                                          rel={hasVideo ? "noopener noreferrer" : undefined}
                                          onClick={(e) => {
                                            if (!hasVideo) {
                                              e.preventDefault()
                                            }
                                          }}
                                          className={`text-sm underline-offset-4 ${
                                            hasVideo
                                              ? "text-green-600 hover:text-green-800 hover:underline cursor-pointer"
                                              : "text-gray-400 cursor-not-allowed"
                                          }`}
                                        >
                                          查看视频
                                        </a>
                                      )
                                    })()}
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                        )
                      })}
                    </div>
                  )
                })}
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
