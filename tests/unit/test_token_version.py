"""H-06 token_version JWT 撤销机制单元测试。

覆盖 ``packages/auth/tokens.py`` 与 ``apps/api/dependencies/auth.py``：
- JWT 包含 token_version claim；
- 认证时复核 token_version 不匹配则拒绝；
- disabled 用户认证被拒绝；
- token_version 不存在时默认为 0；
- 用户不存在时拒绝认证。

本测试为纯单元测试，不依赖数据库。
get_current_user 中的 session_factory 通过 AsyncMock 替身。
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import jwt
import pytest
from unittest.mock import AsyncMock, MagicMock

from packages.auth.tokens import (
    create_access_token,
    decode_access_token,
)
from packages.common.clock import FixedClock
from packages.common.errors import AppError
from apps.api.dependencies.auth import CurrentUser, get_current_user


# ---- 测试常量 ----

SECRET = "test-secret-key-at-least-32-chars-long"
NOW = datetime.now(UTC)
CLOCK = FixedClock(NOW)


def _make_token(token_version: int = 0, user_id: Any | None = None) -> str:
    """签发含 token_version 的 JWT。"""
    return create_access_token(
        user_id=user_id or uuid4(),
        email="user@irip.local",
        roles=["standard_owner"],
        secret=SECRET,
        clock=CLOCK,
        token_version=token_version,
    )


def _make_mock_user(
    *,
    user_id: Any | None = None,
    status: str = "active",
    token_version: int = 0,
    department_id: Any | None = None,
) -> MagicMock:
    """构造 mock AppUser 对象。"""
    user = MagicMock()
    user.id = user_id or uuid4()
    user.email = "user@irip.local"
    user.status = status
    user.token_version = token_version
    user.department_id = department_id
    user.roles = ["standard_owner"]
    return user


def _make_session_factory(user: MagicMock | None) -> Any:
    """构造 mock session_factory（async context manager）。

    session.scalar() 返回指定的 user（或 None）。
    """
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=user)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=mock_ctx)
    return factory


# ---- JWT token_version claim ----


class TestJwtTokenVersionClaim:
    """JWT 包含 token_version claim。"""

    def test_jwt_contains_token_version(self) -> None:
        """签发的 JWT payload 含 token_version 字段。"""
        token = _make_token(token_version=3)
        payload = decode_access_token(token, SECRET)
        assert "token_version" in payload
        assert payload["token_version"] == 3

    def test_jwt_token_version_default_zero(self) -> None:
        """不传 token_version 时默认为 0。"""
        token = create_access_token(
            user_id=uuid4(),
            email="a@b.c",
            roles=[],
            secret=SECRET,
            clock=CLOCK,
        )
        payload = decode_access_token(token, SECRET)
        assert payload["token_version"] == 0

    def test_jwt_token_version_preserved_across_values(self) -> None:
        """不同 token_version 值都能正确编码和解码。"""
        for tv in [0, 1, 5, 99, 255]:
            token = _make_token(token_version=tv)
            payload = decode_access_token(token, SECRET)
            assert payload["token_version"] == tv

    def test_jwt_other_claims_intact(self) -> None:
        """增加 token_version 后原有 claim 仍然存在。"""
        user_id = uuid4()
        token = create_access_token(
            user_id=user_id,
            email="test@irip.local",
            roles=["platform_administrator"],
            secret=SECRET,
            clock=CLOCK,
            token_version=2,
        )
        payload = decode_access_token(token, SECRET)
        assert payload["sub"] == str(user_id)
        assert payload["email"] == "test@irip.local"
        assert payload["roles"] == ["platform_administrator"]
        assert payload["token_version"] == 2
        assert "iat" in payload
        assert "exp" in payload


# ---- get_current_user token_version 复核 ----


class TestTokenVersionRecheck:
    """认证时复核 token_version。"""

    async def test_token_version_match_allows(self) -> None:
        """JWT token_version 与数据库一致时认证通过。"""
        user_id = uuid4()
        token = _make_token(token_version=0, user_id=user_id)
        user = _make_mock_user(user_id=user_id, status="active", token_version=0)

        factory = _make_session_factory(user)
        result = await get_current_user(
            authorization=f"Bearer {token}",
            token_secret=SECRET,
            session_factory=factory,
        )
        assert isinstance(result, CurrentUser)
        assert result.user_id == user_id
        assert result.email == "user@irip.local"

    async def test_token_version_mismatch_rejects(self) -> None:
        """JWT token_version 与数据库不匹配时拒绝（token 已被撤销）。"""
        user_id = uuid4()
        # JWT 中 token_version=0，但数据库中已更新为 1（用户被禁用/改密后）
        token = _make_token(token_version=0, user_id=user_id)
        user = _make_mock_user(user_id=user_id, status="active", token_version=1)

        factory = _make_session_factory(user)
        with pytest.raises(AppError) as exc_info:
            await get_current_user(
                authorization=f"Bearer {token}",
                token_secret=SECRET,
                session_factory=factory,
            )
        assert exc_info.value.code == "token_expired"

    async def test_token_version_increased_after_disable(self) -> None:
        """禁用用户后 token_version +1，旧 token 认证被拒。"""
        user_id = uuid4()
        # 用户被禁用前签发的 token（token_version=0）
        old_token = _make_token(token_version=0, user_id=user_id)
        # 禁用后数据库中 token_version 变为 1，但用户已重新启用
        user = _make_mock_user(user_id=user_id, status="active", token_version=1)

        factory = _make_session_factory(user)
        with pytest.raises(AppError, match="已被撤销"):
            await get_current_user(
                authorization=f"Bearer {old_token}",
                token_secret=SECRET,
                session_factory=factory,
            )

    async def test_token_version_higher_in_jwt_rejects(self) -> None:
        """JWT 中 token_version 高于数据库值时也拒绝。"""
        user_id = uuid4()
        token = _make_token(token_version=5, user_id=user_id)
        user = _make_mock_user(user_id=user_id, status="active", token_version=0)

        factory = _make_session_factory(user)
        with pytest.raises(AppError) as exc_info:
            await get_current_user(
                authorization=f"Bearer {token}",
                token_secret=SECRET,
                session_factory=factory,
            )
        assert exc_info.value.code == "token_expired"


# ---- disabled 用户认证拒绝 ----


class TestDisabledUserRejection:
    """disabled 用户认证被拒绝。"""

    async def test_disabled_user_rejected(self) -> None:
        """status=disabled 的用户认证被拒绝。"""
        user_id = uuid4()
        token = _make_token(token_version=0, user_id=user_id)
        user = _make_mock_user(user_id=user_id, status="disabled", token_version=0)

        factory = _make_session_factory(user)
        with pytest.raises(AppError) as exc_info:
            await get_current_user(
                authorization=f"Bearer {token}",
                token_secret=SECRET,
                session_factory=factory,
            )
        assert exc_info.value.code == "forbidden"
        assert "禁用" in exc_info.value.message

    async def test_disabled_user_rejected_even_with_matching_token_version(self) -> None:
        """disabled 用户即使 token_version 匹配也被拒绝。"""
        user_id = uuid4()
        token = _make_token(token_version=0, user_id=user_id)
        user = _make_mock_user(user_id=user_id, status="disabled", token_version=0)

        factory = _make_session_factory(user)
        with pytest.raises(AppError) as exc_info:
            await get_current_user(
                authorization=f"Bearer {token}",
                token_secret=SECRET,
                session_factory=factory,
            )
        assert exc_info.value.code == "forbidden"


# ---- 边界情况 ----


class TestEdgeCases:
    """认证边界情况。"""

    async def test_user_not_found_rejected(self) -> None:
        """用户不存在时拒绝认证。"""
        user_id = uuid4()
        token = _make_token(token_version=0, user_id=user_id)

        factory = _make_session_factory(None)  # user = None
        with pytest.raises(AppError) as exc_info:
            await get_current_user(
                authorization=f"Bearer {token}",
                token_secret=SECRET,
                session_factory=factory,
            )
        assert exc_info.value.code == "invalid_credentials"
        assert "不存在" in exc_info.value.message

    async def test_missing_authorization_header(self) -> None:
        """缺少 Authorization header 抛 invalid_credentials。"""
        with pytest.raises(AppError) as exc_info:
            await get_current_user(
                authorization=None,
                token_secret=SECRET,
                session_factory=None,
            )
        assert exc_info.value.code == "invalid_credentials"

    async def test_invalid_authorization_format(self) -> None:
        """Authorization 格式错误抛 invalid_credentials。"""
        with pytest.raises(AppError) as exc_info:
            await get_current_user(
                authorization="Basic abc123",
                token_secret=SECRET,
                session_factory=None,
            )
        assert exc_info.value.code == "invalid_credentials"

    async def test_expired_token_rejected(self) -> None:
        """过期 token 抛 token_expired。"""
        from datetime import timedelta
        from packages.auth.tokens import ACCESS_TOKEN_TTL_SECONDS

        past = datetime(2020, 1, 1, tzinfo=UTC)
        past_clock = FixedClock(past)
        user_id = uuid4()
        token = create_access_token(
            user_id=user_id,
            email="user@irip.local",
            roles=[],
            secret=SECRET,
            clock=past_clock,
            token_version=0,
        )

        with pytest.raises(AppError) as exc_info:
            await get_current_user(
                authorization=f"Bearer {token}",
                token_secret=SECRET,
                session_factory=None,
            )
        assert exc_info.value.code == "token_expired"

    async def test_invalid_signature_rejected(self) -> None:
        """签名无效的 token 抛 invalid_credentials。"""
        token = _make_token(token_version=0)
        with pytest.raises(AppError) as exc_info:
            await get_current_user(
                authorization=f"Bearer {token}",
                token_secret="wrong-secret-key-at-least-32-chars-long!!",
                session_factory=None,
            )
        assert exc_info.value.code == "invalid_credentials"

    async def test_no_session_factory_skips_db_check(self) -> None:
        """session_factory=None 时跳过数据库检查（仅解析 JWT）。"""
        user_id = uuid4()
        token = _make_token(token_version=0, user_id=user_id)

        result = await get_current_user(
            authorization=f"Bearer {token}",
            token_secret=SECRET,
            session_factory=None,
        )
        assert isinstance(result, CurrentUser)
        assert result.user_id == user_id
        assert result.department_id is None  # 无 DB 查询时 department_id 为 None


# ---- department_id 填充 ----


class TestDepartmentIdPopulation:
    """认证时填充 department_id。"""

    async def test_department_id_populated_from_db(self) -> None:
        """认证成功时从数据库填充 department_id。"""
        user_id = uuid4()
        dept_id = uuid4()
        token = _make_token(token_version=0, user_id=user_id)
        user = _make_mock_user(
            user_id=user_id,
            status="active",
            token_version=0,
            department_id=dept_id,
        )

        factory = _make_session_factory(user)
        result = await get_current_user(
            authorization=f"Bearer {token}",
            token_secret=SECRET,
            session_factory=factory,
        )
        assert result.department_id == dept_id

    async def test_department_id_none_when_not_assigned(self) -> None:
        """用户未分配实验室时 department_id 为 None。"""
        user_id = uuid4()
        token = _make_token(token_version=0, user_id=user_id)
        user = _make_mock_user(
            user_id=user_id,
            status="active",
            token_version=0,
            department_id=None,
        )

        factory = _make_session_factory(user)
        result = await get_current_user(
            authorization=f"Bearer {token}",
            token_secret=SECRET,
            session_factory=factory,
        )
        assert result.department_id is None
