"""auth_router + me_router API 测试：登录、刷新、登出、当前用户。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_auth_service、get_current_user 依赖
- mock AuthService 的 login / refresh / logout / get_user_by_id
- 验证 HTTP 状态码、响应体字段、错误码（401/422）
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.auth import (
    auth_router,
    get_auth_service,
    get_me_session_factory,
    me_router,
)
from packages.auth.service import AuthService
from packages.auth.tokens import TokenPair
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="admin@irip.local",
        roles=roles or ["platform_administrator"],
        department_id=uuid4(),
        is_root_member=True,
    )


def _make_mock_user(
    display_name: str = "管理员",
    avatar_url: str | None = None,
    department_id: str | None = None,
) -> SimpleNamespace:
    """构造 mock user 对象（service.get_user_by_id 返回值）。"""
    return SimpleNamespace(
        display_name=display_name,
        avatar_url=avatar_url,
        department_id=department_id,
    )


def _make_mock_service() -> MagicMock:
    """构造 mock AuthService。"""
    service = MagicMock(spec=AuthService)
    service.login = AsyncMock()
    service.refresh = AsyncMock()
    service.logout = AsyncMock()
    service.get_user_by_id = AsyncMock()
    return service


def _make_app(
    mock_service: MagicMock,
    mock_user: CurrentUser | None = None,
) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(me_router)

    user = mock_user or _make_current_user()

    async def _override_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_auth_service] = lambda: mock_service
    app.dependency_overrides[get_me_session_factory] = lambda: MagicMock()

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(
            status_code=status,
            content={"error": exc.to_dict()},
        )

    return app


# ===========================================================================
# 1. POST /api/v1/auth/login — 登录
# ===========================================================================


class TestLogin:
    """POST /api/v1/auth/login — 用户登录。"""

    def test_login_success_200(self):
        """登录成功 → 200 + access_token + expires_in"""
        mock_service = _make_mock_service()
        pair = TokenPair(
            access_token="eyJhbGciOiJIUzI1NiJ9.access",
            refresh_token="refresh-token-abc",
            expires_in=900,
        )
        mock_service.login = AsyncMock(return_value=pair)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@irip.local", "password": "password123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "eyJhbGciOiJIUzI1NiJ9.access"
        assert data["expires_in"] == 900
        # refresh token 在 cookie 中
        cookies = response.headers.get("set-cookie", "")
        assert "irip_refresh" in cookies

    def test_login_bad_credentials_401(self):
        """凭据错误 → 401"""
        mock_service = _make_mock_service()
        mock_service.login = AsyncMock(
            side_effect=AppError(
                code="invalid_credentials",
                message="邮箱或密码错误",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/auth/login",
            json={"email": "wrong@irip.local", "password": "wrongpass"},
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"

    def test_login_missing_password_422(self):
        """缺少密码 → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@irip.local"},
        )

        assert response.status_code == 422


# ===========================================================================
# 2. POST /api/v1/auth/refresh — 刷新令牌
# ===========================================================================


class TestRefresh:
    """POST /api/v1/auth/refresh — 刷新令牌旋转。"""

    def test_refresh_success_200(self):
        """刷新成功 → 200 + 新 access_token"""
        mock_service = _make_mock_service()
        pair = TokenPair(
            access_token="new.access.token",
            refresh_token="new-refresh-token",
            expires_in=900,
        )
        mock_service.refresh = AsyncMock(return_value=pair)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/auth/refresh",
            cookies={"irip_refresh": "old-refresh-token"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["access_token"] == "new.access.token"
        assert data["expires_in"] == 900

    def test_refresh_no_cookie_401(self):
        """无 refresh cookie → 401"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post("/api/v1/auth/refresh")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"

    def test_refresh_replayed_401(self):
        """刷新令牌重放 → 401"""
        mock_service = _make_mock_service()
        mock_service.refresh = AsyncMock(
            side_effect=AppError(
                code="refresh_replayed",
                message="刷新令牌已被使用，会话已撤销",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/auth/refresh",
            cookies={"irip_refresh": "replayed-token"},
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "refresh_replayed"


# ===========================================================================
# 3. POST /api/v1/auth/logout — 登出
# ===========================================================================


class TestLogout:
    """POST /api/v1/auth/logout — 用户登出。"""

    def test_logout_success_200(self):
        """登出成功 → 200 ok=True"""
        mock_service = _make_mock_service()
        mock_service.logout = AsyncMock(return_value=None)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/auth/logout",
            cookies={"irip_refresh": "some-token"},
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True
        mock_service.logout.assert_called_once_with("some-token")

    def test_logout_no_cookie_still_ok_200(self):
        """无 cookie 登出 → 200（幂等）"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post("/api/v1/auth/logout")

        assert response.status_code == 200
        assert response.json()["ok"] is True
        mock_service.logout.assert_not_called()


# ===========================================================================
# 4. GET /api/v1/me — 当前用户信息
# ===========================================================================


class TestMe:
    """GET /api/v1/me — 当前用户信息。"""

    def test_me_success_200(self):
        """获取当前用户 → 200 + 含 permissions"""
        mock_service = _make_mock_service()
        dept_id = str(uuid4())
        mock_service.get_user_by_id = AsyncMock(
            return_value=_make_mock_user(
                display_name="管理员",
                avatar_url="https://example.com/avatar.png",
                department_id=dept_id,
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/me")

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "admin@irip.local"
        assert data["display_name"] == "管理员"
        assert data["avatar_url"] == "https://example.com/avatar.png"
        assert data["department_id"] == dept_id
        assert data["is_root_member"] is True
        assert "permissions" in data
        assert isinstance(data["permissions"], list)
        assert len(data["permissions"]) > 0
        assert "feature_flags" in data

    def test_me_user_not_found_200(self):
        """用户在 DB 中不存在 → 仍 200，使用 email 作为 display_name"""
        mock_service = _make_mock_service()
        mock_service.get_user_by_id = AsyncMock(return_value=None)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/me")

        assert response.status_code == 200
        data = response.json()
        assert data["display_name"] == "admin@irip.local"
        assert data["avatar_url"] is None
        assert data["department_id"] is None
