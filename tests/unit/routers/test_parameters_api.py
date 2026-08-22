"""parameters_router API 测试：CRUD + 候选 + 审批 + 弃用。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_parameter_service 和 get_current_user 依赖
- mock ParameterService 的各种方法
- 验证 HTTP 状态码、响应体字段、错误码（404/409/422）
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.parameters import get_parameter_service, parameters_router
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError
from packages.parameters.service import ParameterService, ParameterVersionRef

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有全部权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="admin@irip.local",
        roles=roles or ["platform_administrator"],
        department_id=uuid4(),
        is_root_member=True,
    )


def _make_version_ref(
    parameter_id: UUID | None = None,
    version: int = 1,
) -> ParameterVersionRef:
    """构造 ParameterVersionRef。"""
    return ParameterVersionRef(
        parameter_id=parameter_id or uuid4(),
        version=version,
        version_id=uuid4(),
        variable_code="temp",
        value="100",
        unit="°C",
        confidence="high",
        status="published",
        conditions=None,
        published_at=datetime.now(UTC),
    )


def _make_mock_service() -> MagicMock:
    """构造 mock ParameterService。"""
    service = MagicMock(spec=ParameterService)
    service.create_parameter = AsyncMock()
    service.list_parameters = AsyncMock()
    service.get_parameter = AsyncMock()
    service.get_version = AsyncMock()
    service.create_candidate = AsyncMock()
    service.list_candidates = AsyncMock()
    service.approve = AsyncMock()
    service.reject = AsyncMock()
    service.deprecate = AsyncMock()
    return service


def _make_app(mock_service: MagicMock, mock_user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(parameters_router)

    user = mock_user or _make_current_user()

    async def _override_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_parameter_service] = lambda: mock_service

    _status_map = ErrorCode.to_status_map()

    @app.exception_handler(AppError)
    async def handle_app_error(request, exc: AppError):
        status = _status_map.get(exc.code, 500)
        return JSONResponse(
            status_code=status,
            content={"error": exc.to_dict()},
        )

    return app


# ===========================================================================
# 1. POST /api/v1/parameters — 创建参数
# ===========================================================================


class TestCreateParameter:
    """POST /api/v1/parameters — 创建参数。"""

    def test_create_201(self):
        """创建成功 → 201"""
        mock_service = _make_mock_service()
        param_id = uuid4()
        object_id = uuid4()
        mock_service.create_parameter = AsyncMock(
            return_value={
                "parameter_id": str(param_id),
                "variable_code": "temp",
                "object_id": str(object_id),
                "status": "draft",
            }
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/parameters",
            json={"variable_code": "temp", "object_id": str(object_id)},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["variable_code"] == "temp"
        assert data["status"] == "draft"

    def test_create_missing_field_422(self):
        """缺少必填字段 → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/parameters",
            json={"variable_code": "temp"},
        )

        assert response.status_code == 422


# ===========================================================================
# 2. GET /api/v1/parameters — 列表
# ===========================================================================


class TestListParameters:
    """GET /api/v1/parameters — 分页列表。"""

    def test_list_200(self):
        """列表查询成功 → 200"""
        mock_service = _make_mock_service()
        param_id = uuid4()
        object_id = uuid4()
        items = [
            {
                "parameter_id": str(param_id),
                "variable_code": "temp",
                "object_id": str(object_id),
                "status": "draft",
            }
        ]
        mock_service.list_parameters = AsyncMock(return_value=(items, None))

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/parameters")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["variable_code"] == "temp"
        assert data["next_cursor"] is None

    def test_list_with_filter(self):
        """按状态筛选 → 200"""
        mock_service = _make_mock_service()
        mock_service.list_parameters = AsyncMock(return_value=([], None))

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/parameters?status=published")

        assert response.status_code == 200
        call_kwargs = mock_service.list_parameters.call_args.kwargs
        assert call_kwargs.get("filters", {}).get("status") == "published"


# ===========================================================================
# 3. GET /api/v1/parameters/{id} — 详情
# ===========================================================================


