"""departments_router API 测试：CRUD + 状态切换 + 删除 + re-parent 预览。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_department_service 和 get_current_user 依赖
- mock DepartmentService 的 create / list_all / get / update / set_status / delete /
  get_name_map / reparent_impact_preview
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
from apps.api.dependencies.departments import get_department_service
from apps.api.routers.departments import departments_router
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError
from packages.departments.service import DepartmentListResult, DepartmentService

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 manage 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="admin@irip.local",
        roles=roles or ["platform_administrator"],
        department_id=uuid4(),
        is_root_member=True,
    )


def _make_dept(
    dept_id: UUID | None = None,
    code: str = "dept_001",
    display_name: str = "测试部门",
    description: str | None = "描述",
    status: str = "active",
    sort_order: int = 0,
    lock_version: int = 0,
    parent_id: UUID | None = None,
) -> SimpleNamespace:
    """构造 Department 实体（使用 SimpleNamespace 避免 ORM 初始化复杂度）。"""
    return SimpleNamespace(
        id=dept_id or uuid4(),
        code=code,
        display_name=display_name,
        description=description,
        status=status,
        sort_order=sort_order,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        lock_version=lock_version,
        parent_id=parent_id,
    )


def _make_mock_service() -> MagicMock:
    """构造 mock DepartmentService。"""
    service = MagicMock(spec=DepartmentService)
    service.session_factory = MagicMock()
    service.create = AsyncMock()
    service.list_all = AsyncMock()
    service.get = AsyncMock()
    service.update = AsyncMock()
    service.set_status = AsyncMock()
    service.delete = AsyncMock()
    service.get_name_map = AsyncMock()
    service.reparent_impact_preview = AsyncMock()
    return service


def _make_app(mock_service: MagicMock, mock_user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(departments_router)

    user = mock_user or _make_current_user()

    async def _override_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_department_service] = lambda: mock_service

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
# 1. POST /api/v1/departments — 创建实验室
# ===========================================================================


class TestCreateDepartment:
    """POST /api/v1/departments — 创建实验室。"""

    def test_create_201(self):
        """创建成功 → 201"""
        mock_service = _make_mock_service()
        dept = _make_dept(code="dept_new01", display_name="新部门")
        mock_service.create = AsyncMock(return_value=dept)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/departments",
            json={
                "display_name": "新部门",
                "description": "描述",
                "sort_order": 0,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["display_name"] == "新部门"
        assert data["status"] == "active"
        assert data["code"] == "dept_new01"

    def test_create_conflict_409(self):
        """编码冲突 → 409"""
        mock_service = _make_mock_service()
        mock_service.create = AsyncMock(
            side_effect=AppError(
                code="conflict",
                message="实验室编码已存在",
                retryable=False,
                fields={"code": "dept_dup"},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/departments",
            json={"display_name": "重复部门", "sort_order": 0},
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_create_missing_name_422(self):
        """缺少 display_name → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/departments",
            json={"sort_order": 0},
        )

        assert response.status_code == 422


# ===========================================================================
# 2. GET /api/v1/departments — 列表查询
# ===========================================================================


class TestListDepartments:
    """GET /api/v1/departments — 分页列表。"""

    def test_list_200(self):
        """列表查询成功 → 200"""
        mock_service = _make_mock_service()
        dept = _make_dept(code="dept_list01", display_name="列表部门")
        result = DepartmentListResult(
            items=[(dept, 5, 2, 3)],
            next_cursor=None,
            has_more=False,
        )
        mock_service.list_all = AsyncMock(return_value=result)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/departments")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["display_name"] == "列表部门"
        assert data["items"][0]["member_count"] == 5
        assert data["items"][0]["children_count"] == 2
        assert data["items"][0]["equipment_count"] == 3
        assert data["has_more"] is False

    def test_list_with_status_filter(self):
        """状态筛选 → 200"""
        mock_service = _make_mock_service()
        result = DepartmentListResult(items=[], next_cursor=None, has_more=False)
        mock_service.list_all = AsyncMock(return_value=result)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/departments?status=disabled")

        assert response.status_code == 200
        call_kwargs = mock_service.list_all.call_args.kwargs
        assert call_kwargs.get("status") == "disabled"


# ===========================================================================
# 3. GET /api/v1/departments/name-map — 名称映射
# ===========================================================================


