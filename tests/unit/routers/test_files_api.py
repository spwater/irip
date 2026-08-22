"""files_router API 测试：文件浏览 + 上传。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user 和 get_artifact_service
- 文件浏览端点使用真实文件系统（临时目录），不涉及外部服务
- 上传端点 mock ArtifactService.put_bytes
- 验证 HTTP 状态码、响应体字段、错误码（422）
"""

import io
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.uploads import get_artifact_service
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 flow:read 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="user@irip.local",
        roles=roles or ["lab_member"],
        department_id=uuid4(),
        is_root_member=False,
    )


def _make_mock_artifact_service() -> MagicMock:
    """构造 mock ArtifactService。"""
    service = MagicMock()
    service.put_bytes = AsyncMock()
    return service


def _make_app(
    service: MagicMock | None = None,
    user: CurrentUser | None = None,
) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    from apps.api.routers.files import files_router

    app = FastAPI()
    app.include_router(files_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_artifact_service] = lambda: (
        service or _make_mock_artifact_service()
    )

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. GET /api/v1/files/browse — 文件浏览
# ===========================================================================


class TestBrowseFiles:
    """GET /api/v1/files/browse — 列出目录内容。"""

    def test_browse_root_200(self, tmp_path):
        """浏览根目录 → 200"""
        # 创建临时文件和目录
        (tmp_path / "file1.txt").write_text("hello")
        (tmp_path / "subdir").mkdir()
        (tmp_path / ".hidden").write_text("hidden")

        with patch("apps.api.routers.files._BROWSE_ROOT", str(tmp_path)):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/files/browse")

        assert response.status_code == 200
        data = response.json()
        names = [item["name"] for item in data["items"]]
        assert "file1.txt" in names
        assert "subdir" in names
        # 隐藏文件不返回
        assert ".hidden" not in names

    def test_browse_subdir_200(self, tmp_path):
        """浏览子目录 → 200"""
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "inner.txt").write_text("inner")

        with patch("apps.api.routers.files._BROWSE_ROOT", str(tmp_path)):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/files/browse?path=subdir")

        assert response.status_code == 200
        data = response.json()
        names = [item["name"] for item in data["items"]]
        assert "inner.txt" in names

    def test_browse_parent_path_200(self, tmp_path):
        """子目录有 parent_path → 200"""
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "inner.txt").write_text("inner")

        with patch("apps.api.routers.files._BROWSE_ROOT", str(tmp_path)):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/files/browse?path=subdir")

        assert response.status_code == 200
        data = response.json()
        assert data["parent_path"] is not None


# ===========================================================================
# 2. POST /api/v1/files/upload — 文件上传
# ===========================================================================


class TestUploadFile:
    """POST /api/v1/files/upload — 上传文件到 MinIO。"""

    def test_upload_200(self):
        """上传成功 → 200"""
        from types import SimpleNamespace

        service = _make_mock_artifact_service()
        ref = SimpleNamespace(artifact_id=uuid4())
        service.put_bytes = AsyncMock(return_value=ref)

        app = _make_app(service=service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("test.txt", io.BytesIO(b"hello world"), "text/plain")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "test.txt"
        assert data["size"] == 11

    def test_upload_unsupported_type_415(self):
        """不支持文件类型 → 415"""
        app = _make_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("test.exe", io.BytesIO(b"\x00\x01"), "application/x-msdownload")},
        )

        assert response.status_code == 415
        assert response.json()["error"]["code"] == "unsupported_media_type"
