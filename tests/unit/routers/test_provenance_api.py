"""provenance_router API 测试：证据集 / 配方 / 推导运行 / 溯源图。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user 及四个 service 依赖
- Mock EvidenceService / RecipeService / DerivationService / ProvenanceGraphService
- 验证 HTTP 状态码、响应体字段、错误码（404/422/409）
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.provenance import provenance_router
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


def _make_evidence_set_ref(
    set_id: UUID | None = None,
    version: int = 1,
    member_count: int = 0,
    status: str = "frozen",
) -> SimpleNamespace:
    return SimpleNamespace(
        set_id=set_id or uuid4(),
        version=version,
        version_id=uuid4(),
        member_count=member_count,
        status=status,
    )


def _make_evidence_member(
    fact_id: UUID | None = None,
    decision: str = "accept",
) -> SimpleNamespace:
    return SimpleNamespace(
        fact_id=fact_id or uuid4(),
        observation_id=uuid4(),
        decision=decision,
        reason="测试理由",
    )


def _make_recipe_version(
    recipe_id: UUID | None = None,
    version: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        recipe_id=recipe_id or uuid4(),
        version=version,
        component_name="test_comp",
        component_version="1.0.0",
        parameters={"param1": "value1"},
        random_seed=42,
        output_definitions=("output1",),
        status="published",
    )


def _make_derivation_run_ref(
    run_id: UUID | None = None,
    status: str = "completed",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=run_id or uuid4(),
        status=status,
        output_digest="sha256:abc123",
        outputs=[
            SimpleNamespace(
                variable_code="temperature",
                value="25.5",
                unit="°C",
                confidence=0.95,
                exclusion_reasons=[],
            )
        ],
    )


def _make_provenance_graph() -> SimpleNamespace:
    return SimpleNamespace(
        nodes=[
            SimpleNamespace(
                id=uuid4(),
                node_type="evidence_set",
                label="证据集",
                version="v1",
                status="frozen",
            )
        ],
        edges=[
            SimpleNamespace(
                source_id=uuid4(),
                source_type="evidence_set",
                target_id=uuid4(),
                target_type="recipe",
                edge_type="derived_from",
            )
        ],
    )


def _make_evidence_service() -> MagicMock:
    service = MagicMock()
    service.create_set = AsyncMock()
    service.freeze = AsyncMock()
    service.get_set = AsyncMock()
    service.list_members = AsyncMock()
    return service


def _make_recipe_service() -> MagicMock:
    service = MagicMock()
    service.create_recipe = AsyncMock()
    service.publish_version = AsyncMock()
    service.list_recipes = AsyncMock()
    service.get_recipe = AsyncMock()
    return service


def _make_derivation_service() -> MagicMock:
    service = MagicMock()
    service.create_run = AsyncMock()
    service.replay = AsyncMock()
    service.get_run = AsyncMock()
    service.list_runs = AsyncMock()
    return service


def _make_graph_service() -> MagicMock:
    service = MagicMock()
    service.get_graph = AsyncMock()
    return service


def _make_app(
    evidence: MagicMock | None = None,
    recipe: MagicMock | None = None,
    derivation: MagicMock | None = None,
    graph: MagicMock | None = None,
    user: CurrentUser | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(provenance_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user

    from apps.api.routers.provenance import (
        get_derivation_service,
        get_evidence_service,
        get_provenance_graph_service,
        get_recipe_service,
    )

    if evidence is not None:
        app.dependency_overrides[get_evidence_service] = lambda: evidence
    if recipe is not None:
        app.dependency_overrides[get_recipe_service] = lambda: recipe
    if derivation is not None:
        app.dependency_overrides[get_derivation_service] = lambda: derivation
    if graph is not None:
        app.dependency_overrides[get_provenance_graph_service] = lambda: graph

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. 证据集
# ===========================================================================


class TestEvidenceSet:
    """证据集创建 / 冻结 / 详情 / 成员。"""

    def test_create_evidence_set_201(self):
        evidence = _make_evidence_service()
        evidence.create_set = AsyncMock(
            return_value={"set_id": uuid4(), "name": "测试集", "status": "draft"}
        )

        app = _make_app(evidence=evidence)
        client = TestClient(app)

        response = client.post(
            "/api/v1/provenance/evidence-sets",
            json={"name": "测试集"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "测试集"
        assert data["status"] == "draft"

    def test_create_evidence_set_missing_name_422(self):
        evidence = _make_evidence_service()
        app = _make_app(evidence=evidence)
        client = TestClient(app)

        response = client.post("/api/v1/provenance/evidence-sets", json={})

        assert response.status_code == 422

    def test_freeze_evidence_set_200(self):
        evidence = _make_evidence_service()
        ref = _make_evidence_set_ref(member_count=5)
        evidence.freeze = AsyncMock(return_value=ref)

        app = _make_app(evidence=evidence)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/provenance/evidence-sets/{uuid4()}/freeze",
            json={},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["member_count"] == 5
        assert data["status"] == "frozen"

    def test_get_evidence_set_200(self):
        evidence = _make_evidence_service()
        evidence.get_set = AsyncMock(
            return_value={
                "set_id": uuid4(),
                "name": "证据集",
                "status": "frozen",
                "version": 1,
                "version_id": uuid4(),
                "member_count": 3,
            }
        )

        app = _make_app(evidence=evidence)
        client = TestClient(app)

        response = client.get(f"/api/v1/provenance/evidence-sets/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["member_count"] == 3

    def test_get_evidence_set_not_found_404(self):
        evidence = _make_evidence_service()
        evidence.get_set = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(evidence=evidence)
        client = TestClient(app)

        response = client.get(f"/api/v1/provenance/evidence-sets/{uuid4()}")

        assert response.status_code == 404

    def test_list_evidence_members_200(self):
        evidence = _make_evidence_service()
        members = [_make_evidence_member() for _ in range(3)]
        evidence.list_members = AsyncMock(return_value=members)

        app = _make_app(evidence=evidence)
        client = TestClient(app)

        response = client.get(f"/api/v1/provenance/evidence-sets/{uuid4()}/members")

        assert response.status_code == 200
        assert len(response.json()["members"]) == 3


# ===========================================================================
# 2. 配方
# ===========================================================================


class TestRecipe:
    """配方创建 / 发布版本 / 列表 / 详情。"""

    def test_create_recipe_201(self):
        recipe = _make_recipe_service()
        recipe.create_recipe = AsyncMock(
            return_value={
                "recipe_id": uuid4(),
                "code": "rcp_001",
                "display_name": "测试配方",
                "status": "draft",
            }
        )

        app = _make_app(recipe=recipe)
        client = TestClient(app)

        response = client.post(
            "/api/v1/provenance/recipes",
            json={"code": "rcp_001", "display_name": "测试配方"},
        )

        assert response.status_code == 201
        assert response.json()["code"] == "rcp_001"

    def test_create_recipe_missing_fields_422(self):
        recipe = _make_recipe_service()
        app = _make_app(recipe=recipe)
        client = TestClient(app)

        response = client.post("/api/v1/provenance/recipes", json={"code": "rcp"})

        assert response.status_code == 422

    def test_publish_recipe_version_200(self):
        recipe = _make_recipe_service()
        rv = _make_recipe_version()
        recipe.publish_version = AsyncMock(return_value=rv)

        app = _make_app(recipe=recipe)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/provenance/recipes/{uuid4()}/publish",
            json={
                "component_name": "test_comp",
                "component_version": "1.0.0",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["component_name"] == "test_comp"
        assert data["version"] == 1

    def test_list_recipes_200(self):
        recipe = _make_recipe_service()
        items = [
            {
                "recipe_id": uuid4(),
                "code": "rcp1",
                "display_name": "配方1",
                "status": "published",
                "version": 1,
            },
            {
                "recipe_id": uuid4(),
                "code": "rcp2",
                "display_name": "配方2",
                "status": "draft",
                "version": 0,
            },
        ]
        recipe.list_recipes = AsyncMock(return_value=(items, None))

        app = _make_app(recipe=recipe)
        client = TestClient(app)

        response = client.get("/api/v1/provenance/recipes")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["next_cursor"] is None

    def test_get_recipe_200(self):
        recipe = _make_recipe_service()
        recipe.get_recipe = AsyncMock(
            return_value={
                "recipe_id": uuid4(),
                "code": "rcp1",
                "display_name": "配方1",
                "status": "published",
                "version": 1,
            }
        )

        app = _make_app(recipe=recipe)
        client = TestClient(app)

        response = client.get(f"/api/v1/provenance/recipes/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["code"] == "rcp1"

    def test_get_recipe_not_found_404(self):
        recipe = _make_recipe_service()
        recipe.get_recipe = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(recipe=recipe)
        client = TestClient(app)

        response = client.get(f"/api/v1/provenance/recipes/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 3. 推导运行
# ===========================================================================


class TestDerivationRun:
    """推导运行创建 / 回放 / 详情 / 列表。"""

    def test_create_derivation_run_201(self):
        derivation = _make_derivation_service()
        ref = _make_derivation_run_ref()
        derivation.create_run = AsyncMock(return_value=ref)

        app = _make_app(derivation=derivation)
        client = TestClient(app)

        response = client.post(
            "/api/v1/provenance/derivation-runs",
            json={
                "evidence_set_version_id": str(uuid4()),
                "recipe_version_id": str(uuid4()),
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "completed"
        assert len(data["outputs"]) == 1
        assert data["outputs"][0]["variable_code"] == "temperature"

    def test_create_derivation_run_missing_fields_422(self):
        derivation = _make_derivation_service()
        app = _make_app(derivation=derivation)
        client = TestClient(app)

        response = client.post(
            "/api/v1/provenance/derivation-runs",
            json={"evidence_set_version_id": str(uuid4())},
        )

        assert response.status_code == 422

    def test_replay_derivation_run_200(self):
        derivation = _make_derivation_service()
        ref = _make_derivation_run_ref()
        derivation.replay = AsyncMock(return_value=ref)

        app = _make_app(derivation=derivation)
        client = TestClient(app)

        response = client.post(f"/api/v1/provenance/derivation-runs/{uuid4()}/replay")

        assert response.status_code == 200
        assert response.json()["output_digest"] == "sha256:abc123"

    def test_get_derivation_run_200(self):
        derivation = _make_derivation_service()
        ref = _make_derivation_run_ref()
        derivation.get_run = AsyncMock(return_value=ref)

        app = _make_app(derivation=derivation)
        client = TestClient(app)

        response = client.get(f"/api/v1/provenance/derivation-runs/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["id"] == str(ref.id)

    def test_list_derivation_runs_200(self):
        derivation = _make_derivation_service()
        refs = [_make_derivation_run_ref() for _ in range(2)]
        derivation.list_runs = AsyncMock(return_value=(refs, None))

        app = _make_app(derivation=derivation)
        client = TestClient(app)

        response = client.get("/api/v1/provenance/derivation-runs")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_get_derivation_run_not_found_404(self):
        derivation = _make_derivation_service()
        derivation.get_run = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(derivation=derivation)
        client = TestClient(app)

        response = client.get(f"/api/v1/provenance/derivation-runs/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 4. 溯源图
# ===========================================================================


class TestProvenanceGraph:
    """GET derivation-runs/{id}/graph。"""

    def test_get_provenance_graph_200(self):
        graph = _make_graph_service()
        g = _make_provenance_graph()
        graph.get_graph = AsyncMock(return_value=g)

        app = _make_app(graph=graph)
        client = TestClient(app)

        response = client.get(f"/api/v1/provenance/derivation-runs/{uuid4()}/graph")

        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) == 1
        assert len(data["edges"]) == 1
        assert data["nodes"][0]["node_type"] == "evidence_set"

    def test_get_provenance_graph_not_found_404(self):
        graph = _make_graph_service()
        graph.get_graph = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(graph=graph)
        client = TestClient(app)

        response = client.get(f"/api/v1/provenance/derivation-runs/{uuid4()}/graph")

        assert response.status_code == 404
