"""research_timeline_router API 测试：时间线 / 轮次 / 推荐 / 结论。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user 及 timeline_dependencies 中的 service 依赖
- Mock TimelineQueryService / TurnService / ConclusionService / RecommendationService
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
from apps.api.routers.research_timeline import research_timeline_router
from apps.api.routers.timeline_dependencies import (
    get_conclusion_service,
    get_recommendation_service,
    get_timeline_query_service,
    get_turn_service,
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


def _make_timeline_card(
    turn_id: UUID | None = None,
    turn_number: int = 1,
    kind: str = "analysis",
    status: str = "completed",
) -> SimpleNamespace:
    return SimpleNamespace(
        turn_id=turn_id or uuid4(),
        turn_number=turn_number,
        kind=kind,
        status=status,
        question_text="分析什么？",
        question_origin="manual",
        snapshot_number=1,
        selected_conclusion_count=0,
        has_result=True,
        has_candidates=False,
        created_at=datetime.now(UTC),
    )


def _make_timeline_page(
    items: list | None = None,
    next_cursor: str | None = None,
    active_run_status: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        items=items or [_make_timeline_card()],
        next_cursor=next_cursor,
        active_run_status=active_run_status,
    )


def _make_turn_ref(
    turn_id: UUID | None = None,
    workspace_id: UUID | None = None,
    kind: str = "analysis",
    status: str = "created",
) -> SimpleNamespace:
    return SimpleNamespace(
        turn_id=turn_id or uuid4(),
        workspace_id=workspace_id or uuid4(),
        turn_number=1,
        kind=kind,
        status=status,
        question_text="测试问题",
        question_origin="manual",
        evidence_snapshot_id=uuid4(),
    )


def _make_batch_ref(
    batch_id: UUID | None = None,
    workspace_id: UUID | None = None,
    status: str = "ready",
    item_count: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        batch_id=batch_id or uuid4(),
        workspace_id=workspace_id or uuid4(),
        status=status,
        item_count=item_count,
    )


def _make_conclusion_ref(
    conclusion_id: UUID | None = None,
    workspace_id: UUID | None = None,
    source_type: str = "manual",
    evidence_status: str = "none",
    status: str = "active",
    revision_number: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        conclusion_id=conclusion_id or uuid4(),
        workspace_id=workspace_id or uuid4(),
        source_type=source_type,
        evidence_status=evidence_status,
        status=status,
        revision_number=revision_number,
        statement="测试结论",
        current_revision_id=uuid4(),
    )


def _make_timeline_query_service() -> MagicMock:
    service = MagicMock()
    service.list_timeline = AsyncMock()
    return service


def _make_turn_service() -> MagicMock:
    service = MagicMock()
    service.create_analysis_turn = AsyncMock()
    service.create_synthesis_turn = AsyncMock()
    return service


def _make_conclusion_service() -> MagicMock:
    service = MagicMock()
    service.create_manual = AsyncMock()
    service.revise = AsyncMock()
    return service


def _make_recommendation_service() -> MagicMock:
    service = MagicMock()
    service.get_active = AsyncMock()
    service.retry_batch = AsyncMock()
    service.request_followup = AsyncMock()
    return service


def _make_app(
    timeline_query: MagicMock | None = None,
    turn_service: MagicMock | None = None,
    conclusion_service: MagicMock | None = None,
    recommendation_service: MagicMock | None = None,
    user: CurrentUser | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(research_timeline_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user

    if timeline_query is not None:
        app.dependency_overrides[get_timeline_query_service] = lambda: timeline_query
    if turn_service is not None:
        app.dependency_overrides[get_turn_service] = lambda: turn_service
    if conclusion_service is not None:
        app.dependency_overrides[get_conclusion_service] = lambda: conclusion_service
    if recommendation_service is not None:
        app.dependency_overrides[get_recommendation_service] = lambda: recommendation_service

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. GET 时间线
# ===========================================================================


class TestListTimeline:
    """GET /api/v1/research/workspaces/{id}/timeline"""

    def test_list_timeline_200(self):
        query_service = _make_timeline_query_service()
        page = _make_timeline_page()
        query_service.list_timeline = AsyncMock(return_value=page)

        app = _make_app(timeline_query=query_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/timeline")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["turn_number"] == 1
        assert data["next_cursor"] is None

    def test_list_timeline_with_pagination(self):
        query_service = _make_timeline_query_service()
        page = _make_timeline_page(next_cursor="cursor_abc", active_run_status="running")
        query_service.list_timeline = AsyncMock(return_value=page)

        app = _make_app(timeline_query=query_service)
        client = TestClient(app)

        response = client.get(
            f"/api/v1/research/workspaces/{uuid4()}/timeline?cursor=prev&page_size=10"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["next_cursor"] == "cursor_abc"
        assert data["active_run_status"] == "running"


# ===========================================================================
# 2. 创建轮次
# ===========================================================================


class TestCreateTurn:
    """POST /api/v1/research/workspaces/{id}/turns"""

    def test_create_turn_201(self):
        turn_service = _make_turn_service()
        ref = _make_turn_ref()
        turn_service.create_analysis_turn = AsyncMock(return_value=ref)

        app = _make_app(turn_service=turn_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/turns",
            json={
                "question_text": "分析什么？",
                "evidence_snapshot_id": str(uuid4()),
                "idempotency_key": "key_001",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["kind"] == "analysis"
        assert data["question_text"] == "测试问题"

    def test_create_turn_missing_fields_422(self):
        turn_service = _make_turn_service()
        app = _make_app(turn_service=turn_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/turns",
            json={"question_text": "分析？"},
        )

        assert response.status_code == 422

    def test_create_turn_empty_question_422(self):
        turn_service = _make_turn_service()
        app = _make_app(turn_service=turn_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/turns",
            json={
                "question_text": "",
                "evidence_snapshot_id": str(uuid4()),
                "idempotency_key": "key_001",
            },
        )

        assert response.status_code == 422


class TestCreateSynthesisTurn:
    """POST /api/v1/research/workspaces/{id}/synthesis-turns"""

    def test_create_synthesis_turn_201(self):
        turn_service = _make_turn_service()
        ref = _make_turn_ref(kind="synthesis")
        turn_service.create_synthesis_turn = AsyncMock(return_value=ref)

        app = _make_app(turn_service=turn_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/synthesis-turns",
            json={
                "evidence_snapshot_id": str(uuid4()),
                "selected_conclusion_revision_ids": [str(uuid4()), str(uuid4())],
                "idempotency_key": "key_002",
            },
        )

        assert response.status_code == 201
        assert response.json()["kind"] == "synthesis"

    def test_create_synthesis_turn_too_few_ids_422(self):
        turn_service = _make_turn_service()
        app = _make_app(turn_service=turn_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/synthesis-turns",
            json={
                "evidence_snapshot_id": str(uuid4()),
                "selected_conclusion_revision_ids": [str(uuid4())],
                "idempotency_key": "key_002",
            },
        )

        assert response.status_code == 422


# ===========================================================================
# 3. 推荐
# ===========================================================================


class TestRecommendations:
    """GET active recommendation / POST retry / POST followup"""

    def test_get_active_recommendation_200(self):
        rec_service = _make_recommendation_service()
        rec_service.get_active = AsyncMock(
            return_value={
                "batch_id": str(uuid4()),
                "workspace_id": str(uuid4()),
                "status": "ready",
                "items": [
                    {"id": "item_1", "question": "分析A？", "rationale": "因为A"},
                    {"id": "item_2", "question": "分析B？", "rationale": "因为B"},
                ],
            }
        )

        app = _make_app(recommendation_service=rec_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/recommendations/active")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert len(data["items"]) == 2

    def test_retry_recommendation_200(self):
        rec_service = _make_recommendation_service()
        ref = _make_batch_ref(status="retrying")
        rec_service.retry_batch = AsyncMock(return_value=ref)

        app = _make_app(recommendation_service=rec_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/recommendations/{uuid4()}/retry"
        )

        assert response.status_code == 200
        assert response.json()["status"] == "retrying"

    def test_request_followup_200(self):
        rec_service = _make_recommendation_service()
        ref = _make_batch_ref()
        rec_service.request_followup = AsyncMock(return_value=ref)

        app = _make_app(recommendation_service=rec_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/recommendations/followup",
            json={
                "snapshot_id": str(uuid4()),
                "idempotency_key": "key_003",
            },
        )

        assert response.status_code == 200
        assert response.json()["item_count"] == 3


# ===========================================================================
# 4. 结论
# ===========================================================================


class TestManualConclusion:
    """POST /conclusions/manual"""

    def test_create_manual_conclusion_201(self):
        conclusion_service = _make_conclusion_service()
        ref = _make_conclusion_ref()
        conclusion_service.create_manual = AsyncMock(return_value=ref)

        app = _make_app(conclusion_service=conclusion_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/conclusions/manual",
            json={"statement": "手动结论", "idempotency_key": "key_004"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["source_type"] == "manual"
        assert data["statement"] == "测试结论"

    def test_create_manual_conclusion_empty_statement_422(self):
        conclusion_service = _make_conclusion_service()
        app = _make_app(conclusion_service=conclusion_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/conclusions/manual",
            json={"statement": "", "idempotency_key": "key_004"},
        )

        assert response.status_code == 422


class TestReviseConclusion:
    """PATCH /conclusions/{id}"""

    def test_revise_conclusion_200(self):
        conclusion_service = _make_conclusion_service()
        ref = _make_conclusion_ref(revision_number=2)
        conclusion_service.revise = AsyncMock(return_value=ref)

        app = _make_app(conclusion_service=conclusion_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/research/workspaces/{uuid4()}/conclusions/{uuid4()}",
            json={"statement": "修订结论", "expected_lock_version": 0},
        )

        assert response.status_code == 200
        assert response.json()["revision_number"] == 2

    def test_revise_conclusion_missing_lock_version_422(self):
        conclusion_service = _make_conclusion_service()
        app = _make_app(conclusion_service=conclusion_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/research/workspaces/{uuid4()}/conclusions/{uuid4()}",
            json={"statement": "修订结论"},
        )

        assert response.status_code == 422

    def test_revise_conclusion_conflict_409(self):
        conclusion_service = _make_conclusion_service()
        conclusion_service.revise = AsyncMock(
            side_effect=AppError(code="conflict", message="版本冲突", retryable=False, fields={})
        )

        app = _make_app(conclusion_service=conclusion_service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/research/workspaces/{uuid4()}/conclusions/{uuid4()}",
            json={"statement": "修订", "expected_lock_version": 0},
        )

        assert response.status_code == 409


# ===========================================================================
# 5. 模型验证
# ===========================================================================


class TestRequestModels:
    """验证 Pydantic 请求模型。"""

    def test_create_turn_request_validation(self):
        from apps.api.routers.research_timeline import CreateTurnRequest

        body = CreateTurnRequest(
            question_text="分析",
            evidence_snapshot_id=str(uuid4()),
            idempotency_key="key",
        )
        assert body.question_text == "分析"
        assert body.selected_conclusion_revision_ids == []

    def test_manual_conclusion_request_defaults(self):
        from apps.api.routers.research_timeline import ManualConclusionRequest

        body = ManualConclusionRequest(
            statement="结论",
            idempotency_key="key",
        )
        assert body.scope is None
        assert body.limitations is None

    def test_revise_conclusion_request(self):
        from apps.api.routers.research_timeline import ReviseConclusionRequest

        body = ReviseConclusionRequest(
            statement="修订",
            expected_lock_version=1,
        )
        assert body.expected_lock_version == 1
