"""facts_router API 测试：创建 + 列表 + 搜索 + 详情 + 数据 + 归档 + 删除。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_fact_service、get_fact_query_service、get_current_user 依赖
- mock FactService 和 FactQueryService
- 验证 HTTP 状态码、响应体字段、错误码（404/422）
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.facts import facts_router, get_fact_query_service, get_fact_service
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError
from packages.facts.observations import FactDetailRow, FactMeta, FactRef
from packages.facts.query_service import FactQueryService
from packages.facts.service import FactService

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 read+write 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="admin@irip.local",
        roles=roles or ["platform_administrator"],
        department_id=uuid4(),
        is_root_member=True,
    )


def _make_fact_ref(
    fact_id: UUID | None = None,
    fact_type: str = "experiment_run",
    status: str = "active",
) -> FactRef:
    """构造 FactRef。"""
    return FactRef(
        fact_id=fact_id or uuid4(),
        fact_type=fact_type,
        subject_id="subject_001",
        status=status,
    )


def _make_detail_row(
    fact_id: UUID | None = None,
    fact_type: str = "experiment_run",
    status: str = "active",
) -> FactDetailRow:
    """构造 FactDetailRow。"""
    return FactDetailRow(
        fact_id=fact_id or uuid4(),
        fact_type=fact_type,
        subject_id="subject_001",
        status=status,
        task_code="task_001",
        task_name="测试任务",
        project_name="测试项目",
        department_name="研发部",
        operator="admin",
        run_operator=None,
        equipment_name=None,
        data_summary=None,
        created_at=datetime.now(UTC),
    )


def _make_fact_meta(
    fact_id: UUID | None = None,
    source_artifact_id: UUID | None = None,
    flow_run_id: UUID | None = None,
) -> FactMeta:
    """构造 FactMeta。"""
    return FactMeta(
        fact_id=fact_id or uuid4(),
        source_artifact_id=source_artifact_id,
        department_id=uuid4(),
        owner_user_id=uuid4(),
        flow_run_id=flow_run_id,
    )


def _make_mock_fact_service() -> MagicMock:
    """构造 mock FactService。"""
    service = MagicMock(spec=FactService)
    service.department_id = uuid4()
    service.session_factory = MagicMock()
    service.create = AsyncMock()
    service.archive = AsyncMock()
    service.get_fact_meta = AsyncMock()
    service.delete_fact_record = AsyncMock()
    service.get_facts_meta_by_task = AsyncMock()
    service.delete_facts_records = AsyncMock()
    return service


def _make_mock_query_service() -> MagicMock:
    """构造 mock FactQueryService。"""
    service = MagicMock(spec=FactQueryService)
    service.list_facts_detail = AsyncMock()
    service.search_facts_detail = AsyncMock()
    service.search_by_data = AsyncMock()
    service.get_fact_detail = AsyncMock()
    service.get_fact_data = AsyncMock()
    return service


def _make_app(
    mock_service: MagicMock,
    mock_query_service: MagicMock,
    mock_user: CurrentUser | None = None,
) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(facts_router)

    user = mock_user or _make_current_user()

    async def _override_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_fact_service] = lambda: mock_service
    app.dependency_overrides[get_fact_query_service] = lambda: mock_query_service

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
# 1. POST /api/v1/facts — 创建事实
# ===========================================================================


class TestCreateFact:
    """POST /api/v1/facts — 创建事实。"""

    def test_create_201(self):
        """创建成功 → 201"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        ref = _make_fact_ref(fact_type="experiment_run")
        mock_service.create = AsyncMock(return_value=ref)

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.post(
            "/api/v1/facts",
            json={
                "fact_type": "experiment_run",
                "object_id": str(uuid4()),
                "subject_id": "subject_001",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["fact_type"] == "experiment_run"
        assert data["subject_id"] == "subject_001"
        assert data["status"] == "active"

    def test_create_invalid_fact_type_422(self):
        """非法 fact_type → 422"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.post(
            "/api/v1/facts",
            json={
                "fact_type": "invalid_type",
                "object_id": str(uuid4()),
                "subject_id": "subject_001",
            },
        )

        assert response.status_code == 422

    def test_create_missing_subject_id_422(self):
        """缺少 subject_id → 422"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.post(
            "/api/v1/facts",
            json={
                "fact_type": "experiment_run",
                "object_id": str(uuid4()),
            },
        )

        assert response.status_code == 422


# ===========================================================================
# 2. GET /api/v1/facts — 列表
# ===========================================================================


class TestListFacts:
    """GET /api/v1/facts — 分页列表。"""

    def test_list_200(self):
        """列表查询成功 → 200"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        row = _make_detail_row()
        mock_query.list_facts_detail = AsyncMock(return_value=([row], None, {"task_001": 1}))

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.get("/api/v1/facts")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["fact_type"] == "experiment_run"
        assert data["group_counts"]["task_001"] == 1

    def test_list_with_filter(self):
        """按 fact_type 筛选 → 200"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        mock_query.list_facts_detail = AsyncMock(return_value=([], None, {}))

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.get("/api/v1/facts?fact_type=simulation_run")

        assert response.status_code == 200
        call_kwargs = mock_query.list_facts_detail.call_args.kwargs
        assert call_kwargs.get("filters", {}).get("fact_type") == "simulation_run"


# ===========================================================================
# 3. GET /api/v1/facts/search — 全文搜索
# ===========================================================================


class TestSearchFacts:
    """GET /api/v1/facts/search — 全文搜索。"""

    def test_search_200(self):
        """搜索成功 → 200"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        row = _make_detail_row()
        mock_query.search_facts_detail = AsyncMock(return_value=([row], None, {}))

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.get("/api/v1/facts/search?q=subject")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1

    def test_search_missing_query_422(self):
        """缺少 q 参数 → 422"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.get("/api/v1/facts/search")

        assert response.status_code == 422


# ===========================================================================
# 4. GET /api/v1/facts/search-data — 按数据内容搜索
# ===========================================================================


class TestSearchFactsByData:
    """GET /api/v1/facts/search-data — 按数据内容搜索。"""

    def test_search_data_200(self):
        """按 key+value 搜索 → 200"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        row = _make_detail_row()
        mock_query.search_by_data = AsyncMock(return_value=([row], {}))

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.get("/api/v1/facts/search-data?key=组分&value=Na2O")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1

    def test_search_data_no_conditions_422(self):
        """无任何搜索条件 → 422"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.get("/api/v1/facts/search-data")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


# ===========================================================================
# 5. GET /api/v1/facts/{id} — 获取事实
# ===========================================================================


class TestGetFact:
    """GET /api/v1/facts/{fact_id} — 获取事实详情。"""

    def test_get_200(self):
        """获取成功 → 200"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        fact_id = uuid4()
        row = _make_detail_row(fact_id=fact_id)
        mock_query.get_fact_detail = AsyncMock(return_value=row)

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.get(f"/api/v1/facts/{fact_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["fact_id"] == str(fact_id)
        assert data["task_name"] == "测试任务"

    def test_get_not_found_404(self):
        """查询不存在 → 404"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        mock_query.get_fact_detail = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="事实不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.get(f"/api/v1/facts/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 6. GET /api/v1/facts/{id}/data — 获取事实数据
# ===========================================================================


class TestGetFactData:
    """GET /api/v1/facts/{fact_id}/data — 获取事实关联数据。"""

    def test_get_data_200(self):
        """获取数据成功 → 200"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        data = {"metadata": {"key": "val"}, "points": [], "series": []}
        mock_query.get_fact_data = AsyncMock(return_value=data)

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.get(f"/api/v1/facts/{uuid4()}/data")

        assert response.status_code == 200
        result = response.json()
        assert "metadata" in result
        assert "points" in result


# ===========================================================================
# 7. POST /api/v1/facts/{id}/archive — 归档
# ===========================================================================


class TestArchiveFact:
    """POST /api/v1/facts/{fact_id}/archive — 归档事实。"""

    def test_archive_204(self):
        """归档成功 → 204"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        mock_service.archive = AsyncMock(return_value=None)

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.post(f"/api/v1/facts/{uuid4()}/archive")

        assert response.status_code == 204

    def test_archive_not_found_404(self):
        """归档不存在 → 404"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        mock_service.archive = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="事实不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.post(f"/api/v1/facts/{uuid4()}/archive")

        assert response.status_code == 404


# ===========================================================================
# 8. DELETE /api/v1/facts/{id} — 删除
# ===========================================================================


class TestDeleteFact:
    """DELETE /api/v1/facts/{fact_id} — 物理删除事实。"""

    def test_delete_204(self):
        """删除成功 → 204"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        meta = _make_fact_meta(source_artifact_id=None, flow_run_id=None)
        mock_service.get_fact_meta = AsyncMock(return_value=meta)
        mock_service.delete_fact_record = AsyncMock(return_value=None)

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        with patch(
            "apps.api.dependencies.dept_scope.check_management_permission",
            new_callable=AsyncMock,
        ):
            response = client.delete(f"/api/v1/facts/{meta.fact_id}")

        assert response.status_code == 204

    def test_delete_not_found_404(self):
        """删除不存在 → 404"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        mock_service.get_fact_meta = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="事实不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.delete(f"/api/v1/facts/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 9. DELETE /api/v1/facts/by-task/{task_code} — 按任务删除
# ===========================================================================


class TestDeleteFactsByTask:
    """DELETE /api/v1/facts/by-task/{task_code} — 按任务批量删除。"""

    def test_delete_by_task_204(self):
        """按任务删除成功 → 204"""
        mock_service = _make_mock_fact_service()
        mock_query = _make_mock_query_service()
        meta = _make_fact_meta(source_artifact_id=None, flow_run_id=None)
        mock_service.get_facts_meta_by_task = AsyncMock(return_value=[meta])
        mock_service.delete_facts_records = AsyncMock(return_value=None)

        app = _make_app(mock_service, mock_query)
        client = TestClient(app)

        response = client.delete("/api/v1/facts/by-task/task_001")

        assert response.status_code == 204
