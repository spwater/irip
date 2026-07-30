"""认证路由：登录、刷新、登出、当前用户。

端点（docs/arch-v0.md §2.6）：
  POST /api/v1/auth/login   — 邮箱+密码登录，返回 access_token + Set-Cookie irip_refresh
  POST /api/v1/auth/refresh — 刷新令牌旋转，返回新 access_token + 新 Cookie
  POST /api/v1/auth/logout  — 登出，撤销当前会话，清除 Cookie
  GET  /api/v1/me           — 返回当前用户信息（需 Authorization: Bearer）

安全约定：
- access_token 放 JSON body 返回（不设 cookie）；
- refresh_token 放 HttpOnly SameSite=Strict cookie（irip_refresh），绝不放 JSON body；
- 错误响应统一格式：{"error": {"code", "message", "retryable", "fields"}}。

H-07 增强：
- 密码 max_length=128（防止 DoS）；
- 邮箱 max_length=254（RFC 5321 上限）；
- IP+账号双维限流（IP 20 次/分钟，账号 5 次/分钟）。

H-13 增强：
- refresh cookie secure=True（生产环境）；
- refresh cookie path 改为 /api/v1/auth（最小 path）。
"""

import os
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.dependencies.auth import CurrentUser, get_current_user
from packages.auth.entities import AppUser
from packages.auth.permissions import BUILTIN_ROLES
from packages.auth.service import AuthService
from packages.auth.tokens import TokenPair
from packages.common.errors import AppError
from packages.common.rate_limiter import get_rate_limiter

#: Refresh cookie 名称。
REFRESH_COOKIE_NAME: str = "irip_refresh"

#: Refresh cookie 有效期（秒），7 天。
REFRESH_COOKIE_MAX_AGE: int = 7 * 24 * 3600

#: H-07: IP 维限流（每分钟 20 次）。
LOGIN_IP_LIMIT: int = 20
LOGIN_IP_WINDOW: int = 60

#: H-07: 账号维限流（每分钟 5 次）。
LOGIN_ACCOUNT_LIMIT: int = 5
LOGIN_ACCOUNT_WINDOW: int = 60


# ---- 请求/响应模型 ----


class LoginRequest(BaseModel):
    """登录请求体（H-07: 密码/邮箱长度上限）。"""

    email: str = Field(..., max_length=254, description="用户邮箱（RFC 5321 上限 254）")
    password: str = Field(
        ..., min_length=1, max_length=128, description="用户密码（上限 128 防 DoS）"
    )


class TokenResponse(BaseModel):
    """令牌响应体（access_token + expires_in，refresh_token 在 cookie 中）。"""

    access_token: str
    expires_in: int


class OkResponse(BaseModel):
    """通用成功响应。"""

    ok: bool = True


class MeResponse(BaseModel):
    """当前用户信息响应。"""

    id: str
    email: str
    display_name: str
    roles: list[str]
    permissions: list[str]


# ---- 依赖占位（由应用启动或测试覆盖）----


