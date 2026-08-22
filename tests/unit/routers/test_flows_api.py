"""flows_router API 测试：定义管理 + 执行管理。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_flow_service 和 get_current_user 依赖
- mock FlowRuntimeService 的各种方法
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
from apps.api.routers.flows import flows_router, get_flow_service
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError
from packages.components.flow.flow_runtime import FlowRuntimeService

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 manage+execute+read 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="admin@irip.local",
        roles=roles or ["platform_administrator"],
        department_id=uuid4(),
        is_root_member=True,
    )


def _make_definition(
    def_id: UUID | None = None,
    code: str = "task_001",
    display_name: str = "测试流程",
    status: str = "draft",
    lock_version: int = 0,
    department_id: UUID | None = None,
) -> SimpleNamespace:
    """构造 FlowDefinition 实体。"""
    return SimpleNamespace(
        id=def_id or uuid4(),
        department_id=department_id or uuid4(),
        code=code,
        display_name=display_name,
        status=status,
        lock_version=lock_version,
        owner_user_id=uuid4(),
        project_id=None,
        operator="operator",
        experimental_object_code=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_version(
    version_id: UUID | None = None,
    flow_definition_id: UUID | None = None,
    version: int = 1,
    status: str = "published",
) -> SimpleNamespace:
    """构造 FlowDefinitionVersionORM 实体。"""
    return SimpleNamespace(
        id=version_id or uuid4(),
        flow_definition_id=flow_definition_id or uuid4(),
        version=version,
        nodes_json=[],
        edges_json=[],
        random_seed=0,
        digest="abc123",
        status=status,
        published_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )


def _make_run(
    run_id: UUID | None = None,
    status: str = "pending",
    flow_version_id: UUID | None = None,
) -> SimpleNamespace:
    """构造 FlowRun 实体。"""
    return SimpleNamespace(
        id=run_id or uuid4(),
        department_id=uuid4(),
        flow_version_id=flow_version_id or uuid4(),
        status=status,
        job_id=None,
        input_snapshot={},
        output_digest=None,
        started_at=None,
        completed_at=None,
        created_at=datetime.now(UTC),
    )


def _make_node_execution(
    exec_id: UUID | None = None,
    node_id: str = "node_1",
    status: str = "succeeded",
) -> SimpleNamespace:
    """构造 FlowNodeExecution 实体。"""
    return SimpleNamespace(
        id=exec_id or uuid4(),
        node_id=node_id,
        status=status,
        input_summary={},
        output_summary={},
        diagnostics=None,
        started_at=None,
        completed_at=None,
        duration_ms=100,
    )


def _make_mock_service() -> MagicMock:
    """构造 mock FlowRuntimeService。"""
    service = MagicMock(spec=FlowRuntimeService)
    service.department_id = uuid4()
    service.actor_id = uuid4()
    service.session_factory = MagicMock()
    service.create_definition = AsyncMock()
    service.publish_version = AsyncMock()
    service.list_definitions = AsyncMock()
    service.get_definition = AsyncMock()
    service.deprecate_definition = AsyncMock()
    service.restore_definition = AsyncMock()
    service.delete_flow = AsyncMock()
    service.update_definition = AsyncMock()
    service.list_runs = AsyncMock()
    service.create_run = AsyncMock()
    service.get_run = AsyncMock()
    service.delete_run = AsyncMock()
    service.resume = AsyncMock()
    service.cancel = AsyncMock()
    service.retry_node = AsyncMock()
    service.get_run_fact_ids = AsyncMock()
    service.get_latest_node_executions = AsyncMock()
    service.list_facts_by_flow = AsyncMock()
    return service


def _make_app(mock_service: MagicMock, mock_user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(flows_router)

    user = mock_user or _make_current_user()

    async def _override_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_flow_service] = lambda: mock_service

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
# 1. POST /api/v1/flows/ — 创建流程定义
# ===========================================================================


class TestCreateFlow:
    """POST /api/v1/flows/ — 创建流程定义。"""

    def test_create_201(self):
        """创建成功 → 201"""
        mock_service = _make_mock_service()
        definition = _make_definition(code="task_new01", display_name="新流程")
        mock_service.create_definition = AsyncMock(return_value=definition)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/flows/",
            json={
                "display_name": "新流程",
                "operator": "tester",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["display_name"] == "新流程"
        assert data["status"] == "draft"

    def test_create_conflict_409(self):
        """编码冲突 → 409"""
        mock_service = _make_mock_service()
        mock_service.create_definition = AsyncMock(
            side_effect=AppError(
                code="conflict",
                message="流程编码已存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/flows/",
            json={"display_name": "重复流程", "operator": "tester"},
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_create_missing_display_name_422(self):
        """缺少 display_name → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/flows/",
            json={"operator": "tester"},
        )

        assert response.status_code == 422


