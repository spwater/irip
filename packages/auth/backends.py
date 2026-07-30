"""认证后端协议与本地实现。

AuthBackend 是认证抽象协议，定义 authenticate(email, password) 接口。
LocalAuthBackend 使用数据库 + Argon2id 密码验证实现该协议。

设计意图（docs/arch-v0.md §3.2 类图）：
  API 依赖层只依赖 AuthBackend 协议，未来接入 OIDC 后端时不需修改领域服务。

H-07 增强：
- 不存在用户执行 dummy Argon2（恒定时间），防止时延侧用户枚举攻击。
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.passwords import verify_password
from packages.auth.repository import AuthRepository
from packages.common.errors import AppError

#: H-07: dummy Argon2 哈希值（用于不存在用户时保持恒定时间校验）。
#: 这是一个合法的 Argon2id 哈希字符串，verify_password 对其校验总是失败，
#: 但消耗的时间与真实用户校验相同。
_DUMMY_HASH: str = (
    "$argon2id$v=19$m=19456,t=2,p=1$"
    "c2FsdHZlcmlmaWVkYWNjb3VudA$"
    "rW8xK5nLnF0Y3J5cHRvaW5mbw"
)


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """认证成功后返回的身份信息。

    Attributes:
        user_id: 用户 UUID。
        email: 用户邮箱。
        display_name: 用户显示名。
        status: 账户状态（active / disabled）。
        roles: 用户角色列表（从 app_user.roles 读取）。
        token_version: JWT 撤销版本号（H-06，用于签发含 token_version 的 JWT）。
    """

    user_id: UUID
    email: str
    display_name: str
    status: str
    roles: list[str]
    token_version: int = 0


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

        查找用户 -> Argon2id 验证密码 -> 检查账户状态 -> 返回身份。
        任何一步失败均抛出 AppError(invalid_credentials)，
        不区分"用户不存在"与"密码错误"，防止枚举攻击。

        H-07: 不存在用户时执行 dummy Argon2 校验，保持恒定时间，
        防止时延侧用户枚举攻击。

        Raises:
            AppError: code="invalid_credentials"，当认证失败时。
        """
        user = await self._repository.find_user_by_email(session, email)
        if user is None:
            # H-07: 不存在用户执行 dummy Argon2（恒定时间）
            verify_password(_DUMMY_HASH, password)
            raise AppError(
                code="invalid_credentials",
                message="邮箱或密码错误",
                retryable=False,
                fields={},
            )
        if not verify_password(user.password_hash, password):
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
            roles=list(user.roles),
            token_version=user.token_version,
        )
