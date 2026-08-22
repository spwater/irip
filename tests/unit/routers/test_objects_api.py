"""objects_router API 测试：创建 / 列表 / 详情 / 编辑 / 状态切换 / 删除。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user、get_object_graph_service
- Mock ObjectGraphService
- 验证 HTTP 状态码、响应体字段、错误码（404/409/422）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.objects import get_object_graph_service, objects_router
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    return CurrentUser(
        user_id=uuid4(),
        email="admin@irip.local",
        roles=roles or ["platform_administrator"],
        department_id=uuid4(),
        is_root_member=True,
    )


def _make_object(
    obj_id: UUID | None = None,
    object_type: str = "material",
    code: str = "obj_001",
    display_name: str = "测试对象",
    status: str = "active",
    lock_version: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=obj_id or uuid4(),
        department_id=uuid4(),
        object_type=object_type,
        code=code,
        display_name=display_name,
        description="描述",
        component_id=None,
        visible_departments=[],
        status=status,
        owner_user_id=uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        lock_version=lock_version,
    )


def _make_mock_service() -> MagicMock:
    service = MagicMock()
    service.session_factory = MagicMock()
    service.add_object = AsyncMock()
    service.list_objects = AsyncMock()
    service.get_object = AsyncMock()
    service.update_object = AsyncMock()
    service.set_object_status = AsyncMock()
    service.delete_object = AsyncMock()
    service.count_facts_by_object = AsyncMock(return_value=0)
    return service


def _make_app(service: MagicMock, user: CurrentUser | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(objects_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_object_graph_service] = lambda: service

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. POST 创建对象
# ===========================================================================


class TestCreateObject:
    """POST /api/v1/objects"""

    def test_create_201(self):
        service = _make_mock_service()
        obj = _make_object()
        service.add_object = AsyncMock(return_value=obj)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/objects",
            json={
                "object_type": "material",
                "display_name": "测试对象",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["code"] == "obj_001"
        assert data["status"] == "active"

    def test_create_missing_display_name_422(self):
        service = _make_mock_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.post("/api/v1/objects", json={"object_type": "material"})

        assert response.status_code == 422

    def test_create_conflict_409(self):
        service = _make_mock_service()
        service.add_object = AsyncMock(
            side_effect=AppError(code="conflict", message="编码已存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/objects",
            json={"object_type": "material", "display_name": "重复"},
        )

        assert response.status_code == 409


# ===========================================================================
# 2. GET 列表
# ===========================================================================


class TestListObjects:
    """GET /api/v1/objects"""

    def test_list_200(self):
        service = _make_mock_service()
        objs = [_make_object(code=f"obj_{i}") for i in range(2)]
        service.list_objects = AsyncMock(return_value=(objs, None))

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/objects")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["next_cursor"] is None

    def test_list_with_type_filter(self):
        service = _make_mock_service()
        service.list_objects = AsyncMock(return_value=([], None))

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/objects?type=material")

        assert response.status_code == 200
        call_kwargs = service.list_objects.call_args.kwargs
        assert call_kwargs["object_type"] == "material"

    def test_list_with_multi_type_filter(self):
        service = _make_mock_service()
        service.list_objects = AsyncMock(return_value=([], None))

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/objects?type=material,sample,product")

        assert response.status_code == 200
        call_kwargs = service.list_objects.call_args.kwargs
        assert call_kwargs["object_type"] == ["material", "sample", "product"]


# ===========================================================================
# 3. GET 详情
# ===========================================================================


class TestGetObject:
    """GET /api/v1/objects/{id}"""

    def test_get_200(self):
        service = _make_mock_service()
        obj = _make_object(code="obj_detail")
        service.get_object = AsyncMock(return_value=obj)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/objects/{obj.id}")

        assert response.status_code == 200
        assert response.json()["code"] == "obj_detail"

    def test_get_not_found_404(self):
        service = _make_mock_service()
        service.get_object = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/objects/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 4. PATCH 编辑
# ===========================================================================


class TestUpdateObject:
    """PATCH /api/v1/objects/{id}"""

    def test_update_200(self):
        service = _make_mock_service()
        existing = _make_object()
        updated = _make_object(display_name="新名称")
        service.get_object = AsyncMock(return_value=existing)
        service.update_object = AsyncMock(return_value=updated)

        app = _make_app(service)
        client = TestClient(app)

        with patch(
            "apps.api.dependencies.dept_scope.check_management_permission",
            new_callable=AsyncMock,
        ):
            response = client.patch(
                f"/api/v1/objects/{existing.id}",
                json={"display_name": "新名称"},
            )

        assert response.status_code == 200
        assert response.json()["display_name"] == "新名称"

    def test_update_not_found_404(self):
        service = _make_mock_service()
        service.get_object = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/objects/{uuid4()}",
            json={"display_name": "新名称"},
        )

        assert response.status_code == 404


# ===========================================================================
# 5. PATCH 状态切换
# ===========================================================================


class TestUpdateObjectStatus:
    """PATCH /api/v1/objects/{id}/status"""

    def test_update_status_200(self):
        service = _make_mock_service()
        existing = _make_object()
        updated = _make_object(status="inactive")
        service.get_object = AsyncMock(return_value=existing)
        service.set_object_status = AsyncMock(return_value=updated)

        app = _make_app(service)
        client = TestClient(app)

        with patch(
            "apps.api.dependencies.dept_scope.check_management_permission",
            new_callable=AsyncMock,
        ):
            response = client.patch(
                f"/api/v1/objects/{existing.id}/status",
                json={"status": "inactive"},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "inactive"

    def test_update_status_invalid_value_422(self):
        service = _make_mock_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/objects/{uuid4()}/status",
            json={"status": "deleted"},
        )

        assert response.status_code == 422


# ===========================================================================
# 6. DELETE 删除
# ===========================================================================


class TestDeleteObject:
    """DELETE /api/v1/objects/{id}"""

    def test_delete_204(self):
        service = _make_mock_service()
        existing = _make_object()
        service.get_object = AsyncMock(return_value=existing)
        service.count_facts_by_object = AsyncMock(return_value=0)
        service.delete_object = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        with patch(
            "apps.api.dependencies.dept_scope.check_management_permission",
            new_callable=AsyncMock,
        ):
            response = client.delete(f"/api/v1/objects/{existing.id}")

        assert response.status_code == 204

    def test_delete_conflict_has_facts_409(self):
        service = _make_mock_service()
        existing = _make_object()
        service.get_object = AsyncMock(return_value=existing)
        service.count_facts_by_object = AsyncMock(return_value=5)

        app = _make_app(service)
        client = TestClient(app)

        with patch(
            "apps.api.dependencies.dept_scope.check_management_permission",
            new_callable=AsyncMock,
        ):
            response = client.delete(f"/api/v1/objects/{existing.id}")

        assert response.status_code == 409
        assert "error" in response.json()

    def test_delete_not_found_404(self):
        service = _make_mock_service()
        service.get_object = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/objects/{uuid4()}")

        assert response.status_code == 404
