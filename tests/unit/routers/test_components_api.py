"""components_router API 测试：发布 / 列表 / 详情 / 版本 / 归档 / 删除。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user、get_component_registry_service
- Mock ComponentRegistryService
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
from apps.api.routers.components import (
    components_router,
    get_component_registry_service,
)
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


def _make_component(
    comp_id: UUID | None = None,
    name: str = "comp_test",
    kind: str = "transform",
    status: str = "published",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=comp_id or uuid4(),
        component_id=uuid4(),
        name=name,
        display_name="测试组件",
        version="1.0.0",
        kind=kind,
        runtime="python",
        status=status,
        manifest_sha256="sha256:abc",
        published_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        experimental_object_code=None,
        equipment_id=None,
        department_id=None,
        owner_user_id=uuid4(),
        active_version_id=uuid4(),
        visible_departments=[],
        manifest_yaml="name: comp_test\ndisplay_name: 测试组件\nkind: transform\nruntime: python\n",
    )


def _make_version(
    version_id: UUID | None = None,
    version: str = "1.0.0",
    status: str = "published",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=version_id or uuid4(),
        component_id=uuid4(),
        version=version,
        runtime="python",
        status=status,
        manifest_sha256="sha256:abc",
        published_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        experimental_object_code=None,
        equipment_id=None,
        manifest_yaml="name: comp_test\ndisplay_name: 测试组件\nkind: transform\nruntime: python\n",
    )


def _make_mock_service() -> MagicMock:
    service = MagicMock()
    service.session_factory = MagicMock()
    service.publish = AsyncMock()
    service.list_all = AsyncMock()
    service.get_by_component_id = AsyncMock()
    service.list_versions = AsyncMock()
    service.deprecate = AsyncMock()
    service.restore = AsyncMock()
    service.activate_version = AsyncMock()
    service.update_component_fields = AsyncMock()
    service.delete_component = AsyncMock()
    service.get_industrial_object_by_code = AsyncMock()
    return service


def _make_app(service: MagicMock, user: CurrentUser | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(components_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_component_registry_service] = lambda: service

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. POST 发布组件
# ===========================================================================


class TestPublishComponent:
    """POST /api/v1/components/"""

    def test_publish_201(self):
        service = _make_mock_service()
        version = _make_version()
        service.publish = AsyncMock(return_value=version)

        app = _make_app(service)
        client = TestClient(app)

        with patch(
            "packages.components.manifest.ManifestValidator.validate",
            return_value=SimpleNamespace(
                name="comp_test",
                display_name="测试组件",
                kind="transform",
            ),
        ):
            response = client.post(
                "/api/v1/components/",
                json={
                    "manifest_yaml": "name: comp_test\ndisplay_name: 测试组件\nkind: transform\n",
                },
            )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "comp_test"
        assert data["kind"] == "transform"

    def test_publish_empty_manifest_422(self):
        service = _make_mock_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.post("/api/v1/components/", json={"manifest_yaml": ""})

        assert response.status_code == 422

    def test_publish_with_experimental_object_code_not_found(self):
        service = _make_mock_service()
        service.get_industrial_object_by_code = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        with patch(
            "packages.components.manifest.ManifestValidator.validate",
            return_value=SimpleNamespace(
                name="comp_test",
                display_name="测试组件",
                kind="transform",
            ),
        ):
            response = client.post(
                "/api/v1/components/",
                json={
                    "manifest_yaml": "name: comp_test\ndisplay_name: 测试组件\nkind: transform\n",
                    "experimental_object_code": "nonexistent",
                },
            )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


# ===========================================================================
# 2. GET 列表
# ===========================================================================


class TestListComponents:
    """GET /api/v1/components/"""

    def test_list_200(self):
        service = _make_mock_service()
        comp = _make_component()
        ver = _make_version()
        service.list_all = AsyncMock(return_value=[(comp, ver)])

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/components/")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "comp_test"

    def test_list_with_kind_filter(self):
        service = _make_mock_service()
        service.list_all = AsyncMock(return_value=[])

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/components/?kind=transform")

        assert response.status_code == 200
        call_kwargs = service.list_all.call_args.kwargs
        assert call_kwargs["kind"] == "transform"


# ===========================================================================
# 3. GET 详情 + 版本列表
# ===========================================================================


class TestGetComponent:
    """GET /api/v1/components/{id}"""

    def test_get_200(self):
        service = _make_mock_service()
        comp = _make_component()
        ver = _make_version()
        service.get_by_component_id = AsyncMock(return_value=(comp, ver))

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/components/{uuid4()}")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "comp_test"
        assert "manifest_yaml" in data

    def test_get_not_found_404(self):
        service = _make_mock_service()
        service.get_by_component_id = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/components/{uuid4()}")

        assert response.status_code == 404

    def test_list_versions_200(self):
        service = _make_mock_service()
        comp = _make_component()
        ver = _make_version()
        versions = [_make_version(version=f"{i}.0.0") for i in range(1, 3)]
        service.get_by_component_id = AsyncMock(return_value=(comp, ver))
        service.list_versions = AsyncMock(return_value=versions)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/components/{uuid4()}/versions")

        assert response.status_code == 200
        assert len(response.json()) == 2


# ===========================================================================
# 4. 归档 / 恢复 / 激活版本
# ===========================================================================


class TestArchiveRestore:
    """PATCH archive / PATCH restore / POST activate。"""

    def test_archive_200(self):
        service = _make_mock_service()
        comp = _make_component()
        ver = _make_version()
        service.get_by_component_id = AsyncMock(return_value=(comp, ver))
        service.deprecate = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(f"/api/v1/components/{uuid4()}/archive")

        assert response.status_code == 200
        assert response.json()["status"] == "deprecated"

    def test_restore_200(self):
        service = _make_mock_service()
        comp = _make_component()
        ver = _make_version()
        service.get_by_component_id = AsyncMock(return_value=(comp, ver))
        service.restore = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(f"/api/v1/components/{uuid4()}/restore")

        assert response.status_code == 200
        assert response.json()["status"] == "published"

    def test_activate_version_200(self):
        service = _make_mock_service()
        service.activate_version = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(f"/api/v1/components/{uuid4()}/activate")

        assert response.status_code == 200
        assert response.json()["status"] == "activated"


# ===========================================================================
# 5. 编辑 + 删除
# ===========================================================================


class TestUpdateAndDeleteComponent:
    """PATCH update / DELETE delete。"""

    def test_update_200(self):
        service = _make_mock_service()
        comp = _make_component()
        ver = _make_version()
        service.get_by_component_id = AsyncMock(return_value=(comp, ver))
        service.update_component_fields = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        with patch(
            "apps.api.dependencies.dept_scope.check_management_permission",
            new_callable=AsyncMock,
        ):
            response = client.patch(
                f"/api/v1/components/{uuid4()}",
                json={"department_id": str(uuid4())},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "updated"

    def test_delete_200(self):
        service = _make_mock_service()
        comp = _make_component()
        ver = _make_version()
        service.get_by_component_id = AsyncMock(return_value=(comp, ver))
        service.delete_component = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        with patch(
            "apps.api.dependencies.dept_scope.check_management_permission",
            new_callable=AsyncMock,
        ):
            response = client.delete(f"/api/v1/components/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
