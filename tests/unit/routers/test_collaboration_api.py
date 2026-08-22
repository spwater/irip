"""collaboration_router API 测试：协作参与者 CRUD + @人列表 + 退出对话。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user 和 get_ai_service
- Mock AIService 的 add_participant / list_participants / remove_participant /
  leave_conversation / resolve_dept_id / list_mentionable_users
- 验证 HTTP 状态码、响应体字段、错误码（404/403/409/422）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.collaboration import collaboration_router, get_ai_service
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有协作权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="user@irip.local",
        roles=roles or ["lab_member"],
        department_id=uuid4(),
        is_root_member=False,
    )


def _make_participant_ref(
    user_id: UUID | None = None,
    role: str = "member",
    display_name: str = "参与者",
) -> SimpleNamespace:
    """构造参与者引用。"""
    return SimpleNamespace(
        user_id=user_id or uuid4(),
        display_name=display_name,
        avatar_url=None,
        role=role,
        joined_at=datetime.now(UTC),
    )


def _make_mentionable_user(
    uid: UUID | None = None,
    display_name: str = "可@用户",
) -> SimpleNamespace:
    """构造可 @ 用户引用。"""
    return SimpleNamespace(
        id=uid or uuid4(),
        display_name=display_name,
        avatar_url=None,
        roles=["lab_member"],
    )


def _make_mock_service() -> MagicMock:
    """构造 mock AIService。"""
    service = MagicMock()
    service.add_participant = AsyncMock()
    service.list_participants = AsyncMock()
    service.remove_participant = AsyncMock()
    service.leave_conversation = AsyncMock()
    service.resolve_dept_id = AsyncMock(return_value=uuid4())
    service.list_mentionable_users = AsyncMock()
    return service


def _make_app(service: MagicMock, user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(collaboration_router)

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
# 1. POST /conversations/{id}/participants — 邀请成员
# ===========================================================================


class TestInviteParticipant:
    """POST /api/v1/collaboration/conversations/{id}/participants — 邀请成员。"""

    def test_invite_201(self):
        """邀请成功 → 201"""
        service = _make_mock_service()
        target_uid = uuid4()
        ref = _make_participant_ref(user_id=target_uid, display_name="新成员")
        service.add_participant = AsyncMock(return_value=ref)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/collaboration/conversations/{uuid4()}/participants",
            json={"user_id": str(target_uid)},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["display_name"] == "新成员"
        assert data["role"] == "member"

    def test_invite_invalid_user_id_422(self):
        """无效用户 ID → 422"""
        service = _make_mock_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/collaboration/conversations/{uuid4()}/participants",
            json={"user_id": "not-a-uuid"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"

    def test_invite_not_found_404(self):
        """对话不存在 → 404"""
        service = _make_mock_service()
        service.add_participant = AsyncMock(
            side_effect=AppError(code="not_found", message="对话不存在", retryable=False, fields={})
        )
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/collaboration/conversations/{uuid4()}/participants",
            json={"user_id": str(uuid4())},
        )

        assert response.status_code == 404

    def test_invite_conflict_409(self):
        """已是参与者 → 409"""
        service = _make_mock_service()
        service.add_participant = AsyncMock(
            side_effect=AppError(code="conflict", message="已是参与者", retryable=False, fields={})
        )
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/collaboration/conversations/{uuid4()}/participants",
            json={"user_id": str(uuid4())},
        )

        assert response.status_code == 409


# ===========================================================================
# 2. GET /conversations/{id}/participants — 列出参与者
# ===========================================================================


class TestListParticipants:
    """GET /api/v1/collaboration/conversations/{id}/participants — 列出参与者。"""

    def test_list_200(self):
        """列出成功 → 200"""
        service = _make_mock_service()
        refs = [
            _make_participant_ref(role="owner", display_name="所有者"),
            _make_participant_ref(role="member", display_name="成员"),
        ]
        service.list_participants = AsyncMock(return_value=refs)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/collaboration/conversations/{uuid4()}/participants")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["role"] == "owner"


# ===========================================================================
# 3. DELETE /conversations/{id}/participants/{uid} — 移除成员
# ===========================================================================


class TestRemoveParticipant:
    """DELETE /api/v1/collaboration/conversations/{id}/participants/{uid} — 移除成员。"""

    def test_remove_204(self):
        """移除成功 → 204"""
        service = _make_mock_service()
        service.remove_participant = AsyncMock(return_value=None)

        # lab_director has conversation:remove_member permission
        app = _make_app(service, user=_make_current_user(roles=["lab_director"]))
        client = TestClient(app)

        response = client.delete(
            f"/api/v1/collaboration/conversations/{uuid4()}/participants/{uuid4()}"
        )

        assert response.status_code == 204

    def test_remove_forbidden_403(self):
        """非 owner 移除 → 403"""
        service = _make_mock_service()
        service.remove_participant = AsyncMock(
            side_effect=AppError(code="forbidden", message="非 owner", retryable=False, fields={})
        )
        # lab_director has conversation:remove_member permission
        app = _make_app(service, user=_make_current_user(roles=["lab_director"]))
        client = TestClient(app)

        response = client.delete(
            f"/api/v1/collaboration/conversations/{uuid4()}/participants/{uuid4()}"
        )

        assert response.status_code == 403


# ===========================================================================
# 4. POST /conversations/{id}/leave — 退出对话
# ===========================================================================


class TestLeaveConversation:
    """POST /api/v1/collaboration/conversations/{id}/leave — 退出对话。"""

    def test_leave_204(self):
        """退出成功 → 204"""
        service = _make_mock_service()
        service.leave_conversation = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(f"/api/v1/collaboration/conversations/{uuid4()}/leave")

        assert response.status_code == 204

    def test_leave_owner_forbidden_403(self):
        """owner 退出 → 403"""
        service = _make_mock_service()
        service.leave_conversation = AsyncMock(
            side_effect=AppError(
                code="forbidden", message="owner 不能退出", retryable=False, fields={}
            )
        )
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(f"/api/v1/collaboration/conversations/{uuid4()}/leave")

        assert response.status_code == 403


# ===========================================================================
# 5. GET /mentionable-users — 可 @ 用户列表
# ===========================================================================


class TestMentionableUsers:
    """GET /api/v1/collaboration/mentionable-users — 可 @ 用户列表。"""

    def test_mentionable_200(self):
        """列出成功 → 200"""
        service = _make_mock_service()
        refs = [
            _make_mentionable_user(display_name="用户A"),
            _make_mentionable_user(display_name="用户B"),
        ]
        service.list_mentionable_users = AsyncMock(return_value=refs)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/collaboration/mentionable-users")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["items"][0]["display_name"] == "用户A"
