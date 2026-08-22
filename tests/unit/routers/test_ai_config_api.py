"""ai_config_router API 测试：AI 大模型配置管理。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock 依赖
- 覆盖 get_current_user，patch session_scope 和 config_store 函数
- 验证 HTTP 状态码、响应体字段、错误码（422/validation_failed）
- patch EnvelopeCrypto 避免真实加密
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.ai_config import ai_config_router, set_session_factory
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


@pytest.fixture(autouse=True)
def _setup_session_factory():
    """设置 mock session factory。"""
    set_session_factory(MagicMock())
    yield
    set_session_factory(None)


def _make_app(user: CurrentUser | None = None) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(ai_config_router)

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


def _mock_session_ctx():
    """构造 mock async context manager。"""
    mock_session = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return mock_ctx, mock_session


def _make_config_row(
    base_url: str = "https://api.openai.com/v1",
    api_key: str = "encrypted_key_data",
    model_name: str = "gpt-4o",
    assistant_model_name: str = "qwen-plus",
    research_model_name: str = "",
    enabled: bool = True,
    meta_prompt: str | None = "系统提示词",
    updated_at: datetime | None = None,
) -> dict:
    """构造 config row 字典（模拟 get_config_row 返回值）。"""
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model_name": model_name,
        "assistant_model_name": assistant_model_name,
        "research_model_name": research_model_name,
        "enabled": enabled,
        "meta_prompt": meta_prompt,
        "model_thinking_enabled": False,
        "assistant_thinking_enabled": True,
        "research_thinking_enabled": True,
        "updated_at": updated_at or datetime.now(UTC),
    }


# ===========================================================================
# 1. GET /api/v1/ai-config — 获取配置
# ===========================================================================


class TestGetAIConfig:
    """GET /api/v1/ai-config — 获取当前 AI 配置（密钥脱敏）。"""

    def test_get_config_with_data_200(self):
        """有配置时返回脱敏密钥 → 200"""
        row = _make_config_row()
        mock_ctx, _ = _mock_session_ctx()
        mock_crypto = MagicMock()
        mock_crypto.decrypt = MagicMock(return_value="sk-1234567890abcdef")

        with (
            patch("apps.api.routers.ai_config.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_config.get_config_row",
                new_callable=AsyncMock,
                return_value=row,
            ),
            patch("apps.api.routers.ai_config.EnvelopeCrypto.from_env", return_value=mock_crypto),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/ai-config")

        assert response.status_code == 200
        data = response.json()
        assert data["base_url"] == "https://api.openai.com/v1"
        assert data["model_name"] == "gpt-4o"
        assert data["enabled"] is True
        assert "***" in data["api_key_masked"]

    def test_get_config_empty_200(self):
        """无配置时返回空默认 → 200"""
        mock_ctx, _ = _mock_session_ctx()
        with (
            patch("apps.api.routers.ai_config.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_config.get_config_row",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/ai-config")

        assert response.status_code == 200
        data = response.json()
        assert data["base_url"] == ""
        assert data["enabled"] is False


# ===========================================================================
# 2. PUT /api/v1/ai-config — 更新配置
# ===========================================================================


class TestUpdateAIConfig:
    """PUT /api/v1/ai-config — 更新 AI 配置。"""

    def test_update_config_200(self):
        """更新成功 → 200"""
        mock_ctx, _ = _mock_session_ctx()
        mock_crypto = MagicMock()
        mock_crypto.encrypt = MagicMock(return_value="encrypted_key")

        with (
            patch("apps.api.routers.ai_config.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_config.get_config_row",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("apps.api.routers.ai_config.EnvelopeCrypto.from_env", return_value=mock_crypto),
            patch(
                "apps.api.routers.ai_config.upsert_config",
                new_callable=AsyncMock,
                return_value=_make_config_row(),
            ),
            patch("apps.api.routers.ai_config.SystemClock") as mock_clock_cls,
        ):
            mock_clock = MagicMock()
            mock_clock.now.return_value = datetime.now(UTC)
            mock_clock_cls.return_value = mock_clock

            app = _make_app()
            client = TestClient(app)
            response = client.put(
                "/api/v1/ai-config",
                json={
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-test-key-1234",
                    "model_name": "gpt-4o",
                    "enabled": True,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["model_name"] == "gpt-4o"
        assert data["enabled"] is True

    def test_update_config_use_saved_no_existing_422(self):
        """__use_saved__ 但无已保存配置 → 422"""
        mock_ctx, _ = _mock_session_ctx()
        with (
            patch("apps.api.routers.ai_config.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_config.get_config_row",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.put(
                "/api/v1/ai-config",
                json={
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "__use_saved__",
                    "model_name": "gpt-4o",
                    "enabled": True,
                },
            )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


# ===========================================================================
# 3. PUT /api/v1/ai-config/meta-prompt — 更新提示词
# ===========================================================================


class TestUpdateMetaPrompt:
    """PUT /api/v1/ai-config/meta-prompt — 更新系统提示词。"""

    def test_update_meta_prompt_200(self):
        """更新提示词成功 → 200"""
        with (
            patch(
                "apps.api.routers.ai_config.upsert_meta_prompt",
                new_callable=AsyncMock,
            ),
            patch("apps.api.routers.ai_config.SystemClock") as mock_clock_cls,
        ):
            mock_clock = MagicMock()
            mock_clock.now.return_value = datetime.now(UTC)
            mock_clock_cls.return_value = mock_clock

            app = _make_app()
            client = TestClient(app)
            response = client.put(
                "/api/v1/ai-config/meta-prompt",
                json={"meta_prompt": "新提示词"},
            )

        assert response.status_code == 200
        assert response.json()["meta_prompt"] == "新提示词"


# ===========================================================================
# 4. GET /api/v1/ai-config/meta-prompt — 获取提示词
# ===========================================================================


class TestGetMetaPrompt:
    """GET /api/v1/ai-config/meta-prompt — 获取系统提示词。"""

    def test_get_meta_prompt_with_config_200(self):
        """有配置时返回自定义提示词 → 200"""
        row = _make_config_row(meta_prompt="自定义提示词")
        mock_ctx, _ = _mock_session_ctx()
        with (
            patch("apps.api.routers.ai_config.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_config.get_config_row",
                new_callable=AsyncMock,
                return_value=row,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/ai-config/meta-prompt")

        assert response.status_code == 200
        assert response.json()["meta_prompt"] == "自定义提示词"

    def test_get_meta_prompt_no_config_200(self):
        """无配置时返回默认提示词 → 200"""
        mock_ctx, _ = _mock_session_ctx()
        with (
            patch("apps.api.routers.ai_config.session_scope", return_value=mock_ctx),
            patch(
                "apps.api.routers.ai_config.get_config_row",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.get("/api/v1/ai-config/meta-prompt")

        assert response.status_code == 200
        assert response.json()["meta_prompt"] is not None


# ===========================================================================
# 5. POST /api/v1/ai-config/test — 测试连接
# ===========================================================================


class TestAIConnectionTest:
    """POST /api/v1/ai-config/test — 测试 AI 连接。"""

    def test_test_connection_success_200(self):
        """连接成功 → 200"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "OK"}}],
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("apps.api.routers.ai_config.SafeHTTPClient", return_value=mock_client):
            app = _make_app()
            client = TestClient(app)
            response = client.post(
                "/api/v1/ai-config/test",
                json={
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-test",
                    "model_name": "gpt-4o",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["model_response"] == "OK"

    def test_test_connection_api_error_200(self):
        """API 返回错误 → 200（success=False）"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("apps.api.routers.ai_config.SafeHTTPClient", return_value=mock_client):
            app = _make_app()
            client = TestClient(app)
            response = client.post(
                "/api/v1/ai-config/test",
                json={
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-bad",
                    "model_name": "gpt-4o",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
