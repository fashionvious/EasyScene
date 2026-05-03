"""
text2video API 路由测试
测试文生视频相关接口及数据增删改查
"""
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    CharacterInfo,
    SceneGraph,
    Script,
    ShotScript,
    User,
)


def _create_random_script(db: Session, user_id: uuid.UUID) -> Script:
    script = Script(
        script_name=f"test_script_{uuid.uuid4().hex[:8]}",
        script_content="这是一个测试剧本内容，长度超过十个字符",
        status=0,
        share_perm=0,
        creator_id=user_id,
        last_editor_id=user_id,
    )
    db.add(script)
    db.commit()
    db.refresh(script)
    return script


def _create_random_character(
    db: Session, script_id: uuid.UUID
) -> CharacterInfo:
    character = CharacterInfo(
        script_id=script_id,
        role_name=f"角色_{uuid.uuid4().hex[:4]}",
        role_desc="测试角色描述",
        version=1,
        create_time=datetime.now(UTC),
        update_time=datetime.now(UTC),
        is_deleted=0,
    )
    db.add(character)
    db.commit()
    db.refresh(character)
    return character


def _create_random_shot_script(
    db: Session, script_id: uuid.UUID, shot_no: int = 1
) -> ShotScript:
    shot = ShotScript(
        script_id=script_id,
        shot_no=shot_no,
        total_script="测试分镜头脚本内容",
        scene_group=1,
        scene_name="默认场景",
        shot_group=1,
        version=1,
        create_time=datetime.now(UTC),
        update_time=datetime.now(UTC),
        is_deleted=0,
    )
    db.add(shot)
    db.commit()
    db.refresh(shot)
    return shot


def _mock_redis_manager():
    mock_manager = AsyncMock()
    mock_manager.create_project = AsyncMock(return_value=None)
    mock_manager.update_project_state = AsyncMock(return_value=None)
    mock_manager.get_project_state = AsyncMock(return_value=None)
    mock_manager.complete_project_review = AsyncMock(return_value=None)
    mock_manager.close = AsyncMock(return_value=None)
    return mock_manager


BASE_URL = f"{settings.API_V1_STR}"


# =====================================================================
# API 路由测试
# =====================================================================


