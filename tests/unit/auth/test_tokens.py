"""JWT 与刷新令牌单元测试（实施计划 Task 4 Step 1）。

覆盖：
- Access token 签发/验证往返（sub/email/roles/exp/iat 字段）；
- Access token 过期检测；
- Access token 无效密钥检测；
- Refresh token 生成唯一性；
- Refresh token 摘要计算（SHA-256 hex，64 字符，小写）。
"""

from datetime import UTC, datetime
from uuid import uuid4

import jwt
import pytest

from packages.auth.tokens import (
    ACCESS_TOKEN_TTL_SECONDS,
    compute_refresh_digest,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
)
from packages.common.clock import FixedClock

# ---- Access token ----


def test_access_token_roundtrip() -> None:
    """签发并验证 JWT，所有 payload 字段正确。"""
    now = datetime.now(UTC)
    clock = FixedClock(now)
    user_id = uuid4()
    email = "test@irip.local"
    roles = ["standard_owner"]
    secret = "test-secret-key-at-least-32-chars-long"

    token = create_access_token(user_id, email, roles, secret, clock)
    payload = decode_access_token(token, secret)

    assert payload["sub"] == str(user_id)
    assert payload["email"] == email
    assert payload["roles"] == roles
    assert payload["iat"] == int(now.timestamp())
    assert payload["exp"] == int(now.timestamp()) + ACCESS_TOKEN_TTL_SECONDS


def test_access_token_expires_in_15_minutes() -> None:
    """Access token 有效期恰为 15 分钟（900 秒）。"""
    now = datetime.now(UTC)
    clock = FixedClock(now)
    secret = "test-secret-key-at-least-32-chars-long"
    token = create_access_token(uuid4(), "a@b.c", [], secret, clock)
    payload = decode_access_token(token, secret)
    ttl = payload["exp"] - payload["iat"]
    assert ttl == ACCESS_TOKEN_TTL_SECONDS
    assert ttl == 900


def test_access_token_expired_raises() -> None:
    """过期的 JWT 抛出 ExpiredSignatureError。"""
    past = datetime(2020, 1, 1, tzinfo=UTC)
    clock = FixedClock(past)
    secret = "test-secret-key-at-least-32-chars-long"
    token = create_access_token(uuid4(), "a@b.c", [], secret, clock)

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, secret)


def test_access_token_wrong_secret_raises() -> None:
    """密钥不匹配的 JWT 抛出 InvalidTokenError。"""
    now = datetime.now(UTC)
    clock = FixedClock(now)
    token = create_access_token(
        uuid4(), "a@b.c", [], "secret-key-A-at-least-32-chars-long", clock
    )

    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token, "secret-key-B-at-least-32-chars-long")


def test_access_token_roles_default_empty() -> None:
    """T04 无 RBAC，roles 为空列表。"""
    now = datetime.now(UTC)
    clock = FixedClock(now)
    secret = "test-secret-key-at-least-32-chars-long"
    token = create_access_token(uuid4(), "a@b.c", [], secret, clock)
    payload = decode_access_token(token, secret)
    assert payload["roles"] == []


# ---- Refresh token ----


def test_refresh_token_is_url_safe_string() -> None:
    """生成的 refresh token 为非空 URL-safe 字符串。"""
    token = generate_refresh_token()
    assert isinstance(token, str)
    assert len(token) > 0
    # URL-safe base64 字符集
    assert all(c.isalnum() or c in "-_" for c in token)


def test_refresh_token_uniqueness() -> None:
    """连续生成 100 个 refresh token 全部唯一。"""
    tokens = {generate_refresh_token() for _ in range(100)}
    assert len(tokens) == 100


def test_refresh_digest_is_sha256_hex() -> None:
    """摘要为 64 字符小写十六进制 SHA-256。"""
    token = generate_refresh_token()
    digest = compute_refresh_digest(token)

    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_refresh_digest_differs_from_token() -> None:
    """摘要与明文不同（仅存摘要，不泄露明文）。"""
    token = generate_refresh_token()
    digest = compute_refresh_digest(token)
    assert digest != token


def test_refresh_digest_deterministic() -> None:
    """相同明文产生相同摘要（验证时需重新计算）。"""
    token = "test-token-value"
    d1 = compute_refresh_digest(token)
    d2 = compute_refresh_digest(token)
    assert d1 == d2
