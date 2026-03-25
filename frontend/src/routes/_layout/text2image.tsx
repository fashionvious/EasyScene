import { useState, useRef, useEffect } from "react"
import { createFileRoute } from "@tanstack/react-router"
import { useMutation } from "@tanstack/react-query"
import {
  Image,
  Sparkles,
  Loader2,
  Download,
  Check,
  RefreshCw,
  AlertCircle,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { cn } from "@/lib/utils"

// 类型定义
interface GeneratePromptResponse {
  prompt: string
}

interface GenerateImageResponse {
  image_url: string
  prompt: string
}

// API 基础 URL
const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

// 生成提示词API（流式）
async function generatePrompt(
  userInput: string,
  onChunk: (chunk: string) => void,
): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/api/v1/text2image/generate-prompt`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ user_input: userInput }),
  })

  if (!response.ok) {
    throw new Error(`生成提示词失败: ${response.statusText}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error("无法读取响应流")
  }

  const decoder = new TextDecoder()
  let fullPrompt = ""

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    const chunk = decoder.decode(value, { stream: true })
    fullPrompt += chunk
    onChunk(chunk)
  }

  return fullPrompt
}

// 修改提示词API（流式）
async function modifyPrompt(
  userInput: string,
  currentPrompt: string,
  onChunk: (chunk: string) => void,
): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/api/v1/text2image/modify-prompt`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ user_input: userInput, current_prompt: currentPrompt }),
  })

  if (!response.ok) {
    throw new Error(`修改提示词失败: ${response.statusText}`)
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error("无法读取响应流")
  }

  const decoder = new TextDecoder()
  let fullPrompt = ""

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    const chunk = decoder.decode(value, { stream: true })
    fullPrompt += chunk
    onChunk(chunk)
  }

  return fullPrompt
}

// 生成图片API
async function generateImage(
  prompt: string,
  size: string,
  n: number,
): Promise<GenerateImageResponse[]> {
  const response = await fetch(`${API_BASE_URL}/api/v1/text2image/generate-image`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ prompt, size, n }),
  })

  if (!response.ok) {
    throw new Error(`生成图片失败: ${response.statusText}`)
  }

  return response.json()
}

export const Route = createFileRoute("/_layout/text2image")({
  component: Text2Image,
  head: () => ({
    meta: [
      {
        title: "文生图 - FastAPI Cloud",
      },
    ],
  }),
})

function Text2Image() {
  // 初始状态
  const [userInput, setUserInput] = useState("")
  const [imageSize, setImageSize] = useState("1024*1024")
  const [imageCount, setImageCount] = useState("1")

  // 提示词生成后状态
  const [generatedPrompt, setGeneratedPrompt] = useState("")
  const [isStreamingPrompt, setIsStreamingPrompt] = useState(false)
  const [showPromptArea, setShowPromptArea] = useState(false)

  // 修改意见
  const [modifyInput, setModifyInput] = useState("")

  // 图片生成状态
  const [generatedImages, setGeneratedImages] = useState<GenerateImageResponse[]>([])
  const [isGeneratingImage, setIsGeneratingImage] = useState(false)

  // 错误提示
  const [error, setError] = useState<string | null>(null)

  // 提示词显示区域引用
  const promptDisplayRef = useRef<HTMLDivElement>(null)

  // 自动滚动到底部
  useEffect(() => {
    if (promptDisplayRef.current && isStreamingPrompt) {
      promptDisplayRef.current.scrollTop = promptDisplayRef.current.scrollHeight
    }
  }, [generatedPrompt, isStreamingPrompt])

  // 重置状态
  const resetState = () => {
    setUserInput("")
    setImageSize("1024*1024")
    setImageCount("1")
    setGeneratedPrompt("")
    setShowPromptArea(false)
    setModifyInput("")
    setGeneratedImages([])
    setError(null)
  }

  // 生成提示词
  const handleGeneratePrompt = async () => {
    if (!userInput.trim()) {
      setError("请输入图片描述")
      return
    }

    setError(null)
    setGeneratedPrompt("")
    setShowPromptArea(true)
    setIsStreamingPrompt(true)
    setGeneratedImages([])

    try {
      await generatePrompt(userInput, (chunk) => {
        setGeneratedPrompt((prev) => prev + chunk)
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成提示词失败")
      setShowPromptArea(false)
    } finally {
      setIsStreamingPrompt(false)
    }
  }

  // 满意 - 生成图片
  const handleSatisfied = async () => {
    if (modifyInput.trim()) {
      setError("请清空修改内容后再点击满意")
      return
    }

    setError(null)
    setIsGeneratingImage(true)

    try {
      const images = await generateImage(
        generatedPrompt,
        imageSize,
        parseInt(imageCount),
      )
      setGeneratedImages(images)
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成图片失败")
    } finally {
      setIsGeneratingImage(false)
    }
  }

  // 修改提示词
  const handleModify = async () => {
    if (!modifyInput.trim()) {
      setError("请输入修改意见")
      return
    }

    setError(null)
    setGeneratedPrompt("")
    setIsStreamingPrompt(true)

    try {
      await modifyPrompt(modifyInput, generatedPrompt, (chunk) => {
        setGeneratedPrompt((prev) => prev + chunk)
      })
      setModifyInput("")
    } catch (err) {
      setError(err instanceof Error ? err.message : "修改提示词失败")
    } finally {
      setIsStreamingPrompt(false)
    }
  }

  // 下载图片
  const handleDownload = async (imageUrl: string, index: number) => {
    try {
      const response = await fetch(imageUrl)
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `generated_image_${index + 1}.png`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
    } catch (err) {
      setError("下载图片失败")
    }
  }

  return (
    <div className="flex flex-col gap-6 p-6 md:p-8">
      {/* 页面标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Image className="h-6 w-6 text-purple-500" />
            文生图
          </h1>
          <p className="text-muted-foreground">
            输入你想要生成的图片吧！
          </p>
        </div>
      </div>

      {/* 输入区域 */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">图片描述</CardTitle>
          <CardDescription>
            输入你想要生成的图片描述，AI将为你生成专业的提示词
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* 用户输入 */}
          <div className="space-y-2">
            <Label htmlFor="user-input">图片描述</Label>
            <div className="flex gap-2">
              <Input
                id="user-input"
                placeholder="例如: 黑夜下的一辆炫酷跑车..."
                value={userInput}
                onChange={(e) => setUserInput(e.target.value)}
                className="flex-1"
                disabled={showPromptArea}
              />
              {!showPromptArea && (
                <Button
                  onClick={handleGeneratePrompt}
                  disabled={!userInput.trim() || isStreamingPrompt}
                >
                  {isStreamingPrompt ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Sparkles className="h-4 w-4" />
                  )}
                  生成
                </Button>
              )}
            </div>
          </div>

          {/* 图片设置 */}
          {!showPromptArea && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>图片尺寸</Label>
                <Select value={imageSize} onValueChange={setImageSize}>
                  <SelectTrigger>
                    <SelectValue placeholder="选择图片尺寸" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="1024*1024">1024 × 1024</SelectItem>
                    <SelectItem value="720*1280">720 × 1280</SelectItem>
                    <SelectItem value="768*1152">768 × 1152</SelectItem>
                    <SelectItem value="1280*720">1280 × 720</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>生成数量</Label>
                <Select value={imageCount} onValueChange={setImageCount}>
                  <SelectTrigger>
                    <SelectValue placeholder="选择生成数量" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="1">1 张</SelectItem>
                    <SelectItem value="2">2 张</SelectItem>
                    <SelectItem value="3">3 张</SelectItem>
                    <SelectItem value="4">4 张</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 错误提示 */}
      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* 提示词显示区域 */}
      {showPromptArea && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-purple-500" />
              生成的提示词
            </CardTitle>
            <CardDescription>
              AI根据你的描述生成的专业文生图提示词
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* 提示词显示 */}
            <div
              ref={promptDisplayRef}
              className="min-h-[100px] max-h-[200px] overflow-y-auto bg-muted/50 p-4 rounded-lg font-mono text-sm whitespace-pre-wrap"
            >
              {generatedPrompt || (
                <span className="text-muted-foreground">
                  正在生成提示词...
                </span>
              )}
              {isStreamingPrompt && (
                <span className="inline-block w-2 h-4 bg-primary animate-pulse ml-1" />
              )}
            </div>

            {/* 修改意见输入 */}
            <div className="space-y-2">
              <Label htmlFor="modify-input">修改意见（可选）</Label>
              <div className="flex gap-2">
                <Input
                  id="modify-input"
                  placeholder="请输入你的修改意见..."
                  value={modifyInput}
                  onChange={(e) => setModifyInput(e.target.value)}
                  className="flex-1"
                  disabled={isStreamingPrompt || isGeneratingImage}
                />
              </div>
            </div>

            {/* 操作按钮 */}
            <div className="flex gap-2">
              <Button
                variant="default"
                onClick={handleSatisfied}
                disabled={isStreamingPrompt || isGeneratingImage}
                className="flex-1"
              >
                {isGeneratingImage ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                    正在生成图片...
                  </>
                ) : (
                  <>
                    <Check className="h-4 w-4 mr-2" />
                    满意
                  </>
                )}
              </Button>
              <Button
                variant="secondary"
                onClick={handleModify}
                disabled={isStreamingPrompt || isGeneratingImage || !modifyInput.trim()}
                className="flex-1"
              >
                {isStreamingPrompt ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                    正在修改...
                  </>
                ) : (
                  <>
                    <RefreshCw className="h-4 w-4 mr-2" />
                    修改
                  </>
                )}
              </Button>
              <Button
                variant="outline"
                onClick={resetState}
                disabled={isStreamingPrompt || isGeneratingImage}
              >
                重新开始
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 图片生成加载状态 */}
      {isGeneratingImage && (
        <Card>
          <CardContent className="py-12">
            <div className="flex flex-col items-center justify-center gap-4">
              <div className="relative">
                <div className="w-16 h-16 border-4 border-primary/30 rounded-full" />
                <div className="absolute top-0 left-0 w-16 h-16 border-4 border-primary border-t-transparent rounded-full animate-spin" />
              </div>
              <div className="text-center">
                <h3 className="text-lg font-semibold">正在生成图片</h3>
                <p className="text-muted-foreground text-sm">
                  AI正在根据提示词创作图片，请稍候...
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 生成的图片 */}
      {generatedImages.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg flex items-center gap-2">
              <Image className="h-5 w-5 text-purple-500" />
              生成的图片
            </CardTitle>
            <CardDescription>
              共生成 {generatedImages.length} 张图片
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {generatedImages.map((image, index) => (
                <div
                  key={index}
                  className="relative group border rounded-lg overflow-hidden"
                >
                  <img
                    src={image.image_url}
                    alt={`生成的图片 ${index + 1}`}
                    className="w-full h-auto object-cover"
                  />
                  <div className="absolute top-2 right-2">
                    <Button
                      size="icon"
                      variant="secondary"
                      className="opacity-0 group-hover:opacity-100 transition-opacity"
                      onClick={() => handleDownload(image.image_url, index)}
                    >
                      <Download className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 空状态 */}
      {!showPromptArea && (
        <div className="flex flex-col items-center justify-center text-center py-12">
          <div className="rounded-full bg-muted p-4 mb-4">
            <Image className="h-8 w-8 text-muted-foreground" />
          </div>
          <h3 className="text-lg font-semibold">开始创作图片</h3>
          <p className="text-muted-foreground">
            输入图片描述，AI将为你生成专业的提示词并创作图片
          </p>
        </div>
      )}
    </div>
  )
}
