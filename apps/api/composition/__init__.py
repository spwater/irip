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
        root_dept_id: root 哨兵部门 ID，用于平台管理员/平台监督员
            的 RLS 绕过（使其 current_visible_dept_ids() 返回全部部门）。
            启动时查询一次，缓存在此处。可能为 None（DB 中无 root 部门）。
    """

    app: FastAPI
    session_factory: async_sessionmaker[AsyncSession]
    s3_repo: object
    redis_url: str
    token_secret: str
    root_dept_id: UUID | None = None


async def lookup_dept_id(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: UUID,
) -> UUID:
    """从数据库查询用户的 department_id（fail-closed）。

    阶段2：department_id 为主要租户标识，不可为空。

    安全约定：
    - 查不到用户或 department_id 时 raise AppError(code="forbidden")；
    - fail-closed 确保未分配部门的用户无法访问任何资源。

    Args:
        session_factory: 异步会话工厂。
        user_id: 当前用户 UUID。

    Returns:
        UUID: 用户的 department_id。

    Raises:
        AppError: code="forbidden"，当用户不存在或无 department_id 时。
    """
    import sqlalchemy as sa

    from packages.auth.entities import AppUser

    async with session_factory() as session:
        user: AppUser | None = await session.scalar(sa.select(AppUser).where(AppUser.id == user_id))
        if user is None:
            raise AppError(
                code="forbidden",
                message=f"用户不存在或未分配部门: {user_id}",
                retryable=False,
                fields={"user_id": str(user_id)},
            )
        if user.department_id is None:
            raise AppError(
                code="forbidden",
                message=f"用户未分配部门（department_id 为空）: {user_id}",
                retryable=False,
                fields={"user_id": str(user_id)},
            )
        return user.department_id


async def lookup_root_dept_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> UUID | None:
    """查询 root 哨兵部门 ID（code='root'）。

    启动时调用一次，缓存在 CompositionContext 中。
    平台管理员/平台监督员（非 root 成员）使用此 ID 设置 RLS GUC，
    使 current_visible_dept_ids() 返回全部部门（root + 所有子孙 = 全部）。

    Args:
        session_factory: 异步会话工厂。

    Returns:
        UUID | None: root 部门 ID，不存在时返回 None。
    """
    import sqlalchemy as sa

    from packages.departments.entities import Department

    async with session_factory() as session:
        result = await session.execute(
            sa.select(Department.id).where(Department.code == "root")
        )
        row = result.first()
        return row[0] if row is not None else None


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
