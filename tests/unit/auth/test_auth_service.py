"""Unit tests for packages.auth.service — AuthService business logic.

Tests login, refresh rotation, logout, disable_user, update_profile,
change_password, delete_account, verify_password, and set_avatar_url
with mocked repository and backend.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from packages.auth.service import AuthService


@pytest.fixture(autouse=True)
def _patch_session_scope():
    """Patch session_scope to yield a mock session (avoids real DB).

    session_scope in packages.auth.service is the real function which calls
    factory() and session.begin() — with a MagicMock factory, session.begin()
    returns a coroutine that can't be used as an async context manager.
    """
    mock_session = AsyncMock()

    @asynccontextmanager
    async def fake_scope(_factory, **kwargs):
        yield mock_session

    with patch("packages.auth.service.session_scope", fake_scope):
        yield mock_session


@pytest.fixture
def mock_backend() -> MagicMock:
    """Mock AuthBackend."""
    backend = MagicMock()
    identity = MagicMock()
    identity.user_id = UUID("00000000-0000-0000-0000-000000000001")
    identity.email = "user@irip.local"
    identity.roles = ["lab_member"]
    identity.token_version = 0
    backend.authenticate = AsyncMock(return_value=identity)
    return backend


@pytest.fixture
def mock_repository() -> MagicMock:
    """Mock AuthRepository."""
    repo = MagicMock()
    repo.create_refresh_session = AsyncMock()
    repo.find_session_by_digest_for_update = AsyncMock(return_value=None)
    repo.find_session_by_digest = AsyncMock(return_value=None)
    repo.rotate_session = AsyncMock()
    repo.revoke_family = AsyncMock()
    repo.revoke_session = AsyncMock()
    repo.revoke_family_by_user = AsyncMock()
    repo.find_user_by_id = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_clock() -> MagicMock:
    """Mock Clock."""
    clock = MagicMock()
    clock.now.return_value = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    return clock


@pytest.fixture
def auth_service(
    mock_backend: MagicMock,
    mock_repository: MagicMock,
    mock_clock: MagicMock,
) -> AuthService:
    """AuthService with mocked dependencies."""
    return AuthService(
        backend=mock_backend,
        repository=mock_repository,
        session_factory=MagicMock(),
        token_secret="test-secret",
        clock=mock_clock,
    )


class TestLogin:
    """Tests for AuthService.login."""

    async def test_successful_login(
        self,
        auth_service: AuthService,
        mock_backend: MagicMock,
        mock_repository: MagicMock,
    ) -> None:
        result = await auth_service.login("user@irip.local", "pass123")

        assert result.access_token is not None
        assert result.refresh_token is not None
        assert result.expires_in > 0
        mock_backend.authenticate.assert_called_once()
        mock_repository.create_refresh_session.assert_called_once()

    async def test_login_passes_created_ip_and_user_agent(
        self,
        auth_service: AuthService,
        mock_repository: MagicMock,
    ) -> None:
        await auth_service.login(
            "user@irip.local",
            "pass",
            created_ip="10.0.0.1",
            user_agent="test-agent",
        )
        call_kwargs = mock_repository.create_refresh_session.call_args[1]
        assert call_kwargs["created_ip"] == "10.0.0.1"
        assert call_kwargs["user_agent"] == "test-agent"


class TestRefresh:
    """Tests for AuthService.refresh."""

    async def test_session_not_found_raises(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        mock_repository.find_session_by_digest_for_update.return_value = None
        with pytest.raises(Exception, match="刷新令牌无效"):
            await auth_service.refresh("invalid-token")

    async def test_replaced_by_raises_replay(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        old_session = MagicMock()
        old_session.replaced_by = UUID("00000000-0000-0000-0000-000000000002")
        old_session.family_id = UUID("00000000-0000-0000-0000-000000000003")
        old_session.id = UUID("00000000-0000-0000-0000-000000000004")
        old_session.user_id = UUID("00000000-0000-0000-0000-000000000005")
        mock_repository.find_session_by_digest_for_update.return_value = old_session

        from packages.common.errors import AppError

        with pytest.raises(AppError, match="刷新令牌已被使用"):
            await auth_service.refresh("some-token")
        mock_repository.revoke_family.assert_called_once()

    async def test_revoked_raises_invalid(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        old_session = MagicMock()
        old_session.replaced_by = None
        old_session.revoked_at = datetime(2025, 1, 1, tzinfo=UTC)
        old_session.id = UUID("00000000-0000-0000-0000-000000000004")
        old_session.family_id = UUID("00000000-0000-0000-0000-000000000003")
        mock_repository.find_session_by_digest_for_update.return_value = old_session

        with pytest.raises(Exception, match="刷新令牌已失效"):
            await auth_service.refresh("some-token")

    async def test_expired_raises_invalid(
        self, auth_service: AuthService, mock_repository: MagicMock, mock_clock: MagicMock
    ) -> None:
        old_session = MagicMock()
        old_session.replaced_by = None
        old_session.revoked_at = None
        old_session.expires_at = datetime(2025, 1, 1, tzinfo=UTC)  # Before now
        old_session.id = UUID("00000000-0000-0000-0000-000000000004")
        old_session.family_id = UUID("00000000-0000-0000-0000-000000000003")
        old_session.user_id = UUID("00000000-0000-0000-0000-000000000005")
        mock_repository.find_session_by_digest_for_update.return_value = old_session

        with pytest.raises(Exception, match="刷新令牌已过期"):
            await auth_service.refresh("some-token")

    async def test_normal_rotation(
        self, auth_service: AuthService, mock_repository: MagicMock, mock_clock: MagicMock
    ) -> None:
        old_session = MagicMock()
        old_session.replaced_by = None
        old_session.revoked_at = None
        old_session.expires_at = datetime(2026, 12, 31, tzinfo=UTC)  # After now
        old_session.id = UUID("00000000-0000-0000-0000-000000000004")
        old_session.family_id = UUID("00000000-0000-0000-0000-000000000003")
        old_session.user_id = UUID("00000000-0000-0000-0000-000000000005")
        old_session.created_ip = None
        old_session.user_agent = None

        user = MagicMock()
        user.id = old_session.user_id
        user.email = "user@irip.local"
        user.roles = ["lab_member"]
        user.token_version = 1
        user.status = "active"

        mock_repository.find_session_by_digest_for_update.return_value = old_session
        mock_repository.find_user_by_id.return_value = user

        result = await auth_service.refresh("valid-token")
        assert result.access_token is not None
        assert result.refresh_token is not None
        mock_repository.create_refresh_session.assert_called_once()
        mock_repository.rotate_session.assert_called_once()

    async def test_disabled_user_revoked(
        self, auth_service: AuthService, mock_repository: MagicMock, mock_clock: MagicMock
    ) -> None:
        old_session = MagicMock()
        old_session.replaced_by = None
        old_session.revoked_at = None
        old_session.expires_at = datetime(2026, 12, 31, tzinfo=UTC)
        old_session.id = UUID("00000000-0000-0000-0000-000000000004")
        old_session.family_id = UUID("00000000-0000-0000-0000-000000000003")
        old_session.user_id = UUID("00000000-0000-0000-0000-000000000005")
        old_session.created_ip = None
        old_session.user_agent = None

        user = MagicMock()
        user.id = old_session.user_id
        user.email = "user@irip.local"
        user.roles = ["lab_member"]
        user.token_version = 1
        user.status = "disabled"

        mock_repository.find_session_by_digest_for_update.return_value = old_session
        mock_repository.find_user_by_id.return_value = user

        from packages.common.errors import AppError

        with pytest.raises(AppError, match="用户已被禁用"):
            await auth_service.refresh("valid-token")
        mock_repository.revoke_family.assert_called_once()

    async def test_user_not_found_during_rotation(
        self, auth_service: AuthService, mock_repository: MagicMock, mock_clock: MagicMock
    ) -> None:
        old_session = MagicMock()
        old_session.replaced_by = None
        old_session.revoked_at = None
        old_session.expires_at = datetime(2026, 12, 31, tzinfo=UTC)
        old_session.id = UUID("00000000-0000-0000-0000-000000000004")
        old_session.family_id = UUID("00000000-0000-0000-0000-000000000003")
        old_session.user_id = UUID("00000000-0000-0000-0000-000000000005")
        old_session.created_ip = None
        old_session.user_agent = None

        mock_repository.find_session_by_digest_for_update.return_value = old_session
        mock_repository.find_user_by_id.return_value = None

        with pytest.raises(Exception, match="用户不存在"):
            await auth_service.refresh("valid-token")


class TestLogout:
    """Tests for AuthService.logout."""

    async def test_logout_revokes_active_session(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        old_session = MagicMock()
        old_session.id = UUID("00000000-0000-0000-0000-000000000010")
        old_session.revoked_at = None
        mock_repository.find_session_by_digest.return_value = old_session

        await auth_service.logout("valid-token")
        mock_repository.revoke_session.assert_called_once()

    async def test_logout_idempotent_when_not_found(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        mock_repository.find_session_by_digest.return_value = None
        await auth_service.logout("invalid-token")
        mock_repository.revoke_session.assert_not_called()

    async def test_logout_idempotent_when_already_revoked(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        old_session = MagicMock()
        old_session.revoked_at = datetime(2025, 1, 1, tzinfo=UTC)
        mock_repository.find_session_by_digest.return_value = old_session
        await auth_service.logout("already-revoked")
        mock_repository.revoke_session.assert_not_called()


class TestDisableUser:
    """Tests for AuthService.disable_user."""

    async def test_disable_user_success(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        user = MagicMock()
        user.id = UUID("00000000-0000-0000-0000-000000000020")
        user.status = "active"
        user.token_version = 0
        mock_repository.find_user_by_id.return_value = user

        await auth_service.disable_user(user.id)
        assert user.status == "disabled"
        assert user.token_version == 1
        mock_repository.revoke_family_by_user.assert_called_once()

    async def test_disable_user_not_found(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        mock_repository.find_user_by_id.return_value = None
        from packages.common.errors import AppError

        with pytest.raises(AppError, match="用户不存在"):
            await auth_service.disable_user(UUID("00000000-0000-0000-0000-000000000021"))


class TestGetUserById:
    """Tests for AuthService.get_user_by_id."""

    async def test_returns_user(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        user = MagicMock()
        mock_repository.find_user_by_id.return_value = user
        result = await auth_service.get_user_by_id(UUID("00000000-0000-0000-0000-000000000030"))
        assert result is user

    async def test_returns_none_when_not_found(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        mock_repository.find_user_by_id.return_value = None
        result = await auth_service.get_user_by_id(UUID("00000000-0000-0000-0000-000000000031"))
        assert result is None


class TestUpdateProfile:
    """Tests for AuthService.update_profile."""

    async def test_update_display_name(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        user = MagicMock()
        user.display_name = "old"
        mock_repository.find_user_by_id.return_value = user

        result = await auth_service.update_profile(
            UUID("00000000-0000-0000-0000-000000000040"), display_name="new name"
        )
        assert user.display_name == "new name"
        assert result is user

    async def test_update_avatar_url(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        user = MagicMock()
        user.avatar_url = None
        mock_repository.find_user_by_id.return_value = user

        await auth_service.update_profile(
            UUID("00000000-0000-0000-0000-000000000041"), avatar_url="http://avatar.png"
        )
        assert user.avatar_url == "http://avatar.png"

    async def test_update_not_found(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        mock_repository.find_user_by_id.return_value = None
        from packages.common.errors import AppError

        with pytest.raises(AppError, match="用户不存在"):
            await auth_service.update_profile(
                UUID("00000000-0000-0000-0000-000000000042"), display_name="x"
            )


class TestChangePassword:
    """Tests for AuthService.change_password."""

    async def test_change_password_success(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        from packages.auth.passwords import hash_password

        user = MagicMock()
        user.password_hash = hash_password("old-pass")
        user.token_version = 0
        mock_repository.find_user_by_id.return_value = user

        with patch("packages.auth.service.hash_password", return_value="new-hash"):
            await auth_service.change_password(
                UUID("00000000-0000-0000-0000-000000000050"), "old-pass", "new-pass"
            )
        assert user.password_hash == "new-hash"
        assert user.token_version == 1

    async def test_wrong_old_password(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        from packages.auth.passwords import hash_password

        user = MagicMock()
        user.password_hash = hash_password("correct-pass")
        mock_repository.find_user_by_id.return_value = user

        from packages.common.errors import AppError

        with pytest.raises(AppError, match="旧密码不正确"):
            await auth_service.change_password(
                UUID("00000000-0000-0000-0000-000000000051"), "wrong-pass", "new-pass"
            )

    async def test_user_not_found(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        mock_repository.find_user_by_id.return_value = None
        from packages.common.errors import AppError

        with pytest.raises(AppError, match="用户不存在"):
            await auth_service.change_password(
                UUID("00000000-0000-0000-0000-000000000052"), "old", "new"
            )


class TestVerifyPassword:
    """Tests for AuthService.verify_password (static method)."""

    def test_verify_correct_password(self) -> None:
        from packages.auth.passwords import hash_password

        user = MagicMock()
        user.password_hash = hash_password("test-pass")
        assert AuthService.verify_password(user, "test-pass") is True

    def test_verify_wrong_password(self) -> None:
        from packages.auth.passwords import hash_password

        user = MagicMock()
        user.password_hash = hash_password("correct-pass")
        assert AuthService.verify_password(user, "wrong-pass") is False


class TestSetAvatarUrl:
    """Tests for AuthService.set_avatar_url."""

    async def test_set_avatar_calls_update(
        self, auth_service: AuthService, _patch_session_scope: AsyncMock
    ) -> None:
        mock_session = _patch_session_scope
        await auth_service.set_avatar_url(
            UUID("00000000-0000-0000-0000-000000000060"), "http://avatar"
        )
        mock_session.execute.assert_called_once()


class TestDeleteAccount:
    """Tests for AuthService.delete_account."""

    async def test_delete_account_anonymizes(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        user = MagicMock()
        user.id = UUID("00000000-0000-0000-0000-000000000070")
        user.status = "active"
        user.display_name = "John"
        user.email = "john@irip.local"
        user.avatar_url = "http://avatar"
        user.token_version = 0
        mock_repository.find_user_by_id.return_value = user

        await auth_service.delete_account(user.id)
        assert user.status == "deleted"
        assert user.display_name == "已删除用户"
        assert "deleted.local" in user.email
        assert user.avatar_url is None
        assert user.token_version == 1

    async def test_delete_account_not_found_silent(
        self, auth_service: AuthService, mock_repository: MagicMock
    ) -> None:
        mock_repository.find_user_by_id.return_value = None
        # Should not raise
        await auth_service.delete_account(UUID("00000000-0000-0000-0000-000000000071"))
