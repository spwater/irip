"""JWT 签发/验证与刷新令牌生成。

- Access token: JWT HS256，15 分钟有效期，payload 含 sub/email/roles/exp/iat；
- Refresh token: secrets.token_urlsafe(32) 生成明文，仅存 SHA-256 摘要到数据库；
- TokenPair: 返回 access_token（明文，放 JSON body）
  + refresh_token（明文，放 HttpOnly cookie）。

安全约定（docs/arch-v0.md §1.2 刷新令牌安全）：
  "仅持久化 SHA-256 摘要 + 家族 ID + 单用途旋转 + 重放即家族撤销"
"""

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

import jwt

from packages.common.clock import Clock
from packages.common.hashing import sha256_bytes

#: Access token 有效期（秒），15 分钟。
ACCESS_TOKEN_TTL_SECONDS: int = 900

#: Refresh token 有效期，7 天。
REFRESH_TOKEN_TTL: timedelta = timedelta(days=7)

#: JWT 签名算法。
JWT_ALGORITHM: str = "HS256"


@dataclass(frozen=True)
class TokenPair:
    """登录/刷新成功后返回的令牌对。

    Attributes:
        access_token: JWT 访问令牌（明文，放入 JSON body 返回给客户端）。
        refresh_token: 刷新令牌明文（仅通过 HttpOnly cookie 传递，绝不入库）。
        expires_in: access token 有效期（秒）。
    """

    access_token: str
    refresh_token: str
    expires_in: int


def create_access_token(
    user_id: UUID,
    email: str,
    roles: list[str],
    secret: str,
    clock: Clock,
    token_version: int = 0,
) -> str:
    """签发 JWT access token。

    H-06: payload 中增加 token_version claim，用于 JWT 撤销。
    每次认证时复核 token_version 与数据库中的值是否匹配，
    不匹配则拒绝（token 已被撤销）。

    Args:
        user_id: 用户 UUID。
        email: 用户邮箱。
        roles: 用户角色列表（T04 为空，T05 RBAC 后填充）。
        secret: JWT 签名密钥。
        clock: 时钟依赖（用于 iat/exp 时间戳）。
        token_version: JWT 撤销版本号（H-06，默认 0）。

    Returns:
        str: 编码后的 JWT 字符串。
    """
    now = clock.now()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "roles": roles,
        "token_version": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, secret: str) -> dict[str, Any]:
    """验证并解码 JWT access token。

    Args:
        token: JWT 字符串。
        secret: JWT 签名密钥。

    Returns:
        dict: JWT payload（含 sub/email/roles/exp/iat）。

    Raises:
        jwt.ExpiredSignatureError: 令牌已过期。
        jwt.InvalidTokenError: 令牌无效（签名错误、格式错误等）。
    """
    return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])


def generate_refresh_token() -> str:
    """生成随机刷新令牌明文。

    使用 secrets.token_urlsafe(32) 生成约 43 字符的 URL-safe base64 随机串。
    明文仅在调用方内存中短暂存在，绝不入库——只存其 SHA-256 摘要。

    Returns:
        str: 刷新令牌明文（通过 HttpOnly cookie 传递给客户端）。
    """
    return secrets.token_urlsafe(32)


def compute_refresh_digest(token: str) -> str:
    """计算刷新令牌的 SHA-256 十六进制摘要。

    数据库中仅存储此摘要，不存储明文 token。

    Args:
        token: 刷新令牌明文。

    Returns:
        str: 64 位小写十六进制 SHA-256 摘要。
    """
    return sha256_bytes(token.encode("utf-8"))