class TestNameMap:
    """GET /api/v1/departments/name-map — 部门 ID→名称映射。"""

    def test_name_map_200(self):
        """名称映射成功 → 200"""
        mock_service = _make_mock_service()
        dept_id = uuid4()
        mock_service.get_name_map = AsyncMock(
            return_value=[(dept_id, "研发一部"), (uuid4(), "研发二部")]
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/departments/name-map")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["display_name"] == "研发一部"
        assert "id" in data[0]


# ===========================================================================
# 4. GET /api/v1/departments/{id} — 详情查询
# ===========================================================================


class TestGetDepartment:
    """GET /api/v1/departments/{id} — 详情查询。"""

    def test_get_200(self):
        """详情查询成功 → 200"""
        mock_service = _make_mock_service()
        dept = _make_dept(code="dept_detail01", display_name="详情部门")
        mock_service.get = AsyncMock(return_value=dept)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/departments/{dept.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "dept_detail01"
        assert data["display_name"] == "详情部门"

    def test_get_not_found_404(self):
        """查询不存在 → 404"""
        mock_service = _make_mock_service()
        mock_service.get = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="实验室不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/departments/{uuid4()}")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


# ===========================================================================
# 5. PATCH /api/v1/departments/{id} — 编辑（乐观锁）
# ===========================================================================


class TestUpdateDepartment:
    """PATCH /api/v1/departments/{id} — 编辑。"""

    def test_update_200(self):
        """编辑成功 → 200"""
        mock_service = _make_mock_service()
        dept = _make_dept(code="dept_edit01", display_name="新名称", lock_version=1)
        mock_service.update = AsyncMock(return_value=dept)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/departments/{dept.id}",
            json={
                "display_name": "新名称",
                "description": "新描述",
                "sort_order": 0,
                "lock_version": 0,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["display_name"] == "新名称"
        assert data["lock_version"] == 1

    def test_update_lock_conflict_409(self):
        """乐观锁冲突 → 409"""
        mock_service = _make_mock_service()
        mock_service.update = AsyncMock(
            side_effect=AppError(
                code="conflict",
                message="数据已被修改，请刷新后重试",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/departments/{uuid4()}",
            json={
                "display_name": "新名称",
                "sort_order": 0,
                "lock_version": 0,
            },
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_update_not_found_404(self):
        """编辑不存在 → 404"""
        mock_service = _make_mock_service()
        mock_service.update = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="实验室不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/departments/{uuid4()}",
            json={
                "display_name": "新名称",
                "sort_order": 0,
                "lock_version": 0,
            },
        )

        assert response.status_code == 404

    def test_update_reparent_forbidden_403(self):
        """re-parent 哨兵部门 → 403"""
        mock_service = _make_mock_service()
        dept_id = uuid4()

        app = _make_app(mock_service)
        client = TestClient(app)

        with patch(
            "apps.api.routers.departments.can_reparent_department",
            new_callable=AsyncMock,
            return_value=False,
        ):
            response = client.patch(
                f"/api/v1/departments/{dept_id}",
                json={
                    "display_name": "新名称",
                    "sort_order": 0,
                    "lock_version": 0,
                    "parent_id": str(uuid4()),
                },
            )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"


# ===========================================================================
# 6. GET /api/v1/departments/{id}/reparent-impact — re-parent 影响预览
# ===========================================================================


class TestReparentImpact:
    """GET /api/v1/departments/{id}/reparent-impact — 影响预览。"""

    def test_reparent_impact_200(self):
        """影响预览成功 → 200"""
        mock_service = _make_mock_service()
        dept_id = uuid4()
        new_parent_id = uuid4()
        mock_service.reparent_impact_preview = AsyncMock(
            return_value={
                "department_id": str(dept_id),
                "department_name": "研发部",
                "new_parent_id": str(new_parent_id),
                "subtree_count": 3,
                "equipment_count": 5,
            }
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(
            f"/api/v1/departments/{dept_id}/reparent-impact?new_parent_id={new_parent_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["department_name"] == "研发部"
        assert data["subtree_count"] == 3
        assert data["equipment_count"] == 5


# ===========================================================================
# 7. PATCH /api/v1/departments/{id}/status — 启用/禁用
# ===========================================================================


class TestUpdateStatus:
    """PATCH /api/v1/departments/{id}/status — 启用/禁用。"""

    def test_disable_200(self):
        """禁用成功 → 200"""
        mock_service = _make_mock_service()
        dept = _make_dept(code="dept_dis01", status="disabled", lock_version=1)
        mock_service.set_status = AsyncMock(return_value=dept)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/departments/{dept.id}/status",
            json={"status": "disabled", "lock_version": 0},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "disabled"

    def test_enable_200(self):
        """启用成功 → 200"""
        mock_service = _make_mock_service()
        dept = _make_dept(code="dept_en01", status="active", lock_version=2)
        mock_service.set_status = AsyncMock(return_value=dept)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/departments/{dept.id}/status",
            json={"status": "active", "lock_version": 1},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "active"

    def test_status_lock_conflict_409(self):
        """状态切换乐观锁冲突 → 409"""
        mock_service = _make_mock_service()
        mock_service.set_status = AsyncMock(
            side_effect=AppError(
                code="conflict",
                message="数据已被修改，请刷新后重试",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/departments/{uuid4()}/status",
            json={"status": "disabled", "lock_version": 0},
        )

        assert response.status_code == 409

    def test_invalid_status_422(self):
        """非法 status → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/departments/{uuid4()}/status",
            json={"status": "deleted", "lock_version": 0},
        )

        assert response.status_code == 422


# ===========================================================================
# 8. DELETE /api/v1/departments/{id} — 删除
# ===========================================================================


class TestDeleteDepartment:
    """DELETE /api/v1/departments/{id} — 物理删除。"""

    def test_delete_204(self):
        """删除成功 → 204"""
        mock_service = _make_mock_service()
        mock_service.delete = AsyncMock(return_value=None)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/departments/{uuid4()}")

        assert response.status_code == 204

    def test_delete_has_children_409(self):
        """有子部门 → 409"""
        mock_service = _make_mock_service()
        mock_service.delete = AsyncMock(
            side_effect=AppError(
                code="conflict",
                message="存在子部门，不允许删除",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/departments/{uuid4()}")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_delete_not_found_404(self):
        """删除不存在 → 404"""
        mock_service = _make_mock_service()
        mock_service.delete = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="实验室不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/departments/{uuid4()}")

        assert response.status_code == 404
