import { useQuery } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import {
  AlertTriangle,
  ArrowLeft,
  Brain,
  ChevronRight,
  FileText,
  History,
  Image as ImageIcon,
  LayoutGrid,
  Loader2,
  MessageSquare,
  Plus,
  Send,
  Users,
  Video,
  Wrench,
} from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { ControlBar } from "@/components/Edit/ControlBar"
import { ErrorCard } from "@/components/Edit/ErrorCard"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Textarea } from "@/components/ui/textarea"

// ==================== 类型定义 ====================

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
  scene_group: number
  scene_name: string
  shot_group: number
  grid_image_path?: string | null
  first_frame_image_path?: string | null
  last_frame_image_path?: string | null
  video_path?: string | null
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

// ==================== 聊天相关类型 ====================

/** 工具调用步骤 */
interface ToolStep {
  tool_name: string
  tool_args?: string
  result?: string
  status: "running" | "completed"
}

/** 消息时间线段 - 按时间线记录工具调用和文本 */
interface MessageSegment {
  type: "text" | "tool"
  content?: string
  tool_name?: string
  tool_args?: string
  result?: string
  status?: "running" | "completed"
}

/** 聊天消息 - 支持结构化内容 */
interface ChatMessage {
  role: "user" | "assistant"
  content: string
  toolSteps?: ToolStep[]  // 向后兼容旧格式
  segments?: MessageSegment[]  // 新格式：按时间线排列的混合内容段
  streaming?: boolean
}

interface Conversation {
  id: string
  title: string
  create_time: string
  title_set: boolean
  messages: ChatMessage[]
}

// ==================== 常量 ====================

const API_BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"
const VIDEO_AGENT_CHAT_URL = `${API_BASE_URL}/api/v1/videoagent/chat`
const VIDEO_AGENT_STREAM_URL = `${API_BASE_URL}/api/v1/videoagent/chat/stream`
const CONVERSATIONS_URL = `${API_BASE_URL}/api/v1/videoagent/conversations`
const STREAM_FLAG = true

// ==================== 工具函数 ====================

function generateId(): string {
  // 生成标准 UUID v4 格式，与后端数据库 UUID 兼容
  return crypto.randomUUID()
}

function formatNow(): string {
  return new Date().toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

/** 格式化 AI 回复，处理 <think>...</think> 思考标签 */
function formatResponse(text: string): string {
  let formatted = text.replace(/<tool_call>/g, "**思考过程**：\n")
  formatted = formatted.replace(/<\/think>/g, "\n\n**最终回复**：\n")
  return formatted.trim()
}

/** 工具名称友好显示 */
function friendlyToolName(name: string): string {
  const map: Record<string, string> = {
    load_skill: "加载技能",
    resolve_media: "解析媒体文件",
    list_media: "列出媒体文件",
    execute_cli_script: "执行CLI脚本",
    list_cli_scripts: "列出CLI脚本",
    execute_jyproject_code: "执行剪辑代码",
    validate_jyproject_code: "验证剪辑代码",
  }
  return map[name] || name
}

function convertImagePathToUrl(
  imagePath: string | null | undefined,
  scriptId?: string,
): string | null {
  if (!imagePath) return null
  const pathParts = imagePath.split(/[/\\]/)
  const generatedImagesIndex = pathParts.indexOf("generated_images")
  if (generatedImagesIndex > 0) {
    const extractedScriptId = pathParts[generatedImagesIndex - 1]
    const fileName = pathParts[pathParts.length - 1]
    if (extractedScriptId && fileName) {
      return `${API_BASE_URL}/static/${extractedScriptId}/generated_images/${fileName}`
    }
  }
  const fileName = pathParts[pathParts.length - 1]
  if (fileName && scriptId) {
    return `${API_BASE_URL}/static/${scriptId}/generated_images/${fileName}`
  }
  return null
}

// ==================== API ====================

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem("access_token")
  const headers: Record<string, string> = {}
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  return headers
}

async function getScriptStatus(
  scriptId: string,
): Promise<ScriptStatusResponse> {
  const token = localStorage.getItem("access_token")
  if (!token) throw new Error("用户未登录")
  const response = await fetch(
    `${API_BASE_URL}/api/v1/text2video/script-status/${scriptId}`,
    {
      headers: { Authorization: `Bearer ${token}` },
    },
  )
  if (!response.ok) {
    throw new Error(`获取剧本状态失败: ${response.statusText}`)
  }
  return response.json()
}

/** 从后端获取指定剧本下的会话列表 */
async function fetchConversations(scriptId: string): Promise<Conversation[]> {
  const response = await fetch(`${CONVERSATIONS_URL}?script_id=${scriptId}`, {
    headers: getAuthHeaders(),
  })
  if (!response.ok) throw new Error(`获取会话列表失败: ${response.statusText}`)
  const data: {
    id: string
    title: string
    create_time: string
    title_set: boolean
  }[] = await response.json()
  return data.map((c) => ({
    id: c.id,
    title: c.title,
    create_time: c.create_time,
    title_set: c.title_set,
    messages: [], // 消息按需加载
  }))
}