@patch("app.api.routes.text2video.get_video_project_manager")
@patch("app.api.routes.text2video.start_video_generation")
def test_create_script(
    mock_start_task: MagicMock,
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_get_redis.return_value = _mock_redis_manager()
    mock_start_task.return_value = "fake-task-id"

    data = {
        "script_name": "测试剧本",
        "script_content": "这是一个测试剧本内容，长度超过十个字符",
    }
    response = client.post(
        f"{BASE_URL}/text2video/create-script",
        headers=superuser_token_headers,
        json=data,
    )
    assert response.status_code == 200
    content = response.json()
    assert "script_id" in content
    assert content["script_name"] == data["script_name"]
    assert "message" in content


@patch("app.api.routes.text2video.get_video_project_manager")
def test_get_scripts(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_get_redis.return_value = _mock_redis_manager()

    response = client.get(
        f"{BASE_URL}/text2video/scripts",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    content = response.json()
    assert "scripts" in content
    assert "total" in content
    assert isinstance(content["scripts"], list)


@patch("app.api.routes.text2video.get_video_project_manager")
def test_get_script_status(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_redis = _mock_redis_manager()
    mock_get_redis.return_value = mock_redis

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    response = client.get(
        f"{BASE_URL}/text2video/script-status/{script.id}",
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    content = response.json()
    assert content["script_id"] == str(script.id)
    assert content["script_name"] == script.script_name
    assert "characters" in content
    assert "shot_scripts" in content


@patch("app.api.routes.text2video.get_video_project_manager")
def test_get_script_status_not_found(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    mock_get_redis.return_value = _mock_redis_manager()

    fake_id = str(uuid.uuid4())
    response = client.get(
        f"{BASE_URL}/text2video/script-status/{fake_id}",
        headers=superuser_token_headers,
    )
    assert response.status_code == 404


@patch("app.api.routes.text2video.get_video_project_manager")
def test_get_script_status_invalid_id(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    mock_get_redis.return_value = _mock_redis_manager()

    response = client.get(
        f"{BASE_URL}/text2video/script-status/invalid-uuid",
        headers=superuser_token_headers,
    )
    assert response.status_code == 400


@patch("app.api.routes.text2video.get_video_project_manager")
def test_update_single_character(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_redis = _mock_redis_manager()
    mock_get_redis.return_value = mock_redis

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)
    character = _create_random_character(db, script.id)

    with patch(
        "app.agent.generateVideo.tasks.resume_character_review_task"
    ) as mock_resume:
        mock_celery_result = MagicMock()
        mock_celery_result.id = "fake-celery-task-id"
        mock_resume.delay = MagicMock(return_value=mock_celery_result)

        data = {
            "role_name": "更新后角色名",
            "role_desc": "更新后角色描述",
        }
        response = client.patch(
            f"{BASE_URL}/text2video/character/{character.id}",
            headers=superuser_token_headers,
            json=data,
        )
        assert response.status_code == 200
        content = response.json()
        assert content["role_name"] == data["role_name"]
        assert content["role_desc"] == data["role_desc"]


@patch("app.api.routes.text2video.get_video_project_manager")
def test_update_single_character_not_found(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    mock_get_redis.return_value = _mock_redis_manager()

    fake_id = str(uuid.uuid4())
    data = {"role_name": "角色名", "role_desc": "角色描述"}
    response = client.patch(
        f"{BASE_URL}/text2video/character/{fake_id}",
        headers=superuser_token_headers,
        json=data,
    )
    assert response.status_code == 404


def test_update_single_shot(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)
    shot = _create_random_shot_script(db, script.id)

    data = {"total_script": "更新后的分镜头脚本内容"}
    response = client.patch(
        f"{BASE_URL}/text2video/shot/{shot.id}",
        headers=superuser_token_headers,
        json=data,
    )
    assert response.status_code == 200
    content = response.json()
    assert content["total_script"] == data["total_script"]
    assert content["shot_no"] == shot.shot_no


def test_update_single_shot_not_found(
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    fake_id = str(uuid.uuid4())
    data = {"total_script": "更新内容"}
    response = client.patch(
        f"{BASE_URL}/text2video/shot/{fake_id}",
        headers=superuser_token_headers,
        json=data,
    )
    assert response.status_code == 404


@patch("app.api.routes.text2video.get_video_project_manager")
def test_update_shots(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_redis = _mock_redis_manager()
    mock_get_redis.return_value = mock_redis

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    data = {
        "shot_scripts": [
            {"shot_no": 1, "total_script": "分镜1内容", "scene_group": 1},
            {"shot_no": 2, "total_script": "分镜2内容", "scene_group": 1},
        ]
    }
    response = client.post(
        f"{BASE_URL}/text2video/update-shots/{script.id}",
        headers=superuser_token_headers,
        json=data,
    )
    assert response.status_code == 200
    content = response.json()
    assert "message" in content
    assert len(content["shot_scripts"]) == 2


@patch("app.api.routes.text2video.get_video_project_manager")
def test_update_shots_not_found(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
) -> None:
    mock_get_redis.return_value = _mock_redis_manager()

    fake_id = str(uuid.uuid4())
    data = {"shot_scripts": [{"shot_no": 1, "total_script": "内容"}]}
    response = client.post(
        f"{BASE_URL}/text2video/update-shots/{fake_id}",
        headers=superuser_token_headers,
        json=data,
    )
    assert response.status_code == 404


@patch("app.api.routes.text2video.get_video_project_manager")
def test_confirm_character_three_view_no_image(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_get_redis.return_value = _mock_redis_manager()

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)
    character = _create_random_character(db, script.id)

    data = {"character_id": str(character.id)}
    response = client.post(
        f"{BASE_URL}/text2video/confirm-character-three-view",
        headers=superuser_token_headers,
        json=data,
    )
    assert response.status_code == 400
    assert "还没有生成四视图" in response.json()["detail"]


@patch("app.api.routes.text2video.get_video_project_manager")
def test_confirm_character_three_view_success(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_redis = _mock_redis_manager()
    mock_get_redis.return_value = mock_redis

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)
    character = _create_random_character(db, script.id)
    character.three_view_image_path = "/fake/path/three_view.png"
    db.add(character)
    db.commit()
    db.refresh(character)

    with patch(
        "app.agent.generateVideo.tasks.resume_character_review_task"
    ) as mock_resume:
        mock_celery_result = MagicMock()
        mock_celery_result.id = "fake-celery-task-id"
        mock_resume.delay = MagicMock(return_value=mock_celery_result)

        data = {"character_id": str(character.id)}
        response = client.post(
            f"{BASE_URL}/text2video/confirm-character-three-view",
            headers=superuser_token_headers,
            json=data,
        )
        assert response.status_code == 200
        content = response.json()
        assert content["three_view_image_path"] == "/fake/path/three_view.png"


@patch("app.api.routes.text2video.get_video_project_manager")
def test_generate_scene_background(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_redis = _mock_redis_manager()
    mock_get_redis.return_value = mock_redis

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    with patch(
        "app.agent.generateVideo.tasks.generate_scene_background_task"
    ) as mock_task:
        mock_celery_result = MagicMock()
        mock_celery_result.id = "fake-bg-task-id"
        mock_task.delay = MagicMock(return_value=mock_celery_result)

        data = {
            "scene_group_no": 1,
            "scene_name": "森林场景",
            "shot_scripts": [{"shot_no": 1, "total_script": "分镜1"}],
        }
        response = client.post(
            f"{BASE_URL}/text2video/generate-background/{script.id}",
            headers=superuser_token_headers,
            json=data,
        )
        assert response.status_code == 200
        content = response.json()
        assert content["success"] is True
        assert content["scene_group"] == 1


@patch("app.api.routes.text2video.get_video_project_manager")
def test_generate_grid_image(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_redis = _mock_redis_manager()
    mock_get_redis.return_value = mock_redis

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    with patch(
        "app.agent.generateVideo.tasks.generate_grid_image_task"
    ) as mock_task:
        mock_celery_result = MagicMock()
        mock_celery_result.id = "fake-grid-task-id"
        mock_task.delay = MagicMock(return_value=mock_celery_result)

        data = {
            "shot_no": 1,
            "shot_script_text": "分镜脚本内容",
            "scene_group_no": 1,
        }
        response = client.post(
            f"{BASE_URL}/text2video/generate-grid-image/{script.id}",
            headers=superuser_token_headers,
            json=data,
        )
        assert response.status_code == 200
        content = response.json()
        assert content["success"] is True


@patch("app.api.routes.text2video.get_video_project_manager")
def test_generate_first_frame_image(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_redis = _mock_redis_manager()
    mock_get_redis.return_value = mock_redis

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    with patch(
        "app.agent.generateVideo.tasks.generate_first_frame_image_task"
    ) as mock_task:
        mock_celery_result = MagicMock()
        mock_celery_result.id = "fake-ff-task-id"
        mock_task.delay = MagicMock(return_value=mock_celery_result)

        data = {
            "shot_no": 1,
            "shot_script_text": "分镜脚本内容",
            "scene_group_no": 1,
            "script_name": script.script_name,
        }
        response = client.post(
            f"{BASE_URL}/text2video/generate-first-frame-image/{script.id}",
            headers=superuser_token_headers,
            json=data,
        )
        assert response.status_code == 200
        content = response.json()
        assert content["success"] is True


@patch("app.api.routes.text2video.get_video_project_manager")
def test_generate_first_and_last_frame(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_redis = _mock_redis_manager()
    mock_get_redis.return_value = mock_redis

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    with patch(
        "app.agent.generateVideo.tasks.generate_first_and_last_frame_task"
    ) as mock_task:
        mock_celery_result = MagicMock()
        mock_celery_result.id = "fake-fl-task-id"
        mock_task.delay = MagicMock(return_value=mock_celery_result)

        data = {
            "shot_no": 1,
            "shot_script_text": "分镜脚本内容",
            "scene_group_no": 1,
            "script_name": script.script_name,
            "is_first_in_scene_group": True,
        }
        response = client.post(
            f"{BASE_URL}/text2video/generate-first-and-last-frame/{script.id}",
            headers=superuser_token_headers,
            json=data,
        )
        assert response.status_code == 200
        content = response.json()
        assert content["success"] is True


@patch("app.api.routes.text2video.get_video_project_manager")
def test_generate_video(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_redis = _mock_redis_manager()
    mock_get_redis.return_value = mock_redis

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    with patch(
        "app.agent.generateVideo.tasks.generate_video_task"
    ) as mock_task:
        mock_celery_result = MagicMock()
        mock_celery_result.id = "fake-video-task-id"
        mock_task.delay = MagicMock(return_value=mock_celery_result)

        data = {"shot_no": 1, "shotlist_text": "分镜头脚本内容"}
        response = client.post(
            f"{BASE_URL}/text2video/generate-video/{script.id}",
            headers=superuser_token_headers,
            json=data,
        )
        assert response.status_code == 200
        content = response.json()
        assert content["success"] is True


@patch("app.api.routes.text2video.get_video_project_manager")
def test_generate_video_from_first_frame(
    mock_get_redis: MagicMock,
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    mock_redis = _mock_redis_manager()
    mock_get_redis.return_value = mock_redis

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    with patch(
        "app.agent.generateVideo.tasks.generate_video_from_first_frame_task"
    ) as mock_task:
        mock_celery_result = MagicMock()
        mock_celery_result.id = "fake-seedance-task-id"
        mock_task.delay = MagicMock(return_value=mock_celery_result)

        data = {"shot_no": 1, "shotlist_text": "分镜头脚本内容"}
        response = client.post(
            f"{BASE_URL}/text2video/generate-video-from-first-frame/{script.id}",
            headers=superuser_token_headers,
            json=data,
        )
        assert response.status_code == 200
        content = response.json()
        assert content["success"] is True


# =====================================================================
# CRUD / 数据库操作测试
# =====================================================================


def test_save_characters_to_db(db: Session) -> None:
    from app.agent.generateVideo.service import save_characters_to_db

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    characters = [
        {"role_name": "角色A", "role_desc": "角色A的描述"},
        {"role_name": "角色B", "role_desc": "角色B的描述"},
    ]
    result = save_characters_to_db(str(script.id), characters)
    assert result is True

    db_chars = db.exec(
        select(CharacterInfo).where(
            CharacterInfo.script_id == script.id,
            CharacterInfo.is_deleted == 0,
        )
    ).all()
    assert len(db_chars) == 2
    role_names = {c.role_name for c in db_chars}
    assert "角色A" in role_names
    assert "角色B" in role_names


def test_save_characters_to_db_soft_delete_old(
    db: Session,
) -> None:
    from app.agent.generateVideo.service import save_characters_to_db

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)
    _create_random_character(db, script.id)

    new_characters = [
        {"role_name": "新角色", "role_desc": "新角色描述"},
    ]
    result = save_characters_to_db(str(script.id), new_characters)
    assert result is True

    active_chars = db.exec(
        select(CharacterInfo).where(
            CharacterInfo.script_id == script.id,
            CharacterInfo.is_deleted == 0,
        )
    ).all()
    assert len(active_chars) == 1
    assert active_chars[0].role_name == "新角色"


def test_save_shot_scripts_to_db(db: Session) -> None:
    from app.agent.generateVideo.service import save_shot_scripts_to_db

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    shot_scripts = [
        {"shot_no": 1, "total_script": "分镜1脚本", "scene_group": 1},
        {"shot_no": 2, "total_script": "分镜2脚本", "scene_group": 1},
    ]
    result = save_shot_scripts_to_db(str(script.id), shot_scripts)
    assert result is True

    db_shots = db.exec(
        select(ShotScript).where(
            ShotScript.script_id == script.id,
            ShotScript.is_deleted == 0,
        )
    ).all()
    assert len(db_shots) == 2


def test_update_character_three_view_in_db(
    db: Session,
) -> None:
    from app.agent.generateVideo.service import (
        update_character_three_view_in_db,
    )

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)
    character = _create_random_character(db, script.id)

    result = update_character_three_view_in_db(
        str(script.id),
        character.role_name,
        "/path/to/three_view.png",
    )
    assert result is True

    db.refresh(character)
    assert character.three_view_image_path == "/path/to/three_view.png"


def test_update_character_three_view_not_found(
    db: Session,
) -> None:
    from app.agent.generateVideo.service import (
        update_character_three_view_in_db,
    )

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    result = update_character_three_view_in_db(
        str(script.id),
        "不存在的角色",
        "/path/to/three_view.png",
    )
    assert result is False


def test_update_shot_grid_image_in_db(db: Session) -> None:
    from app.agent.generateVideo.service import (
        update_shot_grid_image_in_db,
    )

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)
    shot = _create_random_shot_script(db, script.id)

    result = update_shot_grid_image_in_db(
        str(script.id), shot.shot_no, "/path/to/grid.png"
    )
    assert result is True

    db.refresh(shot)
    assert shot.grid_image_path == "/path/to/grid.png"


def test_update_shot_first_frame_image_in_db(
    db: Session,
) -> None:
    from app.agent.generateVideo.service import (
        update_shot_first_frame_image_in_db,
    )

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)
    shot = _create_random_shot_script(db, script.id)

    result = update_shot_first_frame_image_in_db(
        str(script.id), shot.shot_no, "/path/to/first_frame.png"
    )
    assert result is True

    db.refresh(shot)
    assert shot.first_frame_image_path == "/path/to/first_frame.png"


def test_update_shot_last_frame_image_in_db(
    db: Session,
) -> None:
    from app.agent.generateVideo.service import (
        update_shot_last_frame_image_in_db,
    )

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)
    shot = _create_random_shot_script(db, script.id)

    result = update_shot_last_frame_image_in_db(
        str(script.id), shot.shot_no, "/path/to/last_frame.png"
    )
    assert result is True

    db.refresh(shot)
    assert shot.last_frame_image_path == "/path/to/last_frame.png"


def test_update_shot_video_path_in_db(db: Session) -> None:
    from app.agent.generateVideo.service import (
        update_shot_video_path_in_db,
    )

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)
    shot = _create_random_shot_script(db, script.id)

    result = update_shot_video_path_in_db(
        str(script.id), shot.shot_no, "/path/to/video.mp4"
    )
    assert result is True

    db.refresh(shot)
    assert shot.video_path == "/path/to/video.mp4"


def test_update_shot_video_path_not_found(db: Session) -> None:
    from app.agent.generateVideo.service import (
        update_shot_video_path_in_db,
    )

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    result = update_shot_video_path_in_db(
        str(script.id), 999, "/path/to/video.mp4"
    )
    assert result is False


def test_save_scene_background_to_db(db: Session) -> None:
    from app.agent.generateVideo.service import (
        save_scene_background_to_db,
    )

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    result = save_scene_background_to_db(
        str(script.id),
        scene_group_no=1,
        scene_name="森林场景",
        background_image_path="/path/to/background.png",
    )
    assert result is True

    scene_graph = db.exec(
        select(SceneGraph).where(
            SceneGraph.script_id == script.id,
            SceneGraph.is_deleted == 0,
        )
    ).first()
    assert scene_graph is not None
    assert scene_graph.scene_name == "森林场景"
    assert scene_graph.scene_image_path == "/path/to/background.png"


def test_save_scene_background_update_existing(
    db: Session,
) -> None:
    from app.agent.generateVideo.service import (
        save_scene_background_to_db,
    )

    superuser = db.exec(
        select(User).where(User.is_superuser == True)  # noqa: E712
    ).first()
    assert superuser is not None

    script = _create_random_script(db, superuser.id)

    save_scene_background_to_db(
        str(script.id),
        scene_group_no=1,
        scene_name="旧场景",
        background_image_path="/path/to/old_bg.png",
    )

    result = save_scene_background_to_db(
        str(script.id),
        scene_group_no=1,
        scene_name="新场景",
        background_image_path="/path/to/new_bg.png",
    )
    assert result is True

    scene_graphs = db.exec(
        select(SceneGraph).where(
            SceneGraph.script_id == script.id,
            SceneGraph.scene_group == 1,
            SceneGraph.is_deleted == 0,
        )
    ).all()
    assert len(scene_graphs) == 1
    assert scene_graphs[0].scene_name == "新场景"
    assert scene_graphs[0].scene_image_path == "/path/to/new_bg.png"
