"""research_router API 测试：研究工作空间 CRUD + 证据 + 快照 + Fact 搜索。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user、get_workspace_service、get_snapshot_service
- Mock WorkspaceService 和 EvidenceSnapshotService
- 使用真实 dataclass（WorkspaceRef、EvidenceRefDTO 等）构造返回值
- 验证 HTTP 状态码、响应体字段
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.research import (
    get_snapshot_service,
    get_workspace_service,
    research_router,
)
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError
from packages.research.dtos import (
    EvidenceRefDTO,
    FactSummary,
    SnapshotRef,
    WorkspaceDetail,
    WorkspaceRef,
)

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 research:use 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="researcher@irip.local",
        roles=roles or ["lab_member"],
        department_id=uuid4(),
        is_root_member=False,
    )


def _make_workspace_ref(
    workspace_id=None,
    name: str = "测试工作空间",
    status: str = "draft",
) -> WorkspaceRef:
    """构造 WorkspaceRef。"""
    return WorkspaceRef(
        workspace_id=workspace_id or uuid4(),
        name=name,
        status=status,
        latest_snapshot_number=None,
        turn_count=0,
        active_run_status=None,
    )


def _make_workspace_detail(
    workspace_id=None,
    name: str = "详情工作空间",
    status: str = "draft",
    evidence_count: int = 2,
) -> WorkspaceDetail:
    """构造 WorkspaceDetail。"""
    return WorkspaceDetail(
        workspace_id=workspace_id or uuid4(),
        name=name,
        status=status,
        evidence_count=evidence_count,
        snapshots=[],
        latest_snapshot_number=None,
        turn_count=0,
        active_run_status=None,
    )


def _make_evidence_ref(
    ref_id=None,
    source_namespace: str = "core:fact",
) -> EvidenceRefDTO:
    """构造 EvidenceRefDTO。"""
    return EvidenceRefDTO(
        ref_id=ref_id or uuid4(),
        source_namespace=source_namespace,
        source_id=uuid4(),
        source_version=None,
        source_name="测试证据",
        status="active",
    )


def _make_snapshot_ref(
    snapshot_id=None,
    snapshot_number: int = 1,
) -> SnapshotRef:
    """构造 SnapshotRef。"""
    return SnapshotRef(
        snapshot_id=snapshot_id or uuid4(),
        snapshot_number=snapshot_number,
        content_hash="sha256:abc",
        captured_at=datetime.now(UTC),
    )


def _make_fact_summary(
    fact_id=None,
    fact_type: str = "experiment_run",
) -> FactSummary:
    """构造 FactSummary。"""
    return FactSummary(
        fact_id=fact_id or uuid4(),
        fact_type=fact_type,
        subject_id="subject_001",
        status="active",
        department_name="测试部门",
    )


def _make_mock_workspace_service() -> MagicMock:
    """构造 mock WorkspaceService。"""
    service = MagicMock()
    service.create_workspace = AsyncMock()
    service.list_workspaces = AsyncMock()
    service.get_workspace = AsyncMock()
    service.update_workspace_name = AsyncMock()
    service.delete_workspace = AsyncMock()
    service.archive_workspace = AsyncMock()
    service.restore_workspace = AsyncMock()
    service.add_evidence = AsyncMock()
    service.remove_evidence = AsyncMock()
    service.list_evidence = AsyncMock()
    service.search_facts = AsyncMock()
    return service


def _make_mock_snapshot_service() -> MagicMock:
    """构造 mock EvidenceSnapshotService。"""
    service = MagicMock()
    service.freeze_snapshot = AsyncMock()
    service.list_snapshots = AsyncMock()
    return service


def _make_app(
    ws_service: MagicMock | None = None,
    snap_service: MagicMock | None = None,
    user: CurrentUser | None = None,
) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(research_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_workspace_service] = lambda: (
        ws_service or _make_mock_workspace_service()
    )
    app.dependency_overrides[get_snapshot_service] = lambda: (
        snap_service or _make_mock_snapshot_service()
    )

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. POST /api/v1/research/workspaces — 创建工作空间
# ===========================================================================


class TestCreateWorkspace:
    """POST /api/v1/research/workspaces — 创建工作空间。"""

    def test_create_201(self):
        """创建成功 → 201"""
        service = _make_mock_workspace_service()
        ref = _make_workspace_ref(name="新工作空间")
        service.create_workspace = AsyncMock(return_value=ref)

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/research/workspaces",
            json={"name": "新工作空间"},
        )

        assert response.status_code == 201
        assert response.json()["name"] == "新工作空间"

    def test_create_missing_name_422(self):
        """缺少 name → 422"""
        service = _make_mock_workspace_service()
        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.post("/api/v1/research/workspaces", json={})

        assert response.status_code == 422


# ===========================================================================
# 2. GET /api/v1/research/workspaces — 列表
# ===========================================================================


class TestListWorkspaces:
    """GET /api/v1/research/workspaces — 分页列表。"""

    def test_list_200(self):
        """列表查询成功 → 200"""
        service = _make_mock_workspace_service()
        refs = [_make_workspace_ref(name="空间A"), _make_workspace_ref(name="空间B")]
        service.list_workspaces = AsyncMock(return_value=(refs, None))

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.get("/api/v1/research/workspaces")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["next_cursor"] is None

    def test_list_with_status_filter(self):
        """状态筛选 → 200"""
        service = _make_mock_workspace_service()
        service.list_workspaces = AsyncMock(return_value=([], None))

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.get("/api/v1/research/workspaces?status=archived")

        assert response.status_code == 200
        call_kwargs = service.list_workspaces.call_args.kwargs
        assert call_kwargs.get("status") == "archived"


# ===========================================================================
# 3. GET /api/v1/research/workspaces/{id} — 详情
# ===========================================================================


class TestGetWorkspace:
    """GET /api/v1/research/workspaces/{id} — 工作空间详情。"""

    def test_get_200(self):
        """详情查询成功 → 200"""
        service = _make_mock_workspace_service()
        detail = _make_workspace_detail(name="详情空间", evidence_count=3)
        service.get_workspace = AsyncMock(return_value=detail)

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{detail.workspace_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "详情空间"
        assert data["evidence_count"] == 3

    def test_get_not_found_404(self):
        """不存在 → 404"""
        service = _make_mock_workspace_service()
        service.get_workspace = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 4. PATCH /api/v1/research/workspaces/{id} — 更新名称
# ===========================================================================


class TestUpdateWorkspace:
    """PATCH /api/v1/research/workspaces/{id} — 更新名称。"""

    def test_update_200(self):
        """更新成功 → 200"""
        service = _make_mock_workspace_service()
        ref = _make_workspace_ref(name="新名称")
        service.update_workspace_name = AsyncMock(return_value=ref)

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.patch(
            f"/api/v1/research/workspaces/{ref.workspace_id}",
            json={"name": "新名称"},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "新名称"


# ===========================================================================
# 5. DELETE /api/v1/research/workspaces/{id} — 删除
# ===========================================================================


class TestDeleteWorkspace:
    """DELETE /api/v1/research/workspaces/{id} — 删除。"""

    def test_delete_204(self):
        """删除成功 → 204"""
        service = _make_mock_workspace_service()
        service.delete_workspace = AsyncMock(return_value=None)

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/research/workspaces/{uuid4()}")

        assert response.status_code == 204


# ===========================================================================
# 6. POST /api/v1/research/workspaces/{id}/archive — 归档
# ===========================================================================


class TestArchiveWorkspace:
    """POST /api/v1/research/workspaces/{id}/archive — 归档。"""

    def test_archive_204(self):
        """归档成功 → 204"""
        service = _make_mock_workspace_service()
        service.archive_workspace = AsyncMock(return_value=None)

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.post(f"/api/v1/research/workspaces/{uuid4()}/archive")

        assert response.status_code == 204


# ===========================================================================
# 7. POST /api/v1/research/workspaces/{id}/restore — 恢复
# ===========================================================================


class TestRestoreWorkspace:
    """POST /api/v1/research/workspaces/{id}/restore — 恢复。"""

    def test_restore_204(self):
        """恢复成功 → 204"""
        service = _make_mock_workspace_service()
        service.restore_workspace = AsyncMock(return_value=None)

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.post(f"/api/v1/research/workspaces/{uuid4()}/restore")

        assert response.status_code == 204


# ===========================================================================
# 8. POST /api/v1/research/workspaces/{id}/evidence — 加入证据
# ===========================================================================


class TestAddEvidence:
    """POST /api/v1/research/workspaces/{id}/evidence — 加入证据。"""

    def test_add_evidence_201(self):
        """加入证据成功 → 201"""
        service = _make_mock_workspace_service()
        ref = _make_evidence_ref()
        service.add_evidence = AsyncMock(return_value=ref)

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/research/workspaces/{uuid4()}/evidence",
            json={
                "source_namespace": "core:fact",
                "source_id": str(uuid4()),
            },
        )

        assert response.status_code == 201
        assert response.json()["source_namespace"] == "core:fact"


# ===========================================================================
# 9. DELETE /api/v1/research/workspaces/{id}/evidence/{ref_id} — 移除证据
# ===========================================================================


class TestRemoveEvidence:
    """DELETE /api/v1/research/workspaces/{id}/evidence/{ref_id} — 移除证据。"""

    def test_remove_evidence_204(self):
        """移除证据成功 → 204"""
        service = _make_mock_workspace_service()
        service.remove_evidence = AsyncMock(return_value=None)

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.delete(f"/api/v1/research/workspaces/{uuid4()}/evidence/{uuid4()}")

        assert response.status_code == 204


# ===========================================================================
# 10. GET /api/v1/research/workspaces/{id}/evidence — 证据列表
# ===========================================================================


class TestListEvidence:
    """GET /api/v1/research/workspaces/{id}/evidence — 证据列表。"""

    def test_list_evidence_200(self):
        """证据列表成功 → 200"""
        service = _make_mock_workspace_service()
        refs = [_make_evidence_ref(), _make_evidence_ref()]
        service.list_evidence = AsyncMock(return_value=refs)

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/evidence")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2


# ===========================================================================
# 11. POST /api/v1/research/workspaces/{id}/snapshot — 冻结快照
# ===========================================================================


class TestFreezeSnapshot:
    """POST /api/v1/research/workspaces/{id}/snapshot — 冻结快照。"""

    def test_freeze_snapshot_201(self):
        """冻结成功 → 201"""
        snap_service = _make_mock_snapshot_service()
        ref = _make_snapshot_ref(snapshot_number=1)
        snap_service.freeze_snapshot = AsyncMock(return_value=ref)

        app = _make_app(snap_service=snap_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/research/workspaces/{uuid4()}/snapshot")

        assert response.status_code == 201
        data = response.json()
        assert data["snapshot_number"] == 1
        assert data["content_hash"] == "sha256:abc"


# ===========================================================================
# 12. GET /api/v1/research/workspaces/{id}/snapshots — 快照列表
# ===========================================================================


class TestListSnapshots:
    """GET /api/v1/research/workspaces/{id}/snapshots — 快照列表。"""

    def test_list_snapshots_200(self):
        """快照列表成功 → 200"""
        snap_service = _make_mock_snapshot_service()
        refs = [_make_snapshot_ref(snapshot_number=1), _make_snapshot_ref(snapshot_number=2)]
        snap_service.list_snapshots = AsyncMock(return_value=refs)

        app = _make_app(snap_service=snap_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/research/workspaces/{uuid4()}/snapshots")

        assert response.status_code == 200
        assert len(response.json()["items"]) == 2


# ===========================================================================
# 13. GET /api/v1/research/facts/search — Fact 搜索
# ===========================================================================


class TestSearchFacts:
    """GET /api/v1/research/facts/search — 搜索 Fact。"""

    def test_search_200(self):
        """搜索成功 → 200"""
        service = _make_mock_workspace_service()
        summaries = [_make_fact_summary(), _make_fact_summary()]
        service.search_facts = AsyncMock(return_value=(summaries, None))

        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.get("/api/v1/research/facts/search?q=测试")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 2
        assert data["next_cursor"] is None

    def test_search_missing_query_422(self):
        """缺少 q 参数 → 422"""
        service = _make_mock_workspace_service()
        app = _make_app(ws_service=service)
        client = TestClient(app)

        response = client.get("/api/v1/research/facts/search")

        assert response.status_code == 422
