"""认证依赖：CurrentUser 与 JWT 解析。

提供 FastAPI Depends 依赖：
- get_token_secret: 从环境变量获取 JWT 密钥；
- get_current_user: 从 Authorization header 解析 JWT，返回 CurrentUser。

所有需要认证的 /api/v1/* 端点通过 Depends(get_current_user) 注入当前用户
（docs/arch-v0.md §7.5 鉴权约定）。
"""

import os
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Header

from packages.auth.tokens import decode_access_token
from packages.common.errors import AppError


@dataclass(frozen=True)
class CurrentUser:
    """当前认证用户（从 JWT 解析）。

    Attributes:
        user_id: 用户 UUID。
        email: 用户邮箱。
        roles: 用户角色列表。
    """

    user_id: UUID
    email: str
    roles: list[str]


def get_token_secret() -> str:
    """获取 JWT 签名密钥。

    从环境变量 IRIP_JWT_SECRET 读取，开发环境使用默认值。

    Returns:
        str: JWT 签名密钥。
    """
    return os.getenv("IRIP_JWT_SECRET", "irip-dev-secret-2026")


def get_current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    token_secret: Annotated[str, Depends(get_token_secret)] = "",
) -> CurrentUser:
    """从 Authorization header 解析 JWT，返回当前用户。

    期望格式：``Authorization: Bearer <jwt>``

    Raises:
        AppError: code="invalid_credentials"，当缺少或无效令牌时。
        AppError: code="token_expired"，当令牌已过期时。
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise AppError(
            code="invalid_credentials",
            message="缺少认证令牌",
            retryable=False,
            fields={},
        )
    token = authorization[len("Bearer "):]
    try:
        payload = decode_access_token(token, token_secret)
    except jwt.ExpiredSignatureError as exc:
        raise AppError(
            code="token_expired",
            message="访问令牌已过期",
            retryable=False,
            fields={},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise AppError(
            code="invalid_credentials",
            message="访问令牌无效",
            retryable=False,
            fields={},
        ) from exc

    sub: object = payload.get("sub", "")
    roles_raw: object = payload.get("roles", [])
    roles: list[str] = roles_raw if isinstance(roles_raw, list) else []

    return CurrentUser(
        user_id=UUID(str(sub)),
        email=str(payload.get("email", "")),
        roles=roles,
    )
