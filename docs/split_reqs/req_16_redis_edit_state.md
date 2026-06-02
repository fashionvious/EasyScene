# 需求 16：Redis EditTaskState 模型与管理器

> 原 PRD 编号: P0-7 的 Redis 部分 (E-2) | 优先级: P0

## 1. 依赖关系

- **前置依赖**：req_04（PG 数据模型 — EditTask/EditStep 表供 Redis 互补引用）
- **被谁依赖**：req_03（StepOrchestrator — 更新 Redis 实时状态）、req_08（API 路由 — `/progress` 端点查询 Redis）、req_12（用户交互队列 — 复用 Redis 连接）、req_19（前端视频预览 — Redis PubSub 推送预览就绪事件）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

项目已有成熟且完整的 Redis 管理模块（[utils/redis.py](../../../backend/app/agent/utils/redis.py)）：

```python
class ProjectGlobalState(BaseModel):    # 项目级状态
class TaskExecutionState(BaseModel):    # 任务级状态
class UserTodoItem(BaseModel):          # 用户待办项
class VideoProjectRedisManager:         # 完整的管理器（~400 行）
```

**PRD 策略**：直接复用 `VideoProjectRedisManager` 的模式，新增 `EditTaskState` 模型和 `EditTaskRedisManager`。

**Redis 与 PG 的分工**：

| 存储 | 用途 | TTL |
|------|------|-----|
| PostgreSQL (edit_task + edit_step) | 永久数据——步骤详情、完整日志、历史记录 | 永久 |
| Redis (EditTaskState) | 实时数据——当前进度、Celery task_id、最新错误 | 72 小时 |

### 代码库校验结论

- 现有 `redis.py` 已包含 `RedisConfig`（TTL 配置、键前缀）、`VideoProjectRedisManager`（CRUD 模式）、`UserTodoItem`（待办项模型）
- 新增 `EditTaskState` 模型只需约 30 行（参考 `ProjectGlobalState` 结构）
- 新增 `EditTaskRedisManager` 只需约 100 行（复用现有 redis 客户端 + 模式）
- 不需要新的 Redis 连接——复用 `get_video_project_manager()` 返回的 `redis.Redis` 客户端

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `backend/app/agent/utils/redis.py` | 新增 `EditTaskState` 模型 + `EditTaskRedisManager` |

### 核心技术细节

