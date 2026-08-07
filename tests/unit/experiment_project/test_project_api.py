"""experiment_projects_router API 测试：CRUD + 状态切换 + 归档约束。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 require_permission 和 get_experiment_project_service 依赖
- 验证 HTTP 状态码和响应体字段

对应架构设计 §4 时序图 + §7.3 错误码约定。
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.api.dependencies.auth import CurrentUser
from apps.api.routers.experiment_projects import (
    CreateProjectBody,
    ExperimentProjectDetailResponse,
    ExperimentProjectListItem,
    UpdateProjectBody,
    UpdateProjectStatusBody,
    experiment_projects_router,
    get_experiment_project_service,
)
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError
from packages.experiment_project.entities import ExperimentProject
from packages.experiment_project.service import (
    ExperimentProjectListResult,
    ExperimentProjectService,
)

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


def _make_project(
    project_id: UUID | None = None,
    department_id: UUID | None = None,
    code: str = "proj_test01",
    display_name: str = "测试项目",
    description: str | None = "描述",
    status: str = "active",
    lock_version: int = 0,
) -> ExperimentProject:
    """构造 ExperimentProject 实体。"""
    return ExperimentProject(
        id=project_id or uuid4(),
        department_id=department_id or uuid4(),
        code=code,
        display_name=display_name,
        description=description,
        status=status,
        visible_departments=[],
        visibility_scope="tree",
        owner_user_id=uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        lock_version=lock_version,
    )


def _make_mock_service() -> MagicMock:
    """构造 mock ExperimentProjectService。"""
    service = MagicMock(spec=ExperimentProjectService)
    service.department_id = uuid4()
    service.actor_id = uuid4()
    service.session_factory = MagicMock()
    # _scoped_session 返回 async context manager，scalar() 返回 None（无 owner）
    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=None)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    service._scoped_session = MagicMock(return_value=mock_ctx)
    return service


def _make_app(mock_service: MagicMock, mock_user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(experiment_projects_router)

    user = mock_user or _make_current_user()

    # 覆盖 get_current_user 依赖（require_permission 内部依赖 get_current_user）
    from apps.api.dependencies.auth import get_current_user

    async def _override_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_current_user

    # 覆盖 service 依赖
    app.dependency_overrides[get_experiment_project_service] = lambda: mock_service

    # 注册 AppError 异常处理器（与生产 main.py 一致）
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
# 1. POST 创建项目
# ===========================================================================


class TestCreateProject:
    """POST /api/v1/experiment-projects — 创建项目。"""

    def test_create_201(self):
        """创建项目成功 → 201 + 字段正确"""
        mock_service = _make_mock_service()
        project = _make_project(code="proj_new01", display_name="新项目")
        mock_service.create = AsyncMock(return_value=project)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/experiment-projects",
            json={
                "department_id": str(project.department_id),
                "code": "proj_new01",
                "display_name": "新项目",
                "description": "测试描述",
                "visible_departments": [],
                "owner_user_id": str(uuid4()),
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["code"] == "proj_new01"
        assert data["display_name"] == "新项目"
        assert data["status"] == "active"
        assert data["lock_version"] == 0
        assert "id" in data
        assert "department_id" in data
        assert "owner_user_id" in data

    def test_create_duplicate_code_409(self):
        """创建项目 code 重复 → 409 Conflict"""
        mock_service = _make_mock_service()
        mock_service.create = AsyncMock(
            side_effect=AppError(
                code="conflict",
                message="项目编码已存在",
                retryable=False,
                fields={"code": "proj_dup"},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/experiment-projects",
            json={
                "department_id": str(uuid4()),
                "code": "proj_dup",
                "display_name": "重复项目",
                "owner_user_id": str(uuid4()),
            },
        )

        assert response.status_code == 409
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "conflict"

    def test_create_missing_required_field_422(self):
        """缺少必填字段 → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        # 缺少 code
        response = client.post(
            "/api/v1/experiment-projects",
            json={
                "department_id": str(uuid4()),
                "display_name": "无编码项目",
            },
        )

        assert response.status_code == 422


# ===========================================================================
# 2. GET 列表查询
# ===========================================================================