# ===========================================================================
# 2. GET /api/v1/flows/ — 列表
# ===========================================================================


class TestListFlows:
    """GET /api/v1/flows/ — 列表查询。"""

    def test_list_200(self):
        """列表查询成功 → 200"""
        mock_service = _make_mock_service()
        definition = _make_definition(code="task_list01")
        version = _make_version(flow_definition_id=definition.id)
        mock_service.list_definitions = AsyncMock(return_value=[(definition, version)])

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/flows/")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["code"] == "task_list01"

    def test_list_with_status_filter(self):
        """状态筛选 → 200"""
        mock_service = _make_mock_service()
        mock_service.list_definitions = AsyncMock(return_value=[])

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/flows/?status=published")

        assert response.status_code == 200
        call_kwargs = mock_service.list_definitions.call_args.kwargs
        assert call_kwargs.get("status") == "published"


# ===========================================================================
# 3. GET /api/v1/flows/{id} — 详情
# ===========================================================================


class TestGetFlow:
    """GET /api/v1/flows/{flow_id} — 详情查询。"""

    def test_get_200(self):
        """详情查询成功 → 200"""
        mock_service = _make_mock_service()
        definition = _make_definition(code="task_detail01")
        version = _make_version(flow_definition_id=definition.id)
        mock_service.get_definition = AsyncMock(return_value=(definition, version))

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/flows/{definition.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "task_detail01"
        assert data["latest_version"] is not None

    def test_get_not_found_404(self):
        """查询不存在 → 404"""
        mock_service = _make_mock_service()
        mock_service.get_definition = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="流程不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/flows/{uuid4()}")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


# ===========================================================================
# 4. POST /api/v1/flows/{id}/archive — 归档
# ===========================================================================


class TestArchiveFlow:
    """POST /api/v1/flows/{flow_id}/archive — 归档。"""

    def test_archive_200(self):
        """归档成功 → 200"""
        mock_service = _make_mock_service()
        definition = _make_definition(code="task_arch01", status="deprecated")
        mock_service.deprecate_definition = AsyncMock(return_value=definition)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/flows/{definition.id}/archive")

        assert response.status_code == 200
        assert response.json()["status"] == "deprecated"


# ===========================================================================
# 5. POST /api/v1/flows/{id}/restore — 恢复
# ===========================================================================


class TestRestoreFlow:
    """POST /api/v1/flows/{flow_id}/restore — 恢复。"""

    def test_restore_200(self):
        """恢复成功 → 200"""
        mock_service = _make_mock_service()
        definition = _make_definition(code="task_rest01", status="published")
        mock_service.restore_definition = AsyncMock(return_value=definition)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/flows/{definition.id}/restore")

        assert response.status_code == 200
        assert response.json()["status"] == "published"


# ===========================================================================
# 6. PATCH /api/v1/flows/{id} — 更新
# ===========================================================================


