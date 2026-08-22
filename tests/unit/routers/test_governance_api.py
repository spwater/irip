"""governance_router API 测试：用户管理 + 数据移交 + root 统计。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock 依赖
- 覆盖 get_governance_session_factory 和 get_current_user 依赖
- mock GovernanceService 通过 patch _make_service
- 验证 HTTP 状态码、响应体字段、错误码（404/409/403/422）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.governance import (
    get_governance_session_factory,
    governance_router,
)
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(
    roles: list[str] | None = None,
    department_id: UUID | None = None,
    user_id: UUID | None = None,
) -> CurrentUser:
    """构造 platform_administrator 当前用户。"""
    return CurrentUser(
        user_id=user_id or uuid4(),
        email="admin@irip.local",
        roles=roles or ["platform_administrator"],
        department_id=department_id or uuid4(),
        is_root_member=True,
    )


def _make_user(
    user_id: UUID | None = None,
    email: str = "user@irip.local",
    display_name: str = "测试用户",
    roles: list[str] | None = None,
    status: str = "active",
    department_id: UUID | None = None,
) -> SimpleNamespace:
    """构造 AppUser 实体。"""
    return SimpleNamespace(
        id=user_id or uuid4(),
        email=email,
        display_name=display_name,
        roles=roles or ["lab_member"],
        status=status,
        department_id=department_id or uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_mock_service() -> MagicMock:
    """构造 mock GovernanceService。"""
    service = MagicMock()
    service.list_users = AsyncMock()
    service.create_user = AsyncMock()
    service.update_user = AsyncMock()
    service.assign_roles = AsyncMock()
    service.remove_role = AsyncMock()
    service.update_user_status = AsyncMock()
    service.delete_user = AsyncMock()
    service.transfer_data = AsyncMock()
    service.get_root_data_stats = AsyncMock()
    return service


def _make_app(
    mock_service: MagicMock,
    mock_user: CurrentUser | None = None,
) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(governance_router)

    user = mock_user or _make_current_user()

    async def _override_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_current_user

    # session_factory mock
    mock_session_factory = MagicMock()
    app.dependency_overrides[get_governance_session_factory] = lambda: mock_session_factory

    # patch _make_service to return our mock_service
    patcher = patch(
        "apps.api.routers.governance._make_service",
        return_value=mock_service,
    )
    patcher.start()

    # Also patch lookup_dept_id to avoid DB access
    patcher2 = patch(
        "apps.api.routers.governance.lookup_dept_id",
        new_callable=AsyncMock,
        return_value=uuid4(),
    )
    patcher2.start()

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
# 1. GET /api/v1/governance/users — 列出用户
# ===========================================================================


class TestListUsers:
    """GET /api/v1/governance/users — 列出用户（分页）。"""

    def test_list_200(self):
        """列表查询成功 → 200"""
        mock_service = _make_mock_service()
        user = _make_user(email="list@irip.local", display_name="列表用户")
        mock_service.list_users = AsyncMock(return_value=([user], False, None))

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/governance/users")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["email"] == "list@irip.local"
        assert data["has_more"] is False

    def test_list_with_status_filter(self):
        """状态筛选 → 200"""
        mock_service = _make_mock_service()
        mock_service.list_users = AsyncMock(return_value=([], False, None))

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/governance/users?status=disabled")

        assert response.status_code == 200
        call_kwargs = mock_service.list_users.call_args.kwargs
        assert call_kwargs.get("status") == "disabled"


# ===========================================================================
# 2. POST /api/v1/governance/users — 新建用户
# ===========================================================================


class TestCreateUser:
    """POST /api/v1/governance/users — 新建用户。"""

    def test_create_201(self):
        """创建成功 → 201"""
        mock_service = _make_mock_service()
        user = _make_user(email="new@irip.local", display_name="新用户")
        mock_service.create_user = AsyncMock(return_value=user)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/governance/users",
            json={
                "email": "new@irip.local",
                "display_name": "新用户",
                "password": "password123",
                "roles": ["lab_member"],
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "new@irip.local"

    def test_create_conflict_409(self):
        """邮箱冲突 → 409"""
        mock_service = _make_mock_service()
        mock_service.create_user = AsyncMock(
            side_effect=AppError(
                code="conflict",
                message="邮箱已存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/governance/users",
            json={
                "email": "dup@irip.local",
                "display_name": "重复用户",
                "password": "password123",
                "roles": ["lab_member"],
            },
        )

        assert response.status_code == 409

    def test_create_invalid_role_422(self):
        """未知角色 → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/governance/users",
            json={
                "email": "bad@irip.local",
                "display_name": "坏角色用户",
                "password": "password123",
                "roles": ["unknown_role"],
            },
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"

    def test_create_short_password_422(self):
        """密码太短 → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/governance/users",
            json={
                "email": "short@irip.local",
                "display_name": "短密码用户",
                "password": "123",
                "roles": ["lab_member"],
            },
        )

        assert response.status_code == 422


# ===========================================================================
# 3. PATCH /api/v1/governance/users/{id} — 编辑用户
# ===========================================================================


class TestUpdateUser:
    """PATCH /api/v1/governance/users/{user_id} — 编辑用户。"""

    def test_update_200(self):
        """编辑成功 → 200"""
        mock_service = _make_mock_service()
        user = _make_user(display_name="新名称")
        mock_service.update_user = AsyncMock(return_value=user)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/governance/users/{user.id}",
            json={"display_name": "新名称"},
        )

        assert response.status_code == 200
        assert response.json()["display_name"] == "新名称"

    def test_update_not_found_404(self):
        """编辑不存在 → 404"""
        mock_service = _make_mock_service()
        mock_service.update_user = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="用户不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/governance/users/{uuid4()}",
            json={"display_name": "新名称"},
        )

        assert response.status_code == 404


# ===========================================================================
# 4. POST /api/v1/governance/users/{id}/roles — 分配角色
# ===========================================================================


class TestAssignRoles:
    """POST /api/v1/governance/users/{user_id}/roles — 分配角色。"""

    def test_assign_roles_200(self):
        """分配角色成功 → 200"""
        mock_service = _make_mock_service()
        user = _make_user(roles=["lab_member", "lab_viewer"])
        mock_service.assign_roles = AsyncMock(return_value=user)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/governance/users/{user.id}/roles",
            json={"roles": ["lab_viewer"]},
        )

        assert response.status_code == 200
        assert "lab_viewer" in response.json()["roles"]

    def test_assign_roles_invalid_role_422(self):
        """未知角色 → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/governance/users/{uuid4()}/roles",
            json={"roles": ["bad_role"]},
        )

        assert response.status_code == 422


