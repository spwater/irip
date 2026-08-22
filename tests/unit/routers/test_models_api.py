"""models_router API 测试：CRUD + 版本 + 验证 + 发布 + 回滚 + 预测 + 废弃。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock service
- 覆盖 get_model_service 和 get_current_user 依赖
- mock ModelService 的各种方法
- 验证 HTTP 状态码、响应体字段、错误码（404/409/422/503）
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.models import get_model_service, models_router
from packages.common.error_codes import ErrorCode
from packages.common.errors import AppError

# ===========================================================================
# 测试 fixtures
# ===========================================================================


def _make_current_user(roles: list[str] | None = None) -> CurrentUser:
    """构造有 manage+read 权限的当前用户。"""
    return CurrentUser(
        user_id=uuid4(),
        email="admin@irip.local",
        roles=roles or ["platform_administrator"],
        department_id=uuid4(),
        is_root_member=True,
    )


def _make_model(
    model_id=None,
    code: str = "model_test01",
    display_name: str = "测试模型",
    status: str = "draft",
    lock_version: int = 0,
) -> SimpleNamespace:
    """构造 Model 实体。"""
    return SimpleNamespace(
        id=model_id or uuid4(),
        code=code,
        display_name=display_name,
        status=status,
        current_version_id=None,
        lock_version=lock_version,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_version(
    version_id=None,
    model_id=None,
    version: int = 1,
    status: str = "validated",
) -> SimpleNamespace:
    """构造 ModelVersion 实体。"""
    return SimpleNamespace(
        id=version_id or uuid4(),
        model_id=model_id or uuid4(),
        version=version,
        status=status,
        contract_json={"sha256": "abc123"},
        model_artifact_id=None,
        metrics_json={"accuracy": 0.95},
        applicability_domain_json={},
        code_hash=None,
        dependency_hash=None,
        model_hash=None,
        created_at=datetime.now(UTC),
        published_at=None,
    )


def _make_mock_service() -> MagicMock:
    """构造 mock ModelService。"""
    service = MagicMock()
    service.create_model = AsyncMock()
    service.list_models = AsyncMock()
    service.get_model = AsyncMock()
    service.get_versions = AsyncMock()
    service.validate = AsyncMock()
    service.publish = AsyncMock()
    service.rollback = AsyncMock()
    service.predict = AsyncMock()
    service.deprecate = AsyncMock()
    return service


def _make_app(mock_service: MagicMock, mock_user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(models_router)

    user = mock_user or _make_current_user()

    async def _override_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_model_service] = lambda: mock_service

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
# 1. POST /api/v1/models/ — 创建模型
# ===========================================================================


class TestCreateModel:
    """POST /api/v1/models/ — 创建模型。"""

    def test_create_201(self):
        """创建成功 → 201"""
        mock_service = _make_mock_service()
        model = _make_model(code="model_new01", display_name="新模型")
        mock_service.create_model = AsyncMock(return_value=model)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/models/",
            json={"code": "model_new01", "display_name": "新模型"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["code"] == "model_new01"
        assert data["status"] == "draft"

    def test_create_conflict_409(self):
        """编码冲突 → 409"""
        mock_service = _make_mock_service()
        mock_service.create_model = AsyncMock(
            side_effect=AppError(
                code="conflict",
                message="模型编码已存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/models/",
            json={"code": "model_dup", "display_name": "重复模型"},
        )

        assert response.status_code == 409

    def test_create_invalid_code_pattern_422(self):
        """编码格式不合法（大写字母） → 422"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            "/api/v1/models/",
            json={"code": "Model_Bad", "display_name": "非法编码"},
        )

        assert response.status_code == 422


# ===========================================================================
# 2. GET /api/v1/models/ — 列表
# ===========================================================================


class TestListModels:
    """GET /api/v1/models/ — 列表查询。"""

    def test_list_200(self):
        """列表查询成功 → 200"""
        mock_service = _make_mock_service()
        model = _make_model(code="model_list01")
        mock_service.list_models = AsyncMock(return_value=[model])

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/models/")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["code"] == "model_list01"

    def test_list_with_status_filter(self):
        """状态筛选 → 200"""
        mock_service = _make_mock_service()
        mock_service.list_models = AsyncMock(return_value=[])

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get("/api/v1/models/?status=published")

        assert response.status_code == 200
        call_kwargs = mock_service.list_models.call_args.kwargs
        assert call_kwargs.get("status") == "published"


# ===========================================================================
# 3. GET /api/v1/models/{id} — 详情
# ===========================================================================


