# 需求 08：API 路由骨架（编辑任务 CRUD + 进度查询）

> 原 PRD 编号: P0-8 的 API 部分 (F-5) | 优先级: P0

## 1. 依赖关系

- **前置依赖**：req_03（StepOrchestrator — 提供编辑任务执行能力）、req_05（CRUD 函数 — 提供数据读写）、req_06（StoryboardToSteps — `/edit/start` 端点调用）
- **被谁依赖**：req_16（前端步骤进度条 — 消费 API 数据）、req_18（前端错误卡片 — 消费 `/progress` 和 `/steps` 数据）、req_19（前端视频预览 — 消费进度数据）、req_21（前端控制工具栏 — 消费 pause/resume/cancel 端点）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

项目已有成熟的 API 路由模式：
- [videoagent.py](../../../backend/app/agent/api/routes/videoagent.py) — VideoAgent 聊天 API（chat + stream + conversations）
- [text2video.py](../../../backend/app/agent/api/routes/text2video.py) — 文生视频 API（脚本创建、角色生成、分镜头）
- 路由使用 FastAPI `APIRouter` + `CurrentUser` 依赖注入 + `SessionDep`
- 认证通过 JWT Bearer token（`get_auth_headers()` 函数）

**需要新增的路由**：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/edit/start` | POST | 发起编辑任务 |
| `/api/v1/edit/{task_id}/progress` | GET | 轮询实时进度 |
| `/api/v1/edit/{task_id}/steps` | GET | 获取步骤列表及状态 |
| `/api/v1/edit/{task_id}/pause` | POST | 暂停 |
| `/api/v1/edit/{task_id}/resume` | POST | 继续 |
| `/api/v1/edit/{task_id}/cancel` | POST | 取消 |
| `/api/v1/edit/{task_id}/retry-step` | POST | 重试某步骤 |
| `/api/v1/edit/{task_id}/skip-step` | POST | 跳过某步骤 |
| `/api/v1/edit/pending` | GET | 查询待决策任务 |
| `/ws/edit/{task_id}` | WebSocket | 实时推送进度 |

### 代码库校验结论

- 新增路由文件放在 `backend/app/api/routes/edit.py`
- 路由注册到 `backend/app/main.py`（FastAPI app.include_router）
- 复用 `app.api.deps` 中的 `CurrentUser`、`SessionDep`
- `retry-step` 和 `skip-step` 端点需要 StepOrchestrator 提供对应方法（在 req_03 中保留接口）

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `backend/app/api/routes/edit.py` | 编辑任务所有 API 路由 |
| 修改 | `backend/app/main.py` | 注册新路由 |

### 核心技术细节

```python
"""edit.py — 编辑任务 API 路由"""
import uuid
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from sqlmodel import Session
from app.api.deps import CurrentUser, SessionDep
from app import crud
from app.models import EditTaskCreate, EditTaskUpdate, EditTaskPublic, EditStepPublic

router = APIRouter(prefix="/edit", tags=["edit"])


@router.post("/start", response_model=EditTaskPublic)
async def start_edit(
    storyboard_json: dict,   # LLM 生成的分镜方案 JSON
    script_id: str,
    current_user: CurrentUser,
    session: SessionDep,
):
    """发起编辑任务。接收 LLM 生成的分镜 JSON，创建 EditPlan 并开始执行。"""
    from app.agent.skills_agent.storyboard_parser import StoryboardParser
    from app.agent.skills_agent.step_orchestrator import get_orchestrator

    # 1. 解析分镜方案 → EditPlan
    parser = StoryboardParser()
    plan = parser.parse(
        storyboard_json, str(current_user.id), script_id,
    )

    # 2. 持久化 EditTask（初始状态）
    task_in = EditTaskCreate(
        user_id=current_user.id,
        script_id=uuid.UUID(script_id),
        total_steps=len(plan.steps),
        project_state_json=json.dumps(plan.project_state),
    )
    task = crud.create_edit_task(session=session, task_in=task_in)

    # 3. 持久化所有步骤
    for i, step_spec in enumerate(plan.steps):
        step = crud.upsert_edit_step(
            session=session,
            task_id=task.id,
            step_index=i,
            step_data={
                "tool_name": step_spec.tool,
                "args_json": json.dumps(step_spec.args),
                "status": "pending",
            },
        )

    # 4. 启动编排器（异步后台执行）
    plan.task_id = str(task.id)
    orchestrator = get_orchestrator()
    # 使用 asyncio.create_task 或 BackgroundTasks 启动异步执行
    import asyncio
    asyncio.create_task(orchestrator.execute(plan))

    return task


