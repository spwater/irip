"""object_types_router API 测试：实验对象类型管理。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock 依赖
- 覆盖 get_current_user，patch session_scope 和 ObjectTypeService 方法
- 使用 SimpleNamespace 构造 ObjectTypeDict 避免 ORM 初始化
- 验证 HTTP 状态码、响应体字段、错误码（404/409/422）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.object_types import object_types_router, set_session_factory
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 standard:write 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="admin@irip.local",
        roles=roles or ["lab_director"],
        department_id=uuid4(),
        is_root_member=False,
    )


def _make_object_type(
    obj_id: UUID | None = None,
    code: str = "obj_type_001",
    display_name: str = "测试对象类型",
    description: str | None = "描述",
    sort_order: int = 0,
) -> SimpleNamespace:
    """构造 ObjectTypeDict（使用 SimpleNamespace）。"""
    return SimpleNamespace(
        id=obj_id or uuid4(),
        code=code,
        display_name=display_name,
        description=description,
        sort_order=sort_order,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def _setup_session_factory():
    """设置 mock session factory。"""
    set_session_factory(MagicMock())
    yield
    set_session_factory(None)


def _make_app(user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(object_types_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


def _mock_session_ctx():
    """构造 mock async context manager。"""
    mock_session = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return mock_ctx, mock_session


# ===========================================================================
# 1. GET /api/v1/object-types — 列表
# ===========================================================================


class TestListObjectTypes:
    """GET /api/v1/object-types — 列出对象类型。"""

    def test_list_200(self):
        """列表查询成功 → 200"""
        items = [
            _make_object_type(code="obj_a", display_name="类型A"),
            _make_object_type(code="obj_b", display_name="类型B"),
        ]
        with patch("apps.api.routers.object_types.ObjectTypeService") as MockService:
            instance = MockService.return_value
            instance.list_object_types = AsyncMock(return_value=items)

            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/object-types")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["code"] == "obj_a"


# ===========================================================================
# 2. POST /api/v1/object-types — 创建
# ===========================================================================


class TestCreateObjectType:
    """POST /api/v1/object-types — 创建对象类型。"""

    def test_create_201(self):
        """创建成功 → 201"""
        obj = _make_object_type(code="obj_new", display_name="新类型")
        with patch("apps.api.routers.object_types.ObjectTypeService") as MockService:
            instance = MockService.return_value
            instance.create_object_type = AsyncMock(return_value=obj)

            app = _make_app()
            client = TestClient(app)
            response = client.post(
                "/api/v1/object-types",
                json={"display_name": "新类型", "description": "描述"},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["display_name"] == "新类型"
        assert data["code"] == "obj_new"

    def test_create_missing_name_422(self):
        """缺少 display_name → 422"""
        app = _make_app()
        client = TestClient(app)
        response = client.post(
            "/api/v1/object-types",
            json={"description": "描述"},
        )
        assert response.status_code == 422


# ===========================================================================
# 3. PATCH /api/v1/object-types/{type_id} — 更新
# ===========================================================================


class TestUpdateObjectType:
    """PATCH /api/v1/object-types/{type_id} — 更新对象类型。"""

    def test_update_200(self):
        """更新成功 → 200"""
        obj = _make_object_type(display_name="新名称")
        with patch("apps.api.routers.object_types.ObjectTypeService") as MockService:
            instance = MockService.return_value
            instance.update_object_type = AsyncMock(return_value=obj)

            app = _make_app()
            client = TestClient(app)
            response = client.patch(
                f"/api/v1/object-types/{obj.id}",
                json={"display_name": "新名称"},
            )

        assert response.status_code == 200
        assert response.json()["display_name"] == "新名称"


# ===========================================================================
# 4. DELETE /api/v1/object-types/{type_id} — 删除
# ===========================================================================


class TestDeleteObjectType:
    """DELETE /api/v1/object-types/{type_id} — 删除对象类型。"""

    def test_delete_204(self):
        """删除成功 → 204"""
        with patch("apps.api.routers.object_types.ObjectTypeService") as MockService:
            instance = MockService.return_value
            instance.delete_object_type = AsyncMock(return_value=None)

            app = _make_app()
            client = TestClient(app)
            response = client.delete(f"/api/v1/object-types/{uuid4()}")

        assert response.status_code == 204
