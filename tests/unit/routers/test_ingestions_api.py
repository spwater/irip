"""ingestions_router API 测试：数据源预览。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user 和 get_ingestion_service
- Mock IngestionService.preview
- 验证 HTTP 状态码、响应体字段
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.ingestions import get_ingestion_service, ingestions_router
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError
from packages.connectors.contracts import PreviewTable

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 ingestion:write 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="user@irip.local",
        roles=roles or ["lab_member"],
        department_id=uuid4(),
        is_root_member=False,
    )


def _make_mock_service() -> MagicMock:
    """构造 mock IngestionService。"""
    service = MagicMock()
    service.preview = AsyncMock()
    return service


def _make_app(service: MagicMock, user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(ingestions_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_ingestion_service] = lambda: service

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. POST /api/v1/ingestions/preview — 预览数据源
# ===========================================================================


class TestPreviewSource:
    """POST /api/v1/ingestions/preview — 预览数据源。"""

    def test_preview_file_200(self):
        """文件数据源预览成功 → 200"""
        service = _make_mock_service()
        table = PreviewTable(
            columns=("col1", "col2"),
            rows=(("1", "2"), ("3", "4")),
            row_count=100,
        )
        service.preview = AsyncMock(return_value=table)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/ingestions/preview",
            json={
                "source": {
                    "kind": "file",
                    "file": {
                        "artifact_id": str(uuid4()),
                        "format": "csv",
                    },
                },
                "limit": 10,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["columns"] == ["col1", "col2"]
        assert len(data["rows"]) == 2
        assert data["rows"][0]["values"] == ["1", "2"]
        assert data["row_count"] == 100

    def test_preview_postgres_200(self):
        """PostgreSQL 数据源预览成功 → 200"""
        service = _make_mock_service()
        table = PreviewTable(
            columns=("id", "name"),
            rows=(("1", "测试"),),
            row_count=50,
        )
        service.preview = AsyncMock(return_value=table)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/ingestions/preview",
            json={
                "source": {
                    "kind": "postgres",
                    "postgres": {
                        "secret_id": str(uuid4()),
                        "query": "SELECT 1",
                    },
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["columns"] == ["id", "name"]
        assert data["row_count"] == 50

    def test_preview_missing_source_422(self):
        """缺少 source → 422"""
        service = _make_mock_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/ingestions/preview",
            json={"limit": 10},
        )

        assert response.status_code == 422
