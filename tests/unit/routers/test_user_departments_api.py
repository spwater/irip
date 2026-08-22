"""user_departments_router API 测试：用户-实验室关联管理。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user 和 get_user_department_service
- Mock UserDepartmentService 的 set_user_departments / get_user_departments / get_department_users
- 验证 HTTP 状态码、响应体字段、错误码（403）
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.dependencies.departments import get_user_department_service
from apps.api.routers.user_departments import user_departments_router
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
        roles=roles or ["platform_administrator"],
        department_id=uuid4(),
        is_root_member=False,
    )


def _make_user_dept_item(
    user_id=None,
    dept_id=None,
    code: str = "dept_001",
    display_name: str = "测试部门",
    is_primary: bool = True,
) -> SimpleNamespace:
    """构造 UserDepartmentItem。"""
    return SimpleNamespace(
        user_id=user_id or uuid4(),
        department_id=dept_id or uuid4(),
        department_code=code,
        department_display_name=display_name,
        is_primary=is_primary,
    )


def _make_dept_user_item(
    user_id=None,
    email: str = "user@irip.local",
    display_name: str = "用户",
    is_primary: bool = True,
) -> SimpleNamespace:
    """构造 DepartmentUserItem。"""
    return SimpleNamespace(
        user_id=user_id or uuid4(),
        email=email,
        display_name=display_name,
        is_primary=is_primary,
    )


def _make_mock_service() -> MagicMock:
    """构造 mock UserDepartmentService。"""
    service = MagicMock()
    service.set_user_departments = AsyncMock()
    service.get_user_departments = AsyncMock()
    service.get_department_users = AsyncMock()
    return service


def _make_app(
    service: MagicMock,
    user: CurrentUser | None = None,
) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(user_departments_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_user_department_service] = lambda: service

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. PUT /api/v1/users/{user_id}/departments — 批量设置用户实验室
# ===========================================================================


class TestSetUserDepartments:
    """PUT /api/v1/users/{user_id}/departments — 批量设置用户实验室。"""

    def test_set_200(self):
        """设置成功 → 200"""
        service = _make_mock_service()
        service.set_user_departments = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        dept_ids = [str(uuid4()), str(uuid4())]
        response = client.put(
            f"/api/v1/users/{uuid4()}/departments",
            json={
                "department_ids": dept_ids,
                "primary_department_id": dept_ids[0],
            },
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True


# ===========================================================================
# 2. GET /api/v1/users/{user_id}/departments — 查询用户实验室列表
# ===========================================================================


class TestGetUserDepartments:
    """GET /api/v1/users/{user_id}/departments — 查询用户实验室列表。"""

    def test_get_as_self_200(self):
        """本人查询 → 200"""
        service = _make_mock_service()
        user_id = uuid4()
        items = [_make_user_dept_item(user_id=user_id)]
        service.get_user_departments = AsyncMock(return_value=items)

        app = _make_app(service, user=_make_current_user(user_id=user_id))
        client = TestClient(app)

        response = client.get(f"/api/v1/users/{user_id}/departments")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["department_code"] == "dept_001"

    def test_get_as_admin_200(self):
        """有 user:manage 权限查询他人 → 200"""
        service = _make_mock_service()
        items = [_make_user_dept_item()]
        service.get_user_departments = AsyncMock(return_value=items)

        app = _make_app(service, user=_make_current_user(roles=["platform_administrator"]))
        client = TestClient(app)

        response = client.get(f"/api/v1/users/{uuid4()}/departments")

        assert response.status_code == 200

    def test_get_forbidden_403(self):
        """无权访问他人 → 403"""
        service = _make_mock_service()

        # lab_viewer 无 user:manage 权限
        app = _make_app(service, user=_make_current_user(roles=["lab_viewer"]))
        client = TestClient(app)

        response = client.get(f"/api/v1/users/{uuid4()}/departments")

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"


# ===========================================================================
# 3. GET /api/v1/departments/{department_id}/users — 查询实验室下用户
# ===========================================================================


class TestGetDepartmentUsers:
    """GET /api/v1/departments/{department_id}/users — 查询实验室下用户。"""

    def test_get_dept_users_200(self):
        """查询成功 → 200"""
        service = _make_mock_service()
        items = [
            _make_dept_user_item(email="a@irip.local", display_name="用户A"),
            _make_dept_user_item(email="b@irip.local", display_name="用户B"),
        ]
        service.get_department_users = AsyncMock(return_value=items)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/departments/{uuid4()}/users")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["email"] == "a@irip.local"