/** 从后端获取指定会话的消息列表 */
async function fetchConversationMessages(
  conversationId: string,
): Promise<ChatMessage[]> {
  const response = await fetch(
    `${CONVERSATIONS_URL}/${conversationId}/messages`,
    {
      headers: getAuthHeaders(),
    },
  )
  if (!response.ok) throw new Error(`获取消息失败: ${response.statusText}`)
  const data: {
    id: string
    role: string
    content: string
    create_time: string
    tool_steps_json?: string | null
  }[] = await response.json()
  return data.map((m) => {
    // Bug2: 从后端恢复工具调用 segments
    let segments: MessageSegment[] | undefined
    if (m.tool_steps_json) {
      try {
        segments = JSON.parse(m.tool_steps_json) as MessageSegment[]
      } catch { /* JSON 解析失败则忽略 */ }
    }
    // Bug2: 如果后端没有 segments，尝试从 localStorage 恢复
    if (!segments || segments.length === 0) {
      try {
        const cached = localStorage.getItem(`chat_segments_${conversationId}`)
        if (cached) {
          segments = JSON.parse(cached) as MessageSegment[]
        }
      } catch { /* localStorage 读取失败则忽略 */ }
    }
    return {
      role: m.role as "user" | "assistant",
      content: m.content,
      segments,
    }
  })
}

// ==================== 路由 ====================

export const Route = createFileRoute("/_layout/video_editing/$scriptId")({
  component: ScriptDetailPage,
})

// ==================== 历史会话弹窗 ====================

