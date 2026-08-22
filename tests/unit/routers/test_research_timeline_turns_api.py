"""research_timeline_turns 路由测试：轮次详情 / 计划 / 结论 / 分析。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user 及 timeline_dependencies 中的 service 依赖
- Mock TimelineQueryService / TurnService / ConclusionService / AnalysisService
- 验证 HTTP 状态码、响应体字段、错误码（404/422/409）
- 共用 research_timeline_router（与 research_timeline_turns.py 相同路由实例）
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

# Import turns module so its @research_timeline_router decorators register routes
import apps.api.routers.research_timeline_turns  # noqa: F401
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.research_timeline import research_timeline_router
from apps.api.routers.timeline_dependencies import (
    get_analysis_service,
    get_conclusion_service,
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


def _make_turn_ref(
    turn_id: UUID | None = None,
    status: str = "plan_requested",
) -> SimpleNamespace:
    return SimpleNamespace(
        turn_id=turn_id or uuid4(),
        status=status,
    )


def _make_plan_ref(
    plan_id: UUID | None = None,
    turn_id: UUID | None = None,
    version_number: int = 1,
    status: str = "confirmed",
) -> SimpleNamespace:
    return SimpleNamespace(
        plan_id=plan_id or uuid4(),
        turn_id=turn_id or uuid4(),
        version_number=version_number,
        status=status,
    )


def _make_timeline_query_service() -> MagicMock:
    service = MagicMock()
    service.get_turn_detail_api = AsyncMock()
    return service


def _make_turn_service() -> MagicMock:
    service = MagicMock()
    service.delete_turn = AsyncMock()
    service.start_planning = AsyncMock()
    service.confirm_plan = AsyncMock()
    return service


def _make_conclusion_service() -> MagicMock:
    service = MagicMock()
    service.delete_conclusion = AsyncMock()
    service.list_conclusions = AsyncMock()
    service.save_from_block = AsyncMock()
    return service


def _make_analysis_service() -> MagicMock:
    service = MagicMock()
    service.submit_run = AsyncMock()
    return service


def _make_app(
    timeline_query: MagicMock | None = None,
    turn_service: MagicMock | None = None,
    conclusion_service: MagicMock | None = None,
    analysis_service: MagicMock | None = None,
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
    if analysis_service is not None:
        app.dependency_overrides[get_analysis_service] = lambda: analysis_service

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. 删除轮次
# ===========================================================================


class TestDeleteTurn:
    """DELETE /api/v1/research/workspaces/{id}/turns/{turn_id}"""

    def test_delete_turn_200(self):
        turn_service = _make_turn_service()
        turn_service.delete_turn = AsyncMock(return_value=None)

        app = _make_app(turn_service=turn_service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/research/workspaces/{uuid4()}/turns/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_delete_turn_not_found_404(self):
        turn_service = _make_turn_service()
        turn_service.delete_turn = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(turn_service=turn_service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/research/workspaces/{uuid4()}/turns/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 2. 轮次详情
# ===========================================================================


class TestGetTurnDetail:
    """GET /api/v1/research/workspaces/{id}/turns/{turn_id}"""

    def test_get_turn_detail_200(self):
        query_service = _make_timeline_query_service()
        detail = {"turn_id": str(uuid4()), "status": "completed", "steps": []}
        query_service.get_turn_detail_api = AsyncMock(return_value=detail)

        app = _make_app(timeline_query=query_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/turns/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["status"] == "completed"


# ===========================================================================
# 3. 计划相关
# ===========================================================================


class TestPlanning:
    """POST plan / POST confirm-plan"""

    def test_start_planning_200(self):
        turn_service = _make_turn_service()
        ref = _make_turn_ref(status="plan_requested")
        turn_service.start_planning = AsyncMock(return_value=ref)

        app = _make_app(turn_service=turn_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/research/workspaces/{uuid4()}/turns/{uuid4()}/plan")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "plan_requested"

    def test_confirm_plan_200(self):
        turn_service = _make_turn_service()
        ref = _make_plan_ref()
        turn_service.confirm_plan = AsyncMock(return_value=ref)

        app = _make_app(turn_service=turn_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/turns/{uuid4()}/confirm-plan",
            json={"plan_id": str(ref.plan_id)},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "confirmed"
        assert data["version_number"] == 1

    def test_confirm_plan_missing_plan_id_422(self):
        turn_service = _make_turn_service()
        app = _make_app(turn_service=turn_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/turns/{uuid4()}/confirm-plan",
            json={},
        )

        assert response.status_code == 422


# ===========================================================================
# 4. 结论操作
# ===========================================================================


class TestConclusionOps:
    """DELETE conclusion / GET list / POST save-from-block"""

    def test_delete_conclusion_200(self):
        conclusion_service = _make_conclusion_service()
        conclusion_service.delete_conclusion = AsyncMock(
            return_value={"ok": True, "archived": True}
        )

        app = _make_app(conclusion_service=conclusion_service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/research/workspaces/{uuid4()}/conclusions/{uuid4()}")

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_list_conclusions_200(self):
        conclusion_service = _make_conclusion_service()
        conclusion_service.list_conclusions = AsyncMock(
            return_value={"items": [{"conclusion_id": str(uuid4()), "status": "active"}]}
        )

        app = _make_app(conclusion_service=conclusion_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/conclusions")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_save_as_conclusion_201(self):
        conclusion_service = _make_conclusion_service()
        conclusion_service.save_from_block = AsyncMock(
            return_value={"conclusion_id": str(uuid4()), "status": "active"}
        )

        app = _make_app(conclusion_service=conclusion_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/turns/{uuid4()}/save-conclusion",
            json={"statement": "表格结论", "block_type": "table"},
        )

        assert response.status_code == 201

    def test_save_as_conclusion_empty_422(self):
        conclusion_service = _make_conclusion_service()
        app = _make_app(conclusion_service=conclusion_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/turns/{uuid4()}/save-conclusion",
            json={"statement": "  ", "block_type": "table"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


# ===========================================================================
# 5. 分析
# ===========================================================================


class TestRunAnalysis:
    """POST /turns/{turn_id}/analyze"""

    def test_run_analysis_202(self):
        analysis_service = _make_analysis_service()
        analysis_service.submit_run = AsyncMock(
            return_value={"run_id": str(uuid4()), "status": "queued"}
        )

        app = _make_app(analysis_service=analysis_service)
        client = TestClient(app)

        with patch("packages.common.feature_flags.RESEARCH_ANALYSIS_ENABLED", True):
            response = client.post(f"/api/v1/research/workspaces/{uuid4()}/turns/{uuid4()}/analyze")

        assert response.status_code == 202
        assert response.json()["status"] == "queued"


# ===========================================================================
# 6. 提取文本
# ===========================================================================


class TestExtractText:
    """POST /extract-text"""

    def test_extract_text_200(self):
        app = _make_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/research/extract-text",
            data={"file": "hello world"},
        )

        assert response.status_code == 200
        assert response.json()["text"] == "hello world"
