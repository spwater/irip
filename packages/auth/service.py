"""认证业务服务：登录、刷新旋转、登出。

核心流程（docs/arch-v0.md §4.1 时序图）：

login(email, password):
  1. backend.authenticate 验证凭据 → AuthenticatedIdentity
  2. 生成 refresh token 明文 + SHA-256 摘要
  3. INSERT refresh_session（新 family_id）
  4. 签发 JWT access token
  5. 返回 TokenPair

refresh(refresh_token):
  1. 计算摘要 → SELECT ... FOR UPDATE 查找会话
  2. 若 replaced_by 非空 → 重放攻击 → 整族撤销 → 抛 refresh_replayed
  3. 若 revoked_at 非空 → 令牌已失效 → 抛 invalid_credentials
  4. 若已过期 → 抛 invalid_credentials
  5. 正常旋转：旧行 replaced_by+revoked_at，新行同 family_id
  6. 签发新 access token → 返回新 TokenPair

logout(refresh_token):
  撤销当前会话（幂等，不存在则静默返回）。
"""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.auth.backends import AuthBackend
from packages.auth.repository import AuthRepository
from packages.auth.tokens import (
    ACCESS_TOKEN_TTL_SECONDS,
    REFRESH_TOKEN_TTL,
    TokenPair,
    compute_refresh_digest,
    create_access_token,
    generate_refresh_token,
)
from packages.common.clock import Clock
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id