function HistoryDialog({
  open,
  onOpenChange,
  conversations,
  onLoadConversation,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  conversations: Conversation[]
  onLoadConversation: (convId: string) => void
}) {
  const sortedConversations = [...conversations].sort(
    (a, b) =>
      new Date(b.create_time).getTime() - new Date(a.create_time).getTime(),
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>历史会话</DialogTitle>
          <DialogDescription>选择一个历史会话以继续对话</DialogDescription>
        </DialogHeader>
        <ScrollArea className="max-h-[400px]">
          {sortedConversations.length === 0 ? (
            <p className="text-muted-foreground text-sm py-6 text-center">
              暂无历史会话
            </p>
          ) : (
            <div className="flex flex-col gap-2 py-2">
              {sortedConversations.map((conv) => (
                <button
                  key={conv.id}
                  type="button"
                  onClick={() => {
                    onLoadConversation(conv.id)
                    onOpenChange(false)
                  }}
                  className="flex items-start gap-3 rounded-[--radius-standard] p-3
                    hover:bg-muted/60 transition-colors text-left w-full"
                >
                  <MessageSquare className="h-4 w-4 mt-0.5 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium truncate">{conv.title}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {conv.create_time}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          )}
        </ScrollArea>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" size="sm">
              关闭
            </Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ==================== 消息段渲染（Bug3: 按时间线排列） ====================

/** 单个工具步骤卡片 — 直接展示全部信息 */
function ToolSegmentView({ segment }: { segment: MessageSegment }) {
  const isRunning = segment.status === "running"
  const isCompleted = segment.status === "completed"

  return (
    <div className="flex items-start gap-2 text-xs my-1">
      {/* 状态指示点 */}
      <div className="shrink-0 pt-1">
        <div
          className={`h-3 w-3 rounded-full border-2 flex items-center justify-center
            ${isRunning ? "border-blue-400 bg-blue-100 dark:bg-blue-900" : ""}
            ${isCompleted ? "border-emerald-400 bg-emerald-100 dark:bg-emerald-900" : ""}
            ${!isRunning && !isCompleted ? "border-muted-foreground/30" : ""}`}
        >
          {isRunning && <Loader2 className="h-2 w-2 animate-spin text-blue-500" />}
          {isCompleted && <div className="h-1.5 w-1.5 rounded-full bg-emerald-500" />}
        </div>
      </div>

      {/* 工具卡片 */}
      <div
        className={`min-w-0 flex-1 rounded-md border px-2 py-1.5
          ${isRunning
            ? "bg-blue-500/8 dark:bg-blue-400/10 border-blue-500/15 dark:border-blue-400/20"
            : ""
          }
          ${isCompleted
            ? "bg-emerald-500/8 dark:bg-emerald-400/10 border-emerald-500/15 dark:border-emerald-400/20"
            : ""
          }
          ${!isRunning && !isCompleted ? "bg-muted/40 border-border/50" : ""}`}
      >
        <div className="flex items-center gap-1.5">
          <Wrench
            className={`h-3 w-3 shrink-0
              ${isRunning ? "text-blue-500" : ""}
              ${isCompleted ? "text-emerald-500" : ""}
              ${!isRunning && !isCompleted ? "text-muted-foreground" : ""}`}
          />
          <span
            className={`font-semibold
              ${isRunning ? "text-blue-600 dark:text-blue-300" : ""}
              ${isCompleted ? "text-emerald-600 dark:text-emerald-300" : ""}
              ${!isRunning && !isCompleted ? "text-muted-foreground" : ""}`}
          >
            {friendlyToolName(segment.tool_name || "unknown")}
          </span>
          {isRunning && (
            <span className="text-[10px] text-blue-500">执行中</span>
          )}
          {isCompleted && (
            <span className="text-[10px] text-emerald-500">完成</span>
          )}
        </div>

        {segment.tool_args && (
          <pre className="mt-1 text-[11px] font-mono whitespace-pre-wrap break-all text-muted-foreground bg-muted/50 rounded px-1.5 py-0.5 max-h-[120px] overflow-y-auto">
            {segment.tool_args}
          </pre>
        )}
        {segment.result && (
          <pre className="mt-1 text-[11px] font-mono whitespace-pre-wrap break-all text-emerald-700 dark:text-emerald-400 bg-muted/50 rounded px-1.5 py-0.5 max-h-[200px] overflow-y-auto">
            {segment.result}
          </pre>
        )}
      </div>
    </div>
  )
}

/** 按时间线渲染消息段（替代旧的 ToolStepsView） */
function MessageSegments({ segments }: { segments: MessageSegment[] }) {
  if (segments.length === 0) return null

  return (
    <div className="flex flex-col gap-1">
      {segments.map((seg, idx) => {
        if (seg.type === "tool") {
          return <ToolSegmentView key={idx} segment={seg} />
        }
        if (seg.type === "text" && seg.content) {
          return (
            <div key={idx} className="whitespace-pre-wrap text-sm leading-relaxed">
              {formatResponse(seg.content)}
            </div>
          )
        }
        return null
      })}
    </div>
  )
}

// ==================== 消息气泡渲染 ====================

function MessageBubble({ msg }: { msg: ChatMessage }) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-[--radius-standard] px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap bg-primary text-primary-foreground">
          {msg.content}
        </div>
      </div>
    )
  }

  // assistant 消息 — Bug3 修复：优先使用 segments 按时间线渲染
  const hasSegments = msg.segments && msg.segments.length > 0
  const hasToolSteps = !hasSegments && msg.toolSteps && msg.toolSteps.length > 0
  const hasContent = msg.content.trim().length > 0
  const isStreaming = msg.streaming && !hasContent && !hasSegments && !hasToolSteps

  return (
    <div className="flex justify-start">
      <div
        className="max-w-[85%] rounded-[--radius-standard] px-3 py-2 text-sm leading-relaxed
        bg-muted/60 text-foreground"
      >
        {/* Bug3 修复: 按 segments 时间线顺序渲染 */}
        {hasSegments && <MessageSegments segments={msg.segments!} />}

        {/* 向后兼容: 旧格式 toolSteps（无 segments 时使用） */}
        {hasToolSteps && (
          <div className="flex flex-col gap-1.5 mb-2">
            {msg.toolSteps!.map((step, idx) => (
              <div
                key={idx}
                className="flex items-start gap-2 text-xs rounded-md px-2.5 py-1.5
                  bg-blue-500/8 dark:bg-blue-400/10 border border-blue-500/15 dark:border-blue-400/20"
              >
                <Wrench className="h-3.5 w-3.5 mt-0.5 shrink-0 text-blue-500 dark:text-blue-400" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold text-blue-600 dark:text-blue-300">
                      {friendlyToolName(step.tool_name)}
                    </span>
                    {step.status === "running" && (
                      <Loader2 className="h-3 w-3 animate-spin text-blue-400" />
                    )}
                    {step.status === "completed" && (
                      <ChevronRight className="h-3 w-3 text-emerald-500" />
                    )}
                  </div>
                  {step.tool_args && (
                    <p className="text-muted-foreground mt-0.5 line-clamp-2 font-mono text-[11px]">
                      {step.tool_args}
                    </p>
                  )}
                  {step.result && (
                    <p className="mt-1 text-emerald-700 dark:text-emerald-400 line-clamp-3 whitespace-pre-wrap">
                      {step.result}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 文本内容: 无 segments 时显示; 有 segments 但其中不含 text 段时也显示(从后端加载的场景) */}
        {hasContent && (!hasSegments || !msg.segments!.some(s => s.type === "text")) && (
          <div className="whitespace-pre-wrap">
            {formatResponse(msg.content)}
          </div>
        )}

        {/* 流式加载占位 */}
        {isStreaming && (
          <span className="inline-flex items-center gap-1.5 text-muted-foreground">
            <Brain className="h-3.5 w-3.5 animate-pulse" />
            思考中...
          </span>
        )}
      </div>
    </div>
  )
}

// ==================== 聊天面板 ====================

function ChatPanel({
  scriptId,
  onTaskCreated,
}: {
  scriptId: string
  onTaskCreated?: (taskId: string) => void
}) {
  // --- 会话状态 ---
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [currentConvId, setCurrentConvId] = useState<string>("")
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [convTitle, setConvTitle] = useState<string>("新建会话")

  // --- UI 状态 ---
  const [inputValue, setInputValue] = useState("")
  const [isSending, setIsSending] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [_isLoading, setIsLoading] = useState(true)

  // --- 工具执行进度追踪 (inline HITL) ---
  const [runningTools, setRunningTools] = useState<Map<string, { name: string; args: string }>>(new Map())
  const [toolErrors, setToolErrors] = useState<Map<string, string>>(new Map())

  // --- Refs ---
  const scrollRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const _currentConv = conversations.find((c) => c.id === currentConvId)

  // 自动滚动到底部
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [])

  // --- 初始化：从后端加载历史会话列表 ---
  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const convs = await fetchConversations(scriptId)
        if (cancelled) return
        setConversations(convs)
        // 如果有历史会话，默认加载最近的一个
        if (convs.length > 0) {
          const latest = convs[0] // 已按 update_time desc 排序
          setCurrentConvId(latest.id)
          setConvTitle(latest.title)
          const msgs = await fetchConversationMessages(latest.id)
          if (cancelled) return
          setMessages(msgs)
          // 缓存消息到 conversations 中
          setConversations((prev) =>
            prev.map((c) =>
              c.id === latest.id ? { ...c, messages: msgs } : c,
            ),
          )
        }
      } catch (err) {
        console.error("加载会话列表失败:", err)
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [scriptId]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- 新建会话 ---
  const handleNewConversation = useCallback(() => {
    const newId = generateId()
    const now = formatNow()
    const newConv: Conversation = {
      id: newId,
      title: "新建会话",
      create_time: now,
      title_set: false,
      messages: [],
    }
    setConversations((prev) => [newConv, ...prev])
    setCurrentConvId(newId)
    setMessages([])
    setConvTitle("新建会话")
  }, [])

  // --- 加载历史会话（从后端获取消息） ---
  const handleLoadConversation = useCallback(
    async (convId: string) => {
      const conv = conversations.find((c) => c.id === convId)
      if (!conv) return
      setCurrentConvId(convId)
      setConvTitle(conv.title)

      // 如果已有缓存消息，直接使用
      if (conv.messages.length > 0) {
        setMessages(conv.messages)
        return
      }

      // 否则从后端加载
      try {
        const msgs = await fetchConversationMessages(convId)
        setMessages(msgs)
        // 缓存到 conversations
        setConversations((prev) =>
          prev.map((c) => (c.id === convId ? { ...c, messages: msgs } : c)),
        )
      } catch (err) {
        console.error("加载会话消息失败:", err)
        setMessages([])
      }
    },
    [conversations],
  )

  // --- 更新会话数据 ---
  const updateConversation = useCallback(
    (convId: string, updates: Partial<Conversation>) => {
      setConversations((prev) =>
        prev.map((c) => (c.id === convId ? { ...c, ...updates } : c)),
      )
    },
    [],
  )

  // --- 发送消息 ---
  const handleSend = useCallback(async () => {
    const userText = inputValue.trim()
    if (!userText || isSending) return

    // 如果没有当前会话，先新建一个
    let convId = currentConvId
    if (!convId) {
      convId = generateId()
      const newConv: Conversation = {
        id: convId,
        title: "新建会话",
        create_time: formatNow(),
        title_set: false,
        messages: [],
      }
      setConversations((prev) => [newConv, ...prev])
      setCurrentConvId(convId)
    }

    const userMsg: ChatMessage = { role: "user", content: userText }
    const newMessages = [...messages, userMsg]
    setMessages(newMessages)
    setInputValue("")
    setIsSending(true)

    const conv = conversations.find((c) => c.id === convId)
    if (!conv?.title_set) {
      const autoTitle =
        userText.length > 20 ? `${userText.slice(0, 20)}...` : userText
      setConvTitle(autoTitle)
      updateConversation(convId, {
        title: autoTitle,
        title_set: true,
      })
    }

    // 添加助手占位消息（streaming=true 显示"思考中..."）
    const assistantMsg: ChatMessage = {
      role: "assistant",
      content: "",
      toolSteps: [],
      streaming: true,
    }
    const messagesWithPlaceholder = [...newMessages, assistantMsg]
    setMessages(messagesWithPlaceholder)

    const data = {
      message: userText,
      script_id: scriptId,
      conversation_id: convId,
    }

    const token = localStorage.getItem("access_token")
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    }
    if (token) {
      headers.Authorization = `Bearer ${token}`
    }

    const abortController = new AbortController()
    abortRef.current = abortController

    // 累积 assistant 消息的可变状态
    let assistantContent = ""
    let toolSteps: ToolStep[] = []
    // Bug3: 按时间线记录所有事件段
    let segments: MessageSegment[] = []

    try {
      if (STREAM_FLAG) {
        // --- 流式输出 ---
        const response = await fetch(VIDEO_AGENT_STREAM_URL, {
          method: "POST",
          headers,
          body: JSON.stringify(data),
          signal: abortController.signal,
        })

        if (!response.ok) {
          throw new Error(`请求失败: ${response.statusText}`)
        }

        const reader = response.body?.getReader()
        if (!reader) throw new Error("无法获取响应流")

        const decoder = new TextDecoder()

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          const chunk = decoder.decode(value, { stream: true })
          const lines = chunk.split("\n")

          for (const line of lines) {
            const jsonStr = line.replace(/^data:\s*/, "").trim()
            if (!jsonStr) continue

            if (jsonStr.startsWith("{") && jsonStr.endsWith("}")) {
              try {
                const event = JSON.parse(jsonStr)
                const eventType = event.type

                if (eventType === "text" && event.content) {
                  assistantContent += event.content
                  // 合并连续文本段，避免每个 token 拆成独立碎片导致渲染宽度异常
                  const lastSeg = segments[segments.length - 1]
                  if (lastSeg && lastSeg.type === "text") {
                    segments = [
                      ...segments.slice(0, -1),
                      { ...lastSeg, content: (lastSeg.content || "") + event.content },
                    ]
                  } else {
                    segments = [...segments, { type: "text", content: event.content }]
                  }
                  setMessages([
                    ...newMessages,
                    {
                      role: "assistant",
                      content: assistantContent,
                      toolSteps: [...toolSteps],
                      segments: [...segments],
                      streaming: true,
                    },
                  ])
                } else if (eventType === "thinking" && event.content) {
                  assistantContent += event.content
                  const lastSeg = segments[segments.length - 1]
                  if (lastSeg && lastSeg.type === "text") {
                    segments = [
                      ...segments.slice(0, -1),
                      { ...lastSeg, content: (lastSeg.content || "") + event.content },
                    ]
                  } else {
                    segments = [...segments, { type: "text", content: event.content }]
                  }
                  setMessages([
                    ...newMessages,
                    {
                      role: "assistant",
                      content: assistantContent,
                      toolSteps: [...toolSteps],
                      segments: [...segments],
                      streaming: true,
                    },
                  ])
                } else if (eventType === "tool_call") {
                  const newStep: ToolStep = {
                    tool_name: event.tool_name || "unknown",
                    tool_args: event.tool_args,
                    status: "running",
                  }
                  // Track running tool for inline progress bar
                  setRunningTools((prev) => {
                    const next = new Map(prev)
                    next.set(event.tool_name || "unknown", {
                      name: event.tool_name || "unknown",
                      args: event.tool_args || "",
                    })
                    return next
                  })
                  toolSteps = [...toolSteps, newStep]
                  // Bug3: 按时间线插入工具调用段
                  segments = [
                    ...segments,
                    {
                      type: "tool",
                      tool_name: event.tool_name || "unknown",
                      tool_args: event.tool_args,
                      status: "running",
                    },
                  ]
                  setMessages([
                    ...newMessages,
                    {
                      role: "assistant",
                      content: assistantContent,
                      toolSteps: [...toolSteps],
                      segments: [...segments],
                      streaming: true,
                    },
                  ])
                } else if (eventType === "tool_result") {
                  const toolName = event.tool_name
                  let toolLastMatchIdx = -1
                  for (let i = toolSteps.length - 1; i >= 0; i--) {
                    if (toolSteps[i].tool_name === toolName && toolSteps[i].status === "running") {
                      toolLastMatchIdx = i
                      break
                    }
                  }
                  toolSteps = toolSteps.map((step, idx) =>
                    idx === toolLastMatchIdx
                      ? { ...step, result: event.result, status: "completed" as const }
                      : step,
                  )
                  // Bug3: 更新最后一个匹配的 running 工具段为 completed
                  let lastRunningIdx = -1
                  for (let i = segments.length - 1; i >= 0; i--) {
                    if (segments[i].type === "tool" && segments[i].tool_name === toolName && segments[i].status === "running") {
                      lastRunningIdx = i
                      break
                    }
                  }
                  if (lastRunningIdx >= 0) {
                    segments = segments.map((seg, idx) =>
                      idx === lastRunningIdx
                        ? { ...seg, result: event.result, status: "completed" }
                        : seg,
                    )
                  }
                  // Remove completed tool from runningTools
                  setRunningTools((prev) => {
                    const next = new Map(prev)
                    next.delete(toolName)
                    return next
                  })
                  setMessages([
                    ...newMessages,
                    {
                      role: "assistant",
                      content: assistantContent,
                      toolSteps: [...toolSteps],
                      segments: [...segments],
                      streaming: true,
                    },
                  ])
                } else if (eventType === "error") {
                  assistantContent += `\n\n错误: ${event.content || "未知错误"}`
                  // Track tool error for inline error display
                  const errName = event.tool_name || "unknown"
                  setToolErrors((prev) => {
                    const next = new Map(prev)
                    next.set(errName, event.content || "未知错误")
                    return next
                  })
                  setRunningTools((prev) => {
                    const next = new Map(prev)
                    next.delete(errName)
                    return next
                  })
                  const errorText = `\n\n错误: ${event.content || "未知错误"}`
                  const lastSeg = segments[segments.length - 1]
                  if (lastSeg && lastSeg.type === "text") {
                    segments = [
                      ...segments.slice(0, -1),
                      { ...lastSeg, content: (lastSeg.content || "") + errorText },
                    ]
                  } else {
                    segments = [...segments, { type: "text", content: errorText }]
                  }
                  setMessages([
                    ...newMessages,
                    {
                      role: "assistant",
                      content: assistantContent,
                      toolSteps: [...toolSteps],
                      segments: [...segments],
                      streaming: true,
                    },
                  ])
                } else if (eventType === "hitl_continue") {
                  // HITL: recursion limit reached, prompt user to continue
                  assistantContent +=
                    "\n\n---\n> " +
                    (event.message || "Execution limit reached. Reply 'continue' to resume.")
                  setMessages([
                    ...newMessages,
                    {
                      role: "assistant",
                      content: assistantContent,
                      toolSteps: [...toolSteps],
                      segments: [...segments],
                      streaming: false,
                    },
                  ])
                } else if (eventType === "task_created") {
                  // Path B: 分镜方案被后端检测到并启动了编辑任务
                  console.log("[Path B] task_created SSE received:", event.task_id)
                  onTaskCreated?.(event.task_id)
                } else if (eventType === "done") {
                  break
                }
              } catch {
                // JSON 解析错误，跳过
              }
            }
          }
        }

        // 保存最终消息 — Bug2: 同时写入 localStorage 作为备份
        const finalMessages = [
          ...newMessages,
          {
            role: "assistant" as const,
            content: assistantContent,
            toolSteps: toolSteps,
            segments: segments,
            streaming: false,
          },
        ]
        setMessages(finalMessages)
        updateConversation(convId, { messages: finalMessages })
        // Bug2: 持久化到 localStorage
        try {
          localStorage.setItem(
            `chat_segments_${convId}`,
            JSON.stringify(segments),
          )
        } catch { /* localStorage 不可用时静默忽略 */ }
      } else {
        // --- 非流式输出 ---
        const response = await fetch(VIDEO_AGENT_CHAT_URL, {
          method: "POST",
          headers,
          body: JSON.stringify(data),
          signal: abortController.signal,
        })

        if (!response.ok) {
          throw new Error(`请求失败: ${response.statusText}`)
        }

        const responseJson = await response.json()
        const assistantContent = responseJson.message || "无回复内容"

        const finalMessages = [
          ...newMessages,
          {
            role: "assistant" as const,
            content: assistantContent,
            streaming: false,
          },
        ]
        setMessages(finalMessages)
        updateConversation(convId, { messages: finalMessages })
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        // 用户取消
      } else {
        const errorMsg = `请求失败: ${(err as Error).message || "请稍后再试"}`
        const finalMessages = [
          ...newMessages,
          { role: "assistant" as const, content: errorMsg, streaming: false },
        ]
        setMessages(finalMessages)
        updateConversation(convId, { messages: finalMessages })
      }
    } finally {
      setIsSending(false)
      abortRef.current = null
    }
  }, [
    inputValue,
    isSending,
    messages,
    currentConvId,
    conversations,
    scriptId,
    updateConversation,
  ])

  // --- Enter 发送 ---
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend],
  )

  return (
    <section className="flex flex-col h-[600px] rounded-[--radius-card] border border-border/50 bg-card">
      {/* --- 头部 --- */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/50">
        <h2 className="text-sm font-semibold truncate max-w-[200px]">
          {convTitle}
        </h2>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={handleNewConversation}
            title="新建会话"
          >
            <Plus className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setHistoryOpen(true)}
            title="历史会话"
          >
            <History className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* --- 工具执行进度条 (inline HITL) --- */}
      {runningTools.size > 0 && (
        <div className="px-4 py-2 border-b border-border/50 bg-muted/30">
          {Array.from(runningTools.values()).map((t) => (
            <div key={t.name} className="flex items-center gap-2 text-xs">
              <Loader2 className="h-3 w-3 animate-spin text-blue-500" />
              <span className="font-medium text-blue-600">{friendlyToolName(t.name)}</span>
              <span className="text-muted-foreground truncate">执行中...</span>
            </div>
          ))}
        </div>
      )}
      {/* --- 工具错误提示 (inline ErrorCard) --- */}
      {toolErrors.size > 0 && (
        <div className="px-4 py-2 border-b border-amber-500/30 bg-amber-500/5">
          {Array.from(toolErrors.entries()).map(([name, err]) => (
            <div key={name} className="flex items-start gap-2 text-xs">
              <AlertTriangle className="h-3.5 w-3.5 text-amber-500 shrink-0 mt-0.5" />
              <div>
                <span className="font-semibold text-amber-600">
                  {friendlyToolName(name)} 失败:
                </span>{" "}
                <span className="text-muted-foreground line-clamp-2">{err}</span>
              </div>
              <button
                type="button"
                className="text-xs text-blue-500 hover:underline shrink-0 ml-auto"
                onClick={() => setToolErrors((prev) => {
                  const next = new Map(prev); next.delete(name); return next
                })}
              >
                关闭
              </button>
            </div>
          ))}
        </div>
      )}

      {/* --- 消息列表 --- */}
      <ScrollArea className="flex-1 px-4 py-3" ref={scrollRef}>
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center gap-2 py-12">
            <MessageSquare className="h-8 w-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">
              开始对话，AI 将协助你进行视频编辑
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {messages.map((msg, idx) => (
              <MessageBubble key={idx} msg={msg} />
            ))}
          </div>
        )}
      </ScrollArea>

      {/* --- 输入区域 --- */}
      <div className="flex items-end gap-2 px-4 py-3 border-t border-border/50">
        <Textarea
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          className="min-h-[40px] max-h-[120px] resize-none flex-1 text-sm"
          disabled={isSending}
        />
        <Button
          variant="wise"
          size="icon"
          onClick={handleSend}
          disabled={!inputValue.trim() || isSending}
          title="发送"
        >
          {isSending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Send className="h-4 w-4" />
          )}
        </Button>
      </div>

      {/* --- 历史会话弹窗 --- */}
      <HistoryDialog
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        conversations={conversations}
        onLoadConversation={handleLoadConversation}
      />
    </section>
  )
}