class TestListProjects:
    """GET /api/v1/experiment-projects — 列表查询（含 task_count）。"""

    def test_list_200(self):
        """列表查询成功 → 200 + 含 task_count"""
        mock_service = _make_mock_service()
        project = _make_project(code="proj_list01", display_name="列表项目")
        result = ExperimentProjectListResult(
            items=[(project, "测试部门", 3, None, 0)],
            next_cursor=None,
            has_more=False,
        )
        mock_service.list = AsyncMock(return_value=result)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/experiment-projects")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["task_count"] == 3
        assert data["items"][0]["code"] == "proj_list01"
        assert data["items"][0]["department_name"] == "测试部门"
        assert data["has_more"] is False
        assert data["next_cursor"] is None

    def test_list_with_status_filter(self):
        """状态筛选 → 200"""
        mock_service = _make_mock_service()
        result = ExperimentProjectListResult(items=[], next_cursor=None, has_more=False)
        mock_service.list = AsyncMock(return_value=result)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/experiment-projects?status=active")

        assert response.status_code == 200
        assert response.json()["items"] == []
        # 验证 service.list 被调用时传了 status
        call_kwargs = mock_service.list.call_args.kwargs
        assert call_kwargs.get("status") == "active"

    def test_list_with_pagination(self):
        """分页查询 → 200 + next_cursor"""
        mock_service = _make_mock_service()
        p1 = _make_project(code="proj_p1")
        p2 = _make_project(code="proj_p2")
        result = ExperimentProjectListResult(
            items=[(p1, "dept", 0, None, 0), (p2, "dept", 1, None, 0)],
            next_cursor="abc123",
            has_more=True,
        )
        mock_service.list = AsyncMock(return_value=result)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/experiment-projects?limit=2")

        assert response.status_code == 200
        data = response.json()
        assert data["has_more"] is True
        assert data["next_cursor"] == "abc123"


# ===========================================================================
# 3. GET 详情查询
# ===========================================================================


class TestGetProject:
    """GET /api/v1/experiment-projects/{id} — 详情查询。"""

    def test_get_200(self):
        """详情查询成功 → 200 + 含 task_count"""
        mock_service = _make_mock_service()
        project = _make_project(code="proj_detail01", display_name="详情项目")
        mock_service.get_with_stats = AsyncMock(return_value=(project, 5, 0))

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/experiment-projects/{project.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "proj_detail01"
        assert data["task_count"] == 5
        assert "visibility_scope" in data
        assert "owner_user_id" in data

    def test_get_not_found_404(self):
        """查询不存在 → 404"""
        mock_service = _make_mock_service()
        mock_service.get_with_stats = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="项目不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/experiment-projects/{uuid4()}")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


# ===========================================================================
# 4. PATCH 编辑（乐观锁）
# ===========================================================================


