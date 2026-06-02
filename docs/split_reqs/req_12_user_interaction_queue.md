# 需求 12：用户交互队列（Redis pending）

> 原 PRD 编号: P1-7 (E-3) | 优先级: P1

## 1. 依赖关系

- **前置依赖**：req_03（StepOrchestrator — 步骤失败时触发暂停并写入 pending 队列）、req_14（Redis EditTaskState Manager — 提供 Redis 读写能力）
- **被谁依赖**：req_18（前端错误卡片 — 需要从 pending 队列获取待决策任务列表）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前项目已有成熟的用户交互模式（`utils/redis.py` 中的 `VideoProjectRedisManager`）：

```python
# 现有的待办队列模式（可直接复用）
class ProjectStatus(str, Enum):
    WAITING_REVIEW = "waiting_review"  # 等待用户审核
    REVIEWING = "reviewing"            # 用户审核中
    ...

async def add_to_todo_queue(project_id, user_id, stage, message):
    """将项目添加到用户待办队列"""
    todo_key = f"video_todo:{user_id}:{project_id}"
    ...
```

**PRD 定义的用户交互流程**：

```
步骤失败 → Agent 判断 ErrorLevel → USER / FATAL
  ├── USER: 暂停执行，Redis Set user_pending:{user_id} ← task_id
  │         前端轮询发现 → 弹出确认框（重试/跳过/手动修复）
  └── FATAL: 终止执行，记录错误日志，通知前端
```

### 代码库校验结论

- `ProjectStatus.WAITING_REVIEW` 和 `add_to_todo_queue()` / `remove_from_todo_queue()` / `get_user_todo_queue()` 提供了完美的复用模板
- `EditTaskState`（req_14）应新增 `error_message` 字段承载失败原因
- 前端的 text2video 审核交互模式（用户修改角色信息后触发 Celery 恢复流程）可直接复用到编辑步骤的"手动修复"模式
- 交互队列需要与 req_11（错误分类）配合：仅 `RetryAction.NOTIFY_USER` 类错误才进入 pending 队列

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `backend/app/agent/utils/redis.py` | 新增 `EditTaskRedisManager` 扩展用户交互队列方法（或复用现有 `VideoProjectRedisManager`） |
| 修改 | `backend/app/agent/skills_agent/step_orchestrator.py` | 步骤失败时根据错误分类决定是否写入 pending 队列 |

### 核心技术细节

**复用 VideoProjectRedisManager 模式**：

```python
# utils/redis.py — 扩展 VideoProjectRedisManager 或新增 helper

class EditTaskRedisManager:
    """编辑任务 Redis 状态管理器（复用现有 Redis 连接模式）"""

    EDIT_PENDING_PREFIX = "edit_pending"      # user_pending:{user_id}
    EDIT_TASK_STATE_PREFIX = "edit_task"       # edit_task:{task_id}

    def __init__(self, redis_client):
        self.redis = redis_client

    async def add_user_pending(
        self, user_id: str, task_id: str, step_index: int, error: str,
    ) -> bool:
        """将任务添加到用户待决策队列"""
        pending_key = f"{self.EDIT_PENDING_PREFIX}:{user_id}"
        data = {
            "task_id": task_id,
            "step_index": step_index,
            "error": error[:500],  # 截断过长错误信息
            "created_at": time.time(),
        }
        await self.redis.sadd(pending_key, task_id)
        await self.redis.set(
            f"{self.EDIT_PENDING_PREFIX}:detail:{task_id}",
            json.dumps(data),
            ex=86400 * 3,  # 72 小时 TTL
        )
        await self.redis.expire(pending_key, 86400 * 3)
        logger.info(f"添加待决策任务: user={user_id}, task={task_id}, step={step_index}")
        return True

    async def remove_user_pending(self, user_id: str, task_id: str) -> bool:
        """用户处理完成后移除"""
        pending_key = f"{self.EDIT_PENDING_PREFIX}:{user_id}"
        await self.redis.srem(pending_key, task_id)
        await self.redis.delete(f"{self.EDIT_PENDING_PREFIX}:detail:{task_id}")
        return True

    async def get_user_pending(self, user_id: str) -> list[dict]:
        """获取用户的待决策任务列表"""
        pending_key = f"{self.EDIT_PENDING_PREFIX}:{user_id}"
        task_ids = await self.redis.smembers(pending_key)
        items = []
        for tid in task_ids:
            detail = await self.redis.get(f"{self.EDIT_PENDING_PREFIX}:detail:{tid}")
            if detail:
                items.append(json.loads(detail))
            else:
                await self.redis.srem(pending_key, tid)  # 清理过期
        return items

    async def get_pending_count(self, user_id: str) -> int:
        """获取待决策任务数量（供前端 badge 显示）"""
        pending_key = f"{self.EDIT_PENDING_PREFIX}:{user_id}"
        return await self.redis.scard(pending_key)
```

**StepOrchestrator 集成**：

```python
# step_orchestrator.py — _execute_single_step 中集成

from error_retry_map import ErrorRetryConfig, RetryAction

async def _execute_single_step(self, plan, step) -> Any:
    # ... 执行逻辑 ...
    except Exception as e:
        strategy = ErrorRetryConfig.get_strategy(e)

        if strategy["action"] == RetryAction.NOTIFY_USER:
            step.status = StepStatus.FAILED
            step.error = str(e)
            plan.status = TaskStatus.PAUSED

            # 写入用户待决策队列
            if self.redis:
                await self.redis.add_user_pending(
                    user_id=plan.user_id,
                    task_id=plan.task_id,
                    step_index=plan.current_index,
                    error=str(e),
                )

            await self._save_step_checkpoint(plan, step)
            raise StepPausedError(f"步骤需用户决策: {e}")

        elif strategy["action"] == RetryAction.FATAL:
            plan.status = TaskStatus.FAILED
            step.error = f"[FATAL] {e}"
            raise
        # ... RETRY 逻辑不变 ...
```

**API 端点扩展**（在 req_08 的 `edit.py` 中）：

```python
@router.get("/pending")
async def get_pending_tasks(current_user: CurrentUser):
    """查询当前用户待决策的编辑任务"""
    from app.agent.utils.redis import EditTaskRedisManager, get_video_project_manager
    manager = get_video_project_manager()
    edit_manager = EditTaskRedisManager(manager.redis_client)
    items = await edit_manager.get_user_pending(str(current_user.id))
    count = await edit_manager.get_pending_count(str(current_user.id))
    return {"pending_tasks": items, "total": count}
```

### 容错与边界

- Redis TTL 72 小时，过期任务自动清理
- `add_user_pending()` 的 `error` 字段截断至 500 字符（防止过长错误信息）
- 用户同一 task 多次失败写 pending 时，`sadd` 幂等去重
- `get_user_pending()` 自动跳过已过期的 detail key
- 前端轮询 `GET /edit/pending`（每 5 秒轮询替代 WebSocket 推送，保持简单）

## 4. 验收标准 (DoD)

- [ ] `add_user_pending(user_id, task_id, 2, "TTS合成超时")` 成功写入 Redis
- [ ] `get_user_pending(user_id)` 返回包含 `task_id`、`step_index`、`error` 的待决策列表
- [ ] 同一 task 多次写入 pending 队列时去重（只出现一次）
- [ ] `remove_user_pending(user_id, task_id)` 从 set 和 detail key 中删除
- [ ] Redis TTL 到期后待决策项自动消失
- [ ] `GET /edit/pending` API 返回正确的待决策任务列表
- [ ] StepOrchestrator 在 NOTIFY_USER 错误时自动暂停并写入 pending 队列
