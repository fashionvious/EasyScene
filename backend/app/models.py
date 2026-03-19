import uuid
from typing import Optional
from datetime import datetime
from pydantic import EmailStr
from sqlmodel import Field, Relationship, SQLModel

# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)

class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    

# Properties to receive via API on update, all are optional
class UserUpdate(UserBase):
    email: EmailStr | None = Field(default=None, max_length=255)  # type: ignore
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    items: list["Item"] = Relationship(back_populates="owner", cascade_delete=True)
    age: Optional[str] = Field(default=None, max_length=3)

# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(ItemBase):
    title: str | None = Field(default=None, min_length=1, max_length=255)  # type: ignore


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
 
 
 # ==================== Script Models ====================
 # Shared properties
class ScriptBase(SQLModel):
    script_name: str = Field(max_length=255)
    script_content: str
    status: int = Field(default=0)  # 0=draft, 1=characters generated, 2=storyboard generated, 3=video generated, 4=deprecated
    share_perm: int = Field(default=0)  # 0=private, 1=read-only, 2=editable


# Properties to receive on script creation
class ScriptCreate(ScriptBase):
    pass


# Properties to receive on script update
class ScriptUpdate(SQLModel):
    script_name: str | None = Field(default=None, max_length=255)
    script_content: str | None = None
    status: int | None = None
    share_perm: int | None = None
    last_editor_id: uuid.UUID | None = None


# Database model for script table
class Script(ScriptBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    creator_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    last_editor_id: uuid.UUID | None = Field(default=None, foreign_key="user.id")
    create_time: datetime = Field(default_factory=datetime.utcnow)
    update_time: datetime = Field(default_factory=datetime.utcnow)
    is_deleted: int = Field(default=0)

    # Relationships
    characters: list["CharacterInfo"] = Relationship(back_populates="script")
    shot_scripts: list["ShotScript"] = Relationship(back_populates="script")


# Properties to return via API
class ScriptPublic(ScriptBase):
    id: uuid.UUID
    creator_id: uuid.UUID
    last_editor_id: uuid.UUID | None
    create_time: datetime
    update_time: datetime


class ScriptsPublic(SQLModel):
    data: list[ScriptPublic]
    count: int


# ==================== Character Info Models ====================
# Shared properties
class CharacterInfoBase(SQLModel):
    role_name: str = Field(max_length=255)
    role_desc: str
    version: int = Field(default=1)


# Properties to receive on character creation
class CharacterInfoCreate(CharacterInfoBase):
    script_id: uuid.UUID


# Properties to receive on character update
class CharacterInfoUpdate(SQLModel):
    role_name: str | None = Field(default=None, max_length=255)
    role_desc: str | None = None
    version: int | None = None


# Database model for character_info table
class CharacterInfo(CharacterInfoBase, table=True):
    __tablename__ = "character_info"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    script_id: uuid.UUID = Field(foreign_key="script.id", nullable=False)
    create_time: datetime = Field(default_factory=datetime.utcnow)
    update_time: datetime = Field(default_factory=datetime.utcnow)
    is_deleted: int = Field(default=0)

    # Relationships
    script: Script | None = Relationship(back_populates="characters")
    shot_scripts: list["ShotScript"] = Relationship(back_populates="character")


# Properties to return via API
class CharacterInfoPublic(CharacterInfoBase):
    id: uuid.UUID
    script_id: uuid.UUID
    create_time: datetime
    update_time: datetime


class CharacterInfosPublic(SQLModel):
    data: list[CharacterInfoPublic]
    count: int


# ==================== Shot Script Models ====================
# Shared properties
class ShotScriptBase(SQLModel):
    shot_no: int
    version: int = Field(default=1)
    total_script: str


# Properties to receive on shot script creation
class ShotScriptCreate(ShotScriptBase):
    script_id: uuid.UUID
    role_id: uuid.UUID | None = None


# Properties to receive on shot script update
class ShotScriptUpdate(SQLModel):
    shot_no: int | None = None
    version: int | None = None
    total_script: str | None = None
    role_id: uuid.UUID | None = None


# Database model for shot_script table
class ShotScript(ShotScriptBase, table=True):
    __tablename__ = "shot_script"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    script_id: uuid.UUID = Field(foreign_key="script.id", nullable=False)
    role_id: uuid.UUID | None = Field(default=None, foreign_key="character_info.id")
    create_time: datetime = Field(default_factory=datetime.utcnow)
    update_time: datetime = Field(default_factory=datetime.utcnow)
    is_deleted: int = Field(default=0)

    # Relationships
    script: Script | None = Relationship(back_populates="shot_scripts")
    character: CharacterInfo | None = Relationship(back_populates="shot_scripts")


# Properties to return via API
class ShotScriptPublic(ShotScriptBase):
    id: uuid.UUID
    script_id: uuid.UUID
    role_id: uuid.UUID | None
    create_time: datetime
    update_time: datetime


class ShotScriptsPublic(SQLModel):
    data: list[ShotScriptPublic]
    count: int


# ==================== Operation Log Models ====================
# Shared properties
class OperationLogBase(SQLModel):
    target_type: int  # 1=script, 2=character_info, 3=shot_script
    target_id: uuid.UUID
    operate_type: int  # 1=create, 2=modify, 3=AI generate, 4=soft delete, 5=restore, 6=permission change, 7=version switch
    operate_content: str | None = None


# Properties to receive on operation log creation
class OperationLogCreate(OperationLogBase):
    operate_user_id: uuid.UUID


# Database model for operation_log table
class OperationLog(OperationLogBase, table=True):
    __tablename__ = "operation_log"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    operate_user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    create_time: datetime = Field(default_factory=datetime.utcnow)
    is_deleted: int = Field(default=0)


# Properties to return via API
class OperationLogPublic(OperationLogBase):
    id: uuid.UUID
    operate_user_id: uuid.UUID
    create_time: datetime


class OperationLogsPublic(SQLModel):
    data: list[OperationLogPublic]
    count: int
