"""research_lineage_router API 测试：联邦溯源图 / 节点详情 / 知识库检索 / 导出。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user 及三个 service 依赖
- Mock UnifiedProvenanceQueryService / KnowledgeProviderService / KnowledgeReferenceService
- 验证 HTTP 状态码、响应体字段、错误码（404/422）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.research_lineage import research_lineage_router
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


def _make_node_label() -> SimpleNamespace:
    return SimpleNamespace(
        display_label="数据集 #1",
        node_type_label="数据集",
        version_summary="v1",
        namespace="research:derived_dataset",
        icon="dataset",
        jump_target="/datasets/1",
    )


def _make_node(
    namespace: str = "research:derived_dataset",
    node_id: UUID | None = None,
    node_type: str = "dataset",
) -> SimpleNamespace:
    return SimpleNamespace(
        namespace=namespace,
        node_id=node_id or uuid4(),
        version=1,
        node_type=node_type,
        display_label=_make_node_label(),
        attributes={"name": "测试数据集"},
        is_restricted=False,
    )


def _make_edge() -> SimpleNamespace:
    return SimpleNamespace(
        source_namespace="research:derived_dataset",
        source_id=uuid4(),
        source_version=1,
        target_namespace="research:result_version",
        target_id=uuid4(),
        target_version=1,
        edge_type="derived_from",
        edge_type_label="派生自",
    )


def _make_graph() -> SimpleNamespace:
    return SimpleNamespace(
        nodes=[_make_node()],
        edges=[_make_edge()],
        stats=SimpleNamespace(
            total_nodes=1,
            nodes_by_type={"dataset": 1},
            restricted_nodes_count=0,
            truncated_count=0,
        ),
    )


def _make_knowledge_ref() -> SimpleNamespace:
    return SimpleNamespace(
        reference_id=uuid4(),
        workspace_id=uuid4(),
        run_id=uuid4(),
        step_id=None,
        insight_id=None,
        document_id="doc_001",
        document_version="v1",
        title="参考文档",
        content_hash="abc123",
        source_uri="http://example.com/doc",
        retrieval_time=datetime.now(UTC),
        provider_name="default",
    )


def _make_knowledge_detail(
    ref: SimpleNamespace | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        ref=ref or _make_knowledge_ref(),
        snippet_text="这是引用片段",
        section="1.1",
        page=1,
        chunk_id="chunk_001",
        research_question_context="测试问题",
    )


def _make_knowledge_search_result() -> SimpleNamespace:
    return SimpleNamespace(
        document_id="doc_001",
        document_version="v1",
        title="搜索结果",
        section="1.1",
        page=1,
        chunk_id="chunk_001",
        relevance_score=0.95,
        source_uri="http://example.com",
        content_hash="abc",
        snippet="相关片段",
    )


def _make_provenance_service() -> MagicMock:
    service = MagicMock()
    service.query_provenance_graph = AsyncMock()
    service.query_node_detail = AsyncMock()
    return service


def _make_knowledge_provider_service() -> MagicMock:
    service = MagicMock()
    service.search = AsyncMock()
    return service


def _make_knowledge_reference_service() -> MagicMock:
    service = MagicMock()
    service.list_references_by_insight = AsyncMock()
    service.get_reference = AsyncMock()
    return service


def _make_app(
    provenance: MagicMock | None = None,
    knowledge_provider: MagicMock | None = None,
    knowledge_ref: MagicMock | None = None,
    user: CurrentUser | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(research_lineage_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user

    from apps.api.routers.research_lineage import (
        get_knowledge_provider_service,
        get_knowledge_reference_service,
        get_provenance_service,
    )

    if provenance is not None:
        app.dependency_overrides[get_provenance_service] = lambda: provenance
    if knowledge_provider is not None:
        app.dependency_overrides[get_knowledge_provider_service] = lambda: knowledge_provider
    if knowledge_ref is not None:
        app.dependency_overrides[get_knowledge_reference_service] = lambda: knowledge_ref

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. 联邦溯源图
# ===========================================================================


class TestProvenanceGraph:
    """GET /provenance/graph + 便捷端点。"""

    def test_query_graph_200(self):
        provenance = _make_provenance_service()
        provenance.query_provenance_graph = AsyncMock(return_value=_make_graph())

        app = _make_app(provenance=provenance)
        client = TestClient(app)

        response = client.get(
            f"/api/v1/research/provenance/graph?target_namespace=ds&target_id={uuid4()}"
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) == 1
        assert data["stats"]["total_nodes"] == 1

    def test_query_graph_missing_params_422(self):
        provenance = _make_provenance_service()
        app = _make_app(provenance=provenance)
        client = TestClient(app)

        response = client.get("/api/v1/research/provenance/graph")

        assert response.status_code == 422

    def test_query_result_provenance_200(self):
        provenance = _make_provenance_service()
        provenance.query_provenance_graph = AsyncMock(return_value=_make_graph())

        app = _make_app(provenance=provenance)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/provenance/graph/result/{uuid4()}/version/1")

        assert response.status_code == 200
        assert len(response.json()["nodes"]) == 1

    def test_query_dataset_provenance_200(self):
        provenance = _make_provenance_service()
        provenance.query_provenance_graph = AsyncMock(return_value=_make_graph())

        app = _make_app(provenance=provenance)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/provenance/graph/dataset/{uuid4()}/version/1")

        assert response.status_code == 200

    def test_query_view_provenance_200(self):
        provenance = _make_provenance_service()
        provenance.query_provenance_graph = AsyncMock(return_value=_make_graph())

        app = _make_app(provenance=provenance)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/provenance/graph/view/{uuid4()}/version/1")

        assert response.status_code == 200

    def test_query_insight_provenance_200(self):
        provenance = _make_provenance_service()
        provenance.query_provenance_graph = AsyncMock(return_value=_make_graph())

        app = _make_app(provenance=provenance)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/provenance/graph/insight/{uuid4()}/version/1")

        assert response.status_code == 200


# ===========================================================================
# 2. 节点详情
# ===========================================================================


class TestNodeDetail:
    """GET /provenance/node/{namespace}/{node_id}"""

    def test_get_node_200(self):
        provenance = _make_provenance_service()
        node = _make_node()
        provenance.query_node_detail = AsyncMock(return_value=node)

        app = _make_app(provenance=provenance)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/provenance/node/{node.namespace}/{node.node_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["node_type"] == "dataset"
        assert data["display_label"]["display_label"] == "数据集 #1"

    def test_get_node_not_found_404(self):
        provenance = _make_provenance_service()
        provenance.query_node_detail = AsyncMock(return_value=None)

        app = _make_app(provenance=provenance)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/provenance/node/test_ns/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 3. 知识库检索
# ===========================================================================


class TestKnowledgeSearch:
    """GET /knowledge/search"""

    def test_search_200(self):
        provider = _make_knowledge_provider_service()
        provider.search = AsyncMock(return_value=[_make_knowledge_search_result()])

        app = _make_app(knowledge_provider=provider)
        client = TestClient(app)

        response = client.get("/api/v1/research/knowledge/search?search_query=测试")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "搜索结果"
        assert data[0]["relevance_score"] == 0.95

    def test_search_missing_query_422(self):
        provider = _make_knowledge_provider_service()
        app = _make_app(knowledge_provider=provider)
        client = TestClient(app)

        response = client.get("/api/v1/research/knowledge/search")

        assert response.status_code == 422


# ===========================================================================
# 4. 知识引用
# ===========================================================================


class TestKnowledgeReferences:
    """GET /knowledge/references/{insight_id} / GET /knowledge/references/{id}/detail"""

    def test_list_references_by_insight_200(self):
        ref_service = _make_knowledge_reference_service()
        details = [_make_knowledge_detail() for _ in range(2)]
        ref_service.list_references_by_insight = AsyncMock(return_value=details)

        app = _make_app(knowledge_ref=ref_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/knowledge/references/{uuid4()}")

        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_get_reference_detail_200(self):
        ref_service = _make_knowledge_reference_service()
        detail = _make_knowledge_detail()
        ref_service.get_reference = AsyncMock(return_value=detail)

        app = _make_app(knowledge_ref=ref_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/knowledge/references/{uuid4()}/detail")

        assert response.status_code == 200
        assert response.json()["snippet_text"] == "这是引用片段"

    def test_get_reference_detail_not_found_404(self):
        ref_service = _make_knowledge_reference_service()
        ref_service.get_reference = AsyncMock(return_value=None)

        app = _make_app(knowledge_ref=ref_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/knowledge/references/{uuid4()}/detail")

        assert response.status_code == 404


# ===========================================================================
# 5. 导出溯源图
# ===========================================================================


class TestExportGraph:
    """POST /provenance/graph/export"""

    def test_export_json_200(self):
        provenance = _make_provenance_service()
        provenance.query_provenance_graph = AsyncMock(return_value=_make_graph())

        app = _make_app(provenance=provenance)
        client = TestClient(app)

        response = client.post(
            "/api/v1/research/provenance/graph/export",
            json={
                "target_namespace": "research:derived_dataset",
                "target_id": str(uuid4()),
                "format": "json",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["format"] == "json"
        assert "nodes" in data["content"]

    def test_export_png_200(self):
        provenance = _make_provenance_service()
        provenance.query_provenance_graph = AsyncMock(return_value=_make_graph())

        app = _make_app(provenance=provenance)
        client = TestClient(app)

        response = client.post(
            "/api/v1/research/provenance/graph/export",
            json={
                "target_namespace": "research:derived_dataset",
                "target_id": str(uuid4()),
                "format": "png",
            },
        )

        assert response.status_code == 200
        assert response.json()["format"] == "png"

    def test_export_missing_fields_422(self):
        provenance = _make_provenance_service()
        app = _make_app(provenance=provenance)
        client = TestClient(app)

        response = client.post(
            "/api/v1/research/provenance/graph/export",
            json={"target_namespace": "test"},
        )

        assert response.status_code == 422
