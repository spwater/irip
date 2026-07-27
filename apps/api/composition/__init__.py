"""Composition Root：按领域拆分的依赖注入组装层（F-20）。

将 main.py 中的依赖覆盖逻辑按领域分组到 provider 模块，
main.py 的 lifespan 创建共享上下文后调用各 provider 注册依赖。

各 provider 模块均暴露 ``register(ctx: CompositionContext) -> None`` 函数，
在函数内部通过 ``app.dependency_overrides`` 注册对应领域的依赖覆盖。
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.errors import AppError


@dataclass
class CompositionContext:
    """组合根共享上下文。

    在 lifespan 中创建一次，传递给各 provider 模块注册依赖覆盖。

    Attributes:
        app: FastAPI 应用实例（用于设置 dependency_overrides）。
        session_factory: 异步数据库会话工厂。
        s3_repo: S3 / MinIO 存储客户端。
        redis_url: Redis 连接 URL。
        token_secret: JWT 签名密钥。
    """

    app: FastAPI
    session_factory: async_sessionmaker[AsyncSession]
    s3_repo: object
    redis_url: str
    token_secret: str


async def lookup_org_id(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: UUID,
) -> UUID:
    """从数据库查询用户的 organization_id（fail-closed）。

    安全约定（技术设计文档 F-02/F-08）：
    - 查不到用户或 organization_id 时 raise AppError(code="forbidden")；
    - **禁止**回退到 IRIP-DEMO 组织或生成随机 UUID；
    - fail-closed 确保未分配组织的用户无法访问任何资源。

    Args:
        session_factory: 异步会话工厂。
        user_id: 当前用户 UUID。

    Returns:
        UUID: 用户的 organization_id。

    Raises:
        AppError: code="forbidden"，当用户不存在或无 organization_id 时。
    """
    import sqlalchemy as sa

    from packages.auth.entities import AppUser

    async with session_factory() as session:
        user: AppUser | None = await session.scalar(
            sa.select(AppUser).where(AppUser.id == user_id)
        )
        if user is None:
            raise AppError(
                code="forbidden",
                message=f"用户不存在或未分配组织: {user_id}",
                retryable=False,
                fields={"user_id": str(user_id)},
            )
        if user.organization_id is None:
            raise AppError(
                code="forbidden",
                message=f"用户未分配组织（organization_id 为空）: {user_id}",
                retryable=False,
                fields={"user_id": str(user_id)},
            )
        return user.organization_id


def register_all(ctx: CompositionContext) -> None:
    """注册全部领域的依赖覆盖。

    按领域依次调用各 provider 的 register 函数。

    Args:
        ctx: 组合根共享上下文。
    """
    from apps.api.composition.ai import register as register_ai
    from apps.api.composition.auth import register as register_auth
    from apps.api.composition.facts import register as register_facts
    from apps.api.composition.flows import register as register_flows
    from apps.api.composition.infrastructure import (
        register as register_infrastructure,
    )
    from apps.api.composition.jobs import register as register_jobs
    from apps.api.composition.models import register as register_models
    from apps.api.composition.standards import (
        register as register_standards,
    )

    register_auth(ctx)
    register_infrastructure(ctx)
    register_jobs(ctx)
    register_standards(ctx)
    register_facts(ctx)
    register_flows(ctx)
    register_models(ctx)
    register_ai(ctx)
