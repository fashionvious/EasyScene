# 需求 05：CRUD 函数（edit_task / edit_step / user_preference）

> 原 PRD 编号: P0-1 的 CRUD 部分 + P1-8 的 CRUD 部分 | 优先级: P0

## 1. 依赖关系

- **前置依赖**：req_04（PG 数据模型 — 定义 EditTask/EditStep/UserPreference 表结构）
- **被谁依赖**：req_03（StepOrchestrator — 通过 CRUD 读写 checkpoint）、req_08（API 路由 — 查询进度）、req_14（Redis Manager — 与 PG 互补写入）、req_15（会话记忆 — 读写用户偏好）、req_20（指标聚合 — 查询步骤统计数据）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库的 `crud.py`（[backend/app/crud.py](../../../backend/app/crud.py)）已提供了成熟的 CRUD 模式：

```python
def create_user(*, session: Session, user_create: UserCreate) -> User:
    db_obj = User.model_validate(user_create, update={"hashed_password": ...})
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj
```

**现状**：

1. 现有 CRUD 仅覆盖 User/Item/Conversation/ChatMessage 表
2. 无 edit_task / edit_step / user_preference 的 CRUD 函数
3. 现有模式均使用 `sqlmodel.Session`（同步），但 StepOrchestrator 可能需要异步支持（redis 是 async）
4. 项目同时存在同步 Session（`crud.py`）和异步 redis 操作（`utils/redis.py`），需要协调

### 代码库校验结论

- `crud.py` 的函数签名模式 `def xxx(*, session: Session, ...) -> Model:` 直接复用
- 需要新增的函数量：edit_task（5个）、edit_step（4个）、user_preference（3个），共约 12 个
- 对于 Orchestrator 的异步调用，可创建异步包装或使用 `asyncio.to_thread` 执行同步 CRUD
- 或者直接使用 `sqlmodel` 的 `session.exec()` 在异步上下文中（需使用 `AsyncSession`）

**架构决策**：维持同步 CRUD 模式（与现有代码一致），StepOrchestrator 通过 `run_in_executor` 调用。不引入 SQLAlchemy async session 以保持依赖简洁。

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `backend/app/crud.py` | 新增 edit_task / edit_step / user_preference CRUD 函数 |

### 核心技术细节

