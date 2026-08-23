"""Unit tests for apps.worker.tasks.sysuser — system service user resolution.

Tests the sync and async user-ID resolution paths with environment variables
and DB fallback, including caching behavior and error conditions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import apps.worker.tasks.sysuser as sysuser_mod


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset module-level cache before and after each test."""
    sysuser_mod._cached_system_service_user_id = None
    yield
    sysuser_mod._cached_system_service_user_id = None


class TestGetSystemServiceUserIdSync:
    """Tests for get_system_service_user_id_sync."""

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        uid = uuid4()
        monkeypatch.setenv("IRIP_SYSTEM_SERVICE_USER_ID", str(uid))
        result = sysuser_mod.get_system_service_user_id_sync()
        assert result == uid

    def test_cached_returned_on_second_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        uid = uuid4()
        monkeypatch.setenv("IRIP_SYSTEM_SERVICE_USER_ID", str(uid))
        first = sysuser_mod.get_system_service_user_id_sync()
        # Remove env to prove cache is used
        monkeypatch.delenv("IRIP_SYSTEM_SERVICE_USER_ID", raising=False)
        second = sysuser_mod.get_system_service_user_id_sync()
        assert first == second == uid

    def test_raises_when_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("IRIP_SYSTEM_SERVICE_USER_ID", raising=False)
        with pytest.raises(RuntimeError, match="IRIP_SYSTEM_SERVICE_USER_ID not set"):
            sysuser_mod.get_system_service_user_id_sync()


class TestGetSystemServiceUserIdAsync:
    """Tests for get_system_service_user_id (async)."""

    async def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        uid = uuid4()
        monkeypatch.setenv("IRIP_SYSTEM_SERVICE_USER_ID", str(uid))
        result = await sysuser_mod.get_system_service_user_id()
        assert result == uid

    async def test_cached_no_db_query_on_second_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        uid = uuid4()
        monkeypatch.setenv("IRIP_SYSTEM_SERVICE_USER_ID", str(uid))
        first = await sysuser_mod.get_system_service_user_id()
        monkeypatch.delenv("IRIP_SYSTEM_SERVICE_USER_ID", raising=False)
        second = await sysuser_mod.get_system_service_user_id()
        assert first == second == uid

    async def test_raises_when_env_empty_and_no_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from contextlib import asynccontextmanager

        monkeypatch.delenv("IRIP_SYSTEM_SERVICE_USER_ID", raising=False)
        # Patch at the source module since imports are done inside the function
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_session.execute.return_value = mock_result

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        @asynccontextmanager
        async def fake_session_scope(_factory):
            yield mock_session

        import packages.common.database as db_mod

        monkeypatch.setattr(db_mod, "build_session_factory", lambda url: mock_factory)
        monkeypatch.setattr(db_mod, "session_scope", fake_session_scope)
        with pytest.raises(RuntimeError, match="not found in DB"):
            await sysuser_mod.get_system_service_user_id()
