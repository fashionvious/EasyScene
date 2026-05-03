from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.core.config import settings
from app.core.db import engine, init_db
from app.main import app
from app.models import Item, Script, CharacterInfo, User  # 确保都导入了
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


from sqlmodel import SQLModel
# 其他 import ...

@pytest.fixture(scope="session", autouse=True)
def db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        init_db(session)
        yield session
        
        # ✅ 自动按外键依赖关系的逆序，清除所有表的数据
        for table in reversed(SQLModel.metadata.sorted_tables):
            session.execute(table.delete())
        
        session.commit()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )
