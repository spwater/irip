"""jobs_router API 测试：作业提交、查询、取消、列表、详情、重试。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_current_user 和 get_job_service
- Mock JobService 的 accept / get / request_cancel / list / get_raw / get_created_by_name
- 验证 HTTP 状态码、响应体字段、错误码（404/409/422）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.jobs import get_job_service, jobs_router
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError
from packages.jobs.entities import JobRef, JobStatus

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有作业权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="user@irip.local",
        roles=roles or ["lab_member"],
        department_id=uuid4(),
        is_root_member=False,
    )


def _make_job_ref(
    job_id: UUID | None = None,
    status: JobStatus = JobStatus.ACCEPTED,
    kind: str = "flow_execute",
    stage: str = "",
    progress: int = 0,
    retryable: bool = False,
) -> JobRef:
    """构造 JobRef。"""
    return JobRef(
        job_id=job_id or uuid4(),
        status=status,
        kind=kind,
        stage=stage,
        progress=progress,
        retryable=retryable,
    )


def _make_job_entity(
    job_id: UUID | None = None,
    kind: str = "flow_execute",
    status: str = "accepted",
    payload: dict | None = None,
) -> SimpleNamespace:
    """构造 Job ORM 实体（使用 SimpleNamespace）。"""
    return SimpleNamespace(
        id=job_id or uuid4(),
        kind=kind,
        status=status,
        stage="",
        progress=0,
        retryable=False,
        attempt=0,
        max_attempts=3,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        created_by=uuid4(),
        last_error=None,
        result=None,
        payload=payload or {"hello": "world"},
    )


def _make_mock_service() -> MagicMock:
    """构造 mock JobService。"""
    service = MagicMock()
    service.accept = AsyncMock()
    service.get = AsyncMock()
    service.request_cancel = AsyncMock()
    service.list = AsyncMock()
    service.get_raw = AsyncMock()
    service.get_created_by_name = AsyncMock(return_value=None)
    return service


def _make_app(service: MagicMock, user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(jobs_router)

    u = user or _make_current_user()

    async def _override_current_user():
        return u

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_job_service] = lambda: service

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(status_code=status, content={"error": exc.to_dict()})

    return app


# ===========================================================================
# 1. POST /api/v1/jobs — 提交作业
# ===========================================================================


class TestCreateJob:
    """POST /api/v1/jobs — 提交作业（202 Accepted）。"""

    def test_create_202(self):
        """提交成功 → 202"""
        service = _make_mock_service()
        ref = _make_job_ref()
        service.accept = AsyncMock(return_value=ref)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/jobs",
            json={
                "kind": "flow_execute",
                "payload": {"hello": "world"},
                "idempotency_key": "key-001",
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["kind"] == "flow_execute"

    def test_create_unknown_kind_422(self):
        """未知 kind → 422（unknown_job_kind）"""
        service = _make_mock_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/jobs",
            json={
                "kind": "nonexistent_kind",
                "payload": {},
                "idempotency_key": "key-002",
            },
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unknown_job_kind"

    def test_create_missing_idempotency_key_422(self):
        """缺少 idempotency_key → 422"""
        service = _make_mock_service()
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/jobs",
            json={"kind": "echo", "payload": {}},
        )

        assert response.status_code == 422


# ===========================================================================
# 2. GET /api/v1/jobs — 列表查询
# ===========================================================================


class TestListJobs:
    """GET /api/v1/jobs — 分页查询作业列表。"""

    def test_list_200(self):
        """列表查询成功 → 200"""
        service = _make_mock_service()
        job = _make_job_entity()
        service.list = AsyncMock(return_value=([(job, "", 0, False, "", "")], None, False))

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/jobs")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["has_more"] is False

    def test_list_with_status_filter(self):
        """状态筛选 → 200"""
        service = _make_mock_service()
        service.list = AsyncMock(return_value=([], None, False))

        app = _make_app(service)
        client = TestClient(app)

        response = client.get("/api/v1/jobs?status=running")

        assert response.status_code == 200
        call_kwargs = service.list.call_args.kwargs
        assert call_kwargs.get("status") == "running"


# ===========================================================================
# 3. GET /api/v1/jobs/{id} — 查询作业状态
# ===========================================================================


class TestGetJob:
    """GET /api/v1/jobs/{id} — 查询作业状态。"""

    def test_get_200(self):
        """查询成功 → 200"""
        service = _make_mock_service()
        ref = _make_job_ref(status=JobStatus.RUNNING, kind="flow_execute", progress=50)
        service.get = AsyncMock(return_value=ref)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/jobs/{ref.job_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert data["progress"] == 50

    def test_get_not_found_404(self):
        """作业不存在 → 404"""
        service = _make_mock_service()
        service.get = AsyncMock(
            side_effect=AppError(code="not_found", message="作业不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/jobs/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 4. POST /api/v1/jobs/{id}/cancel — 取消作业
# ===========================================================================


class TestCancelJob:
    """POST /api/v1/jobs/{id}/cancel — 请求取消作业。"""

    def test_cancel_200(self):
        """取消成功 → 200"""
        service = _make_mock_service()
        ref = _make_job_ref(status=JobStatus.CANCEL_REQUESTED)
        service.request_cancel = AsyncMock(return_value=ref)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(f"/api/v1/jobs/{ref.job_id}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "cancel_requested"

    def test_cancel_terminal_conflict_409(self):
        """已终态 → 409"""
        service = _make_mock_service()
        service.request_cancel = AsyncMock(
            side_effect=AppError(code="conflict", message="作业已终态", retryable=False, fields={})
        )
        app = _make_app(service)
        client = TestClient(app)

        response = client.post(f"/api/v1/jobs/{uuid4()}/cancel")

        assert response.status_code == 409


# ===========================================================================
# 5. GET /api/v1/jobs/{id}/detail — 作业详情
# ===========================================================================


class TestGetJobDetail:
    """GET /api/v1/jobs/{id}/detail — 作业详情。"""

    def test_detail_200(self):
        """详情查询成功 → 200"""
        service = _make_mock_service()
        ref = _make_job_ref(status=JobStatus.SUCCEEDED)
        job = _make_job_entity(status="succeeded", payload={"input": "data"})
        service.get = AsyncMock(return_value=ref)
        service.get_raw = AsyncMock(return_value=job)

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/jobs/{job.id}/detail")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "succeeded"
        assert data["payload"] == {"input": "data"}

    def test_detail_not_found_404(self):
        """作业不存在 → 404"""
        service = _make_mock_service()
        service.get = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.get(f"/api/v1/jobs/{uuid4()}/detail")

        assert response.status_code == 404


# ===========================================================================
# 6. POST /api/v1/jobs/{id}/retry — 重试作业
# ===========================================================================


class TestRetryJob:
    """POST /api/v1/jobs/{id}/retry — 重试已失败作业。"""

    def test_retry_202(self):
        """重试成功 → 202"""
        service = _make_mock_service()
        original = _make_job_entity(status="failed", kind="flow_execute")
        service.get_raw = AsyncMock(return_value=original)
        new_ref = _make_job_ref(status=JobStatus.ACCEPTED)
        service.accept = AsyncMock(return_value=new_ref)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(f"/api/v1/jobs/{original.id}/retry")

        assert response.status_code == 202
        assert response.json()["status"] == "accepted"

    def test_retry_non_terminal_conflict_409(self):
        """非终态 → 409"""
        service = _make_mock_service()
        original = _make_job_entity(status="running", kind="flow_execute")
        service.get_raw = AsyncMock(return_value=original)

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(f"/api/v1/jobs/{original.id}/retry")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_retry_not_found_404(self):
        """原作业不存在 → 404"""
        service = _make_mock_service()
        service.get_raw = AsyncMock(
            side_effect=AppError(code="not_found", message="不存在", retryable=False, fields={})
        )

        app = _make_app(service)
        client = TestClient(app)

        response = client.post(f"/api/v1/jobs/{uuid4()}/retry")

        assert response.status_code == 404
