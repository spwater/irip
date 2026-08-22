"""component_preview_router API 测试：提示词推荐 + 数据抽取预览。

测试策略：
- 使用 FastAPI TestClient + dependency_overrides 注入 mock 依赖
- 覆盖 get_current_user 和 get_artifact_service
- patch get_scenario_config、_download_artifact、_extract_file_content、_call_llm
- 验证 HTTP 状态码、响应体字段
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.component_preview import component_preview_router
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
    return MagicMock()


def _make_app(
    service: MagicMock | None = None,
    user: CurrentUser | None = None,
) -> FastAPI:
    """构造带 dependency_overrides 的 FastAPI 测试应用。"""
    app = FastAPI()
    app.include_router(component_preview_router)

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
# 1. POST /api/v1/component-preview/prompt-recommend — 提示词推荐
# ===========================================================================


class TestPromptRecommend:
    """POST /api/v1/component-preview/prompt-recommend — 提示词推荐。"""

    def test_recommend_200(self, tmp_path):
        """推荐成功 → 200"""
        tmp_file = tmp_path / "test.csv"
        tmp_file.write_text("col1,col2\n1,2")

        from packages.ai.yaml_config import ScenarioConfig

        scenario_config = ScenarioConfig(
            provider_name="test",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o",
            thinking_enabled=False,
        )

        with (
            patch(
                "apps.api.routers.component_preview.get_scenario_config",
                return_value=scenario_config,
            ),
            patch(
                "apps.api.routers.component_preview._download_artifact",
                new_callable=AsyncMock,
                return_value=tmp_file,
            ),
            patch(
                "apps.api.routers.component_preview._extract_file_content",
                return_value="提取的文本内容",
            ),
            patch(
                "apps.api.routers.component_preview._call_llm",
                new_callable=AsyncMock,
                return_value="推荐的提示词",
            ),
            patch(
                "packages.ai.prompt_store.get_prompt",
                return_value="默认系统提示词 {filename}",
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.post(
                "/api/v1/component-preview/prompt-recommend",
                json={
                    "artifact_id": str(uuid4()),
                    "filename": "test.csv",
                },
            )

        assert response.status_code == 200
        assert response.json()["prompt"] == "推荐的提示词"

    def test_recommend_ai_not_configured_422(self, tmp_path):
        """AI 未配置 → 422"""
        with patch(
            "apps.api.routers.component_preview.get_scenario_config",
            side_effect=KeyError("scenario not found"),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.post(
                "/api/v1/component-preview/prompt-recommend",
                json={
                    "artifact_id": str(uuid4()),
                    "filename": "test.csv",
                },
            )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "ai_not_configured"


# ===========================================================================
# 2. POST /api/v1/component-preview/extract-preview — 数据抽取预览
# ===========================================================================


class TestExtractPreview:
    """POST /api/v1/component-preview/extract-preview — 数据抽取预览。"""

    def test_extract_llm_converter_200(self, tmp_path):
        """LLM 抽取预览成功 → 200"""
        tmp_file = tmp_path / "test.csv"
        tmp_file.write_text("col1,col2\n1,2")

        from packages.ai.yaml_config import ScenarioConfig

        scenario_config = ScenarioConfig(
            provider_name="test",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o",
            thinking_enabled=False,
        )

        mock_converter = MagicMock()
        mock_converter.execute = AsyncMock(return_value={"col1": "1", "col2": "2"})

        with (
            patch(
                "apps.api.routers.component_preview.get_scenario_config",
                return_value=scenario_config,
            ),
            patch(
                "apps.api.routers.component_preview._download_artifact",
                new_callable=AsyncMock,
                return_value=tmp_file,
            ),
            patch(
                "packages.plugins.registry.get",
                return_value=mock_converter,
            ),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.post(
                "/api/v1/component-preview/extract-preview",
                json={
                    "artifact_id": str(uuid4()),
                    "filename": "test.csv",
                    "prompt": "提取数据",
                    "tool_type": "llm_converter",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert "col1" in data["result"]

    def test_extract_missing_plugin_422(self, tmp_path):
        """插件未注册 → 422"""
        tmp_file = tmp_path / "test.csv"
        tmp_file.write_text("col1,col2\n1,2")

        with (
            patch(
                "apps.api.routers.component_preview._download_artifact",
                new_callable=AsyncMock,
                return_value=tmp_file,
            ),
            patch("packages.plugins.registry.get", return_value=None),
        ):
            app = _make_app()
            client = TestClient(app)
            response = client.post(
                "/api/v1/component-preview/extract-preview",
                json={
                    "artifact_id": str(uuid4()),
                    "filename": "test.csv",
                    "tool_type": "xrd_converter",
                },
            )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "missing_dependency"
