"""认证相关依赖覆盖 provider（F-20）。

注册：
- AuthService（认证服务）；
- JWT token_secret；
- /me 端点用的 DB 会话工厂。
"""

from apps.api.composition import CompositionContext
from apps.api.dependencies.auth import get_auth_session_factory, get_token_secret
from apps.api.routers.account import get_account_service
from apps.api.routers.auth import get_auth_service, get_me_session_factory


def register(ctx: CompositionContext) -> None:
    """注册认证相关依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from packages.auth.backends import LocalAuthBackend
    from packages.auth.repository import AuthRepository
    from packages.auth.service import AuthService
    from packages.common.clock import SystemClock

    auth_repository = AuthRepository()
    auth_backend = LocalAuthBackend(auth_repository)
    auth_service = AuthService(
        backend=auth_backend,
        repository=auth_repository,
        session_factory=ctx.session_factory,
        token_secret=ctx.token_secret,
        clock=SystemClock(),
    )

    ctx.app.dependency_overrides[get_auth_service] = lambda: auth_service
    ctx.app.dependency_overrides[get_token_secret] = lambda: ctx.token_secret

    # 账户自助服务复用同一个 AuthService 实例（ORM 操作已下沉到 service 层）
    ctx.app.dependency_overrides[get_account_service] = lambda: auth_service

    # /me 端点用的 DB 会话工厂
    ctx.app.dependency_overrides[get_me_session_factory] = lambda: ctx.session_factory

    # get_current_user 查询 department_id 用的 DB 会话工厂
    ctx.app.dependency_overrides[get_auth_session_factory] = lambda: ctx.session_factory
