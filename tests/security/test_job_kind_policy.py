"""C-02 安全测试：作业越权防护。

覆盖 T02 修改的 ``apps/api/routers/jobs.py``：
- 通用 POST /jobs 接口只允许 allow_general_submit=True 的 kind；
- 特权 kind（backup/restore/audit_export）通过通用接口提交 → 403 forbidden；
- 未知 kind 通过通用接口提交 → 422 unknown_job_kind；
- 普通用户提交 flow_execute 成功（202 Accepted）；
- admin 通过专用 API（via_general=False）提交 backup 成功（策略级验证）。

使用 FastAPI TestClient + 依赖覆盖，不依赖真实数据库。
"""

from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.jobs import get_job_service, jobs_router
from packages.common.errors import AppError
from packages.common.job_policy import JobKindPolicy
from packages.jobs.entities import JobRef, JobStatus

# ============================================================
# 辅助：Mock JobService
# ============================================================


class MockJobService:
    """模拟 JobService，记录 accept 调用并返回虚拟 JobRef。"""

    def __init__(self) -> None:
        self.accepted: list[tuple[str, dict, str]] = []

    async def accept(self, kind: str, payload: dict, idempotency_key: str) -> JobRef:
        """记录调用并返回虚拟 JobRef。"""
        self.accepted.append((kind, payload, idempotency_key))
        return JobRef(
            job_id=uuid4(),
            status=JobStatus.ACCEPTED,
            kind=kind,
        )


# ============================================================
# 辅助：构建 TestClient
# ============================================================


def _make_client(user: CurrentUser, service: MockJobService) -> TestClient:
    """构建挂载 jobs_router 的 TestClient，覆盖认证与服务依赖。"""
    app = FastAPI(title="IRIP Job Policy Security Test")
    app.include_router(jobs_router)

    # 覆盖 get_current_user：返回指定用户
    app.dependency_overrides[get_current_user] = lambda: user

    # 覆盖 get_job_service：返回 Mock 服务
    app.dependency_overrides[get_job_service] = lambda: service

    # AppError → HTTP 统一错误响应
    _STATUS_MAP: dict[str, int] = {
        "forbidden": 403,
        "unknown_job_kind": 422,
        "not_found": 404,
        "conflict": 409,
        "validation_failed": 422,
        "internal_error": 500,
    }

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        status = _STATUS_MAP.get(exc.code, 500)
        return JSONResponse(
            status_code=status,
            content={"error": exc.to_dict()},
        )

    return TestClient(app)


def _make_user(roles: list[str]) -> CurrentUser:
    """构造测试用 CurrentUser。"""
    return CurrentUser(
        user_id=uuid4(),
        email=f"test-{'-'.join(roles)}@irip.local",
        roles=roles,
    )


def _submit_job(
    client: TestClient, kind: str, idempotency_key: str | None = None
) -> tuple[int, dict]:
    """通过通用 POST /jobs 提交作业，返回 (status_code, body)。"""
    body: dict = {"kind": kind, "payload": {"test": True}}
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    else:
        body["idempotency_key"] = f"idem-{kind}-{uuid4().hex[:8]}"
    resp = client.post("/api/v1/jobs", json=body)
    is_json = resp.headers.get("content-type", "").startswith("application/json")
    body_dict = resp.json() if is_json else {}
    return resp.status_code, body_dict


# ============================================================
# 1. 普通用户提交特权 kind → 403
# ============================================================


class TestNormalUserPrivilegedKindRejected:
    """普通用户通过通用接口提交特权 kind → 403 forbidden。"""

    @pytest.mark.parametrize("kind", ["backup", "restore", "audit_export"])
    def test_normal_user_privileged_kind_403(self, kind: str) -> None:
        """普通用户（lab_member）提交 backup/restore/audit_export → 403。"""
        user = _make_user(["lab_member"])
        service = MockJobService()
        client = _make_client(user, service)

        status, body = _submit_job(client, kind)
        assert status == 403, f"Expected 403 for kind={kind}, got {status}: {body}"
        assert body["error"]["code"] == "forbidden"
        assert "must be submitted via dedicated API" in body["error"]["message"]

    @pytest.mark.parametrize("kind", ["backup", "restore", "audit_export"])
    def test_normal_user_privileged_kind_not_accepted(self, kind: str) -> None:
        """特权 kind 被拒绝后，JobService.accept 不应被调用。"""
        user = _make_user(["lab_member"])
        service = MockJobService()
        client = _make_client(user, service)

        _submit_job(client, kind)
        assert len(service.accepted) == 0, (
            f"JobService.accept should not be called for privileged kind={kind}"
        )