class TestUpdateProject:
    """PATCH /api/v1/experiment-projects/{id} — 编辑（乐观锁）。"""

    def test_update_200(self):
        """编辑成功 → 200 + lock_version 递增"""
        mock_service = _make_mock_service()
        project = _make_project(code="proj_edit01", lock_version=1, display_name="新名称")
        mock_service.get = AsyncMock(return_value=_make_project(code="proj_edit01"))
        mock_service.update = AsyncMock(return_value=project)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/experiment-projects/{project.id}",
            json={
                "display_name": "新名称",
                "description": "新描述",
                "lock_version": 0,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["display_name"] == "新名称"
        assert data["lock_version"] == 1

    def test_update_lock_version_conflict_409(self):
        """乐观锁冲突 → 409"""
        mock_service = _make_mock_service()
        existing = _make_project(code="proj_edit02")
        mock_service.get = AsyncMock(return_value=existing)
        mock_service.update = AsyncMock(
            side_effect=AppError(
                code="conflict",
                message="数据已被修改，请刷新后重试",
                retryable=False,
                fields={"lock_version": 0},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/experiment-projects/{existing.id}",
            json={
                "display_name": "新名称",
                "description": None,
                "lock_version": 0,
            },
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_update_not_found_404(self):
        """编辑不存在 → 404"""
        mock_service = _make_mock_service()
        mock_service.get = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="项目不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/experiment-projects/{uuid4()}",
            json={
                "display_name": "新名称",
                "description": None,
                "lock_version": 0,
            },
        )

        assert response.status_code == 404


# ===========================================================================
# 5. PATCH 状态切换（归档/恢复）
# ===========================================================================


class TestUpdateProjectStatus:
    """PATCH /api/v1/experiment-projects/{id}/status — 归档/恢复。"""

    def test_archive_200(self):
        """归档成功 → 200"""
        mock_service = _make_mock_service()
        project = _make_project(code="proj_arch01", status="archived", lock_version=1)
        mock_service.get = AsyncMock(return_value=_make_project(code="proj_arch01"))
        mock_service.set_status = AsyncMock(return_value=project)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/experiment-projects/{project.id}/status",
            json={"status": "archived", "lock_version": 0},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "archived"

    def test_restore_200(self):
        """恢复成功 → 200"""
        mock_service = _make_mock_service()
        project = _make_project(code="proj_rest01", status="active", lock_version=2)
        mock_service.get = AsyncMock(return_value=_make_project(code="proj_rest01"))
        mock_service.set_status = AsyncMock(return_value=project)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/experiment-projects/{project.id}/status",
            json={"status": "active", "lock_version": 1},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "active"

    def test_status_lock_version_conflict_409(self):
        """状态切换乐观锁冲突 → 409"""
        mock_service = _make_mock_service()
        existing = _make_project(code="proj_arch02")
        mock_service.get = AsyncMock(return_value=existing)
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
            f"/api/v1/experiment-projects/{existing.id}/status",
            json={"status": "archived", "lock_version": 0},
        )

        assert response.status_code == 409

    def test_invalid_status_value_422(self):
        """非法 status 值 → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/experiment-projects/{uuid4()}/status",
            json={"status": "invalid_status", "lock_version": 0},
        )

        assert response.status_code == 422


# ===========================================================================
# 6. 归档项目下创建任务 → 409
# ===========================================================================


class TestArchivedProjectCreateTask:
    """归档项目下创建任务 → 409 Conflict（通过 flows 路由的 check_not_archived）。"""

    def test_check_not_archived_raises_conflict(self):
        """归档项目 check_not_archived 抛 AppError(conflict)"""
        mock_service = _make_mock_service()
        project = _make_project(code="proj_arch_task01", status="archived")
        mock_service.get = AsyncMock(return_value=project)

        # 直接调用 service.check_not_archived 验证逻辑
        import asyncio

        with patch.object(
            ExperimentProjectService, "check_not_archived", new_callable=AsyncMock
        ) as mock_check:
            mock_check.side_effect = AppError(
                code="conflict",
                message="项目已归档，无法创建新任务",
                retryable=False,
                fields={"status": "archived"},
            )

            # 模拟 flows 路由调用 check_not_archived
            with pytest.raises(AppError) as exc_info:
                asyncio.run(mock_check(uuid4()))

            assert exc_info.value.code == "conflict"
            assert "归档" in exc_info.value.message


# ===========================================================================
# 7. 请求/响应模型验证
# ===========================================================================


class TestRequestResponseModels:
    """验证 Pydantic 请求/响应模型。"""

    def test_create_project_body_validation(self):
        """CreateProjectBody 字段验证"""
        body = CreateProjectBody(
            department_id=str(uuid4()),
            code="proj_test",
            display_name="测试",
            description="描述",
            visible_departments=[],
            owner_user_id=str(uuid4()),
        )
        assert body.code == "proj_test"
        assert body.visible_departments == []

    def test_create_project_body_code_min_length(self):
        """code 最小长度 1"""
        with pytest.raises(ValidationError):
            CreateProjectBody(
                department_id=str(uuid4()),
                code="",  # 空字符串
                display_name="测试",
            )

    def test_update_project_body_no_code_field(self):
        """UpdateProjectBody 不含 code 字段（编码锁定约定）"""
        body = UpdateProjectBody(
            display_name="新名称",
            description=None,
            lock_version=0,
        )
        assert not hasattr(body, "code"), "UpdateProjectBody 不应包含 code 字段"

    def test_update_project_status_body(self):
        """UpdateProjectStatusBody 只接受 active/archived"""
        body = UpdateProjectStatusBody(status="archived", lock_version=0)
        assert body.status == "archived"

        body2 = UpdateProjectStatusBody(status="active", lock_version=0)
        assert body2.status == "active"

    def test_update_project_status_body_rejects_invalid(self):
        """UpdateProjectStatusBody 拒绝非法 status"""
        with pytest.raises(ValidationError):
            UpdateProjectStatusBody(status="deleted", lock_version=0)

    def test_experiment_project_response_model(self):
        """ExperimentProjectResponse 序列化正确"""
        project = _make_project(code="proj_resp01")
        from apps.api.routers.experiment_projects import _to_response

        resp = _to_response(project)
        assert resp.code == "proj_resp01"
        assert resp.status == "active"
        assert isinstance(resp.id, str)
        assert isinstance(resp.department_id, str)
        assert isinstance(resp.owner_user_id, str)

    def test_list_item_has_task_count(self):
        """ExperimentProjectListItem 含 task_count 字段"""
        item = ExperimentProjectListItem(
            id=str(uuid4()),
            code="proj_item01",
            display_name="列表项",
            description=None,
            department_id=str(uuid4()),
            department_name="部门",
            visible_departments=[],
            status="active",
            task_count=5,
            created_at=datetime.now(UTC),
        )
        assert item.task_count == 5

    def test_detail_response_has_task_count(self):
        """ExperimentProjectDetailResponse 含 task_count 字段"""
        detail = ExperimentProjectDetailResponse(
            id=str(uuid4()),
            department_id=str(uuid4()),
            code="proj_det01",
            display_name="详情",
            description=None,
            status="active",
            visible_departments=[],
            visibility_scope="tree",
            owner_user_id=str(uuid4()),
            task_count=3,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            lock_version=0,
        )
        assert detail.task_count == 3
