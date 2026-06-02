import uuid
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.core.security import get_password_hash, verify_password
from app.models import (
    Item, ItemCreate, User, UserCreate, UserUpdate,
    Conversation, ConversationCreate, ConversationUpdate,
    ChatMessage, ChatMessageCreate,
    EditTask, EditTaskCreate, EditTaskUpdate,
    EditStep, EditStepCreate,
    UserPreference, UserPreferenceUpdate,
)


def create_user(*, session: Session, user_create: UserCreate) -> User:
    db_obj = User.model_validate(
        user_create, update={"hashed_password": get_password_hash(user_create.password)}
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def update_user(*, session: Session, db_user: User, user_in: UserUpdate) -> Any:
    user_data = user_in.model_dump(exclude_unset=True)
    extra_data = {}
    if "password" in user_data:
        password = user_data["password"]
        hashed_password = get_password_hash(password)
        extra_data["hashed_password"] = hashed_password
    db_user.sqlmodel_update(user_data, update=extra_data)
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    return db_user


def get_user_by_email(*, session: Session, email: str) -> User | None:
    statement = select(User).where(User.email == email)
    session_user = session.exec(statement).first()
    return session_user


def authenticate(*, session: Session, email: str, password: str) -> User | None:
    db_user = get_user_by_email(session=session, email=email)
    if not db_user:
        return None
    if not verify_password(password, db_user.hashed_password):
        return None
    return db_user


def create_item(*, session: Session, item_in: ItemCreate, owner_id: uuid.UUID) -> Item:
    db_item = Item.model_validate(item_in, update={"owner_id": owner_id})
    session.add(db_item)
    session.commit()
    session.refresh(db_item)
    return db_item


# ==================== Conversation CRUD ====================


def create_conversation(*, session: Session, conv_in: ConversationCreate) -> Conversation:
    db_obj = Conversation.model_validate(conv_in)
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def get_conversation(*, session: Session, conversation_id: uuid.UUID) -> Conversation | None:
    return session.get(Conversation, conversation_id)


def get_conversations_by_user_and_script(
    *, session: Session, user_id: uuid.UUID, script_id: uuid.UUID
) -> list[Conversation]:
    statement = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .where(Conversation.script_id == script_id)
        .where(Conversation.is_deleted == 0)
        .order_by(Conversation.update_time.desc())
    )
    return list(session.exec(statement).all())


def update_conversation(
    *, session: Session, db_conv: Conversation, conv_in: ConversationUpdate
) -> Conversation:
    conv_data = conv_in.model_dump(exclude_unset=True)
    db_conv.sqlmodel_update(conv_data)
    session.add(db_conv)
    session.commit()
    session.refresh(db_conv)
    return db_conv


# ==================== ChatMessage CRUD ====================


def create_chat_message(*, session: Session, msg_in: ChatMessageCreate) -> ChatMessage:
    db_obj = ChatMessage.model_validate(msg_in)
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def get_chat_messages_by_conversation(
    *, session: Session, conversation_id: uuid.UUID
) -> list[ChatMessage]:
    statement = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.create_time.asc())
    )
    return list(session.exec(statement).all())


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
    """创建或更新步骤 checkpoint。首次调用创建，再次调用更新同一 (task_id, step_index)。"""
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
        if step_data.get("started_at") is not None:
            existing.started_at = step_data["started_at"]
        if step_data.get("finished_at") is not None:
            existing.finished_at = step_data["finished_at"]
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
    """获取步骤统计（供指标聚合使用）"""
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
