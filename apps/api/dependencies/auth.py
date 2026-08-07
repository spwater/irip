"""认证依赖：CurrentUser 与 JWT 解析。

提供 FastAPI Depends 依赖：
- get_token_secret: 从环境变量获取 JWT 密钥；
- get_auth_session_factory: 获取数据库会话工厂（DI 覆盖）；
- get_current_user: 从 Authorization header 解析 JWT，返回 CurrentUser。

所有需要认证的 /api/v1/* 端点通过 Depends(get_current_user) 注入当前用户
（docs/arch-v0.md §7.5 鉴权约定）。
"""

import os
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
import sqlalchemy as sa
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.auth.entities import AppUser
from packages.auth.tokens import decode_access_token
from packages.common.errors import AppError


@dataclass(frozen=True)
class CurrentUser:
    """当前认证用户（从 JWT 解析 + 数据库补充）。

    department_id 作为主要租户标识（NOT NULL）。
    is_root_member 标识 root 部门成员（不受数据隔离限制）。

    Attributes:
        user_id: 用户 UUID。
        email: 用户邮箱。
        roles: 用户角色列表。
        department_id: 所属部门 UUID（NOT NULL，租户隔离基础）。
        is_root_member: 是否为 root 部门成员（不受数据隔离限制）。
    """

    user_id: UUID
    email: str
    roles: list[str]
    department_id: UUID = UUID(int=0)
    is_root_member: bool = False


def get_token_secret() -> str:
    """获取 JWT 签名密钥。

    从环境变量 IRIP_JWT_SECRET 读取。
    非测试环境缺密钥时拒绝启动（fail-closed），与 EnvelopeCrypto 策略对齐。
    测试环境使用固定默认值。
    """
    secret = os.getenv("IRIP_JWT_SECRET", "")
    if not secret:
        if os.getenv("IRIP_ENV") != "test":
            raise RuntimeError(
                "IRIP_JWT_SECRET is required in non-test environment. "
                "Set IRIP_ENV=test for test environments or provide IRIP_JWT_SECRET."
            )
        return "irip-dev-secret-2026"
    return secret


def get_auth_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取数据库会话工厂（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入 lifespan 中创建的会话工厂，
    供 get_current_user 查询用户的 department_id。
    """
    raise NotImplementedError(
        "get_auth_session_factory must be overridden via dependency_overrides"
    )


async def get_current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    token_secret: Annotated[str, Depends(get_token_secret)] = "",
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_auth_session_factory)
    ] = None,  # type: ignore[assignment]
) -> CurrentUser:
    """从 Authorization header 解析 JWT，返回当前用户。

    期望格式：``Authorization: Bearer <jwt>``

    解析 JWT 后，从数据库查询用户的 department_id 并填入 CurrentUser，
    用于后续的实验室级数据隔离过滤。

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
    token = authorization[len("Bearer ") :]
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
    token_version_jwt: int = int(payload.get("token_version", 0))

    user_id = UUID(str(sub))

    # H-06: 每次认证复核 is_active 和 token_version
    # fail-closed: session_factory 为 None 时拒绝认证（DI 未覆盖可能是配置错误）
    if session_factory is None:
        raise AppError(
            code="internal_error",
            message="认证服务未正确配置（session_factory 缺失）",
            retryable=False,
            fields={},
        )
    department_id: UUID | None = None
    is_root_member: bool = False
    async with session_factory() as session:
        user: AppUser | None = await session.scalar(sa.select(AppUser).where(AppUser.id == user_id))
        if user is not None:
            department_id = user.department_id
            # H-06: 复核账户状态（disabled 用户拒绝）
            if user.status == "disabled":
                raise AppError(
                    code="forbidden",
                    message="用户已被禁用",
                    retryable=False,
                    fields={},
                )
            # H-06: 复核 token_version（不匹配则 token 已被撤销）
            if user.token_version != token_version_jwt:
                raise AppError(
                    code="token_expired",
                    message="访问令牌已被撤销，请重新登录",
                    retryable=False,
                    fields={},
                )
            # department_id 不可为空（fail-closed）
            if department_id is None:
                raise AppError(
                    code="forbidden",
                    message="用户未分配部门（department_id 为空）",
                    retryable=False,
                    fields={"user_id": str(user_id)},
                )
            # 查询是否 root 部门成员
            from apps.api.dependencies.dept_scope import check_is_root_member

            is_root_member = await check_is_root_member(department_id, session_factory)
        else:
            # 用户不存在，拒绝
            raise AppError(
                code="invalid_credentials",
                message="用户不存在",
                retryable=False,
                fields={},
            )

    return CurrentUser(
        user_id=user_id,
        email=str(payload.get("email", "")),
        roles=roles,
        department_id=department_id or UUID(int=0),
        is_root_member=is_root_member,
    )
