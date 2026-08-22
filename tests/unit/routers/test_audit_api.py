"""audit_router API 测试：审计事件查询 + 异步导出。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock 依赖
- 覆盖 get_current_user 和 get_audit_session_factory
- patch AuditQueryRepository.list_events 和 OutboxDispatcher.enqueue
- 验证 HTTP 状态码、响应体字段、错误码（422/invalid_cursor）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.audit import audit_router, get_audit_session_factory
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 audit:read 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="auditor@irip.local",
        roles=roles or ["platform_auditor"],
        department_id=uuid4(),
        is_root_member=False,
    )


def _make_audit_event(
    event_id=None,
    action: str = "department.create",
) -> SimpleNamespace:
    """构造 AuditEvent 实体。"""
    return SimpleNamespace(
        id=event_id or uuid4(),
        occurred_at=datetime.now(UTC),
        actor_user_id=uuid4(),
        department_id=uuid4(),
        action=action,
        resource_type="department",
        resource_id=uuid4(),
        payload={"key": "value"},
        ip="127.0.0.1",
        user_agent="test-agent",
    )


def _make_mock_session_factory():
    """构造 mock session factory（返回 async context manager）。"""
    mock_session = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=mock_ctx)
    return factory


def _make_app(
    session_factory: MagicMock | None = None,
    user: CurrentUser | None = None,
) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(audit_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_audit_session_factory] = lambda: (
        session_factory or _make_mock_session_factory()
    )

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. GET /api/v1/audit-events/ — 查询审计事件
# ===========================================================================


class TestListAuditEvents:
    """GET /api/v1/audit-events/ — 查询审计事件（分页）。"""

    def test_list_200(self):
        """查询成功 → 200"""
        events = [_make_audit_event(action="dept.create")]
        with patch(
            "apps.api.routers.audit.AuditQueryRepository.list_events",
            new_callable=AsyncMock,
            return_value=events,
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/audit-events/")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["action"] == "dept.create"
        assert data["has_more"] is False

    def test_list_with_filters_200(self):
        """带过滤参数查询 → 200"""
        with patch(
            "apps.api.routers.audit.AuditQueryRepository.list_events",
            new_callable=AsyncMock,
            return_value=[],
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/audit-events/?action=department.create&limit=20")

        assert response.status_code == 200

    def test_list_invalid_cursor_422(self):
        """无效游标 → 422"""
        app = _make_app()
        client = TestClient(app)

        response = client.get("/api/v1/audit-events/?cursor=not-a-date")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_cursor"


# ===========================================================================
# 2. POST /api/v1/audit-events/export — 创建审计导出作业
# ===========================================================================


class TestCreateAuditExport:
    """POST /api/v1/audit-events/export — 创建审计导出作业（异步）。"""

    def test_export_202(self):
        """导出作业创建成功 → 202"""
        mock_session = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("apps.api.routers.audit.session_scope", return_value=mock_ctx),
            patch("apps.api.routers.audit.OutboxDispatcher.enqueue", new_callable=AsyncMock),
            patch("apps.api.routers.audit.new_id", return_value=uuid4()),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.post(
                "/api/v1/audit-events/export",
                json={"format": "csv"},
            )

        assert response.status_code == 202
        data = response.json()
        assert data["kind"] == "audit_export"
        assert data["status"] == "accepted"

    def test_export_invalid_format_422(self):
        """不支持格式 → 422"""
        app = _make_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/audit-events/export",
            json={"format": "xml"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"
