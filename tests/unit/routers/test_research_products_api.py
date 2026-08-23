"""research_products_router API 测试：候选产物 / Dataset / View / Insight / 候选操作 / Catalog。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user、get_product_service、get_candidate_service、get_catalog
- Mock ProductService / CandidateService / ResearchCatalogImpl
- 验证 HTTP 状态码、响应体字段、错误码（404/422/409）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.research_products import (
    get_candidate_service,
    get_catalog,
    get_product_service,
    research_products_router,
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


def _make_dataset_ref(
    dataset_id: UUID | None = None,
    workspace_id: UUID | None = None,
    name: str = "测试数据集",
    status: str = "active",
    current_version: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        dataset_id=dataset_id or uuid4(),
        workspace_id=workspace_id or uuid4(),
        name=name,
        status=status,
        current_version=current_version,
    )


def _make_dataset_detail(
    dataset_id: UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        dataset_id=dataset_id or uuid4(),
        workspace_id=uuid4(),
        name="测试数据集",
        summary="摘要",
        tags=["tag1"],
        status="active",
        current_version=1,
        source_run_id=uuid4(),
        source_snapshot_id=uuid4(),
        current_version_data={"metadata": {}},
    )


def _make_dataset_version_detail() -> SimpleNamespace:
    return SimpleNamespace(
        version_id=uuid4(),
        dataset_id=uuid4(),
        version_number=1,
        metadata_content={"meta": "data"},
        points_content=[],
        series_content=[],
        field_manifest={},
        content_hash="abc",
        source_run_id=uuid4(),
        source_step_id=uuid4(),
        source_artifact_id=uuid4(),
        created_at=datetime.now(UTC),
    )


def _make_view_ref(
    view_id: UUID | None = None,
    name: str = "测试视图",
    status: str = "active",
    current_version: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        view_id=view_id or uuid4(),
        name=name,
        status=status,
        current_version=current_version,
        caption="图表描述",
        display_order=0,
    )


def _make_view_detail() -> SimpleNamespace:
    return SimpleNamespace(
        view_id=uuid4(),
        workspace_id=uuid4(),
        name="测试视图",
        caption="描述",
        display_order=0,
        status="active",
        current_version=1,
        source_run_id=uuid4(),
        current_version_info={},
    )


def _make_view_version_detail() -> SimpleNamespace:
    return SimpleNamespace(
        version_id=uuid4(),
        view_id=uuid4(),
        version_number=1,
        image_storage_path="minio://bucket/img.png",
        image_format="png",
        image_width=800,
        image_height=600,
        image_content_hash="abc",
        chart_code_artifact_id=uuid4(),
        image_digest="digest",
        source_run_id=uuid4(),
        source_step_id=uuid4(),
        source_artifact_id=uuid4(),
        bound_dataset_version_id=uuid4(),
        chart_description="图表说明",
        created_at=datetime.now(UTC),
    )


def _make_insight_ref(
    insight_id: UUID | None = None,
    name: str = "测试洞察",
    status: str = "active",
    current_version: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        insight_id=insight_id or uuid4(),
        name=name,
        status=status,
        current_version=current_version,
    )


def _make_insight_detail() -> SimpleNamespace:
    return SimpleNamespace(
        insight_id=uuid4(),
        workspace_id=uuid4(),
        name="测试洞察",
        status="active",
        current_version=1,
        source_run_id=uuid4(),
        current_version_data={},
    )


def _make_candidate(
    candidate_id: UUID | None = None,
    candidate_type: str = "dataset",
) -> SimpleNamespace:
    return SimpleNamespace(
        candidate_type=candidate_type,
        source_artifact_id=uuid4(),
        candidate_id=candidate_id or uuid4(),
        source_run_id=uuid4(),
        source_step_id=uuid4(),
        step_name="分析步骤",
        step_status="completed",
        preview_data={"preview": "data"},
        status="pending",
        error_reason=None,
    )


def _make_product_ref(
    product_type: str = "derived_dataset",
) -> SimpleNamespace:
    return SimpleNamespace(
        product_type=product_type,
        product_id=uuid4(),
        name="产物1",
        status="active",
        current_version=1,
    )


def _make_product_service() -> MagicMock:
    service = MagicMock()
    service.create_dataset = AsyncMock()
    service.list_datasets = AsyncMock()
    service.get_dataset = AsyncMock()
    service.update_dataset_metadata = AsyncMock()
    service.list_dataset_versions = AsyncMock()
    service.get_dataset_version = AsyncMock()
    service.delete_dataset = AsyncMock()
    service.create_view = AsyncMock()
    service.list_views = AsyncMock()
    service.get_view = AsyncMock()
    service.update_view_metadata = AsyncMock()
    service.list_view_versions = AsyncMock()
    service.get_view_version = AsyncMock()
    service.delete_view = AsyncMock()
    service.list_insights = AsyncMock()
    service.get_insight = AsyncMock()
    service.update_insight_metadata = AsyncMock()
    service.delete_insight = AsyncMock()
    service.list_insight_versions = AsyncMock()
    service.create_insight_from_accept = AsyncMock()
    service.create_insight_from_modify = AsyncMock()
    service.list_products = AsyncMock()
    return service


def _make_candidate_service() -> MagicMock:
    service = MagicMock()
    service.identify_candidates = AsyncMock()
    service.get_candidate_detail = AsyncMock()
    service.list_insight_candidates = AsyncMock()
    service.reject_any_candidate = AsyncMock()
    service.reject_insight_candidate = AsyncMock()
    return service


def _make_catalog() -> MagicMock:
    catalog = MagicMock()
    catalog.search_derived_data = AsyncMock()
    return catalog


def _make_app(
    product_service: MagicMock | None = None,
    candidate_service: MagicMock | None = None,
    catalog: MagicMock | None = None,
    user: CurrentUser | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(research_products_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user

    if product_service is not None:
        app.dependency_overrides[get_product_service] = lambda: product_service
    if candidate_service is not None:
        app.dependency_overrides[get_candidate_service] = lambda: candidate_service
    if catalog is not None:
        app.dependency_overrides[get_catalog] = lambda: catalog

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. 候选产物
# ===========================================================================


class TestCandidates:
    """GET candidates list / GET candidate detail"""

    def test_list_candidates_200(self):
        candidate_service = _make_candidate_service()
        candidates = [_make_candidate() for _ in range(2)]
        candidate_service.identify_candidates = AsyncMock(return_value=candidates)

        app = _make_app(candidate_service=candidate_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/candidates")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_get_candidate_detail_200(self):
        candidate_service = _make_candidate_service()
        candidate_service.get_candidate_detail = AsyncMock(
            return_value=_make_candidate(candidate_type="view")
        )

        app = _make_app(candidate_service=candidate_service)
        client = TestClient(app)

        response = client.get(
            f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/candidates/{uuid4()}"
        )

        assert response.status_code == 200
        assert response.json()["candidate_type"] == "view"


# ===========================================================================
# 2. Derived Dataset
# ===========================================================================


class TestDataset:
    """POST / GET / PATCH / versions / DELETE dataset"""

    def test_create_dataset_201(self):
        service = _make_product_service()
        ref = _make_dataset_ref()
        service.create_dataset = AsyncMock(return_value=ref)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/derived-datasets",
            json={"artifact_id": str(uuid4()), "name": "测试数据集"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "测试数据集"
        assert data["current_version"] == 1

    def test_create_dataset_missing_fields_422(self):
        service = _make_product_service()
        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/derived-datasets",
            json={"name": "测试"},
        )

        assert response.status_code == 422

    def test_list_datasets_200(self):
        service = _make_product_service()
        refs = [_make_dataset_ref() for _ in range(2)]
        service.list_datasets = AsyncMock(return_value=refs)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/derived-datasets")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_get_dataset_200(self):
        service = _make_product_service()
        detail = _make_dataset_detail()
        service.get_dataset = AsyncMock(return_value=detail)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/derived-datasets/{uuid4()}")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "测试数据集"
        assert data["tags"] == ["tag1"]

    def test_update_dataset_200(self):
        service = _make_product_service()
        ref = _make_dataset_ref(name="新名称")
        service.update_dataset_metadata = AsyncMock(return_value=ref)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/research/workspaces/{uuid4()}/derived-datasets/{uuid4()}",
            json={"name": "新名称"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "新名称"

    def test_list_dataset_versions_200(self):
        service = _make_product_service()
        service.list_dataset_versions = AsyncMock(
            return_value=[
                SimpleNamespace(
                    version_id=uuid4(),
                    dataset_id=uuid4(),
                    version_number=1,
                    content_hash="abc",
                    created_at=datetime.now(UTC),
                )
            ]
        )

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(
            f"/api/v1/research/workspaces/{uuid4()}/derived-datasets/{uuid4()}/versions"
        )

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_get_dataset_version_200(self):
        service = _make_product_service()
        detail = _make_dataset_version_detail()
        service.get_dataset_version = AsyncMock(return_value=detail)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(
            f"/api/v1/research/workspaces/{uuid4()}/derived-datasets/{uuid4()}/versions/1"
        )

        assert response.status_code == 200
        assert response.json()["version_number"] == 1

    def test_delete_dataset_204(self):
        service = _make_product_service()
        service.delete_dataset = AsyncMock(return_value=None)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.delete(
            f"/api/v1/research/workspaces/{uuid4()}/derived-datasets/{uuid4()}"
        )

        assert response.status_code == 204

    def test_get_dataset_not_found_404(self):
        service = _make_product_service()
        service.get_dataset = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/derived-datasets/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 3. ResearchView
# ===========================================================================


class TestView:
    """POST / GET / PATCH / versions / image / DELETE view"""

    def test_create_view_201(self):
        service = _make_product_service()
        ref = _make_view_ref()
        service.create_view = AsyncMock(return_value=ref)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/views",
            json={"artifact_id": str(uuid4()), "name": "测试视图"},
        )

        assert response.status_code == 201
        assert response.json()["name"] == "测试视图"

    def test_list_views_200(self):
        service = _make_product_service()
        refs = [_make_view_ref() for _ in range(2)]
        service.list_views = AsyncMock(return_value=refs)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/views")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_get_view_200(self):
        service = _make_product_service()
        detail = _make_view_detail()
        service.get_view = AsyncMock(return_value=detail)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/views/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["name"] == "测试视图"

    def test_update_view_200(self):
        service = _make_product_service()
        ref = _make_view_ref(name="新视图")
        service.update_view_metadata = AsyncMock(return_value=ref)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/research/workspaces/{uuid4()}/views/{uuid4()}",
            json={"name": "新视图"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "新视图"

    def test_list_view_versions_200(self):
        service = _make_product_service()
        service.list_view_versions = AsyncMock(
            return_value=[
                SimpleNamespace(
                    version_id=uuid4(),
                    view_id=uuid4(),
                    version_number=1,
                    image_storage_path="minio://bucket/img.png",
                    image_format="png",
                    created_at=datetime.now(UTC),
                )
            ]
        )

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/views/{uuid4()}/versions")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_get_view_version_200(self):
        service = _make_product_service()
        detail = _make_view_version_detail()
        service.get_view_version = AsyncMock(return_value=detail)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/views/{uuid4()}/versions/1")

        assert response.status_code == 200
        assert response.json()["image_format"] == "png"

    def test_download_view_image_200(self):
        service = _make_product_service()
        detail = _make_view_version_detail()
        service.get_view_version = AsyncMock(return_value=detail)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(
            f"/api/v1/research/workspaces/{uuid4()}/views/{uuid4()}/versions/1/image"
        )

        assert response.status_code == 200
        assert response.headers.get("content-type") == "image/png"

    def test_delete_view_204(self):
        service = _make_product_service()
        service.delete_view = AsyncMock(return_value=None)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/research/workspaces/{uuid4()}/views/{uuid4()}")

        assert response.status_code == 204


# ===========================================================================
# 4. Insight
# ===========================================================================


class TestInsight:
    """GET list / GET detail / PATCH / DELETE / versions insight"""

    def test_list_insights_200(self):
        service = _make_product_service()
        refs = [_make_insight_ref() for _ in range(2)]
        service.list_insights = AsyncMock(return_value=refs)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/insights")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_get_insight_200(self):
        service = _make_product_service()
        detail = _make_insight_detail()
        service.get_insight = AsyncMock(return_value=detail)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/insights/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["name"] == "测试洞察"

    def test_update_insight_200(self):
        service = _make_product_service()
        ref = _make_insight_ref(name="新洞察")
        service.update_insight_metadata = AsyncMock(return_value=ref)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/research/workspaces/{uuid4()}/insights/{uuid4()}",
            json={"name": "新洞察"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "新洞察"

    def test_delete_insight_204(self):
        service = _make_product_service()
        service.delete_insight = AsyncMock(return_value=None)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/research/workspaces/{uuid4()}/insights/{uuid4()}")

        assert response.status_code == 204

    def test_list_insight_versions_200(self):
        service = _make_product_service()
        service.list_insight_versions = AsyncMock(
            return_value=[
                SimpleNamespace(
                    version_id=uuid4(),
                    insight_id=uuid4(),
                    version_number=1,
                    is_modified=False,
                    created_at=datetime.now(UTC),
                )
            ]
        )

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/insights/{uuid4()}/versions")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1


# ===========================================================================
# 5. Insight Candidate 操作
# ===========================================================================


class TestInsightCandidateOps:
    """GET list / accept / modify / reject"""

    def test_list_insight_candidates_200(self):
        candidate_service = _make_candidate_service()
        candidate_service.list_insight_candidates = AsyncMock(
            return_value=[
                SimpleNamespace(
                    candidate_id=uuid4(),
                    run_id=uuid4(),
                    step_id=uuid4(),
                    status="pending",
                    conclusion="结论",
                    evidence_source_label="来源",
                    created_at=datetime.now(UTC),
                )
            ]
        )

        app = _make_app(candidate_service=candidate_service)
        client = TestClient(app)

        response = client.get(
            f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/insight-candidates"
        )

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_accept_insight_candidate_201(self):
        service = _make_product_service()
        ref = _make_insight_ref()
        service.create_insight_from_accept = AsyncMock(return_value=ref)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/insight-candidates/{uuid4()}/accept"
        )

        assert response.status_code == 201
        assert response.json()["name"] == "测试洞察"

    def test_modify_insight_candidate_201(self):
        service = _make_product_service()
        ref = _make_insight_ref()
        service.create_insight_from_modify = AsyncMock(return_value=ref)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/insight-candidates/{uuid4()}/modify",
            json={"modification_note": "修改说明"},
        )

        assert response.status_code == 201

    def test_reject_insight_candidate_204(self):
        candidate_service = _make_candidate_service()
        candidate_service.reject_insight_candidate = AsyncMock(return_value=None)

        app = _make_app(candidate_service=candidate_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/insight-candidates/{uuid4()}/reject",
            json={"reason": "不需要"},
        )

        assert response.status_code == 204

    def test_reject_any_candidate_204(self):
        candidate_service = _make_candidate_service()
        candidate_service.reject_any_candidate = AsyncMock(return_value=None)

        app = _make_app(candidate_service=candidate_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/runs/{uuid4()}/candidates/{uuid4()}/reject",
            json={"reason": "不需要"},
        )

        assert response.status_code == 204


# ===========================================================================
# 6. 产物列表
# ===========================================================================


class TestListProducts:
    """GET /products"""

    def test_list_products_200(self):
        service = _make_product_service()
        products = [
            _make_product_ref(product_type="derived_dataset"),
            _make_product_ref(product_type="view"),
        ]
        service.list_products = AsyncMock(return_value=products)

        app = _make_app(product_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/products")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2


# ===========================================================================
# 7. Catalog 搜索
# ===========================================================================


class TestCatalogSearch:
    """GET /catalog/search"""

    def test_search_catalog_200(self):
        catalog = _make_catalog()
        catalog.search_derived_data = AsyncMock(
            return_value=[{"dataset_id": str(uuid4()), "name": "ds1"}]
        )

        app = _make_app(catalog=catalog)
        client = TestClient(app)

        response = client.get("/api/v1/research/catalog/search?query=test")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_search_catalog_with_workspace_filter(self):
        catalog = _make_catalog()
        catalog.search_derived_data = AsyncMock(return_value=[])

        app = _make_app(catalog=catalog)
        client = TestClient(app)

        ws_id = uuid4()
        response = client.get(f"/api/v1/research/catalog/search?workspace_id={ws_id}")

        assert response.status_code == 200
        call_kwargs = catalog.search_derived_data.call_args.kwargs
        assert call_kwargs["filters"]["workspace_id"] == str(ws_id)