class AuthService:
    """认证业务编排服务。

    依赖注入 AuthBackend（协议）、AuthRepository（数据访问）、
    session_factory（事务管理）、token_secret（JWT 密钥）、Clock（时钟）。
    """

    def __init__(
        self,
        backend: AuthBackend,
        repository: AuthRepository,
        session_factory: async_sessionmaker[AsyncSession],
        token_secret: str,
        clock: Clock,
    ) -> None:
        """初始化认证服务。

        Args:
            backend: 认证后端（协议，可替换为 OIDC 等）。
            repository: 认证数据仓库。
            session_factory: 异步会话工厂（build_session_factory 返回值）。
            token_secret: JWT 签名密钥。
            clock: 时钟依赖（用于签发时间与过期计算）。
        """
        self._backend = backend
        self._repository = repository
        self._session_factory = session_factory
        self._token_secret = token_secret
        self._clock = clock

    async def login(
        self,
        email: str,
        password: str,
        created_ip: str | None = None,
        user_agent: str | None = None,
    ) -> TokenPair:
        """用户登录：验证凭据并签发令牌对。

        Args:
            email: 用户邮箱。
            password: 明文密码。
            created_ip: 客户端 IP（审计辅助）。
            user_agent: User-Agent（审计辅助）。

        Returns:
            TokenPair: access_token（JSON body）+ refresh_token（HttpOnly cookie）。

        Raises:
            AppError: code="invalid_credentials"，当认证失败时。
        """
        async with session_scope(self._session_factory) as session:
            identity = await self._backend.authenticate(session, email, password)

            refresh_token = generate_refresh_token()
            digest = compute_refresh_digest(refresh_token)
            family_id = new_id()
            session_id = new_id()
            now = self._clock.now()
            expires_at = now + REFRESH_TOKEN_TTL

            await self._repository.create_refresh_session(
                session,
                session_id=session_id,
                family_id=family_id,
                user_id=identity.user_id,
                token_digest=digest,
                issued_at=now,
                expires_at=expires_at,
                created_ip=created_ip,
                user_agent=user_agent,
            )

            access_token = create_access_token(
                user_id=identity.user_id,
                email=identity.email,
                roles=identity.roles,
                secret=self._token_secret,
                clock=self._clock,
            )
            return TokenPair(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=ACCESS_TOKEN_TTL_SECONDS,
            )

    async def refresh(self, refresh_token: str) -> TokenPair:
        """刷新令牌旋转：验证旧令牌 → 旋转 → 签发新令牌对。

        旋转逻辑：
        1. 计算摘要 → FOR UPDATE 查找会话
        2. 若 replaced_by 非空 → 重放攻击 → 整族撤销 → 抛 refresh_replayed
        3. 若 revoked_at 非空 → 令牌已失效（logout 或家族撤销） → 抛 invalid_credentials
        4. 若已过期 → 抛 invalid_credentials
        5. 旧行 replaced_by+revoked_at，INSERT 新行（同 family_id）

        重放检测的提交策略：
        先在事务内完成整族撤销，正常退出 session_scope 提交事务，
        然后在事务外抛出 refresh_replayed 错误，确保撤销已持久化。

        Args:
            refresh_token: 刷新令牌明文（从 HttpOnly cookie 获取）。

        Returns:
            TokenPair: 新的 access_token + refresh_token。

        Raises:
            AppError: code="refresh_replayed"，当检测到重放攻击时。
            AppError: code="invalid_credentials"，当令牌无效或已过期时。
        """
        digest = compute_refresh_digest(refresh_token)
        replay_error: AppError | None = None

        async with session_scope(self._session_factory) as session:
            old_session = await self._repository.find_session_by_digest_for_update(
                session, digest
            )
            now = self._clock.now()

            if old_session is None:
                raise AppError(
                    code="invalid_credentials",
                    message="刷新令牌无效",
                    retryable=False,
                    fields={},
                )

            if old_session.replaced_by is not None:
                # 重放攻击：此令牌已被旋转过 → 整族撤销
                await self._repository.revoke_family(
                    session, old_session.family_id, now
                )
                replay_error = AppError(
                    code="refresh_replayed",
                    message="刷新令牌已被使用，疑似重放攻击，已撤销该会话家族",
                    retryable=False,
                    fields={},
                )
            elif old_session.revoked_at is not None:
                raise AppError(
                    code="invalid_credentials",
                    message="刷新令牌已失效",
                    retryable=False,
                    fields={},
                )
            elif old_session.expires_at < now:
                raise AppError(
                    code="invalid_credentials",
                    message="刷新令牌已过期",
                    retryable=False,
                    fields={},
                )
            else:
                # 正常旋转：先 INSERT 新会话，再 UPDATE 旧行 replaced_by
                # （FK 约束要求 replaced_by 引用的行必须已存在）
                new_refresh_token = generate_refresh_token()
                new_digest = compute_refresh_digest(new_refresh_token)
                new_session_id = new_id()
                expires_at: timedelta = REFRESH_TOKEN_TTL
                await self._repository.create_refresh_session(
                    session,
                    session_id=new_session_id,
                    family_id=old_session.family_id,
                    user_id=old_session.user_id,
                    token_digest=new_digest,
                    issued_at=now,
                    expires_at=now + expires_at,
                    created_ip=old_session.created_ip,
                    user_agent=old_session.user_agent,
                )
                await self._repository.rotate_session(
                    session, old_session.id, new_session_id, now
                )
                user = await self._repository.find_user_by_id(
                    session, old_session.user_id
                )
                if user is None:
                    raise AppError(
                        code="invalid_credentials",
                        message="用户不存在",
                        retryable=False,
                        fields={},
                    )
                access_token = create_access_token(
                    user_id=user.id,
                    email=user.email,
                    roles=[],
                    secret=self._token_secret,
                    clock=self._clock,
                )
                return TokenPair(
                    access_token=access_token,
                    refresh_token=new_refresh_token,
                    expires_in=ACCESS_TOKEN_TTL_SECONDS,
                )

        # 事务已提交（含整族撤销），事务外抛出重放错误
        if replay_error is not None:
            raise replay_error

        # 不可达：所有分支均已 return 或 raise
        raise AppError(
            code="internal_error",
            message="刷新流程异常终止",
            retryable=False,
            fields={},
        )

    async def logout(self, refresh_token: str) -> None:
        """登出：撤销当前刷新会话。

        幂等：若令牌无效或会话不存在，静默返回（不泄露信息）。

        Args:
            refresh_token: 刷新令牌明文（从 HttpOnly cookie 获取）。
        """
        digest = compute_refresh_digest(refresh_token)
        async with session_scope(self._session_factory) as session:
            old_session = await self._repository.find_session_by_digest(
                session, digest
            )
            if old_session is not None and old_session.revoked_at is None:
                await self._repository.revoke_session(
                    session, old_session.id, self._clock.now()
                )