def get_auth_service() -> AuthService:
    """获取 AuthService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_auth_service must be overridden via dependency_overrides")


def get_me_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取 /me 端点用的 DB 会话工厂（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_me_session_factory must be overridden via dependency_overrides")


# ---- 类型别名 ----

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


# ---- 路由 ----

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
me_router = APIRouter(tags=["user"])


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """设置 HttpOnly refresh cookie。先清除可能存在的旧 cookie（多 path）。"""
    is_production: bool = os.getenv("IRIP_ENV") == "production"
    # 清除可能残留的旧 cookie（不同 path 都试一次，避免同名 cookie 堆积）
    for p in ("/api/v1/auth", "/api/v1", "/"):
        response.delete_cookie(key=REFRESH_COOKIE_NAME, path=p)
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        samesite="lax" if not is_production else "strict",
        secure=is_production,
        max_age=REFRESH_COOKIE_MAX_AGE,
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    """清除 refresh cookie（H-13: path 与 set 时一致）。"""
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path="/api/v1/auth")


@auth_router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    service: AuthServiceDep,
) -> TokenResponse:
    """用户登录。

    成功返回 access_token（JSON body）+ 设置 irip_refresh HttpOnly cookie。
    失败抛出 AppError(invalid_credentials) -> 401。

    H-07: IP+账号双维限流。
    """
    # H-07: IP+账号双维限流
    client_ip: str | None = None
    if request.client is not None:
        client_ip = request.client.host
    user_agent: str | None = request.headers.get("user-agent")

    rate_limiter = get_rate_limiter()
    if client_ip is not None:
        if not rate_limiter.allow(
            f"login:ip:{client_ip}", limit=LOGIN_IP_LIMIT, window=LOGIN_IP_WINDOW
        ):
            raise AppError(
                code="rate_limited",
                message="请求过于频繁，请稍后再试",
                retryable=False,
                fields={},
            )
    if not rate_limiter.allow(
        f"login:email:{body.email}",
        limit=LOGIN_ACCOUNT_LIMIT,
        window=LOGIN_ACCOUNT_WINDOW,
    ):
        raise AppError(
            code="rate_limited",
            message="账号登录尝试过多，请稍后再试",
            retryable=False,
            fields={},
        )

    pair: TokenPair = await service.login(
        email=body.email,
        password=body.password,
        created_ip=client_ip,
        user_agent=user_agent,
    )
    _set_refresh_cookie(response, pair.refresh_token)
    return TokenResponse(access_token=pair.access_token, expires_in=pair.expires_in)


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    service: AuthServiceDep,
) -> TokenResponse:
    """刷新令牌旋转。

    从 irip_refresh cookie 读取刷新令牌，旋转后返回新 access_token + 新 cookie。
    重放攻击时抛出 AppError(refresh_replayed) → 401。
    """
    refresh_token: str | None = request.cookies.get(REFRESH_COOKIE_NAME)
    # 临时调试：打印 cookie 和请求头
    import logging
    logger = logging.getLogger("uvicorn.error")
    logger.warning("REFRESH DEBUG: cookies=%s, headers=%s, cookie_names=%s",
                   bool(refresh_token), dict(request.headers), list(request.cookies.keys()))
    if not refresh_token:
        raise AppError(
            code="invalid_credentials",
            message="缺少刷新令牌",
            retryable=False,
            fields={},
        )

    pair: TokenPair = await service.refresh(refresh_token)
    _set_refresh_cookie(response, pair.refresh_token)
    return TokenResponse(access_token=pair.access_token, expires_in=pair.expires_in)


@auth_router.post("/logout", response_model=OkResponse)
async def logout(
    request: Request,
    response: Response,
    service: AuthServiceDep,
) -> OkResponse:
    """用户登出。

    撤销当前刷新会话并清除 cookie。即使令牌无效也返回 ok（幂等）。
    """
    refresh_token: str | None = request.cookies.get(REFRESH_COOKIE_NAME)
    if refresh_token:
        await service.logout(refresh_token)
    _clear_refresh_cookie(response)
    return OkResponse(ok=True)


@me_router.get("/api/v1/me", response_model=MeResponse)
async def me(
    current_user: CurrentUserDep,
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_me_session_factory)],
) -> MeResponse:
    """获取当前用户信息。

    需要 Authorization: Bearer <jwt> header。
    从数据库查询 display_name 和角色权限。
    """
    async with session_factory() as session:
        user: AppUser | None = await session.scalar(
            sa.select(AppUser).where(AppUser.id == current_user.user_id)
        )

    display_name = user.display_name if user is not None else current_user.email
    roles = current_user.roles if current_user.roles else []
    permissions: list[str] = []
    for role_code in roles:
        builtin = BUILTIN_ROLES.get(role_code)
        if builtin is not None:
            role_perms = builtin["permissions"]
            if isinstance(role_perms, list):
                permissions.extend(str(p) for p in role_perms)

    return MeResponse(
        id=str(current_user.user_id),
        email=current_user.email,
        display_name=display_name,
        roles=roles,
        permissions=permissions,
    )
