"""showcase_router API 测试：橱窗卡片 CRUD + 排序 + 摘要生成。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user 和 get_ai_service（从 assistant 路由导入）
- Mock AIService 的 add_showcase_item / list_showcase_items / update_showcase_item /
  delete_showcase_item / reorder_showcase_items / generate_summary
- 验证 HTTP 状态码、响应体字段
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.assistant import get_ai_service
from apps.api.routers.showcase import showcase_router
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 assistant:use 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="user@irip.local",
        roles=roles or ["lab_member"],
        department_id=uuid4(),
        is_root_member=False,
    )


def _make_showcase_ref(
    item_id: UUID | None = None,
    conv_id: UUID | None = None,
    block_type: str = "echarts",
    title: str = "测试卡片",
) -> SimpleNamespace:
    """构造橱窗卡片引用。"""
    return SimpleNamespace(
        id=item_id or uuid4(),
        conversation_id=conv_id or uuid4(),
        sort_order=0,
        block_type=block_type,
        title=title,
        content_snapshot='{"data": []}',
        source_message_id=uuid4(),
        source_block_index=0,
        data_source={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_mock_service() -> MagicMock:
    """构造 mock AIService。"""
    service = MagicMock()
    service.add_showcase_item = AsyncMock()
    service.list_showcase_items = AsyncMock()
    service.update_showcase_item = AsyncMock()
    service.delete_showcase_item = AsyncMock()
    service.reorder_showcase_items = AsyncMock()
    service.generate_summary = AsyncMock()
    return service


def _make_app(service: MagicMock, user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(showcase_router)

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
# 1. POST /conversations/{id}/showcase — 添加橱窗卡片
# ===========================================================================


class TestCreateShowcaseItem:
    """POST /api/v1/assistant/conversations/{id}/showcase — 添加橱窗卡片。"""

    def test_create_201(self):
        """添加成功 → 201"""
        service = _make_mock_service()
        ref = _make_showcase_ref(title="图表卡片")
        service.add_showcase_item = AsyncMock(return_value=ref)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/assistant/conversations/{uuid4()}/showcase",
            json={
                "block_type": "echarts",
                "title": "图表卡片",
                "content_snapshot": '{"data": []}',
                "source_message_id": str(uuid4()),
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "图表卡片"
        assert data["block_type"] == "echarts"

    def test_create_missing_content_422(self):
        """缺少 content_snapshot → 422"""
        service = _make_mock_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/assistant/conversations/{uuid4()}/showcase",
            json={
                "block_type": "echarts",
                "source_message_id": str(uuid4()),
            },
        )

        assert response.status_code == 422


# ===========================================================================
# 2. GET /conversations/{id}/showcase — 列出橱窗卡片
# ===========================================================================


class TestListShowcaseItems:
    """GET /api/v1/assistant/conversations/{id}/showcase — 列出橱窗卡片。"""

    def test_list_200(self):
        """列出成功 → 200"""
        service = _make_mock_service()
        refs = [
            _make_showcase_ref(title="卡片1"),
            _make_showcase_ref(title="卡片2"),
        ]
        service.list_showcase_items = AsyncMock(return_value=refs)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/assistant/conversations/{uuid4()}/showcase")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2


# ===========================================================================
# 3. PATCH /showcase/{item_id} — 更新卡片标题
# ===========================================================================


class TestUpdateShowcaseItem:
    """PATCH /api/v1/assistant/showcase/{item_id} — 更新卡片标题。"""

    def test_update_200(self):
        """更新成功 → 200"""
        service = _make_mock_service()
        ref = _make_showcase_ref(title="新标题")
        service.update_showcase_item = AsyncMock(return_value=ref)

        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/assistant/showcase/{uuid4()}",
            json={"title": "新标题"},
        )

        assert response.status_code == 200
        assert response.json()["title"] == "新标题"


# ===========================================================================
# 4. DELETE /showcase/{item_id} — 删除卡片
# ===========================================================================


class TestDeleteShowcaseItem:
    """DELETE /api/v1/assistant/showcase/{item_id} — 删除卡片。"""

    def test_delete_204(self):
        """删除成功 → 204"""
        service = _make_mock_service()
        service.delete_showcase_item = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/assistant/showcase/{uuid4()}")

        assert response.status_code == 204


# ===========================================================================
# 5. PATCH /conversations/{id}/showcase/reorder — 批量重排序
# ===========================================================================


class TestReorderShowcase:
    """PATCH /api/v1/assistant/conversations/{id}/showcase/reorder — 批量重排序。"""

    def test_reorder_200(self):
        """重排序成功 → 200"""
        service = _make_mock_service()
        service.reorder_showcase_items = AsyncMock(return_value=None)

        app = _make_app(service)
        client = TestClient(app)

        ids = [str(uuid4()), str(uuid4())]
        response = client.patch(
            f"/api/v1/assistant/conversations/{uuid4()}/showcase/reorder",
            json={"item_ids": ids},
        )

        assert response.status_code == 200
        assert response.json()["reordered"] == "true"

    def test_reorder_empty_list_422(self):
        """空列表 → 422"""
        service = _make_mock_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/assistant/conversations/{uuid4()}/showcase/reorder",
            json={"item_ids": []},
        )

        assert response.status_code == 422


# ===========================================================================
# 6. POST /conversations/{id}/summary — 生成摘要
# ===========================================================================


class TestGenerateSummary:
    """POST /api/v1/assistant/conversations/{id}/summary — 生成摘要。"""

    def test_summary_200(self):
        """生成摘要成功 → 200"""
        service = _make_mock_service()
        service.generate_summary = AsyncMock(return_value=("## 分析摘要\n内容...", 3))

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(f"/api/v1/assistant/conversations/{uuid4()}/summary")

        assert response.status_code == 200
        data = response.json()
        assert "分析摘要" in data["markdown"]
        assert data["item_count"] == 3
