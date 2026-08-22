"""ai_tools_router API 测试：AI 工具管理 CRUD + 启用/禁用。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock 依赖
- 覆盖 get_current_user，patch session_scope 和 ToolRepository 静态方法
- 使用 SimpleNamespace 构造 AIToolRow 避免 ORM 初始化
- 验证 HTTP 状态码、响应体字段、错误码（404/409/422）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.ai_tools import ai_tools_router, set_session_factory
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 system:manage 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="admin@irip.local",
        roles=roles or ["platform_administrator"],
        department_id=uuid4(),
        is_root_member=True,
    )


def _make_tool_row(
    name: str = "test_tool",
    display_name: str = "测试工具",
    description: str = "工具描述",
    required_permission: str = "fact:read",
    enabled: bool = True,
    lock_version: int = 0,
    category: str = "ai_tool",
) -> SimpleNamespace:
    """构造 AIToolRow（使用 SimpleNamespace 避免数据类冻结约束）。"""
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        display_name=display_name,
        description=description,
        required_permission=required_permission,
        parameters_schema={"type": "object"},
        enabled=enabled,
        lock_version=lock_version,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        updated_by=uuid4(),
        category=category,
    )


@pytest.fixture(autouse=True)
def _setup_session_factory():
    """设置 mock session factory（ai_tools 路由用模块全局 _get_session_factory）。"""
    set_session_factory(MagicMock())
    yield
    # 恢复为 None 避免影响其他测试
    set_session_factory(None)


def _make_app(user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(ai_tools_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


@pytest.fixture
def mock_session_ctx():
    """构造 mock async context manager，模拟 session_scope。"""
    mock_session = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return mock_ctx, mock_session


# ===========================================================================
# 1. GET /api/v1/ai-tools — 列出全部工具
# ===========================================================================


class TestListAITools:
    """GET /api/v1/ai-tools — 列出全部 AI 工具。"""

    def test_list_200(self, mock_session_ctx):
        """列表查询成功 → 200"""
        mock_ctx, _ = mock_session_ctx
        rows = [_make_tool_row(name="tool_a"), _make_tool_row(name="tool_b")]
        with (
            patch("apps.api.routers.ai_tools.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_tools.ToolRepository.list_all",
                new_callable=AsyncMock,
                return_value=rows,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/ai-tools")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] == "tool_a"


# ===========================================================================
# 2. GET /api/v1/ai-tools/unified — 统一工具列表
# ===========================================================================


class TestListUnifiedTools:
    """GET /api/v1/ai-tools/unified — 统一工具列表。"""

    def test_unified_200(self, mock_session_ctx):
        """统一列表成功 → 200"""
        mock_ctx, _ = mock_session_ctx
        rows = [
            _make_tool_row(name="z_tool", category="ai_tool"),
            _make_tool_row(name="a_tool", category="ingestion"),
        ]
        with (
            patch("apps.api.routers.ai_tools.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_tools.ToolRepository.list_all",
                new_callable=AsyncMock,
                return_value=rows,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/ai-tools/unified")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # 按名称排序
        assert data[0]["name"] == "a_tool"
        assert data[1]["name"] == "z_tool"


# ===========================================================================
# 3. GET /api/v1/ai-tools/ingestion/list — ingestion 分类工具列表
# ===========================================================================


class TestListIngestionTools:
    """GET /api/v1/ai-tools/ingestion/list — ingestion 分类工具列表。"""

    def test_ingestion_list_200(self, mock_session_ctx):
        """ingestion 列表成功 → 200，只返回 category=ingestion 的工具"""
        mock_ctx, _ = mock_session_ctx
        rows = [
            _make_tool_row(name="xrd_parser", category="ingestion"),
            _make_tool_row(name="ai_helper", category="ai_tool"),
            _make_tool_row(name="csv_parser", category="ingestion"),
        ]
        with (
            patch("apps.api.routers.ai_tools.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_tools.ToolRepository.list_all",
                new_callable=AsyncMock,
                return_value=rows,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/ai-tools/ingestion/list")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # 只包含 ingestion 分类的工具
        assert all(item["category"] == "ingestion" for item in data)


# ===========================================================================
# 4. GET /api/v1/ai-tools/{name} — 获取工具详情
# ===========================================================================


class TestGetAITool:
    """GET /api/v1/ai-tools/{name} — 获取工具详情。"""

    def test_get_200(self, mock_session_ctx):
        """详情查询成功 → 200"""
        mock_ctx, _ = mock_session_ctx
        row = _make_tool_row(name="my_tool", display_name="我的工具")
        with (
            patch("apps.api.routers.ai_tools.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_tools.ToolRepository.get_by_name",
                new_callable=AsyncMock,
                return_value=row,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/ai-tools/my_tool")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "my_tool"
        assert data["display_name"] == "我的工具"

    def test_get_not_found_404(self, mock_session_ctx):
        """工具不存在 → 404"""
        mock_ctx, _ = mock_session_ctx
        with (
            patch("apps.api.routers.ai_tools.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_tools.ToolRepository.get_by_name",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/ai-tools/nonexistent")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


# ===========================================================================
# 5. POST /api/v1/ai-tools — 新建工具
# ===========================================================================


class TestCreateAITool:
    """POST /api/v1/ai-tools — 新建工具。"""

    def test_create_201(self, mock_session_ctx):
        """创建成功 → 201"""
        mock_ctx, _ = mock_session_ctx
        row = _make_tool_row(name="new_tool", display_name="新工具", lock_version=0)
        with (
            patch("apps.api.routers.ai_tools.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_tools.ToolRepository.create",
                new_callable=AsyncMock,
                return_value=row,
            ),
            patch(
                "apps.api.routers.ai_tools._record_audit",
                new_callable=AsyncMock,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.post(
                "/api/v1/ai-tools",
                json={
                    "name": "new_tool",
                    "display_name": "新工具",
                    "description": "描述",
                    "required_permission": "fact:read",
                    "parameters_schema": {"type": "object"},
                },
            )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "new_tool"
        assert data["enabled"] is True

    def test_create_invalid_name_422(self, mock_session_ctx):
        """name 不符合 pattern → 422"""
        mock_ctx, _ = mock_session_ctx
        app = _make_app()
        client = TestClient(app)
        response = client.post(
            "/api/v1/ai-tools",
            json={
                "name": "Invalid-Name",
                "display_name": "名称",
                "description": "描述",
                "required_permission": "fact:read",
            },
        )
        assert response.status_code == 422


# ===========================================================================
# 6. PATCH /api/v1/ai-tools/{name} — 编辑工具（乐观锁）
# ===========================================================================


class TestUpdateAITool:
    """PATCH /api/v1/ai-tools/{name} — 编辑工具。"""

    def test_update_200(self, mock_session_ctx):
        """编辑成功 → 200"""
        mock_ctx, _ = mock_session_ctx
        before = _make_tool_row(name="edit_tool", display_name="旧名称")
        after = _make_tool_row(name="edit_tool", display_name="新名称", lock_version=1)
        with (
            patch("apps.api.routers.ai_tools.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_tools.ToolRepository.get_by_name",
                new_callable=AsyncMock,
                return_value=before,
            ),
            patch(
                "apps.api.routers.ai_tools.ToolRepository.update",
                new_callable=AsyncMock,
                return_value=after,
            ),
            patch(
                "apps.api.routers.ai_tools._record_audit",
                new_callable=AsyncMock,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.patch(
                "/api/v1/ai-tools/edit_tool",
                json={
                    "display_name": "新名称",
                    "description": "描述",
                    "required_permission": "fact:read",
                    "parameters_schema": {"type": "object"},
                    "lock_version": 0,
                },
            )

        assert response.status_code == 200
        assert response.json()["display_name"] == "新名称"

    def test_update_not_found_404(self, mock_session_ctx):
        """工具不存在 → 404"""
        mock_ctx, _ = mock_session_ctx
        with (
            patch("apps.api.routers.ai_tools.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_tools.ToolRepository.get_by_name",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.patch(
                "/api/v1/ai-tools/nonexistent",
                json={
                    "display_name": "名称",
                    "description": "描述",
                    "required_permission": "fact:read",
                    "parameters_schema": {},
                    "lock_version": 0,
                },
            )

        assert response.status_code == 404


# ===========================================================================
# 7. PATCH /api/v1/ai-tools/{name}/enabled — 启用/禁用工具
# ===========================================================================


class TestToggleAITool:
    """PATCH /api/v1/ai-tools/{name}/enabled — 启用/禁用。"""

    def test_toggle_disable_200(self, mock_session_ctx):
        """禁用成功 → 200"""
        mock_ctx, _ = mock_session_ctx
        before = _make_tool_row(name="toggle_tool", enabled=True)
        after = _make_tool_row(name="toggle_tool", enabled=False, lock_version=1)
        with (
            patch("apps.api.routers.ai_tools.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_tools.ToolRepository.get_by_name",
                new_callable=AsyncMock,
                return_value=before,
            ),
            patch(
                "apps.api.routers.ai_tools.ToolRepository.set_enabled",
                new_callable=AsyncMock,
                return_value=after,
            ),
            patch(
                "apps.api.routers.ai_tools._record_audit",
                new_callable=AsyncMock,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.patch(
                "/api/v1/ai-tools/toggle_tool/enabled",
                json={"enabled": False, "lock_version": 0},
            )

        assert response.status_code == 200
        assert response.json()["enabled"] is False

    def test_toggle_not_found_404(self, mock_session_ctx):
        """工具不存在 → 404"""
        mock_ctx, _ = mock_session_ctx
        with (
            patch("apps.api.routers.ai_tools.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_tools.ToolRepository.get_by_name",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.patch(
                "/api/v1/ai-tools/nonexistent/enabled",
                json={"enabled": True, "lock_version": 0},
            )

        assert response.status_code == 404
