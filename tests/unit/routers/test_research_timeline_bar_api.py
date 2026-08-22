"""research_timeline_bar 路由测试：结论栏 / 发布 / 结果管理。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user 及 ConclusionBarServiceDep 依赖
- Mock ConclusionBarService
- 验证 HTTP 状态码、响应体字段、错误码（404/422）
- 共用 research_timeline_router
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

# Import bar module so its @research_timeline_router decorators register routes
import apps.api.routers.research_timeline_bar  # noqa: F401
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.research_timeline import research_timeline_router
from apps.api.routers.timeline_dependencies import get_conclusion_bar_service
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


def _make_bar_item_ref(
    item_id: UUID | None = None,
    workspace_id: UUID | None = None,
    turn_id: UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(item_id or uuid4()),
        workspace_id=str(workspace_id or uuid4()),
        turn_id=str(turn_id or uuid4()),
        block_type="table",
        title="表格1",
        content_snapshot={"columns": ["A", "B"]},
        source_info={"block_index": 0},
        created_at=datetime.now(UTC).isoformat(),
    )


def _make_bar_service() -> MagicMock:
    service = MagicMock()
    service.list_items = AsyncMock()
    service.push_item = AsyncMock()
    service.remove_item = AsyncMock()
    service.assemble_final_conclusion = AsyncMock()
    service.publish_conclusion = AsyncMock()
    service.list_results = AsyncMock()
    service.get_result_detail = AsyncMock()
    service.withdraw_result = AsyncMock()
    service.republish_result = AsyncMock()
    service.delete_result = AsyncMock()
    return service


def _make_app(service: MagicMock, user: CurrentUser | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(research_timeline_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_conclusion_bar_service] = lambda: service

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. 结论栏
# ===========================================================================


class TestBarItems:
    """GET / PUT / DELETE bar items"""

    def test_list_bar_items_200(self):
        service = _make_bar_service()
        item = _make_bar_item_ref()
        service.list_items = AsyncMock(
            return_value={
                "items": [
                    item.__dict__
                    if hasattr(item, "__dict__")
                    else {
                        "id": item.id,
                        "workspace_id": item.workspace_id,
                        "turn_id": item.turn_id,
                        "block_type": item.block_type,
                        "title": item.title,
                        "content_snapshot": item.content_snapshot,
                        "source_info": item.source_info,
                        "created_at": item.created_at,
                    }
                ]
            }
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/conclusion-bar/items")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_push_bar_item_201(self):
        service = _make_bar_service()
        ref = _make_bar_item_ref()
        service.push_item = AsyncMock(return_value=ref)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/turns/{uuid4()}/conclusion-bar/items",
            json={
                "block_type": "table",
                "title": "表格1",
                "content_snapshot": {"columns": ["A"]},
                "block_index": 0,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["block_type"] == "table"
        assert data["title"] == "表格1"

    def test_push_bar_item_missing_fields_422(self):
        service = _make_bar_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/turns/{uuid4()}/conclusion-bar/items",
            json={"block_type": "table", "title": "表格"},
        )

        assert response.status_code == 422

    def test_remove_bar_item_200(self):
        service = _make_bar_service()
        service.remove_item = AsyncMock(return_value={"ok": True})

        app = _make_app(service)
        client = TestClient(app)

        response = client.delete(
            f"/api/v1/research/workspaces/{uuid4()}/conclusion-bar/items/{uuid4()}"
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True


# ===========================================================================
# 2. 组装最终结论
# ===========================================================================


class TestFinalize:
    """POST /conclusion-bar/finalize"""

    def test_finalize_201(self):
        service = _make_bar_service()
        service.assemble_final_conclusion = AsyncMock(
            return_value={"result_id": str(uuid4()), "statement": "最终结论", "item_count": 2}
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/conclusion-bar/finalize",
            json={"item_ids": [str(uuid4()), str(uuid4())], "idempotency_key": "key"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["item_count"] == 2
        assert data["statement"] == "最终结论"

    def test_finalize_empty_items_422(self):
        service = _make_bar_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/conclusion-bar/finalize",
            json={"item_ids": [], "idempotency_key": "key"},
        )

        assert response.status_code == 422


# ===========================================================================
# 3. 发布结论
# ===========================================================================


class TestPublishConclusion:
    """POST /conclusions/{id}/publish"""

    def test_publish_conclusion_201(self):
        service = _make_bar_service()
        service.publish_conclusion = AsyncMock(
            return_value={"result_id": str(uuid4()), "version_number": 1}
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/conclusions/{uuid4()}/publish",
            json={"idempotency_key": "key"},
        )

        assert response.status_code == 201
        assert response.json()["version_number"] == 1

    def test_publish_conclusion_missing_key_422(self):
        service = _make_bar_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/conclusions/{uuid4()}/publish",
            json={},
        )

        assert response.status_code == 422


# ===========================================================================
# 4. 结果管理
# ===========================================================================


class TestResults:
    """GET results / GET detail / PATCH withdraw / PATCH publish / DELETE"""

    def test_list_results_200(self):
        service = _make_bar_service()
        service.list_results = AsyncMock(
            return_value={
                "items": [
                    {
                        "id": str(uuid4()),
                        "name": "结果1",
                        "status": "published",
                        "current_version": 1,
                        "created_at": "2024-01-01T00:00:00",
                    }
                ]
            }
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/conclusion-results")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_get_result_detail_200(self):
        service = _make_bar_service()
        service.get_result_detail = AsyncMock(
            return_value={
                "id": str(uuid4()),
                "name": "结果1",
                "status": "published",
                "current_version": 1,
                "created_at": "2024-01-01T00:00:00",
                "version": {
                    "version_number": 1,
                    "title": "v1",
                    "summary": {"text": "摘要"},
                    "source_conclusion_id": str(uuid4()),
                    "published_at": "2024-01-01T00:00:00",
                    "status": "published",
                },
            }
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/conclusion-results/{uuid4()}")

        assert response.status_code == 200
        data = response.json()
        assert data["version"]["version_number"] == 1

    def test_withdraw_result_200(self):
        service = _make_bar_service()
        service.withdraw_result = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/research/workspaces/{uuid4()}/conclusion-results/{uuid4()}/withdraw"
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_republish_result_200(self):
        service = _make_bar_service()
        service.republish_result = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/research/workspaces/{uuid4()}/conclusion-results/{uuid4()}/publish"
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_delete_result_200(self):
        service = _make_bar_service()
        service.delete_result = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        response = client.delete(
            f"/api/v1/research/workspaces/{uuid4()}/conclusion-results/{uuid4()}"
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_delete_result_not_found_404(self):
        service = _make_bar_service()
        service.delete_result = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.delete(
            f"/api/v1/research/workspaces/{uuid4()}/conclusion-results/{uuid4()}"
        )

        assert response.status_code == 404


# ===========================================================================
# 5. 模型验证
# ===========================================================================


class TestRequestModels:
    """验证 Pydantic 请求模型。"""

    def test_push_bar_item_request_validation(self):
        from apps.api.routers.research_timeline_bar import PushBarItemRequest

        body = PushBarItemRequest(
            block_type="table",
            title="标题",
            content_snapshot={"data": "test"},
            block_index=0,
        )
        assert body.block_type == "table"
        assert body.source_info == {}

    def test_finalize_request_validation(self):
        from apps.api.routers.research_timeline_bar import FinalizeRequest

        body = FinalizeRequest(
            item_ids=[str(uuid4())],
            idempotency_key="key",
        )
        assert body.title is None
        assert len(body.item_ids) == 1

    def test_publish_conclusion_request_defaults(self):
        from apps.api.routers.research_timeline_bar import PublishConclusionRequest

        body = PublishConclusionRequest(idempotency_key="key")
        assert body.title is None
