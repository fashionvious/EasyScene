import uuid
from typing import Any

from sqlmodel import Session, select

from app.core.security import get_password_hash, verify_password
from app.models import (
    Item, ItemCreate, User, UserCreate, UserUpdate,
    Conversation, ConversationCreate, ConversationUpdate,
    ChatMessage, ChatMessageCreate,
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