```python
# ==================== EditTask CRUD ====================

def create_edit_task(*, session: Session, task_in: EditTaskCreate) -> EditTask:
    db_obj = EditTask.model_validate(task_in)
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def get_edit_task(*, session: Session, task_id: uuid.UUID) -> EditTask | None:
    return session.get(EditTask, task_id)


def get_edit_tasks_by_user(
    *, session: Session, user_id: uuid.UUID, limit: int = 20
) -> list[EditTask]:
    statement = (
        select(EditTask)
        .where(EditTask.user_id == user_id)
        .where(EditTask.is_deleted == 0)
        .order_by(EditTask.update_time.desc())
        .limit(limit)
    )
    return list(session.exec(statement).all())


def update_edit_task(
    *, session: Session, db_task: EditTask, task_in: EditTaskUpdate
) -> EditTask:
    task_data = task_in.model_dump(exclude_unset=True)
    db_task.sqlmodel_update(task_data)
    db_task.update_time = datetime.utcnow()
    session.add(db_task)
    session.commit()
    session.refresh(db_task)
    return db_task


def update_edit_task_status(
    *, session: Session, task_id: uuid.UUID, status: str
) -> EditTask | None:
    """便捷方法：仅更新任务状态"""
    task = session.get(EditTask, task_id)
    if task:
        task.status = status
        task.update_time = datetime.utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)
    return task


# ==================== EditStep CRUD ====================

def create_edit_step(*, session: Session, step_in: EditStepCreate) -> EditStep:
    db_obj = EditStep.model_validate(step_in)
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def upsert_edit_step(
    *, session: Session, task_id: uuid.UUID, step_index: int, step_data: dict
) -> EditStep:
    """创建或更新步骤 checkpoint"""
    statement = select(EditStep).where(
        EditStep.task_id == task_id,
        EditStep.step_index == step_index,
    )
    existing = session.exec(statement).first()

    if existing:
        existing.status = step_data.get("status", existing.status)
        existing.result_json = step_data.get("result_json", existing.result_json)
        existing.error = step_data.get("error", existing.error)
        existing.retry_count = step_data.get("retry_count", existing.retry_count)
        existing.update_time = datetime.utcnow()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    else:
        step = EditStep(
            task_id=task_id,
            step_index=step_index,
            tool_name=step_data["tool_name"],
            args_json=step_data.get("args_json"),
            status=step_data.get("status", "pending"),
            result_json=step_data.get("result_json"),
            error=step_data.get("error"),
            started_at=step_data.get("started_at"),
            finished_at=step_data.get("finished_at"),
            retry_count=step_data.get("retry_count", 0),
        )
        session.add(step)
        session.commit()
        session.refresh(step)
        return step


def get_steps_by_task(
    *, session: Session, task_id: uuid.UUID
) -> list[EditStep]:
    statement = (
        select(EditStep)
        .where(EditStep.task_id == task_id)
        .order_by(EditStep.step_index.asc())
    )
    return list(session.exec(statement).all())


def get_step_stats(
    *, session: Session, task_id: uuid.UUID
) -> dict:
    """获取步骤统计（供 req_20 指标聚合使用）"""
    steps = get_steps_by_task(session=session, task_id=task_id)
    total = len(steps)
    done = sum(1 for s in steps if s.status == "done")
    failed = sum(1 for s in steps if s.status == "failed")
    total_retries = sum(s.retry_count for s in steps)
    avg_duration = 0.0
    completed = [s for s in steps if s.finished_at and s.started_at]
    if completed:
        durations = [
            (s.finished_at - s.started_at).total_seconds() for s in completed
        ]
        avg_duration = sum(durations) / len(durations)
    return {
        "total": total, "done": done, "failed": failed,
        "total_retries": total_retries, "avg_duration_seconds": avg_duration,
    }


# ==================== UserPreference CRUD ====================

def get_or_create_user_preference(
    *, session: Session, user_id: uuid.UUID
) -> UserPreference:
    statement = select(UserPreference).where(UserPreference.user_id == user_id)
    pref = session.exec(statement).first()
    if not pref:
        pref = UserPreference(user_id=user_id)
        session.add(pref)
        session.commit()
        session.refresh(pref)
    return pref


def update_user_preference(
    *, session: Session, user_id: uuid.UUID, pref_in: UserPreferenceUpdate
) -> UserPreference:
    pref = get_or_create_user_preference(session=session, user_id=user_id)
    pref_data = pref_in.model_dump(exclude_unset=True)
    pref.sqlmodel_update(pref_data)
    pref.update_time = datetime.utcnow()
    session.add(pref)
    session.commit()
    session.refresh(pref)
    return pref
```

### 容错与边界

- `upsert_edit_step` 使用 select-then-insert/update 模式，避免唯一约束冲突
- `get_or_create_user_preference` 确保首次使用时自动创建默认偏好记录
- 所有 CRUD 函数接收和返回 SQLModel 实例，与现有 `crud.py` 模式保持一致
- `session.commit()` 失败时调用方负责 rollback（与现有模式一致）
- `get_step_stats` 仅用于指标查询（req_20），不作为热路径使用

## 4. 验收标准 (DoD)

- [ ] `create_edit_task()` 创建任务后数据库有对应记录
- [ ] `upsert_edit_step()` 首次调用创建步骤，再次调用更新同一 `(task_id, step_index)` 的步骤
- [ ] `get_steps_by_task(task_id)` 按 `step_index` 升序返回所有步骤
- [ ] `get_or_create_user_preference(user_id)` 对不存在的用户自动创建空偏好
- [ ] `get_step_stats(task_id)` 返回正确的 done/failed 计数和平均耗时
- [ ] 所有 CRUD 函数的签名与现有 `crud.py` 模式一致（`*, session: Session`）
- [ ] 数据库 commit 成功后函数返回刷新过的对象（含自增 ID 和时间戳）