class TestUpdateFlow:
    """PATCH /api/v1/flows/{flow_id} — 更新流程定义。"""

    def test_update_200(self):
        """更新成功 → 200"""
        mock_service = _make_mock_service()
        definition = _make_definition(code="task_upd01", display_name="新名称")
        mock_service.get_definition = AsyncMock(
            return_value=(definition, _make_version(flow_definition_id=definition.id))
        )
        mock_service.update_definition = AsyncMock(return_value=definition)

        app = _make_app(mock_service)
        client = TestClient(app)

        with patch(
            "apps.api.dependencies.dept_scope.check_management_permission",
            new_callable=AsyncMock,
        ):
            response = client.patch(
                f"/api/v1/flows/{definition.id}",
                json={"display_name": "新名称"},
            )

        assert response.status_code == 200
        assert response.json()["display_name"] == "新名称"


# ===========================================================================
# 7. DELETE /api/v1/flows/{id} — 删除
# ===========================================================================


class TestDeleteFlow:
    """DELETE /api/v1/flows/{flow_id} — 删除流程。"""

    def test_delete_204(self):
        """删除成功 → 204"""
        mock_service = _make_mock_service()
        definition = _make_definition(code="task_del01")
        mock_service.get_definition = AsyncMock(
            return_value=(definition, _make_version(flow_definition_id=definition.id))
        )
        mock_service.delete_flow = AsyncMock(return_value=None)

        app = _make_app(mock_service)
        client = TestClient(app)

        with patch(
            "apps.api.dependencies.dept_scope.check_management_permission",
            new_callable=AsyncMock,
        ):
            response = client.delete(f"/api/v1/flows/{definition.id}")

        assert response.status_code == 204


# ===========================================================================
# 8. POST /api/v1/flows/{id}/runs — 创建执行
# ===========================================================================


class TestCreateRun:
    """POST /api/v1/flows/{flow_id}/runs — 创建执行。"""

    def test_create_run_202(self):
        """创建执行成功 → 202"""
        mock_service = _make_mock_service()
        definition = _make_definition(code="task_run01", status="published")
        version = _make_version(flow_definition_id=definition.id)
        mock_service.get_definition = AsyncMock(return_value=(definition, version))
        run = _make_run(status="pending", flow_version_id=version.id)
        mock_service.create_run = AsyncMock(return_value=run)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/flows/{definition.id}/runs",
            json={"inputs": {"param1": "value1"}},
        )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "pending"

    def test_create_run_no_published_version_422(self):
        """无已发布版本 → 422"""
        mock_service = _make_mock_service()
        definition = _make_definition(code="task_run02", status="draft")
        mock_service.get_definition = AsyncMock(return_value=(definition, None))

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/flows/{definition.id}/runs",
            json={"inputs": {}},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


# ===========================================================================
# 9. POST /api/v1/flows/runs/{run_id}/cancel — 取消执行
# ===========================================================================


class TestCancelRun:
    """POST /api/v1/flows/runs/{run_id}/cancel — 取消执行。"""

    def test_cancel_200(self):
        """取消成功 → 200"""
        mock_service = _make_mock_service()
        run = _make_run(status="cancelled")
        mock_service.cancel = AsyncMock(return_value=run)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/flows/runs/{run.id}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    def test_cancel_not_found_404(self):
        """取消不存在的执行 → 404"""
        mock_service = _make_mock_service()
        mock_service.cancel = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="执行不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/flows/runs/{uuid4()}/cancel")

        assert response.status_code == 404


# ===========================================================================
# 10. GET /api/v1/flows/runs/{run_id} — 执行详情
# ===========================================================================


class TestGetRun:
    """GET /api/v1/flows/runs/{run_id} — 执行详情。"""

    def test_get_run_200(self):
        """获取执行详情 → 200"""
        mock_service = _make_mock_service()
        run = _make_run(status="succeeded")
        node_exec = _make_node_execution(node_id="node_1", status="succeeded")
        mock_service.get_run = AsyncMock(return_value=(run, [node_exec]))

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/flows/runs/{run.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "succeeded"
        assert len(data["node_executions"]) == 1
        assert data["node_executions"][0]["node_id"] == "node_1"

    def test_get_run_not_found_404(self):
        """执行不存在 → 404"""
        mock_service = _make_mock_service()
        mock_service.get_run = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="执行不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/flows/runs/{uuid4()}")

        assert response.status_code == 404
