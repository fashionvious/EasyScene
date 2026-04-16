  import { useState } from "react"
  import { createFileRoute } from "@tanstack/react-router"
  import { useMutation } from "@tanstack/react-query"
  import {
    Search,
    Flame,
    Link2,
    Settings2,
    ThumbsUp,
    MessageCircle,
    Film,
    Clock,
    Calendar,
    ExternalLink,
    Loader2,
    Sparkles,
    ChevronDown,
    ChevronUp,
  } from "lucide-react"
  
  import { Button } from "@/components/ui/button"
  import { Input } from "@/components/ui/input"
  import { Label } from "@/components/ui/label"
  import { Checkbox } from "@/components/ui/checkbox"
  import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
  } from "@/components/ui/card"
  import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
  } from "@/components/ui/table"
  import { Alert, AlertDescription } from "@/components/ui/alert"
  import { Badge } from "@/components/ui/badge"
  import { cn } from "@/lib/utils"
  
  // 类型定义
  interface VideoStats {
    likes: number
    comments: number
    danmaku: number
    views: number
  }
  
  interface HotspotVideo {
    id: string
    title: string
    url: string
    cover: string
    duration: number
    pubdate: number
    score: number
    stats: VideoStats
  }
  
  interface SearchWeights {
    likes: number
    comments: number
    danmaku: number
    gravity: number
    duration_weight: number
    views: number
  }
  
  interface GenerateResult {
    saved_path: string
    video_summary: string
    prompt_content: string
  }
  
  interface GenerateResponse {
    results: GenerateResult[]
  }
  
  // 格式化时长
  function formatDuration(seconds: number): string {
    const minutes = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${minutes}:${secs.toString().padStart(2, "0")}`
  }
  
  // 格式化数字
  function formatNumber(num: number): string {
    if (num >= 10000) {
      return `${(num / 10000).toFixed(1)}万`
    }
    return num.toString()
  }
  
  // 格式化日期
  function formatDate(timestamp: number): string {
    const date = new Date(timestamp * 1000)
    return date.toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    })
  }
  
  // API 基础 URL（需要根据实际配置调整）
  const API_BASE_URL = import.meta.env.VITE_API_URL || ""
  
  // 模拟 API 调用（实际项目中需要替换为真实的 API Service）
  async function searchHotspotVideos(
    keywords: string[],
    weights: SearchWeights,
  ): Promise<HotspotVideo[]> {
    const response = await fetch("http://127.0.0.1:8000/api/v1/bilibili/hotspot-search", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ keywords, weights }),
    })
    if (!response.ok) {
      throw new Error(`搜索失败: ${response.statusText}`)
    }
    return response.json()
  }
  
  async function generateFromLink(videoUrl: string): Promise<GenerateResponse> {
    const response = await fetch(
      `${API_BASE_URL}/api/hotspot/generate-from-link`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ video_url: videoUrl }),
      },
    )
    if (!response.ok) {
      throw new Error(`处理链接失败: ${response.statusText}`)
    }
    return response.json()
  }
  
  export const Route = createFileRoute("/_layout/spotvideos")({
    component: SpotVideos,
    head: () => ({
      meta: [
        {
          title: "热点视频搜索 - FastAPI Cloud",
        },
      ],
    }),
  })
  
  function SpotVideos() {
    const [keywords, setKeywords] = useState("")
    const [manualUrl, setManualUrl] = useState("")
    const [searchResults, setSearchResults] = useState<HotspotVideo[]>([])
    const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
    const [showWeights, setShowWeights] = useState(false)
    const [weights, setWeights] = useState<SearchWeights>({
      likes: 1.0,
      comments: 0.8,
      danmaku: 0.5,
      gravity: 1.8,
      duration_weight: 0.25,
      views: 0.1,
    })
    const [analysisResults, setAnalysisResults] = useState<GenerateResult[]>([])
  
    // 搜索 mutation
    const searchMutation = useMutation({
      mutationFn: () =>
        searchHotspotVideos(
          keywords.split(",").map((k) => k.trim()).filter(Boolean),
          weights,
        ),
      onSuccess: (data) => {
        setSearchResults(data)
        setSelectedIds(new Set())
      },
    })
  
    // 直接分析链接 mutation
    const analyzeLinkMutation = useMutation({
      mutationFn: generateFromLink,
      onSuccess: (data) => {
        setAnalysisResults(data.results || [])
      },
    })
  
    // 批量分析选中视频 mutation
    const analyzeSelectedMutation = useMutation({
      mutationFn: async (videos: HotspotVideo[]) => {
        const results: GenerateResult[] = []
        for (const video of videos) {
          const response = await generateFromLink(video.url)
          results.push(...(response.results || []))
        }
        return results
      },
      onSuccess: (data) => {
        setAnalysisResults(data)
      },
    })
  
    const handleSelectAll = (checked: boolean) => {
      if (checked) {
        setSelectedIds(new Set(searchResults.map((v) => v.id)))
      } else {
        setSelectedIds(new Set())
      }
    }
  
    const handleSelectOne = (id: string, checked: boolean) => {
      const newSelected = new Set(selectedIds)
      if (checked) {
        newSelected.add(id)
      } else {
        newSelected.delete(id)
      }
      setSelectedIds(newSelected)
    }
  
    const selectedVideos = searchResults.filter((v) => selectedIds.has(v.id))
  
    return (
      <div className="flex flex-col gap-6 p-6 md:p-8">
        {/* 页面标题 */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="heading-card mb-2 flex items-center gap-2">
              <Flame className="h-6 w-6 text-[#ffc091]" />
              热点视频搜索
            </h1>
            <p className="text-muted-foreground text-body-semibold">
              搜索B站热点视频并进行AI分析
            </p>
          </div>
        </div>
  
        {/* 搜索区域 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">搜索设置</CardTitle>
            <CardDescription>
              输入关键词搜索热点视频，或直接输入B站视频链接进行分析
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* 关键词搜索 */}
            <div className="flex flex-col sm:flex-row gap-4">
              <div className="flex-1">
                <Label htmlFor="keywords">搜索关键词</Label>
                <div className="flex gap-2 mt-1.5">
                  <Input
                    id="keywords"
                    placeholder="例如: 科技, AI, 游戏..."
                    value={keywords}
                    onChange={(e) => setKeywords(e.target.value)}
                    className="flex-1"
                  />
                  <Button
                    onClick={() => searchMutation.mutate()}
                    disabled={!keywords.trim() || searchMutation.isPending}
                  >
                    {searchMutation.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Search className="h-4 w-4" />
                    )}
                    搜索
                  </Button>
                </div>
              </div>
            </div>
  
            {/* 直接链接分析 */}
            <div className="flex flex-col sm:flex-row gap-4">
              <div className="flex-1">
                <Label htmlFor="manual-url">或直接输入B站视频URL</Label>
                <div className="flex gap-2 mt-1.5">
                  <Input
                    id="manual-url"
                    placeholder="https://www.bilibili.com/video/..."
                    value={manualUrl}
                    onChange={(e) => setManualUrl(e.target.value)}
                    className="flex-1"
                  />
                  <Button
                    variant="secondary"
                    onClick={() => analyzeLinkMutation.mutate(manualUrl)}
                    disabled={!manualUrl.trim() || analyzeLinkMutation.isPending}
                  >
                    {analyzeLinkMutation.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Link2 className="h-4 w-4" />
                    )}
                    分析
                  </Button>
                </div>
              </div>
            </div>
  
            {/* 权重调整面板 */}
            <div className="border rounded-lg">
              <button
                type="button"
                onClick={() => setShowWeights(!showWeights)}
                className="flex items-center justify-between w-full px-4 py-3 text-sm font-medium hover:bg-muted/50 transition-colors"
              >
                <span className="flex items-center gap-2">
                  <Settings2 className="h-4 w-4" />
                  调整排序算法权重
                </span>
                {showWeights ? (
                  <ChevronUp className="h-4 w-4" />
                ) : (
                  <ChevronDown className="h-4 w-4" />
                )}
              </button>
              {showWeights && (
                <div className="px-4 pb-4 pt-2 border-t">
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    <div className="space-y-2">
                      <Label className="flex items-center gap-1.5">
                        <ThumbsUp className="h-4 w-4" />
                        点赞权重
                      </Label>
                      <Input
                        type="number"
                        step="0.05"
                        min="0"
                        max="2"
                        value={weights.likes}
                        onChange={(e) =>
                          setWeights({ ...weights, likes: parseFloat(e.target.value) || 0 })
                        }
                      />
                    </div>
                    <div className="space-y-2">
                      <Label className="flex items-center gap-1.5">
                        <MessageCircle className="h-4 w-4" />
                        评论权重
                      </Label>
                      <Input
                        type="number"
                        step="0.05"
                        min="0"
                        max="2"
                        value={weights.comments}
                        onChange={(e) =>
                          setWeights({ ...weights, comments: parseFloat(e.target.value) || 0 })
                        }
                      />
                    </div>
                    <div className="space-y-2">
                      <Label className="flex items-center gap-1.5">
                        <Film className="h-4 w-4" />
                        弹幕权重
                      </Label>
                      <Input
                        type="number"
                        step="0.05"
                        min="0"
                        max="2"
                        value={weights.danmaku}
                        onChange={(e) =>
                          setWeights({ ...weights, danmaku: parseFloat(e.target.value) || 0 })
                        }
                      />
                    </div>
                    <div className="space-y-2">
                      <Label className="flex items-center gap-1.5">
                        <Clock className="h-4 w-4" />
                        时间衰减因子
                      </Label>
                      <Input
                        type="number"
                        step="0.05"
                        min="1"
                        max="2.5"
                        value={weights.gravity}
                        onChange={(e) =>
                          setWeights({ ...weights, gravity: parseFloat(e.target.value) || 1 })
                        }
                      />
                    </div>
                    <div className="space-y-2">
                      <Label className="flex items-center gap-1.5">
                        <Clock className="h-4 w-4" />
                        时长惩罚
                      </Label>
                      <Input
                        type="number"
                        step="0.05"
                        min="0"
                        max="1"
                        value={weights.duration_weight}
                        onChange={(e) =>
                          setWeights({
                            ...weights,
                            duration_weight: parseFloat(e.target.value) || 0,
                          })
                        }
                      />
                      <p className="text-xs text-muted-foreground">
                        值越高越偏爱短视频
                      </p>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
  
        {/* 错误提示 */}
        {searchMutation.isError && (
          <Alert variant="destructive">
            <AlertDescription>
              搜索失败: {searchMutation.error?.message || "未知错误"}
            </AlertDescription>
          </Alert>
        )}
  
        {analyzeLinkMutation.isError && (
          <Alert variant="destructive">
            <AlertDescription>
              处理链接失败: {analyzeLinkMutation.error?.message || "未知错误"}
            </AlertDescription>
          </Alert>
        )}
  
        {/* 搜索结果 */}
        {searchResults.length > 0 && (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="text-lg">搜索结果</CardTitle>
                  <CardDescription>
                    共找到 {searchResults.length} 个视频
                  </CardDescription>
                </div>
                {selectedVideos.length > 0 && (
                  <Button
                    onClick={() => analyzeSelectedMutation.mutate(selectedVideos)}
                    disabled={analyzeSelectedMutation.isPending}
                  >
                    {analyzeSelectedMutation.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Sparkles className="h-4 w-4" />
                    )}
                    分析选中 ({selectedVideos.length})
                  </Button>
                )}
              </div>
            </CardHeader>
            <CardContent>
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-12">
                        <Checkbox
                          checked={
                            searchResults.length > 0 &&
                            selectedIds.size === searchResults.length
                          }
                          onCheckedChange={handleSelectAll}
                        />
                      </TableHead>
                      <TableHead>标题</TableHead>
                      <TableHead className="w-24 text-center">热度分</TableHead>
                      <TableHead className="w-20 text-center">时长</TableHead>
                      <TableHead className="w-24 text-right">点赞</TableHead>
                      <TableHead className="w-24 text-right">评论</TableHead>
                      <TableHead className="w-36">发布时间</TableHead>
                      <TableHead className="w-16 text-center">链接</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {searchResults.map((video) => (
                      <TableRow
                        key={video.id}
                        className={cn(
                          selectedIds.has(video.id) && "bg-muted/50",
                        )}
                      >
                        <TableCell>
                          <Checkbox
                            checked={selectedIds.has(video.id)}
                            onCheckedChange={(checked) =>
                              handleSelectOne(video.id, checked as boolean)
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-3">
                            {video.cover && (
                              <img
                                src={video.cover}
                                alt={video.title}
                                className="w-16 h-10 object-cover rounded"
                              />
                            )}
                            <span className="font-medium line-clamp-2 max-w-xs">
                              {video.title}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="text-center">
                          <Badge variant="secondary" className="font-mono">
                            <Flame className="h-3 w-3 mr-1 text-orange-500" />
                            {video.score.toFixed(1)}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-center text-muted-foreground">
                          {formatDuration(video.duration)}
                        </TableCell>
                        <TableCell className="text-right">
                          <span className="flex items-center justify-end gap-1">
                            <ThumbsUp className="h-3 w-3 text-muted-foreground" />
                            {formatNumber(video.stats.likes)}
                          </span>
                        </TableCell>
                        <TableCell className="text-right">
                          <span className="flex items-center justify-end gap-1">
                            <MessageCircle className="h-3 w-3 text-muted-foreground" />
                            {formatNumber(video.stats.comments)}
                          </span>
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          <span className="flex items-center gap-1">
                            <Calendar className="h-3 w-3" />
                            {formatDate(video.pubdate)}
                          </span>
                        </TableCell>
                        <TableCell className="text-center">
                          <a
                            href={video.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center justify-center"
                          >
                            <ExternalLink className="h-4 w-4 text-muted-foreground hover:text-primary" />
                          </a>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        )}
  
        {/* 分析结果 */}
        {analysisResults.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-lg flex items-center gap-2">
                <Sparkles className="h-5 w-5 text-purple-500" />
                分析结果
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              {analysisResults.map((result, index) => (
                <div
                  key={index}
                  className="border rounded-lg p-4 space-y-3"
                >
                  <div className="flex items-center gap-2">
                    <Badge variant="outline">保存路径</Badge>
                    <code className="text-sm bg-muted px-2 py-1 rounded">
                      {result.saved_path}
                    </code>
                  </div>
                  <div className="space-y-2">
                    <h4 className="font-medium flex items-center gap-2">
                      <Sparkles className="h-4 w-4 text-purple-500" />
                      Gemini 视频摘要
                    </h4>
                    <p className="text-muted-foreground bg-muted/50 p-3 rounded-lg">
                      {result.video_summary}
                    </p>
                  </div>
                  <div className="space-y-2">
                    <h4 className="font-medium">生成的 Prompt</h4>
                    <pre className="text-sm bg-muted/50 p-3 rounded-lg overflow-x-auto whitespace-pre-wrap">
                      {result.prompt_content}
                    </pre>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        )}
  
        {/* 空状态 */}
        {searchResults.length === 0 &&
          !searchMutation.isPending &&
          !analyzeLinkMutation.isPending && (
            <div className="flex flex-col items-center justify-center text-center py-12">
              <div className="rounded-full bg-muted p-4 mb-4">
                <Search className="h-8 w-8 text-muted-foreground" />
              </div>
              <h3 className="text-lg font-semibold">开始搜索热点视频</h3>
              <p className="text-muted-foreground">
                输入关键词搜索B站热点视频，或直接输入视频链接进行分析
              </p>
            </div>
          )}
      </div>
    )
  }
  