# ===========================================================================
# 5. DELETE /api/v1/governance/users/{id}/roles/{role} — 移除角色
# ===========================================================================


class TestRemoveRole:
    """DELETE /api/v1/governance/users/{user_id}/roles/{role} — 移除角色。"""

    def test_remove_role_200(self):
        """移除角色成功 → 200"""
        mock_service = _make_mock_service()
        user = _make_user(roles=["lab_member"])
        mock_service.remove_role = AsyncMock(return_value=user)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/governance/users/{user.id}/roles/lab_viewer")

        assert response.status_code == 200


# ===========================================================================
# 6. PATCH /api/v1/governance/users/{id}/status — 启用/禁用
# ===========================================================================


class TestUpdateUserStatus:
    """PATCH /api/v1/governance/users/{user_id}/status — 启用/禁用。"""

    def test_disable_200(self):
        """禁用成功 → 200"""
        mock_service = _make_mock_service()
        user = _make_user(status="disabled")
        mock_service.update_user_status = AsyncMock(return_value=user)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/governance/users/{user.id}/status",
            json={"status": "disabled"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "disabled"

    def test_invalid_status_422(self):
        """非法 status → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/governance/users/{uuid4()}/status",
            json={"status": "banned"},
        )

        assert response.status_code == 422


# ===========================================================================
# 7. DELETE /api/v1/governance/users/{id} — 删除用户
# ===========================================================================


class TestDeleteUser:
    """DELETE /api/v1/governance/users/{user_id} — 删除用户。"""

    def test_delete_204(self):
        """删除成功 → 204"""
        mock_service = _make_mock_service()
        mock_service.delete_user = AsyncMock(return_value=None)
        target_id = uuid4()

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/governance/users/{target_id}")

        assert response.status_code == 204

    def test_delete_self_403(self):
        """删除自己 → 403"""
        my_id = uuid4()
        mock_service = _make_mock_service()
        app = _make_app(mock_service, mock_user=_make_current_user(user_id=my_id))
        client = TestClient(app)

        response = client.delete(f"/api/v1/governance/users/{my_id}")

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"


# ===========================================================================
# 8. POST /api/v1/governance/data-transfer — 数据移交
# ===========================================================================


class TestDataTransfer:
    """POST /api/v1/governance/data-transfer — 批量移交数据。"""

    def test_transfer_200(self):
        """数据移交成功 → 200"""
        mock_service = _make_mock_service()
        mock_service.transfer_data = AsyncMock(return_value=5)

        app = _make_app(mock_service)
        client = TestClient(app)

        from_dept = str(uuid4())
        to_dept = str(uuid4())
        response = client.post(
            "/api/v1/governance/data-transfer",
            json={
                "table": "fact",
                "from_dept_id": from_dept,
                "to_dept_id": to_dept,
                "dry_run": True,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["table"] == "fact"
        assert data["dry_run"] is True
        assert data["affected_rows"] == 5

    def test_transfer_invalid_uuid_422(self):
        """无效 UUID → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/governance/data-transfer",
            json={
                "table": "fact",
                "from_dept_id": "not-a-uuid",
                "to_dept_id": str(uuid4()),
                "dry_run": False,
            },
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


# ===========================================================================
# 9. GET /api/v1/governance/root-data-stats — root 数据统计
# ===========================================================================


class TestRootDataStats:
    """GET /api/v1/governance/root-data-stats — root 部门数据量统计。"""

    def test_root_data_stats_200(self):
        """统计成功 → 200"""
        mock_service = _make_mock_service()
        root_id = str(uuid4())
        mock_service.get_root_data_stats = AsyncMock(
            return_value=(
                root_id,
                "root",
                [{"table": "fact", "count": 100}],
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/governance/root-data-stats")

        assert response.status_code == 200
        data = response.json()
        assert data["root_department_name"] == "root"
        assert len(data["stats"]) == 1
        assert data["stats"][0]["count"] == 100