@router.get("/{task_id}/progress")
async def get_task_progress(
    task_id: uuid.UUID,
    session: SessionDep,
):
    """获取任务实时进度（从 PG + Redis 联合查询）"""
    task = crud.get_edit_task(session=session, task_id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    steps = crud.get_steps_by_task(session=session, task_id=task_id)
    done = sum(1 for s in steps if s.status == "done")
    failed = sum(1 for s in steps if s.status == "failed")

    return {
        "task_id": str(task.id),
        "status": task.status,
        "current_step": task.current_step,
        "total_steps": task.total_steps,
        "steps_done": done,
        "steps_failed": failed,
        "progress_pct": round(done / max(task.total_steps, 1) * 100, 1),
        "current_step_name": (
            steps[task.current_step].tool_name
            if 0 <= task.current_step < len(steps)
            else None
        ),
        "error_message": task.error_message,
    }


@router.get("/{task_id}/steps", response_model=list[EditStepPublic])
async def get_task_steps(
    task_id: uuid.UUID,
    session: SessionDep,
):
    """获取任务所有步骤的详细状态"""
    task = crud.get_edit_task(session=session, task_id=task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return crud.get_steps_by_task(session=session, task_id=task_id)


@router.post("/{task_id}/pause")
async def pause_task(
    task_id: uuid.UUID,
    current_user: CurrentUser,
):
    """暂停任务（当前步骤完成后生效）"""
    orchestrator = get_orchestrator()
    ok = await orchestrator.pause(str(task_id))
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在或无法暂停")
    return {"status": "paused"}


@router.post("/{task_id}/resume")
async def resume_task(
    task_id: uuid.UUID,
    current_user: CurrentUser,
):
    """继续暂停的任务"""
    orchestrator = get_orchestrator()
    plan = await orchestrator.resume(str(task_id))
    return {"status": plan.status.value}


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: uuid.UUID,
    current_user: CurrentUser,
    session: SessionDep,
):
    """取消任务"""
    ok = crud.update_edit_task_status(
        session=session, task_id=task_id, status="failed",
    )
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"status": "cancelled"}


@router.post("/{task_id}/retry-step")
async def retry_step(
    task_id: uuid.UUID,
    step_index: int,
    current_user: CurrentUser,
):
    """重试失败的步骤"""
    orchestrator = get_orchestrator()
    ok = await orchestrator.retry_step(str(task_id), step_index)
    if not ok:
        raise HTTPException(status_code=404, detail="步骤不存在或无法重试")
    return {"status": "retrying"}


@router.post("/{task_id}/skip-step")
async def skip_step(
    task_id: uuid.UUID,
    step_index: int,
    current_user: CurrentUser,
    session: SessionDep,
):
    """跳过失败的步骤"""
    step = crud.upsert_edit_step(
        session=session, task_id=task_id, step_index=step_index,
        step_data={"status": "skipped"},
    )
    return {"status": "skipped"}


@router.get("/pending")
async def get_pending_tasks(current_user: CurrentUser):
    """查询当前用户待决策的任务（暂停/失败状态）"""
    # 在 req_12（用户交互队列）完成后，此端点可查询 Redis pending 队列
    # 当前阶段从 PG 查询
    from app.core.db import engine
    with Session(engine) as session:
        tasks = crud.get_edit_tasks_by_user(
            session=session, user_id=current_user.id,
        )
    return [
        {
            "task_id": str(t.id),
            "script_id": str(t.script_id),
            "status": t.status,
            "error_message": t.error_message,
        }
        for t in tasks if t.status in ("paused", "failed")
    ]


# WebSocket 实时推送（基础框架，完整实现在 req_19）
@router.websocket("/ws/{task_id}")
async def websocket_progress(websocket: WebSocket, task_id: str):
    await websocket.accept()
    try:
        while True:
            # 当前阶段：简单轮询 PG，每 2 秒推送一次
            # req_19 升级为 Redis PubSub
            data = await websocket.receive_text()
            # 返回最新进度...
            await websocket.send_json({"task_id": task_id, "progress": 0})
    except WebSocketDisconnect:
        pass
```

### 容错与边界

- 所有端点需要 JWT 认证（通过 `CurrentUser` 依赖注入）
- `/start` 端点需验证 `storyboard_json` 的结构合法性（调用 `StoryboardParser.validate()`）
- `current_step` 索引越界时返回 `None` 而非崩溃
- WebSocket 端点初始用轮询实现，留出升级到 Redis PubSub 的接口
- `/pending` 端点当前从 PG 查询，req_12 完成后升级为 Redis 查询

## 4. 验收标准 (DoD)

- [ ] `POST /edit/start` 接收合法分镜 JSON 后创建 EditTask 并返回 201
- [ ] `GET /edit/{task_id}/progress` 返回包含 `progress_pct` 和 `steps_done` 的进度对象
- [ ] `GET /edit/{task_id}/steps` 返回步骤列表，按 `step_index` 升序
- [ ] `POST /edit/{task_id}/pause` 返回成功，任务状态变为 paused
- [ ] `POST /edit/{task_id}/resume` 成功恢复暂停的任务
- [ ] `POST /edit/{task_id}/cancel` 将任务状态设为 failed/cancelled
- [ ] 所有端点通过 JWT 认证（未登录返回 401）
- [ ] WebSocket 端点接受连接并推送 JSON 进度数据