# ============================================================
# 2. 未知 kind → 422
# ============================================================


class TestUnknownKindRejected:
    """未知 kind 通过通用接口提交 → 422 unknown_job_kind。"""

    def test_unknown_kind_422(self) -> None:
        """提交未注册的 kind → 422 unknown_job_kind。"""
        user = _make_user(["lab_member"])
        service = MockJobService()
        client = _make_client(user, service)

        status, body = _submit_job(client, "malicious_execute")
        assert status == 422, f"Expected 422, got {status}: {body}"
        assert body["error"]["code"] == "unknown_job_kind"

    def test_unknown_kind_not_accepted(self) -> None:
        """未知 kind 被拒绝后，JobService.accept 不应被调用。"""
        user = _make_user(["lab_member"])
        service = MockJobService()
        client = _make_client(user, service)

        _submit_job(client, "arbitrary_kind")
        assert len(service.accepted) == 0

    def test_empty_kind_422(self) -> None:
        """提交空字符串 kind → 422 unknown_job_kind。"""
        user = _make_user(["lab_member"])
        service = MockJobService()
        client = _make_client(user, service)

        status, body = _submit_job(client, "")
        assert status == 422
        assert body["error"]["code"] == "unknown_job_kind"


# ============================================================
# 3. 普通用户提交 flow_execute → 202 成功
# ============================================================


class TestNormalUserGeneralKindAccepted:
    """普通用户提交通用 kind → 202 Accepted。"""

    @pytest.mark.parametrize(
        "kind",
        [
            "flow_execute",
            "flow_resume",
            "ingestion",
            "model_train",
            "model_predict",
            "model_publish",
        ],
    )
    def test_normal_user_general_kind_202(self, kind: str) -> None:
        """普通用户（lab_member）提交通用 kind → 202 Accepted。"""
        user = _make_user(["lab_member"])
        service = MockJobService()
        client = _make_client(user, service)

        status, body = _submit_job(client, kind)
        assert status == 202, f"Expected 202 for kind={kind}, got {status}: {body}"
        assert "job_id" in body
        assert body["status"] == "accepted"
        assert body["kind"] == kind

    def test_flow_execute_service_called(self) -> None:
        """flow_execute 提交成功后 JobService.accept 被调用。"""
        user = _make_user(["lab_member"])
        service = MockJobService()
        client = _make_client(user, service)

        _submit_job(client, "flow_execute")
        assert len(service.accepted) == 1
        assert service.accepted[0][0] == "flow_execute"

    def test_general_kind_with_lab_director(self) -> None:
        """lab_director 也可以提交通用 kind。"""
        user = _make_user(["lab_director"])
        service = MockJobService()
        client = _make_client(user, service)

        status, body = _submit_job(client, "ingestion")
        assert status == 202
        assert body["kind"] == "ingestion"


# ============================================================
# 4. admin 提交 backup → 403（通用接口拒绝，需专用 API）
# ============================================================


class TestAdminPrivilegedKindViaGeneralRejected:
    """admin 通过通用接口提交特权 kind → 403（必须使用专用 API）。"""

    @pytest.mark.parametrize("kind", ["backup", "restore", "audit_export"])
    def test_admin_privileged_kind_via_general_403(self, kind: str) -> None:
        """admin（platform_administrator）通过通用接口提交 backup → 403。"""
        user = _make_user(["platform_administrator"])
        service = MockJobService()
        client = _make_client(user, service)

        status, body = _submit_job(client, kind)
        assert status == 403, f"Expected 403 for admin via general, got {status}: {body}"
        assert body["error"]["code"] == "forbidden"
        assert "must be submitted via dedicated API" in body["error"]["message"]

    def test_admin_privileged_kind_not_accepted(self) -> None:
        """admin 通过通用接口提交特权 kind 后，accept 不应被调用。"""
        user = _make_user(["platform_administrator"])
        service = MockJobService()
        client = _make_client(user, service)

        _submit_job(client, "backup")
        assert len(service.accepted) == 0