class TestGetModel:
    """GET /api/v1/models/{model_id} — 详情查询。"""

    def test_get_200(self):
        """详情查询成功 → 200"""
        mock_service = _make_mock_service()
        model = _make_model(code="model_detail01")
        mock_service.get_model = AsyncMock(return_value=model)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/models/{model.id}")

        assert response.status_code == 200
        assert response.json()["code"] == "model_detail01"

    def test_get_not_found_404(self):
        """查询不存在 → 404"""
        mock_service = _make_mock_service()
        mock_service.get_model = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="模型不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/models/{uuid4()}")

        assert response.status_code == 404


# ===========================================================================
# 4. GET /api/v1/models/{id}/versions — 版本列表
# ===========================================================================


class TestListVersions:
    """GET /api/v1/models/{model_id}/versions — 版本列表。"""

    def test_list_versions_200(self):
        """版本列表 → 200"""
        mock_service = _make_mock_service()
        model_id = uuid4()
        version = _make_version(model_id=model_id, version=1, status="published")
        mock_service.get_versions = AsyncMock(return_value=[version])

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.get(f"/api/v1/models/{model_id}/versions")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["version"] == 1
        assert data["items"][0]["contract_sha256"] == "abc123"


# ===========================================================================
# 5. POST /api/v1/models/{id}/versions/{vid}/publish — 发布版本
# ===========================================================================


class TestPublishVersion:
    """POST /api/v1/models/{model_id}/versions/{version_id}/publish — 发布。"""

    def test_publish_200(self):
        """发布成功 → 200"""
        mock_service = _make_mock_service()
        model = _make_model(code="model_pub01", status="published")
        model.current_version_id = uuid4()
        mock_service.publish = AsyncMock(return_value=model)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/models/{model.id}/versions/{model.current_version_id}/publish"
        )

        assert response.status_code == 200
        assert response.json()["status"] == "published"


# ===========================================================================
# 6. POST /api/v1/models/{id}/rollback — 回滚
# ===========================================================================


class TestRollback:
    """POST /api/v1/models/{model_id}/rollback — 回滚发布指针。"""

    def test_rollback_200(self):
        """回滚成功 → 200"""
        mock_service = _make_mock_service()
        model = _make_model(code="model_rb01", status="published")
        model.current_version_id = uuid4()
        mock_service.rollback = AsyncMock(return_value=model)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(
            f"/api/v1/models/{model.id}/rollback",
            json={"target_version_id": str(uuid4())},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "published"


# ===========================================================================
# 7. POST /api/v1/models/{id}/predict — 预测
# ===========================================================================


class TestPredict:
    """POST /api/v1/models/{model_id}/predict — 预测。"""

    def test_predict_feature_disabled_503(self):
        """功能开关关闭 → 503"""
        mock_service = _make_mock_service()
        app = _make_app(mock_service)
        client = TestClient(app)

        with patch(
            "packages.common.feature_flags.LEGACY_MODEL_EXECUTION_ENABLED",
            False,
        ):
            response = client.post(
                f"/api/v1/models/{uuid4()}/predict",
                json={"inputs": {"x": 1}},
            )

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "feature_disabled"

    def test_predict_success_200(self):
        """预测成功 → 200（需开启功能开关）"""
        from packages.models.service import PredictionResult

        mock_service = _make_mock_service()
        model_id = uuid4()
        version_id = uuid4()
        result = PredictionResult(
            model_id=model_id,
            model_version_id=version_id,
            version=1,
            predictions={"y": 42.0},
            metadata={"in_domain": True},
            fact_id=None,
        )
        mock_service.predict = AsyncMock(return_value=result)

        app = _make_app(mock_service)
        client = TestClient(app)

        with patch(
            "packages.common.feature_flags.LEGACY_MODEL_EXECUTION_ENABLED",
            True,
        ):
            response = client.post(
                f"/api/v1/models/{model_id}/predict",
                json={"inputs": {"x": 1}},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["predictions"] == {"y": 42.0}
        assert data["version"] == 1


# ===========================================================================
# 8. POST /api/v1/models/{id}/deprecate — 废弃
# ===========================================================================


class TestDeprecate:
    """POST /api/v1/models/{model_id}/deprecate — 废弃模型。"""

    def test_deprecate_200(self):
        """废弃成功 → 200"""
        mock_service = _make_mock_service()
        model = _make_model(code="model_dep01", status="deprecated")
        mock_service.deprecate = AsyncMock(return_value=model)

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/models/{model.id}/deprecate")

        assert response.status_code == 200
        assert response.json()["status"] == "deprecated"

    def test_deprecate_not_found_404(self):
        """废弃不存在 → 404"""
        mock_service = _make_mock_service()
        mock_service.deprecate = AsyncMock(
            side_effect=AppError(
                code="not_found",
                message="模型不存在",
                retryable=False,
                fields={},
            )
        )

        app = _make_app(mock_service)
        client = TestClient(app)

        response = client.post(f"/api/v1/models/{uuid4()}/deprecate")

        assert response.status_code == 404
