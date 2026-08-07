"""IRIP 数据库会话管理。

提供：
- Base：所有 ORM 模型的声明式基类，供 Alembic 迁移读取 metadata；
- build_session_factory：构建异步会话工厂；
- session_scope：事务级异步会话上下文管理器（自动 commit / rollback）。

约定（与 docs/arch-v0.md S7.6 对齐）：
- 所有数据库写操作走 session_scope()；
- session_factory 在应用启动时构建一次，通过依赖注入传递。
- 每事务通过 SET LOCAL 设置 dept + user 两个 GUC（C-03 升级）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from packages.common.tenant_guc import (
    DEPT_GUC,
    USER_GUC,
    set_dept_guc,
    set_user_guc,
)

if TYPE_CHECKING:
    from packages.common.principal import Principal


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。

    Alembic 迁移通过 ``Base.metadata`` 获取表结构定义。
    后续任务的 ORM 模型（app_user / refresh_session / job 等）均继承此类。
    """


def build_session_factory(url: str) -> async_sessionmaker[AsyncSession]:
    """构建异步会话工厂。

    Args:
        url: 异步数据库连接字符串（如 ``postgresql+psycopg_async://...``）。

    Returns:
        async_sessionmaker[AsyncSession]: 会话工厂，``expire_on_commit=False``
        避免提交后访问属性触发隐式刷新。
    """
    engine = create_async_engine(url, pool_pre_ping=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_tenant_guc_default(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        """连接级别设置 GUC 默认值为空字符串。

        事务级别由 session_scope 设置实际租户 ID。
        缺失时 fail closed（RLS 返回空）。
        """
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET {DEPT_GUC} = ''")
        cursor.execute(f"SET {USER_GUC} = ''")
        cursor.close()

    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
    *,
    principal: Principal | None = None,
) -> AsyncIterator[AsyncSession]:
    """事务级异步会话上下文。

    进入时创建会话并开启事务；正常退出时提交，异常时回滚。
    确保所有数据库写操作在同一事务内完成（含 Outbox 事件同事务插入）。

    如果提供了 principal，在事务开始时设置 GUC：
    - SET LOCAL app.current_dept_id = :dept_id（RLS 部门隔离）
    - SET LOCAL app.current_user_id = :user_id（私有可见性 + AI 会话 RLS）

    缺失 principal 时 RLS 保护的表上 fail closed（返回空）。

    Args:
        factory: build_session_factory() 返回的会话工厂。
        principal: 可选的可信身份上下文。提供时设置租户 GUC。

    Yields:
        AsyncSession: 已开启事务的异步会话。
    """
    async with factory() as session:
        async with session.begin():
            if principal is not None:
                # 设置 dept + user GUC
                await set_dept_guc(session, principal.department_id)
                await set_user_guc(session, principal.user_id)
            yield session


@asynccontextmanager
async def scoped_session(
    factory: async_sessionmaker[AsyncSession],
    dept_id: UUID | None = None,
    user_id: UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    """带租户 GUC 的事务级异步会话上下文管理器。

    进入时创建会话并开启事务，在事务开始时按需设置：
    - SET LOCAL app.current_dept_id = :dept_id（RLS 部门隔离）；
    - SET LOCAL app.current_user_id = :user_id（私有可见性 + AI 会话 RLS）。

    用途：替代 Service 中手动的 ``self._factory() as session``（不设 GUC、
    不开事务）与 ``session_scope(self._factory)``（开事务但不设 GUC）两种
    导致 RLS fail-closed 拦截数据的写法。Service 基类 ``ScopedSessionMixin``
    通过 ``self._scoped_session()`` 间接调用本函数；需要按方法传身份的
    场景（如 AIService 每次调用携带不同 user_id）可直接调用本函数。

    缺失 dept_id / user_id 时对应 GUC 不设置，保持连接级空串默认
    （由 ``build_session_factory`` 的 connect 监听器设为 ``''``），
    RLS 保护的表上 fail-closed（返回空），不会泄露跨租户数据。

    Args:
        factory: build_session_factory() 返回的会话工厂。
        dept_id: 部门 UUID，None 时不设 GUC（保持空串 fail-closed）。
        user_id: 用户 UUID，None 时不设 GUC（保持空串 fail-closed）。

    Yields:
        AsyncSession: 已开启事务并设置好 GUC 的异步会话。
    """
    async with factory() as session:
        async with session.begin():
            if dept_id is not None:
                await set_dept_guc(session, dept_id)
            if user_id is not None:
                await set_user_guc(session, user_id)
            yield session


class ScopedSessionMixin:
    """为 Service 提供统一的带租户 GUC 的 session 上下文管理器。

    依赖实例属性：
    - ``_factory``：异步会话工厂（``async_sessionmaker``）；
    - ``_dept_id``：当前部门 ID（RLS 部门隔离锚点）；
    - ``_actor_id``：当前操作者用户 ID（可选，私有可见性 + AI 会话 RLS）。

    子类继承本 Mixin 后，所有数据库方法统一使用 ``self._scoped_session()``
    获取已设置 GUC 的事务会话，替代手动的 ``self._factory() as session``
    与 ``session_scope(self._factory)``，从根本上保证 RLS 上下文一致，
    避免 fail-closed 拦截数据或逐方法手动设 GUC 的补丁式写法。

    缺失 ``_dept_id`` / ``_actor_id`` 时对应 GUC 不设置（保持连接级空串
    默认，RLS fail-closed），确保无身份上下文的调用不会泄露跨租户数据。
    """

    @asynccontextmanager
    async def _scoped_session(self) -> AsyncIterator[AsyncSession]:
        """带 GUC 的事务会话上下文，用 ``_rls_dept_id`` / ``_dept_id`` / ``_actor_id`` 自动设 GUC。

        RLS 部门 GUC 优先使用 ``_rls_dept_id``（平台管理员绕过隔离），
        缺失时回退到 ``_dept_id``（正常租户隔离）。

        ``_rls_dept_id`` 仅影响 RLS GUC（app.current_dept_id），
        ``_dept_id`` 仍用于业务逻辑（如设备编码唯一性检查），不受影响。
        ``_actor_id`` 始终使用实际用户 ID（私有数据可见性不受影响）。

        Yields:
            AsyncSession: 已开启事务并设置好租户 GUC 的异步会话。
        """
        rls_dept_id: UUID | None = getattr(self, "_rls_dept_id", None)
        dept_id: UUID | None = (
            rls_dept_id if rls_dept_id is not None else getattr(self, "_dept_id", None)
        )
        user_id: UUID | None = getattr(self, "_actor_id", None)
        async with scoped_session(self._factory, dept_id, user_id) as session:  # type: ignore[attr-defined]
            yield session