// ==================== 主页面组件 ====================

function ScriptDetailPage() {
  const navigate = useNavigate()
  const { scriptId } = Route.useParams()

  // Path B: edit task tracking
  const [editTaskId, setEditTaskId] = useState<string | null>(null)

  useEffect(() => {
    if (editTaskId) console.log("[Path B] editTaskId set, starting poll:", editTaskId)
  }, [editTaskId])

  const { data: editProgress } = useQuery({
    queryKey: ["editProgress", editTaskId],
    queryFn: async () => {
      if (!editTaskId) return null
      const res = await fetch(`${API_BASE_URL}/api/v1/edit/${editTaskId}/progress`, {
        headers: getAuthHeaders(),
      })
      if (!res.ok) return null
      return res.json() as Promise<{
        task_id: string; status: string; current_step: number
        total_steps: number; steps_done: number; steps_failed: number
        progress_pct: number; current_step_name?: string; error_message?: string
      }>
    },
    enabled: !!editTaskId,
    refetchInterval: (query) => {
      const d = query.state.data
      if (d && (d.status === "running" || d.status === "planning")) return 2000
      return false
    },
  })

  const {
    data: scriptData,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["scriptStatus", scriptId],
    queryFn: () => getScriptStatus(scriptId),
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.is_generating_characters || data?.is_generating_shots) {
        return 3000
      }
      return false
    },
    staleTime: 0,
    refetchOnMount: true,
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error || !scriptData) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
        <p className="text-muted-foreground">加载剧本详情失败</p>
        <button
          type="button"
          onClick={() => navigate({ to: "/video_editing" })}
          className="wise-pill-button text-sm"
        >
          返回列表
        </button>
      </div>
    )
  }

  const { script_name, characters, shot_scripts, scene_backgrounds } =
    scriptData

  const sceneGroups = Array.from(
    new Set(shot_scripts.map((s) => s.scene_group)),
  ).sort((a, b) => a - b)

  return (
    <div className="flex flex-col gap-10 p-6 md:p-10 max-w-[1400px] mx-auto">
      {/* 返回按钮 */}
      <button
        type="button"
        onClick={() => navigate({ to: "/video_editing" })}
        className="inline-flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors w-fit text-caption"
      >
        <ArrowLeft className="h-4 w-4" />
        返回剧本列表
      </button>

      {/* ===== 剧本名 + 聊天对话框 ===== */}
      <section className="space-y-4">
        <h1 className="text-3xl font-black tracking-tight">{script_name}</h1>
        <ChatPanel
          scriptId={scriptId}
          onTaskCreated={(tid) => setEditTaskId(tid)}
        />

        {/* Path B: 编辑任务实时进度 */}
        {editProgress && editProgress.status !== "completed" && editProgress.status !== "failed" && (
          <ControlBar
            taskId={editProgress.task_id}
            status={editProgress.status}
            currentStep={editProgress.current_step}
            totalSteps={editProgress.total_steps}
            progressPct={editProgress.progress_pct}
            currentStepName={editProgress.current_step_name}
          />
        )}
        {editProgress && editProgress.status === "failed" && editProgress.error_message && (
          <ErrorCard
            taskId={editProgress.task_id}
            stepIndex={editProgress.current_step}
            stepName={editProgress.current_step_name || "unknown"}
            error={editProgress.error_message}
            onDismiss={() => setEditTaskId(null)}
          />
        )}
        {editProgress && editProgress.status === "completed" && (
          <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-4 text-sm text-emerald-600">
            编辑任务已完成！共 {editProgress.total_steps} 步。
          </div>
        )}
      </section>

      {/* ===== 角色四视图 ===== */}
      {characters.length > 0 && (
        <section className="space-y-5">
          <h2 className="heading-feature flex items-center gap-2">
            <Users className="h-5 w-5 text-primary" />
            角色四视图
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
            {characters.map((char) => {
              const imgUrl = convertImagePathToUrl(
                char.three_view_image_path,
                scriptId,
              )
              return (
                <div
                  key={char.id}
                  className="group flex flex-col items-center gap-3"
                >
                  <div
                    className="relative w-full rounded-[16px] overflow-hidden
                      bg-muted/40 border border-border/50
                      group-hover:shadow-lg transition-shadow duration-200"
                  >
                    {imgUrl ? (
                      <img
                        src={imgUrl}
                        alt={char.role_name}
                        className="w-full h-auto object-contain"
                      />
                    ) : (
                      <div className="flex items-center justify-center w-full aspect-[3/4] text-muted-foreground">
                        <Users className="h-10 w-10 opacity-30" />
                      </div>
                    )}
                  </div>
                  <span className="text-caption font-semibold text-center line-clamp-2">
                    {char.role_name}
                  </span>
                  {char.three_view_image_path && (
                    <span className="text-[10px] text-muted-foreground/60 text-center line-clamp-1 w-full">
                      {char.three_view_image_path.split(/[/\\]/).pop()}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        </section>
      )}

      {/* ===== 场景图 ===== */}
      {scene_backgrounds.length > 0 && (
        <section className="space-y-5">
          <h2 className="heading-feature flex items-center gap-2">
            <ImageIcon className="h-5 w-5 text-primary" />
            场景图
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {scene_backgrounds.map((bg) => {
              const imgUrl = convertImagePathToUrl(
                bg.background_image_path,
                scriptId,
              )
              return (
                <div
                  key={bg.scene_group}
                  className="group flex flex-col items-center gap-3"
                >
                  <div
                    className="relative w-full rounded-[16px] overflow-hidden
                      bg-muted/40 border border-border/50
                      group-hover:shadow-lg transition-shadow duration-200"
                  >
                    {imgUrl ? (
                      <img
                        src={imgUrl}
                        alt={`场景组${bg.scene_group}`}
                        className="w-full h-auto object-contain"
                      />
                    ) : (
                      <div className="flex items-center justify-center w-full aspect-video text-muted-foreground">
                        <ImageIcon className="h-10 w-10 opacity-30" />
                      </div>
                    )}
                  </div>
                  <span className="text-caption font-semibold text-center">
                    场景组{bg.scene_group}：{bg.scene_name}
                  </span>
                  {bg.background_image_path && (
                    <span className="text-[10px] text-muted-foreground/60 text-center line-clamp-1 w-full">
                      {bg.background_image_path.split(/[/\\]/).pop()}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        </section>
      )}

      {/* ===== 分镜头首尾帧图 ===== */}
      {shot_scripts.length > 0 && (
        <section className="space-y-5">
          <h2 className="heading-feature flex items-center gap-2">
            <LayoutGrid className="h-5 w-5 text-primary" />
            分镜头首尾帧图
          </h2>
          {sceneGroups.map((sceneGroup) => {
            const sceneShots = shot_scripts.filter(
              (s) => s.scene_group === sceneGroup,
            )
            const sceneName = sceneShots[0]?.scene_name || "默认场景"
            return (
              <div key={sceneGroup} className="space-y-4">
                <h3 className="text-body-semibold text-muted-foreground">
                  场景组{sceneGroup}：{sceneName}
                </h3>
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
                  {sceneShots.map((shot) => {
                    const firstImgUrl = convertImagePathToUrl(
                      shot.first_frame_image_path,
                      scriptId,
                    )
                    const lastImgUrl = convertImagePathToUrl(
                      shot.last_frame_image_path,
                      scriptId,
                    )
                    return (
                      <div
                        key={shot.id}
                        className="group flex flex-col items-center gap-3"
                      >
                        <div
                          className="relative w-full rounded-[16px] overflow-hidden
                            bg-muted/40 border border-border/50
                            group-hover:shadow-lg transition-shadow duration-200"
                        >
                          {firstImgUrl ? (
                            <img
                              src={firstImgUrl}
                              alt={`分镜${shot.shot_no}首帧`}
                              className="w-full h-auto object-contain"
                            />
                          ) : (
                            <div className="flex items-center justify-center w-full aspect-square text-muted-foreground">
                              <LayoutGrid className="h-10 w-10 opacity-30" />
                            </div>
                          )}
                        </div>
                        {lastImgUrl && (
                          <div
                            className="relative w-full rounded-[16px] overflow-hidden
                              bg-muted/40 border border-border/50
                              group-hover:shadow-lg transition-shadow duration-200"
                          >
                            <img
                              src={lastImgUrl}
                              alt={`分镜${shot.shot_no}尾帧`}
                              className="w-full h-auto object-contain"
                            />
                          </div>
                        )}
                        <span className="text-caption font-semibold text-center">
                          分镜{shot.shot_no}
                        </span>
                        {shot.first_frame_image_path && (
                          <span className="text-[10px] text-muted-foreground/60 text-center line-clamp-1 w-full">
                            {shot.first_frame_image_path.split(/[/\\]/).pop()}
                          </span>
                        )}
                        {lastImgUrl && shot.last_frame_image_path && (
                          <span className="text-[10px] text-muted-foreground/60 text-center line-clamp-1 w-full">
                            {shot.last_frame_image_path.split(/[/\\]/).pop()}
                          </span>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </section>
      )}

      {/* ===== 分镜视频 ===== */}
      {shot_scripts.some((s) => s.video_path) && (
        <section className="space-y-5">
          <h2 className="heading-feature flex items-center gap-2">
            <Video className="h-5 w-5 text-primary" />
            分镜视频
          </h2>
          {sceneGroups.map((sceneGroup) => {
            const sceneShots = shot_scripts.filter(
              (s) => s.scene_group === sceneGroup && s.video_path,
            )
            if (sceneShots.length === 0) return null
            const sceneName = sceneShots[0]?.scene_name || "默认场景"
            return (
              <div key={sceneGroup} className="space-y-4">
                <h3 className="text-body-semibold text-muted-foreground">
                  场景组{sceneGroup}：{sceneName}
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                  {sceneShots.map((shot) => {
                    const videoUrl = convertImagePathToUrl(
                      shot.video_path,
                      scriptId,
                    )
                    return (
                      <div
                        key={shot.id}
                        className="group flex flex-col items-center gap-3"
                      >
                        <div
                          className="relative w-full rounded-[16px] overflow-hidden
                            bg-muted/40 border border-border/50
                            group-hover:shadow-lg transition-shadow duration-200"
                        >
                          {videoUrl ? (
                            <video
                              src={videoUrl}
                              controls
                              preload="metadata"
                              className="w-full h-auto object-contain"
                            >
                              您的浏览器不支持视频播放
                            </video>
                          ) : (
                            <div className="flex items-center justify-center w-full aspect-video text-muted-foreground">
                              <Video className="h-10 w-10 opacity-30" />
                            </div>
                          )}
                        </div>
                        <span className="text-caption font-semibold text-center">
                          分镜{shot.shot_no}
                        </span>
                        {shot.video_path && (
                          <span className="text-[10px] text-muted-foreground/60 text-center line-clamp-1 w-full">
                            {shot.video_path.split(/[/\\]/).pop()}
                          </span>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </section>
      )}

      {/* 空状态 */}
      {characters.length === 0 &&
        shot_scripts.length === 0 &&
        scene_backgrounds.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-center gap-3">
            <div className="rounded-full bg-muted p-4">
              <FileText className="h-8 w-8 text-muted-foreground" />
            </div>
            <h3 className="heading-feature">内容生成中</h3>
            <p className="text-muted-foreground">
              AI正在生成角色信息和分镜头脚本，请稍候...
            </p>
          </div>
        )}
    </div>
  )
}
