"""Alembic 迁移环境配置（async）。

从环境变量 ``IRIP_DATABASE_URL`` 读取连接字符串，自动将同步驱动
``postgresql+psycopg://`` 转换为异步驱动 ``postgresql+psycopg_async://``。
迁移通过 ``connection.run_sync()`` 在异步引擎上同步执行。

target_metadata 指向 ``packages.common.database.Base.metadata``，
确保 autogenerate 能发现所有 ORM 模型。
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from packages.common.database import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 从环境变量覆盖数据库 URL（支持 async 驱动）
_db_url = os.getenv("IRIP_DATABASE_URL")
if _db_url is not None:
    if _db_url.startswith("postgresql+psycopg://"):
        _async_url = _db_url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
    else:
        _async_url = _db_url
    config.set_main_option("sqlalchemy.url", _async_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本而不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """在同步连接上执行迁移（由 run_sync 调用）。"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """在线模式：使用异步引擎执行迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
