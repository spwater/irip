"""IRIP 数据库会话管理。

提供：
- Base：所有 ORM 模型的声明式基类，供 Alembic 迁移读取 metadata；
- build_session_factory：构建异步会话工厂；
- session_scope：事务级异步会话上下文管理器（自动 commit / rollback）。

约定（与 docs/arch-v0.md §7.6 对齐）：
- 所有数据库写操作走 session_scope()；
- session_factory 在应用启动时构建一次，通过依赖注入传递。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


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
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """事务级异步会话上下文。

    进入时创建会话并开启事务；正常退出时提交，异常时回滚。
    确保所有数据库写操作在同一事务内完成（含 Outbox 事件同事务插入）。

    Args:
        factory: build_session_factory() 返回的会话工厂。

    Yields:
        AsyncSession: 已开启事务的异步会话。
    """
    async with factory() as session:
        async with session.begin():
            yield session