```python
# utils/redis.py — 新增内容

# ============================================================================
# EditTaskState 实时状态模型（新增）
# ============================================================================

class EditTaskStatus(str, Enum):
    PLANNING = "planning"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class EditTaskState(BaseModel):
    """编辑任务实时状态（Redis Hash，TTL 72h）"""
    task_id: str = Field(..., description="任务唯一标识")
    user_id: str = Field(..., description="用户ID")
    script_id: str = Field(..., description="关联的剧本ID")
    status: str = Field(default="planning", description="planning|running|paused|completed|failed")
    current_step: int = Field(default=0, description="当前执行到第几步")
    total_steps: int = Field(default=0, description="总步骤数")
    step_name: str = Field(default="", description="当前步骤名称")
    step_status: str = Field(default="pending", description="当前步骤状态")
    progress_pct: float = Field(default=0.0, description="当前步骤进度百分比")
    celery_task_id: str | None = Field(default=None, description="关联的 Celery 任务ID")
    error_message: str | None = Field(default=None, description="最近错误信息")
    metadata: dict = Field(default_factory=dict, description="扩展元数据（如 preview_path）")
    updated_at: float = Field(default_factory=time.time, description="最后更新时间戳")


class EditTaskRedisManager:
    """
    编辑任务 Redis 状态管理器。

    复用现有 VideoProjectRedisManager 的 redis 客户端和键前缀模式。
    PG 存永久记录（edit_task/edit_step），Redis 存实时状态（EditTaskState）。
    """

    TASK_TTL = 86400 * 3     # 72 小时
    KEY_PREFIX = "edit_task"

    def __init__(self, redis_client):
        self.redis = redis_client

    # ---- 核心 CRUD ----

    async def create_task_state(self, state: EditTaskState) -> bool:
        """创建任务实时状态"""
        key = f"{self.KEY_PREFIX}:{state.task_id}"
        await self.redis.set(
            key, state.model_dump_json(), ex=self.TASK_TTL,
        )
        logger.info(f"[EditTaskRedis] 创建任务状态: {state.task_id}")
        return True

    async def get_task_state(self, task_id: str) -> EditTaskState | None:
        """获取任务实时状态"""
        key = f"{self.KEY_PREFIX}:{task_id}"
        data = await self.redis.get(key)
        if not data:
            return None
        return EditTaskState.model_validate_json(data)

    async def update_task_state(
        self, task_id: str, **kwargs,
    ) -> bool:
        """部分更新任务状态（仅更新传入的字段）"""
        state = await self.get_task_state(task_id)
        if not state:
            return False

        for field, value in kwargs.items():
            if hasattr(state, field):
                setattr(state, field, value)

        state.updated_at = time.time()
        key = f"{self.KEY_PREFIX}:{task_id}"
        await self.redis.set(
            key, state.model_dump_json(), ex=self.TASK_TTL,
        )
        return True

    async def update_step_progress(
        self, task_id: str, step_index: int, step_name: str,
        step_status: str, progress_pct: float,
    ) -> bool:
        """便捷方法：更新当前步骤进度"""
        return await self.update_task_state(
            task_id=task_id,
            current_step=step_index,
            step_name=step_name,
            step_status=step_status,
            progress_pct=progress_pct,
        )

    async def set_error(self, task_id: str, error: str) -> bool:
        """记录错误"""
        return await self.update_task_state(
            task_id=task_id, error_message=error, status="failed",
        )

    async def set_celery_task_id(self, task_id: str, celery_task_id: str) -> bool:
        """关联 Celery 任务 ID"""
        return await self.update_task_state(
            task_id=task_id, celery_task_id=celery_task_id,
        )

    async def set_metadata(self, task_id: str, key: str, value: any) -> bool:
        """设置扩展元数据（如 preview_path）"""
        state = await self.get_task_state(task_id)
        if not state:
            return False
        state.metadata[key] = value
        state.updated_at = time.time()
        redis_key = f"{self.KEY_PREFIX}:{task_id}"
        await self.redis.set(
            redis_key, state.model_dump_json(), ex=self.TASK_TTL,
        )
        return True

    async def delete_task_state(self, task_id: str) -> bool:
        """任务完成后清理实时状态"""
        key = f"{self.KEY_PREFIX}:{task_id}"
        await self.redis.delete(key)
        return True
```

### 容错与边界

- Redis 不可用时（连接失败），`EditTaskRedisManager` 所有方法应静默降级（catch + log warning）
- `EditTaskState.progress_pct` 由 StepOrchestrator 在每个步骤完成后更新
- `metadata` 字段为 `dict` 类型，用于携带可扩展数据（如 `preview_path`, `ffmpeg_eta`）
- Redis TTL 72 小时后自动过期，前端需处理"Redis 数据不存在"状态（回退到 PG 查询）
- `celery_task_id` 关联在 Celery 任务提交时写入，前端通过此 ID 查询 Celery 任务进度

## 4. 验收标准 (DoD)

- [ ] `create_task_state(EditTaskState(...))` 成功写入 Redis，`get_task_state(task_id)` 可读取
- [ ] `update_task_state(task_id, status="running", current_step=2)` 部分更新成功
- [ ] `update_step_progress(task_id, 3, "smart_rough_cut", "running", 45.0)` 便捷方法正确
- [ ] Redis TTL 72 小时后数据自动过期
- [ ] Redis 连接失败时方法不抛异常（静默降级 + log warning）
- [ ] `set_metadata(task_id, "preview_path", "D:/output/preview.mp4")` 正确存储
- [ ] `delete_task_state(task_id)` 成功删除 key