# ============================================================
# 5. admin 提交 backup 成功（策略级：via_general=False，专用 API）
# ============================================================


class TestAdminBackupViaDedicatedApi:
    """admin 通过专用 API（via_general=False）提交 backup → 成功。"""

    def test_admin_backup_via_dedicated_succeeds(self) -> None:
        """admin 拥有 system:manage，通过专用 API 提交 backup 策略校验通过。"""
        from packages.auth.permissions import get_role_permissions

        admin_perms: set[str] = set()
        for perm in get_role_permissions("platform_administrator"):
            admin_perms.add(perm)

        policy = JobKindPolicy.validate("backup", admin_perms, via_general=False)
        assert policy.required_permission == "system:manage"
        assert policy.allow_general_submit is False
        assert policy.queue == "irip-ops"

    def test_admin_restore_via_dedicated_succeeds(self) -> None:
        """admin 通过专用 API 提交 restore 策略校验通过。"""
        from packages.auth.permissions import get_role_permissions

        admin_perms: set[str] = set(get_role_permissions("platform_administrator"))
        policy = JobKindPolicy.validate("restore", admin_perms, via_general=False)
        assert policy.required_permission == "system:manage"

    def test_admin_audit_export_via_dedicated_succeeds(self) -> None:
        """admin 通过专用 API 提交 audit_export 策略校验通过。"""
        from packages.auth.permissions import get_role_permissions

        admin_perms: set[str] = set(get_role_permissions("platform_administrator"))
        policy = JobKindPolicy.validate("audit_export", admin_perms, via_general=False)
        assert policy.required_permission == "system:manage"

    def test_normal_user_backup_via_dedicated_rejected(self) -> None:
        """普通用户即使通过专用 API 也缺少 system:manage 权限。"""
        from packages.auth.permissions import get_role_permissions

        member_perms: set[str] = set(get_role_permissions("lab_member"))
        with pytest.raises(PermissionError, match="Permission denied"):
            JobKindPolicy.validate("backup", member_perms, via_general=False)


# ============================================================
# 6. admin 提交通用 kind → 202 成功
# ============================================================


class TestAdminGeneralKindAccepted:
    """admin 也可以通过通用接口提交通用 kind。"""

    def test_admin_flow_execute_202(self) -> None:
        """admin（platform_administrator）提交 flow_execute → 202。"""
        user = _make_user(["platform_administrator"])
        service = MockJobService()
        client = _make_client(user, service)

        status, body = _submit_job(client, "flow_execute")
        assert status == 202
        assert body["kind"] == "flow_execute"
        assert len(service.accepted) == 1


# ============================================================
# 7. 无 job:submit 权限的用户 → 403
# ============================================================


class TestNoJobSubmitPermission:
    """缺少 job:submit 权限的用户被 require_permission 拦截。"""

    def test_lab_viewer_rejected(self) -> None:
        """lab_viewer 没有 job:submit 权限 → 403 forbidden。"""
        user = _make_user(["lab_viewer"])
        service = MockJobService()
        client = _make_client(user, service)

        status, body = _submit_job(client, "flow_execute")
        assert status == 403
        assert body["error"]["code"] == "forbidden"

    def test_lab_viewer_privileged_kind_rejected(self) -> None:
        """lab_viewer 提交 backup → 403（require_permission 先拦截）。"""
        user = _make_user(["lab_viewer"])
        service = MockJobService()
        client = _make_client(user, service)

        status, body = _submit_job(client, "backup")
        assert status == 403
        assert len(service.accepted) == 0
