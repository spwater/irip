"""account_router API 测试：个人信息 + 密码修改 + 头像上传 + 数据导出/删除。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user、get_account_service、get_s3_repo
- Mock AuthService 的 get_user_by_id / update_profile / change_password /
  set_avatar_url / verify_password / delete_account
- 验证 HTTP 状态码、响应体字段、错误码（404/422）
"""

import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.account import (
    account_router,
    get_account_service,
    get_s3_repo,
)
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(
    user_id=None,
    roles: list[str] | None = None,
) -> CurrentUser:
    """构造当前用户。"""
    return CurrentUser(
        user_id=user_id or uuid4(),
        email="user@irip.local",
        roles=roles or ["lab_member"],
        department_id=uuid4(),
        is_root_member=False,
    )


def _make_user(
    user_id=None,
    email: str = "user@irip.local",
    display_name: str = "测试用户",
    roles: list[str] | None = None,
    status: str = "active",
) -> SimpleNamespace:
    """构造用户实体。"""
    return SimpleNamespace(
        id=user_id or uuid4(),
        email=email,
        display_name=display_name,
        avatar_url=None,
        roles=roles or ["lab_member"],
        department_id=uuid4(),
        status=status,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_mock_service() -> MagicMock:
    """构造 mock AuthService。"""
    service = MagicMock()
    service.get_user_by_id = AsyncMock()
    service.update_profile = AsyncMock()
    service.change_password = AsyncMock()
    service.set_avatar_url = AsyncMock()
    service.verify_password = MagicMock(return_value=True)
    service.delete_account = AsyncMock()
    return service


def _make_app(
    service: MagicMock | None = None,
    s3_repo: MagicMock | None = None,
    user: CurrentUser | None = None,
) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(account_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_account_service] = lambda: service or _make_mock_service()
    app.dependency_overrides[get_s3_repo] = lambda: s3_repo or MagicMock()

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. GET /api/v1/account/profile — 查询个人信息
# ===========================================================================


class TestGetProfile:
    """GET /api/v1/account/profile — 查询个人信息。"""

    def test_get_profile_200(self):
        """查询成功 → 200"""
        service = _make_mock_service()
        user_entity = _make_user(display_name="显示名")
        service.get_user_by_id = AsyncMock(return_value=user_entity)

        app = _make_app(service=service)
        client = TestClient(app)

        response = client.get("/api/v1/account/profile")

        assert response.status_code == 200
        data = response.json()
        assert data["display_name"] == "显示名"

    def test_get_profile_not_found_404(self):
        """用户不存在 → 404"""
        service = _make_mock_service()
        service.get_user_by_id = AsyncMock(return_value=None)

        app = _make_app(service=service)
        client = TestClient(app)

        response = client.get("/api/v1/account/profile")

        assert response.status_code == 404


# ===========================================================================
# 2. PATCH /api/v1/account/profile — 修改个人信息
# ===========================================================================


class TestUpdateProfile:
    """PATCH /api/v1/account/profile — 修改显示名/头像。"""

    def test_update_profile_200(self):
        """修改成功 → 200"""
        service = _make_mock_service()
        user_entity = _make_user(display_name="新名称")
        service.update_profile = AsyncMock(return_value=user_entity)

        app = _make_app(service=service)
        client = TestClient(app)

        response = client.patch(
            "/api/v1/account/profile",
            json={"display_name": "新名称"},
        )

        assert response.status_code == 200
        assert response.json()["display_name"] == "新名称"


# ===========================================================================
# 3. POST /api/v1/account/password — 修改密码
# ===========================================================================


class TestChangePassword:
    """POST /api/v1/account/password — 修改密码。"""

    def test_change_password_204(self):
        """修改成功 → 204"""
        service = _make_mock_service()
        service.change_password = AsyncMock(return_value=None)

        app = _make_app(service=service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/account/password",
            json={"old_password": "old123", "new_password": "new456"},
        )

        assert response.status_code == 204

    def test_change_password_wrong_old_401(self):
        """旧密码错误 → 401（invalid_credentials）"""
        service = _make_mock_service()
        service.change_password = AsyncMock(
            side_effect=AppError(
                code="invalid_credentials", message="旧密码错误", retryable=False, fields={}
            )
        )

        app = _make_app(service=service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/account/password",
            json={"old_password": "wrong", "new_password": "new456"},
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"

    def test_change_password_short_422(self):
        """新密码太短 → 422"""
        service = _make_mock_service()
        app = _make_app(service=service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/account/password",
            json={"old_password": "old123", "new_password": "ab"},
        )

        assert response.status_code == 422


# ===========================================================================
# 4. POST /api/v1/account/avatar — 上传头像
# ===========================================================================


class TestUploadAvatar:
    """POST /api/v1/account/avatar — 上传头像到 MinIO。"""

    def test_upload_avatar_200(self):
        """上传成功 → 200"""
        service = _make_mock_service()
        service.set_avatar_url = AsyncMock(return_value=None)

        s3_repo = MagicMock()
        s3_repo.put_object = MagicMock(return_value=None)
        s3_repo.presigned_get = MagicMock(return_value="https://minio.local/avatar.jpg")

        app = _make_app(service=service, s3_repo=s3_repo)
        client = TestClient(app)

        response = client.post(
            "/api/v1/account/avatar",
            files={"file": ("avatar.png", io.BytesIO(b"fake-png-data"), "image/png")},
        )

        assert response.status_code == 200
        assert "avatar" in response.json()["avatar_url"]

    def test_upload_avatar_invalid_type_422(self):
        """不支持文件类型 → 422"""
        app = _make_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/account/avatar",
            files={"file": ("avatar.exe", io.BytesIO(b"\x00"), "application/octet-stream")},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


# ===========================================================================
# 5. GET /api/v1/account/export — 数据导出
# ===========================================================================


class TestExportUserData:
    """GET /api/v1/account/export — GDPR 数据导出。"""

    def test_export_200(self):
        """导出成功 → 200"""
        service = _make_mock_service()
        user_entity = _make_user(display_name="导出用户")
        service.get_user_by_id = AsyncMock(return_value=user_entity)

        app = _make_app(service=service)
        client = TestClient(app)

        response = client.get("/api/v1/account/export")

        assert response.status_code == 200
        data = response.json()
        assert data["user"]["display_name"] == "导出用户"
        assert "exported_at" in data

    def test_export_not_found_404(self):
        """用户不存在 → 404"""
        service = _make_mock_service()
        service.get_user_by_id = AsyncMock(return_value=None)

        app = _make_app(service=service)
        client = TestClient(app)

        response = client.get("/api/v1/account/export")

        assert response.status_code == 404


# ===========================================================================
# 6. DELETE /api/v1/account — 删除账户
# ===========================================================================


class TestDeleteAccount:
    """DELETE /api/v1/account — GDPR 账户删除。"""

    def test_delete_204(self):
        """删除成功 → 204"""
        user_id = uuid4()
        service = _make_mock_service()
        user_entity = _make_user(user_id=user_id, email="user@irip.local")
        service.get_user_by_id = AsyncMock(return_value=user_entity)
        service.verify_password = MagicMock(return_value=True)
        service.delete_account = AsyncMock(return_value=None)

        app = _make_app(service=service, user=_make_current_user(user_id=user_id))
        client = TestClient(app)

        response = client.request(
            "DELETE",
            "/api/v1/account",
            json={
                "confirm_email": "user@irip.local",
                "password": "correct-password",
            },
        )

        assert response.status_code == 204

    def test_delete_email_mismatch_422(self):
        """邮箱不匹配 → 422"""
        user_id = uuid4()
        service = _make_mock_service()
        user_entity = _make_user(user_id=user_id, email="user@irip.local")
        service.get_user_by_id = AsyncMock(return_value=user_entity)

        app = _make_app(service=service, user=_make_current_user(user_id=user_id))
        client = TestClient(app)

        response = client.request(
            "DELETE",
            "/api/v1/account",
            json={"confirm_email": "wrong@irip.local", "password": "any"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"

    def test_delete_wrong_password_401(self):
        """密码错误 → 401"""
        user_id = uuid4()
        service = _make_mock_service()
        user_entity = _make_user(user_id=user_id, email="user@irip.local")
        service.get_user_by_id = AsyncMock(return_value=user_entity)
        service.verify_password = MagicMock(return_value=False)

        app = _make_app(service=service, user=_make_current_user(user_id=user_id))
        client = TestClient(app)

        response = client.request(
            "DELETE",
            "/api/v1/account",
            json={"confirm_email": "user@irip.local", "password": "wrong"},
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"
