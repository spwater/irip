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
        """When get_scenario_config raises, build_gateway_from_config returns None."""
        monkeypatch.setattr(
            "packages.ai.yaml_config.get_scenario_config",
            MagicMock(side_effect=KeyError("scenario not found")),
        )

        result = await build_gateway_from_config()
        assert result is None

    async def test_full_config_builds_gateway(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid config builds a SimpleGateway."""
        from packages.ai.yaml_config import ScenarioConfig

        config = ScenarioConfig(
            provider_name="test",
            base_url="http://x/v1",
            api_key="sk",
            model="research-model",
            thinking_enabled=False,
        )
        monkeypatch.setattr(
            "packages.ai.yaml_config.get_scenario_config",
            MagicMock(return_value=config),
        )

        gateway = await build_gateway_from_config()
        assert isinstance(gateway, SimpleGateway)

    async def test_thinking_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Thinking enabled is passed through from config."""
        provider_cls = MagicMock()
        monkeypatch.setattr(
            "packages.research.timeline.simple_gateway.OpenAICompatibleProvider", provider_cls
        )
        from packages.ai.yaml_config import ScenarioConfig

        config = ScenarioConfig(
            provider_name="test",
            base_url="http://x/v1",
            api_key="sk",
            model="fallback-model",
            thinking_enabled=True,
        )
        monkeypatch.setattr(
            "packages.ai.yaml_config.get_scenario_config",
            MagicMock(return_value=config),
        )

        gateway = await build_gateway_from_config()
        assert gateway is not None
        kwargs = provider_cls.call_args.kwargs
        assert kwargs["model"] == "fallback-model"
        assert kwargs["thinking_enabled"] is True
