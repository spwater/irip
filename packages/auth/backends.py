"""认证后端协议与本地实现。

AuthBackend 是认证抽象协议，定义 authenticate(email, password) 接口。
LocalAuthBackend 使用数据库 + Argon2id 密码验证实现该协议。

设计意图（docs/arch-v0.md §3.2 类图）：
  API 依赖层只依赖 AuthBackend 协议，未来接入 OIDC 后端时不需修改领域服务。
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.passwords import verify_password
from packages.auth.repository import AuthRepository
from packages.common.errors import AppError


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """认证成功后返回的身份信息。

    Attributes:
        user_id: 用户 UUID。
        email: 用户邮箱。
        display_name: 用户显示名。
        status: 账户状态（active / disabled）。
        roles: 用户角色列表（T04 为空，T05 RBAC 后填充）。
    """

    user_id: UUID
    email: str
    display_name: str
    status: str
    roles: list[str]


class AuthBackend(Protocol):
    """认证后端协议：验证凭据并返回已认证身份。"""

    async def authenticate(
        self,
        session: AsyncSession,
        email: str,
        password: str,
    ) -> AuthenticatedIdentity:
        """验证邮箱+密码，返回已认证身份。

        Args:
            session: 数据库异步会话。
            email: 用户邮箱（CITEXT 大小写不敏感）。
            password: 明文密码。

        Returns:
            AuthenticatedIdentity: 已认证身份。

        Raises:
            AppError: code="invalid_credentials"，当用户不存在、密码错误或账户禁用时。
        """
        ...


class LocalAuthBackend:
    """本地认证后端：数据库查询 + Argon2id 密码验证。

    依赖 AuthRepository 查询用户，使用 passwords.verify_password 校验密码哈希。
    """

    def __init__(self, repository: AuthRepository) -> None:
        """初始化本地认证后端。

        Args:
            repository: 认证数据仓库（提供用户查询能力）。
        """
        self._repository = repository

    async def authenticate(
        self,
        session: AsyncSession,
        email: str,
        password: str,
    ) -> AuthenticatedIdentity:
        """验证凭据并返回身份。

        查找用户 → Argon2id 验证密码 → 检查账户状态 → 返回身份。
        任何一步失败均抛出 AppError(invalid_credentials)，
        不区分"用户不存在"与"密码错误"，防止枚举攻击。

        Raises:
            AppError: code="invalid_credentials"，当认证失败时。
        """
        user = await self._repository.find_user_by_email(session, email)
        if user is None or not verify_password(user.password_hash, password):
            raise AppError(
                code="invalid_credentials",
                message="邮箱或密码错误",
                retryable=False,
                fields={},
            )
        if user.status == "disabled":
            raise AppError(
                code="invalid_credentials",
                message="账户已被禁用",
                retryable=False,
                fields={},
            )
        return AuthenticatedIdentity(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            status=user.status,
            roles=[],
        )
