# 需求 19：前端视频预览（VideoPreview）+ WebSocket 实时推送

> 原 PRD 编号: P2-2 (F-4) | 优先级: P2

## 1. 依赖关系

- **前置依赖**：req_08（API 路由 — WebSocket 端点 + 进度 API）、req_16（Redis EditTaskState — `metadata.preview_path` 数据源）
- **被谁依赖**：无

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前前端代码（[$scriptId.tsx](../../../frontend/src/routes/_layout/video_editing/$scriptId.tsx)）：

- 已有视频播放器嵌入（`<video>` 标签 + `controls` 属性，第 1156-1163 行）用于展示已生成的分镜视频
- 已有 SSE 流式处理（第 603-705 行）用于聊天流式输出
- 已有 `WebSocket` 用于......当前无 WebSocket 使用（SSE 替代了聊天推送需求）

**PRD 设计的 VideoPreview**：
- 步骤产出可预览的视频片段时（如 `smart_rough_cut` 完成后），内嵌 `<video>` 播放器
- 数据来源：Redis `EditTaskState.metadata.preview_path`
- WebSocket 推送预览就绪事件，前端即时展示

**WebSocket vs SSE 取舍**：
- 进度推送场景：双向推送不多，单向服务器→客户端推送为主
- 但 WebSocket 更通用，支持未来的双向指令（如"跳过当前步骤"的实时响应）
- 收益不高但成本同样低——FastAPI WebSocket 开箱即用

### 代码库校验结论

- `backend/app/api/routes/edit.py`（req_08）已预留 WebSocket 端点骨架
- WebSocket 的 Redis PubSub 推送当前可降级为轮询（每 2 秒从 PG 读取），降低 P2 阶段的实现风险
- 前端已有 `<video>` 标签使用经验（className `w-full h-auto object-contain`）

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `frontend/src/components/Edit/VideoPreview.tsx` | 视频预览组件 |
| 修改 | `backend/app/api/routes/edit.py` | WebSocket 端点升级为 Redis PubSub 推送 |
| 可选 | `backend/app/agent/utils/redis.py` | 新增 Redis PubSub publish 辅助方法 |

### 核心技术细节

**VideoPreview 组件**：

```typescript
// components/Edit/VideoPreview.tsx

interface VideoPreviewProps {
  previewPath: string | null
  stepName: string
  isLoading: boolean
}

export function VideoPreview({ previewPath, stepName, isLoading }: VideoPreviewProps) {
  if (!previewPath && !isLoading) return null

  const videoUrl = previewPath
    ? `${API_BASE_URL}/static/${encodeURIComponent(previewPath)}`
    : null

  return (
    <div className="rounded-lg border border-border/50 bg-card overflow-hidden">
      <div className="px-4 py-2 border-b border-border/50">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Video className="h-4 w-4 text-primary" />
          步骤预览: {stepName}
        </h3>
      </div>

      <div className="p-1">
        {isLoading ? (
          <div className="flex items-center justify-center aspect-video bg-muted/30">
            <div className="flex flex-col items-center gap-2">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              <span className="text-xs text-muted-foreground">正在生成预览...</span>
            </div>
          </div>
        ) : videoUrl ? (
          <video
            src={videoUrl}
            controls
            preload="metadata"
            className="w-full h-auto object-contain rounded"
          >
            您的浏览器不支持视频播放
          </video>
        ) : null}
      </div>
    </div>
  )
}
```

**WebSocket 升级（Redis PubSub）**：

```python
# edit.py — WebSocket 端点升级

@router.websocket("/ws/edit/{task_id}")
async def websocket_edit_progress(websocket: WebSocket, task_id: str):
    await websocket.accept()

    # 通过 Redis PubSub 订阅任务进度更新
    from app.agent.utils.redis import get_video_project_manager
    manager = get_video_project_manager()
    pubsub = manager.redis_client.pubsub()

    channel = f"edit_progress:{task_id}"
    await pubsub.subscribe(channel)

    try:
        # 发送初始状态
        state = await _get_edit_state(task_id)
        if state:
            await websocket.send_json({
                "type": "progress",
                "task_id": task_id,
                "status": state.status,
                "current_step": state.current_step,
                "total_steps": state.total_steps,
                "progress_pct": state.progress_pct,
                "preview_path": state.metadata.get("preview_path"),
            })

        # 持续监听 Redis PubSub 消息
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30)
            if message and message["type"] == "message":
                data = json.loads(message["data"])
                await websocket.send_json(data)

    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel)
```

**Redis PubSub publish 辅助方法**：

```python
# utils/redis.py — EditTaskRedisManager 新增方法

async def publish_progress(self, task_id: str, event_type: str, data: dict) -> None:
    """通过 Redis PubSub 推送进度事件"""
    channel = f"edit_progress:{task_id}"
    message = json.dumps({"type": event_type, "task_id": task_id, **data})
    await self.redis.publish(channel, message)

async def notify_preview_ready(self, task_id: str, preview_path: str) -> None:
    """推送视频预览就绪事件"""
    await self.publish_progress(task_id, "preview_ready", {
        "preview_path": preview_path,
        "message": "视频预览已就绪",
    })
```

### 容错与边界

- WebSocket 断开时前端自动重连（3 次指数退避），重连失败后降级为轮询模式（每 2 秒 `GET /progress`）
- `preview_path` 为绝对文件路径时，需通过 `/static/` 代理访问（或使用 `file://` URL——但浏览器安全策略可能阻止）
- 视频文件不存在时的错误处理：显示占位图标 + 文本提示
- Redis PubSub 不可用时，WebSocket 退化为简单轮询（每 2 秒推送一次）
- VideoPreview 组件对 `previewPath=null` 时返回 `null`（不渲染任何内容）

## 4. 验收标准 (DoD)

- [ ] VideoPreview 组件在 `previewPath` 有值时渲染 `<video>` 播放器
- [ ] VideoPreview 在加载中（`isLoading=true`）时显示 Spinner
- [ ] WebSocket 端点建立连接后推送初始进度状态
- [ ] Redis PubSub 发布 `preview_ready` 事件后，前端实时收到并展示视频
- [ ] WebSocket 断开时前端降级为 HTTP 轮询（每 2 秒）
- [ ] `previewPath` 为 null 时 VideoPreview 不渲染任何内容
- [ ] 视频文件不存在时显示友好的占位提示
