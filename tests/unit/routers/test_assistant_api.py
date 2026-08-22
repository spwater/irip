"""assistant_router API 测试：对话管理 + 消息 + Provider 状态。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user、get_ai_service
- Mock AIService
- 验证 HTTP 状态码、响应体字段、错误码（404/422/403）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.assistant import assistant_router, get_ai_service
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


def _make_conversation_ref(
    conv_id: UUID | None = None,
    user_id: UUID | None = None,
    title: str = "测试对话",
    provider_mode: str = "offline",
    pinned: bool = False,
    archived: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=conv_id or uuid4(),
        user_id=user_id or uuid4(),
        title=title,
        provider_mode=provider_mode,
        pinned=pinned,
        archived=archived,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        system_context=None,
        participants=[],
    )


def _make_message_ref(
    msg_id: UUID | None = None,
    conv_id: UUID | None = None,
    role: str = "user",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=msg_id or uuid4(),
        conversation_id=conv_id or uuid4(),
        role=role,
        content="测试消息",
        tool_calls=[],
        citations=[],
        uncertainty=None,
        created_at=datetime.now(UTC),
        mentions=[],
        sender_user_id=None,
        sender_display_name=None,
        sender_avatar_url=None,
    )


def _make_ask_response(
    conv_id: UUID | None = None,
    answer: str = "AI回答",
) -> SimpleNamespace:
    return SimpleNamespace(
        answer=answer,
        tool_calls=[],
        citations=[],
        uncertainty=None,
        provider_mode="offline",
    )


def _make_mock_service() -> MagicMock:
    service = MagicMock()
    service.resolve_dept_id = AsyncMock(return_value=uuid4())
    service.create_conversation = AsyncMock()
    service.list_conversations = AsyncMock()
    service.search_conversations = AsyncMock()
    service.list_conversations_with_tab = AsyncMock()
    service.toggle_pin = AsyncMock()
    service.toggle_archive = AsyncMock()
    service.get_conversation = AsyncMock()
    service.delete_conversation = AsyncMock()
    service.cancel_request = MagicMock(return_value=True)
    service.ask = AsyncMock()
    service.list_messages = AsyncMock()
    service.reload_tools = AsyncMock()
    service.get_provider_status = MagicMock()
    return service


def _make_app(service: MagicMock, user: CurrentUser | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(assistant_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_ai_service] = lambda: service

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. 创建对话
# ===========================================================================


class TestCreateConversation:
    """POST /api/v1/assistant/conversations"""

    def test_create_201(self):
        service = _make_mock_service()
        ref = _make_conversation_ref()
        service.create_conversation = AsyncMock(return_value=ref)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/assistant/conversations",
            json={"title": "测试对话", "provider_mode": "offline"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "测试对话"
        assert data["provider_mode"] == "offline"

    def test_create_default_title(self):
        service = _make_mock_service()
        ref = _make_conversation_ref(title="")
        service.create_conversation = AsyncMock(return_value=ref)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/assistant/conversations",
            json={},
        )

        assert response.status_code == 201


# ===========================================================================
# 2. 列表对话
# ===========================================================================


class TestListConversations:
    """GET /api/v1/assistant/conversations"""

    def test_list_200(self):
        service = _make_mock_service()
        refs = [_make_conversation_ref() for _ in range(2)]
        service.list_conversations = AsyncMock(return_value=refs)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/assistant/conversations")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_list_with_keyword_search(self):
        service = _make_mock_service()
        service.search_conversations = AsyncMock(return_value=[_make_conversation_ref()])

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/assistant/conversations?keyword=测试")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_list_with_tab_filter(self):
        service = _make_mock_service()
        service.list_conversations_with_tab = AsyncMock(return_value=[_make_conversation_ref()])

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/assistant/conversations?tab=private")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1


# ===========================================================================
# 3. 置顶 / 归档 / 取消 / 删除
# ===========================================================================


class TestConversationActions:
    """PATCH pin / PATCH archive / POST cancel / DELETE"""

    def test_toggle_pin_200(self):
        service = _make_mock_service()
        ref = _make_conversation_ref(pinned=True)
        service.toggle_pin = AsyncMock(return_value=None)
        service.get_conversation = AsyncMock(return_value=ref)

        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(f"/api/v1/assistant/conversations/{uuid4()}/pin")

        assert response.status_code == 200
        assert response.json()["pinned"] is True

    def test_toggle_pin_not_found_404(self):
        service = _make_mock_service()
        service.toggle_pin = AsyncMock(return_value=None)
        service.get_conversation = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(f"/api/v1/assistant/conversations/{uuid4()}/pin")

        assert response.status_code == 404

    def test_toggle_archive_200(self):
        service = _make_mock_service()
        ref = _make_conversation_ref(archived=True)
        service.toggle_archive = AsyncMock(return_value=None)
        service.get_conversation = AsyncMock(return_value=ref)

        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(f"/api/v1/assistant/conversations/{uuid4()}/archive")

        assert response.status_code == 200
        assert response.json()["archived"] is True

    def test_cancel_request_200(self):
        service = _make_mock_service()
        service.cancel_request = MagicMock(return_value=True)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(f"/api/v1/assistant/conversations/{uuid4()}/cancel")

        assert response.status_code == 200
        assert response.json()["cancelled"] == "true"

    def test_delete_conversation_204(self):
        service = _make_mock_service()
        service.delete_conversation = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/assistant/conversations/{uuid4()}")

        assert response.status_code == 204

    def test_delete_conversation_not_found_404(self):
        service = _make_mock_service()
        service.delete_conversation = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/assistant/conversations/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 4. 发送消息
# ===========================================================================


class TestSendMessage:
    """POST /conversations/{id}/messages"""

    def test_send_message_200(self):
        service = _make_mock_service()
        resp = _make_ask_response()
        service.ask = AsyncMock(return_value=resp)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/assistant/conversations/{uuid4()}/messages",
            json={"question": "你好"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["answer"] == "AI回答"
        assert data["provider_mode"] == "offline"

    def test_send_message_empty_question_422(self):
        service = _make_mock_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/assistant/conversations/{uuid4()}/messages",
            json={"question": ""},
        )

        assert response.status_code == 422

    def test_send_message_not_found_404(self):
        service = _make_mock_service()
        service.ask = AsyncMock(
            side_effect=AppError(code="not_found", message="对话不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/assistant/conversations/{uuid4()}/messages",
            json={"question": "你好"},
        )

        assert response.status_code == 404


# ===========================================================================
# 5. 消息列表
# ===========================================================================


class TestListMessages:
    """GET /conversations/{id}/messages"""

    def test_list_messages_200(self):
        service = _make_mock_service()
        refs = [_make_message_ref() for _ in range(3)]
        service.list_messages = AsyncMock(return_value=refs)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/assistant/conversations/{uuid4()}/messages")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 3

    def test_list_messages_forbidden_403(self):
        service = _make_mock_service()
        service.list_messages = AsyncMock(
            side_effect=AppError(code="forbidden", message="无权访问", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/assistant/conversations/{uuid4()}/messages")

        assert response.status_code == 403


# ===========================================================================
# 6. Provider 状态
# ===========================================================================


class TestProviderStatus:
    """GET /provider-status"""

    def test_get_provider_status_200(self):
        service = _make_mock_service()
        service.reload_tools = AsyncMock(return_value=None)
        service.get_provider_status = MagicMock(
            return_value={
                "provider_mode": "offline",
                "whitelist_tools": [
                    {
                        "name": "search",
                        "display_name": "搜索",
                        "description": "搜索工具",
                        "required_permission": "standard:read",
                    }
                ],
                "candidate_tools": [],
            }
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/assistant/provider-status")

        assert response.status_code == 200
        data = response.json()
        assert data["provider_mode"] == "offline"
        assert len(data["whitelist_tools"]) == 1
        assert data["whitelist_tools"][0]["name"] == "search"
        assert len(data["candidate_tools"]) == 0


# ===========================================================================
# 7. 模型验证
# ===========================================================================


class TestRequestModels:
    """验证 Pydantic 请求模型。"""

    def test_create_conversation_request_defaults(self):
        from apps.api.routers.assistant import CreateConversationRequest

        body = CreateConversationRequest()
        assert body.title == ""
        assert body.provider_mode == "offline"

    def test_send_message_request_defaults(self):
        from apps.api.routers.assistant import SendMessageRequest

        body = SendMessageRequest(question="你好")
        assert body.provider_name == "offline"
        assert body.thinking_enabled is False
        assert body.mentions == []
