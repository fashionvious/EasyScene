# 需求 04：PostgreSQL 数据模型（edit_task / edit_step / user_preference）

> 原 PRD 编号: P0-1 的 PG 部分 + P1-8 的 PG 部分 | 优先级: P0

## 1. 依赖关系

- **前置依赖**：无（独立于其他需求，纯数据模型定义）
- **被谁依赖**：req_03（StepOrchestrator — 读写 edit_task/edit_step 表）、req_05（CRUD 函数 — 操作这些表）、req_08（API 路由 — 查询进度）、req_15（会话记忆 — 读写 user_preference 表）、req_20（指标聚合 — 查询 edit_step 统计）

## 2. 现状与痛点分析

### 基于实际代码库的架构修正

当前代码库的 `models.py`（[backend/app/models.py](../../../backend/app/models.py)）已使用 **SQLModel** 模式定义了用户、剧本、会话等表，模式成熟且可直接复用：

```python
class Script(ScriptBase, table=True):     # 已有
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    creator_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    ...
```

**现状**：

1. **无 EditTask/EditStep 表**：Agent 编辑流程的状态完全在内存中（InMemorySaver），无持久化
2. **无 UserPreference 表**：用户偏好（分辨率、帧率、TTS 发音人等）无存储
3. 现有模式模板优秀：UUID 主键 + `is_deleted` 软删除 + `create_time`/`update_time` 时间戳，新表直接复用此模式
4. **PRD v2.0 的修正**：v1.0 曾建议 SQLite checkpoint，v2.0 已修正为复用现有 PostgreSQL 基础设施

### 代码库校验结论

- 直接在 `app/models.py` 中新增 3 个 SQLModel 表类
- 数据库迁移依赖项目已有的 Alembic 或 SQLModel `create_all` 机制
- 新增表的 CRUD 在 req_05 中实现，不在本需求范围

## 3. 代码级实现方案

### 涉及文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `backend/app/models.py` | 新增 EditTask / EditStep / UserPreference 表定义 |

### 核心技术细节

```python
# ==================== Edit Task Models ====================

class EditTaskBase(SQLModel):
    """编辑任务基础字段"""
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    script_id: uuid.UUID = Field(foreign_key="script.id", nullable=False)
    status: str = Field(default="planning", max_length=20)
    # planning | running | paused | completed | failed
    current_step: int = Field(default=0)     # 当前执行到第几步
    total_steps: int = Field(default=0)      # 总步骤数
    project_state_json: str | None = Field(default=None)  # JSON 序列化的 ProjectContext 快照
    error_message: str | None = Field(default=None)


class EditTaskCreate(EditTaskBase):
    pass


class EditTaskUpdate(SQLModel):
    status: str | None = Field(default=None, max_length=20)
    current_step: int | None = None
    total_steps: int | None = None
    project_state_json: str | None = None
    error_message: str | None = None


class EditTask(EditTaskBase, table=True):
    __tablename__ = "edit_task"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    create_time: datetime = Field(default_factory=datetime.utcnow)
    update_time: datetime = Field(default_factory=datetime.utcnow)
    is_deleted: int = Field(default=0)


class EditTaskPublic(EditTaskBase):
    id: uuid.UUID
    create_time: datetime
    update_time: datetime


# ==================== Edit Step Models ====================

class EditStepBase(SQLModel):
    """编辑步骤基础字段"""
    task_id: uuid.UUID = Field(foreign_key="edit_task.id", nullable=False, ondelete="CASCADE")
    step_index: int                        # 步骤序号（从 0 开始）
    tool_name: str = Field(max_length=100) # 工具名（对应 ToolRegistry）
    args_json: str | None = Field(default=None)  # JSON 序列化的参数
    status: str = Field(default="pending", max_length=20)
    # pending | running | done | failed | skipped
    result_json: str | None = Field(default=None)  # JSON 序列化的执行结果
    error: str | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)
    retry_count: int = Field(default=0)


class EditStepCreate(EditStepBase):
    pass


class EditStepUpdate(SQLModel):
    status: str | None = Field(default=None, max_length=20)
    result_json: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_count: int | None = None


class EditStep(EditStepBase, table=True):
    __tablename__ = "edit_step"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    create_time: datetime = Field(default_factory=datetime.utcnow)
    update_time: datetime = Field(default_factory=datetime.utcnow)


class EditStepPublic(EditStepBase):
    id: uuid.UUID
    create_time: datetime
    update_time: datetime


# ==================== User Preference Models ====================

class UserPreferenceBase(SQLModel):
    """用户偏好（剪辑习惯）"""
    preferred_resolution: str | None = Field(default=None, max_length=20)
    # "480p" | "720p" | "1080p" | "2K" | "4K"
    preferred_fps: int | None = Field(default=None)
    # 24 | 25 | 30 | 50 | 60
    preferred_speaker: str | None = Field(default=None, max_length=50)
    # 如 "zh_male_huoli"
    frequent_media_paths_json: str | None = Field(default=None)
    # JSON 序列化的常用素材路径列表
    last_project_name: str | None = Field(default=None, max_length=255)


class UserPreferenceCreate(UserPreferenceBase):
    user_id: uuid.UUID


class UserPreferenceUpdate(SQLModel):
    preferred_resolution: str | None = Field(default=None, max_length=20)
    preferred_fps: int | None = None
    preferred_speaker: str | None = Field(default=None, max_length=50)
    frequent_media_paths_json: str | None = None
    last_project_name: str | None = Field(default=None, max_length=255)


class UserPreference(UserPreferenceBase, table=True):
    __tablename__ = "user_preference"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False, unique=True)
    create_time: datetime = Field(default_factory=datetime.utcnow)
    update_time: datetime = Field(default_factory=datetime.utcnow)
```

### 容错与边界

- `edit_step` 使用 `ondelete="CASCADE"` —— 删除任务时自动级联删除步骤
- `user_preference.user_id` 使用 `unique=True` —— 每个用户仅一条偏好记录
- `project_state_json` 和 `args_json`/`result_json` 使用 `str | None` 类型存储 JSON 字符串，由业务层负责序列化/反序列化
- `status` 字段使用 `str` 而非 `Enum`，允许灵活扩展状态值而不需数据库迁移
- 所有新增表遵循现有 `is_deleted` 软删除 + `create_time`/`update_time` 审计字段约定
- 外键引用 `user.id` 和 `script.id`（已存在的表）

## 4. 验收标准 (DoD)

- [ ] `edit_task` 表创建成功，包含所有定义字段和外键约束
- [ ] `edit_step` 表创建成功，`task_id` 外键 `ON DELETE CASCADE`
- [ ] `user_preference` 表创建成功，`user_id` 唯一约束生效
- [ ] SQLModel `create_all` 或 Alembic 迁移脚本执行成功，无错误
- [ ] 新表与现有 `user` / `script` 表的外键关系正确
- [ ] `is_deleted` 软删除字段默认值为 0
