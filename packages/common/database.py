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
