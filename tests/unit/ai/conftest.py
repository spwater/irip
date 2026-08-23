"""Shared fixtures for AI unit tests.

Provides:
- ``allow_private_network`` (autouse): sets ``IRIP_ALLOW_PRIVATE_NETWORK=1``
  so that ``SafeHTTPClient`` skips DNS SSRF validation, enabling ``respx``
  to intercept httpx calls to localhost / test URLs.
- ``llm_base_url``: standard test LLM base URL.
- ``make_provider``: factory for ``OpenAICompatibleProvider`` with test config.
- ``make_request``: factory for ``AIRequest``.
- ``fake_scoped_session``: helper to patch ``scoped_session`` with a mock session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.ai.openai_compatible import OpenAICompatibleProvider
from packages.ai.providers import AIRequest

#: Standard base URL for mocked LLM API.
LLM_BASE_URL = "http://test-llm:8000/v1"


@pytest.fixture(autouse=True)
def allow_private_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow private network addresses in SafeHTTPClient for all AI unit tests.

    This enables respx to intercept httpx calls without DNS resolution
    blocking localhost / test hostnames.
    """
    monkeypatch.setenv("IRIP_ALLOW_PRIVATE_NETWORK", "1")


def make_provider(
    api_key: str = "test-key",
    base_url: str = LLM_BASE_URL,
    model: str = "test-model",
    thinking_enabled: bool = False,
    timeout: float = 30.0,
) -> OpenAICompatibleProvider:
    """Create an OpenAICompatibleProvider with test configuration."""
    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=base_url,
        model=model,
        thinking_enabled=thinking_enabled,
        timeout=timeout,
    )


def make_request(
    messages: list[dict[str, Any]] | None = None,
    tools: tuple[str, ...] = (),
    user_context: dict[str, Any] | None = None,
    tool_schemas: tuple[dict[str, Any], ...] = (),
    provider_mode: str = "openai_compatible",
) -> AIRequest:
    """Create an AIRequest with sensible defaults."""
    return AIRequest(
        messages=tuple(messages or [{"role": "user", "content": "hello"}]),
        tools=tools,
        user_context=user_context or {},
        tool_schemas=tool_schemas,
        provider_mode=provider_mode,
    )


def make_mock_session() -> AsyncMock:
    """Create a mock AsyncSession for scoped_session patching."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    session.refresh = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=MagicMock())
    return session


def patch_scoped_session(
    module: Any,
    mock_session: AsyncMock | None = None,
) -> tuple[AsyncMock, Any]:
    """Patch ``scoped_session`` in the given module to yield a mock session.

    Returns ``(mock_session, original_scoped_session)`` for restoration.
    """
    if mock_session is None:
        mock_session = make_mock_session()

    original = module.scoped_session

    @asynccontextmanager
    async def fake_scoped_session(
        factory: Any, dept_id: Any = None, user_id: Any = None
    ) -> AsyncIterator[AsyncMock]:
        yield mock_session

    module.scoped_session = fake_scoped_session  # type: ignore[assignment]
    return mock_session, original


def restore_scoped_session(module: Any, original: Any) -> None:
    """Restore the original ``scoped_session`` in the given module."""
    module.scoped_session = original