class TestGetParameter:
    """GET /api/v1/parameters/{parameter_id} — 参数详情。"""

    def test_get_200(self):
        """详情查询成功 → 200"""
        mock_service = _make_mock_service()
        param_id = uuid4()
        object_id = uuid4()
        mock_service.get_parameter = AsyncMock(
            return_value={
                "parameter_id": str(param_id),
                "variable_code": "temp",
                "object_id": str(object_id),
                "status": "published",
                "current_version": 1,
                "current_version_id": str(uuid4()),
                "value": "100",
                "unit": "°C",
            }
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/parameters/{param_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["variable_code"] == "temp"
        assert data["status"] == "published"
        assert data["current_version"] == 1

    def test_get_not_found_404(self):
        """查询不存在 → 404"""
        mock_service = _make_mock_service()
        mock_service.get_parameter = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="参数不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/parameters/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 4. POST /api/v1/parameters/{id}/candidates — 创建候选
# ===========================================================================


class TestCreateCandidate:
    """POST /api/v1/parameters/{parameter_id}/candidates — 创建候选。"""

    def test_create_candidate_201(self):
        """创建候选成功 → 201"""
        mock_service = _make_mock_service()
        param_id = uuid4()
        run_id = uuid4()
        mock_service.create_candidate = AsyncMock(
            return_value={
                "candidate_id": str(uuid4()),
                "parameter_id": str(param_id),
                "derivation_run_id": str(run_id),
                "value": "200",
                "unit": "°C",
                "confidence": "high",
                "status": "pending_review",
            }
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/parameters/{param_id}/candidates",
            json={
                "derivation_run_id": str(run_id),
                "value": "200",
                "unit": "°C",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["value"] == "200"
        assert data["status"] == "pending_review"


# ===========================================================================
# 5. GET /api/v1/parameters/{id}/candidates — 候选列表
# ===========================================================================


class TestListCandidates:
    """GET /api/v1/parameters/{parameter_id}/candidates — 候选列表。"""

    def test_list_candidates_200(self):
        """候选列表 → 200"""
        mock_service = _make_mock_service()
        param_id = uuid4()
        mock_service.list_candidates = AsyncMock(
            return_value=[
                {
                    "candidate_id": str(uuid4()),
                    "parameter_id": str(param_id),
                    "derivation_run_id": str(uuid4()),
                    "value": "100",
                    "unit": "°C",
                    "confidence": "high",
                    "status": "pending_review",
                    "submitted_by": str(uuid4()),
                    "submitted_at": datetime.now(UTC),
                    "reviewed_by": None,
                    "reviewed_at": None,
                    "review_decision": None,
                    "review_comment": None,
                }
            ]
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/parameters/{param_id}/candidates")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["status"] == "pending_review"


# ===========================================================================
# 6. POST /api/v1/parameters/candidates/{id}/approve — 审批通过
# ===========================================================================


class TestApproveCandidate:
    """POST /api/v1/parameters/candidates/{candidate_id}/approve — 审批通过。"""

    def test_approve_200(self):
        """审批通过 → 200 + ParameterVersionResponse"""
        mock_service = _make_mock_service()
        ref = _make_version_ref()
        mock_service.approve = AsyncMock(return_value=ref)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/parameters/candidates/{uuid4()}/approve")

        assert response.status_code == 200
        data = response.json()
        assert data["value"] == "100"
        assert data["status"] == "published"

    def test_approve_candidate_not_pending_409(self):
        """候选非待审状态 → 409"""
        mock_service = _make_mock_service()
        mock_service.approve = AsyncMock(
            side_effect=AppError(
                code="candidate_not_pending",
                message="候选不在待审状态",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/parameters/candidates/{uuid4()}/approve")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "candidate_not_pending"


# ===========================================================================
# 7. POST /api/v1/parameters/candidates/{id}/reject — 拒绝候选
# ===========================================================================


class TestRejectCandidate:
    """POST /api/v1/parameters/candidates/{candidate_id}/reject — 拒绝候选。"""

    def test_reject_200(self):
        """拒绝候选 → 200"""
        mock_service = _make_mock_service()
        mock_service.reject = AsyncMock(
            return_value={
                "candidate_id": str(uuid4()),
                "parameter_id": str(uuid4()),
                "status": "rejected",
            }
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/parameters/candidates/{uuid4()}/reject",
            json={"comment": "数据不准确"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "rejected"

    def test_reject_missing_comment_422(self):
        """缺少 comment → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/parameters/candidates/{uuid4()}/reject",
            json={},
        )

        assert response.status_code == 422


# ===========================================================================
# 8. POST /api/v1/parameters/{id}/deprecate — 弃用参数
# ===========================================================================


class TestDeprecateParameter:
    """POST /api/v1/parameters/{parameter_id}/deprecate — 弃用参数。"""

    def test_deprecate_200(self):
        """弃用成功 → 200"""
        mock_service = _make_mock_service()
        param_id = uuid4()
        mock_service.deprecate = AsyncMock(
            return_value={
                "parameter_id": str(param_id),
                "status": "deprecated",
            }
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/parameters/{param_id}/deprecate")

        assert response.status_code == 200
        assert response.json()["status"] == "deprecated"

    def test_deprecate_not_found_404(self):
        """弃用不存在 → 404"""
        mock_service = _make_mock_service()
        mock_service.deprecate = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="参数不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/parameters/{uuid4()}/deprecate")

        assert response.status_code == 404
