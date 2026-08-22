"""equipment_router API 测试：设备仪器管理 CRUD + 状态切换。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user 和 get_equipment_service
- Mock EquipmentService 的 create / list / get / update / set_status / delete
- patch _check_ownership 避免真实的 dept_scope 校验
- 验证 HTTP 状态码、响应体字段、错误码（404/409/422）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.equipment import equipment_router, get_equipment_service
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError
from packages.equipment.service import EquipmentListResult

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 equipment:manage 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="admin@irip.local",
        roles=roles or ["lab_director"],
        department_id=uuid4(),
        is_root_member=False,
    )


def _make_equipment(
    equip_id=None,
    code: str = "equip_001",
    display_name: str = "测试设备",
    status: str = "active",
    lock_version: int = 0,
) -> SimpleNamespace:
    """构造 Equipment 实体（使用 SimpleNamespace）。"""
    return SimpleNamespace(
        id=equip_id or uuid4(),
        department_id=uuid4(),
        code=code,
        display_name=display_name,
        description="描述",
        visible_departments=[],
        status=status,
        sort_order=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        lock_version=lock_version,
        owner_user_id=uuid4(),
    )


def _make_mock_service() -> MagicMock:
    """构造 mock EquipmentService。"""
    service = MagicMock()
    service._factory = MagicMock()
    service.create = AsyncMock()
    service.list = AsyncMock()
    service.get = AsyncMock()
    service.update = AsyncMock()
    service.set_status = AsyncMock()
    service.delete = AsyncMock()
    return service


def _make_app(service: MagicMock, user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(equipment_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_equipment_service] = lambda: service

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. POST /api/v1/equipment — 创建设备
# ===========================================================================


class TestCreateEquipment:
    """POST /api/v1/equipment — 创建设备。"""

    def test_create_201(self):
        """创建成功 → 201"""
        service = _make_mock_service()
        equip = _make_equipment(code="equip_new", display_name="新设备")
        service.create = AsyncMock(return_value=equip)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/equipment",
            json={
                "display_name": "新设备",
                "description": "描述",
                "department_id": str(uuid4()),
                "sort_order": 0,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["display_name"] == "新设备"

    def test_create_missing_dept_422(self):
        """缺少 department_id → 422"""
        service = _make_mock_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/equipment",
            json={"display_name": "设备"},
        )

        assert response.status_code == 422


# ===========================================================================
# 2. GET /api/v1/equipment — 分页列表
# ===========================================================================


class TestListEquipment:
    """GET /api/v1/equipment — 分页查询设备列表。"""

    def test_list_200(self):
        """列表查询成功 → 200"""
        service = _make_mock_service()
        equip = _make_equipment(display_name="设备A")
        result = EquipmentListResult(
            items=[(equip, "测试部门")],
            next_cursor=None,
            has_more=False,
        )
        service.list = AsyncMock(return_value=result)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/equipment")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["display_name"] == "设备A"
        assert data["items"][0]["department_name"] == "测试部门"
        assert data["has_more"] is False

    def test_list_with_status_filter(self):
        """状态筛选 → 200"""
        service = _make_mock_service()
        result = EquipmentListResult(items=[], next_cursor=None, has_more=False)
        service.list = AsyncMock(return_value=result)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/equipment?status=disabled")

        assert response.status_code == 200
        call_kwargs = service.list.call_args.kwargs
        assert call_kwargs.get("status") == "disabled"


# ===========================================================================
# 3. GET /api/v1/equipment/{id} — 详情
# ===========================================================================


class TestGetEquipment:
    """GET /api/v1/equipment/{id} — 查询设备详情。"""

    def test_get_200(self):
        """详情查询成功 → 200"""
        service = _make_mock_service()
        equip = _make_equipment(display_name="详情设备")
        service.get = AsyncMock(return_value=equip)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/equipment/{equip.id}")

        assert response.status_code == 200
        assert response.json()["display_name"] == "详情设备"

    def test_get_not_found_404(self):
        """设备不存在 → 404"""
        service = _make_mock_service()
        service.get = AsyncMock(
            side_effect=AppError(code="not_found", message="设备不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/equipment/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 4. PATCH /api/v1/equipment/{id} — 编辑设备
# ===========================================================================


class TestUpdateEquipment:
    """PATCH /api/v1/equipment/{id} — 编辑设备。"""

    def test_update_200(self):
        """编辑成功 → 200"""
        service = _make_mock_service()
        existing = _make_equipment(display_name="旧名称")
        updated = _make_equipment(display_name="新名称", lock_version=1)
        service.get = AsyncMock(return_value=existing)
        service.update = AsyncMock(return_value=updated)

        app = _make_app(service)
        client = TestClient(app)

        with patch("apps.api.routers.equipment._check_ownership", new_callable=AsyncMock):
            response = client.patch(
                f"/api/v1/equipment/{existing.id}",
                json={
                    "display_name": "新名称",
                    "lock_version": 0,
                },
            )

        assert response.status_code == 200
        assert response.json()["display_name"] == "新名称"

    def test_update_not_found_404(self):
        """设备不存在 → 404"""
        service = _make_mock_service()
        service.get = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/equipment/{uuid4()}",
            json={"display_name": "名称", "lock_version": 0},
        )

        assert response.status_code == 404


# ===========================================================================
# 5. PATCH /api/v1/equipment/{id}/status — 启用/禁用
# ===========================================================================


class TestUpdateEquipmentStatus:
    """PATCH /api/v1/equipment/{id}/status — 启用/禁用设备。"""

    def test_disable_200(self):
        """禁用成功 → 200"""
        service = _make_mock_service()
        existing = _make_equipment(status="active")
        updated = _make_equipment(status="disabled", lock_version=1)
        service.get = AsyncMock(return_value=existing)
        service.set_status = AsyncMock(return_value=updated)

        app = _make_app(service)
        client = TestClient(app)

        with patch("apps.api.routers.equipment._check_ownership", new_callable=AsyncMock):
            response = client.patch(
                f"/api/v1/equipment/{existing.id}/status",
                json={"status": "disabled", "lock_version": 0},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "disabled"

    def test_invalid_status_422(self):
        """非法 status → 422"""
        service = _make_mock_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/equipment/{uuid4()}/status",
            json={"status": "deleted", "lock_version": 0},
        )

        assert response.status_code == 422


# ===========================================================================
# 6. DELETE /api/v1/equipment/{id} — 删除
# ===========================================================================


class TestDeleteEquipment:
    """DELETE /api/v1/equipment/{id} — 删除设备。"""

    def test_delete_204(self):
        """删除成功 → 204"""
        service = _make_mock_service()
        existing = _make_equipment()
        service.get = AsyncMock(return_value=existing)
        service.delete = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        with patch("apps.api.routers.equipment._check_ownership", new_callable=AsyncMock):
            response = client.delete(f"/api/v1/equipment/{existing.id}")

        assert response.status_code == 204

    def test_delete_not_found_404(self):
        """设备不存在 → 404"""
        service = _make_mock_service()
        service.get = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/equipment/{uuid4()}")

        assert response.status_code == 404
