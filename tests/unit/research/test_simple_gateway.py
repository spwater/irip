"""Tests for SimpleGateway and build_gateway_from_config.

Covers provider construction, the async ``call`` interface, and the
config-driven factory (including None-config and missing-credential paths).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.ai.providers import AIResponse
from packages.research.timeline.simple_gateway import (
    SimpleGateway,
    build_gateway_from_config,
)


class TestSimpleGatewayInit:
    def test_builds_provider_with_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider_cls = MagicMock()
        monkeypatch.setattr(
            "packages.research.timeline.simple_gateway.OpenAICompatibleProvider", provider_cls
        )

        gateway = SimpleGateway(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="qwen3",
            thinking_enabled=True,
        )

        provider_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="qwen3",
            thinking_enabled=True,
        )
        assert gateway._provider is provider_cls.return_value

    def test_thinking_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider_cls = MagicMock()
        monkeypatch.setattr(
            "packages.research.timeline.simple_gateway.OpenAICompatibleProvider", provider_cls
        )

        SimpleGateway(api_key="k", base_url="http://x", model="m")

        _, kwargs = provider_cls.call_args
        assert kwargs["thinking_enabled"] is False


class TestSimpleGatewayCall:
    async def test_call_returns_provider_answer(self) -> None:
        gateway = SimpleGateway(api_key="k", base_url="http://x", model="m")
        gateway._provider = MagicMock()
        gateway._provider.complete = AsyncMock(return_value=AIResponse(answer="hello world"))

        result = await gateway.call("system prompt", "user prompt")

        assert result == "hello world"
        gateway._provider.complete.assert_awaited_once()
        request = gateway._provider.complete.await_args.args[0]
        assert request.messages == (
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user prompt"},
        )
        assert request.tools == ()


class TestBuildGatewayFromConfig:
    async def test_no_config_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "apps.api.routers.ai_config.get_active_ai_config",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr("apps.api.routers.ai_config.set_session_factory", MagicMock())
        monkeypatch.setattr("packages.common.database.build_session_factory", MagicMock())

        result = await build_gateway_from_config()
        assert result is None

    async def test_missing_base_url_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "apps.api.routers.ai_config.get_active_ai_config",
            AsyncMock(return_value={"api_key": "sk", "model_name": "m"}),
        )
        monkeypatch.setattr("apps.api.routers.ai_config.set_session_factory", MagicMock())
        monkeypatch.setattr("packages.common.database.build_session_factory", MagicMock())

        assert await build_gateway_from_config() is None

    async def test_missing_api_key_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "apps.api.routers.ai_config.get_active_ai_config",
            AsyncMock(return_value={"base_url": "http://x", "model_name": "m"}),
        )
        monkeypatch.setattr("apps.api.routers.ai_config.set_session_factory", MagicMock())
        monkeypatch.setattr("packages.common.database.build_session_factory", MagicMock())

        assert await build_gateway_from_config() is None

    async def test_full_config_builds_gateway(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "apps.api.routers.ai_config.get_active_ai_config",
            AsyncMock(
                return_value={
                    "base_url": "http://x/v1",
                    "api_key": "sk",
                    "research_model_name": "research-model",
                    "thinking_enabled": "false",
                }
            ),
        )
        monkeypatch.setattr("apps.api.routers.ai_config.set_session_factory", MagicMock())
        monkeypatch.setattr("packages.common.database.build_session_factory", MagicMock())

        gateway = await build_gateway_from_config()
        assert isinstance(gateway, SimpleGateway)

    async def test_model_fallback_and_thinking_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider_cls = MagicMock()
        monkeypatch.setattr(
            "packages.research.timeline.simple_gateway.OpenAICompatibleProvider", provider_cls
        )
        monkeypatch.setattr(
            "apps.api.routers.ai_config.get_active_ai_config",
            AsyncMock(
                return_value={
                    "base_url": "http://x/v1",
                    "api_key": "sk",
                    "model_name": "fallback-model",
                    "thinking_enabled": "on",
                }
            ),
        )
        monkeypatch.setattr("apps.api.routers.ai_config.set_session_factory", MagicMock())
        monkeypatch.setattr("packages.common.database.build_session_factory", MagicMock())

        gateway = await build_gateway_from_config()
        assert gateway is not None
        kwargs = provider_cls.call_args.kwargs
        assert kwargs["model"] == "fallback-model"
        assert kwargs["thinking_enabled"] is True

    async def test_thinking_numeric_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider_cls = MagicMock()
        monkeypatch.setattr(
            "packages.research.timeline.simple_gateway.OpenAICompatibleProvider", provider_cls
        )
        monkeypatch.setattr(
            "apps.api.routers.ai_config.get_active_ai_config",
            AsyncMock(
                return_value={
                    "base_url": "http://x",
                    "api_key": "sk",
                    "model_name": "m",
                    "thinking_enabled": "1",
                }
            ),
        )
        monkeypatch.setattr("apps.api.routers.ai_config.set_session_factory", MagicMock())
        monkeypatch.setattr("packages.common.database.build_session_factory", MagicMock())

        await build_gateway_from_config()
        assert provider_cls.call_args.kwargs["thinking_enabled"] is True
