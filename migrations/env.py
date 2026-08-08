"""Alembic 迁移环境配置（async）。

从环境变量 ``IRIP_DATABASE_URL`` 读取连接字符串，自动将同步驱动
``postgresql+psycopg://`` 转换为异步驱动 ``postgresql+psycopg_async://``。
迁移通过 ``connection.run_sync()`` 在异步引擎上同步执行。

target_metadata 指向 ``packages.common.database.Base.metadata``，
确保 autogenerate 能发现所有 ORM 模型。
"""

import asyncio
import concurrent.futures
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
# RLS 通电后：应用运行时用 irip_app（非 superuser，受 RLS 约束），
# 迁移用 irip（superuser，可 DDL + 绕过 RLS 做 schema 操作）。
# 优先 IRIP_ALEMBIC_DATABASE_URL（迁移专用），退回 IRIP_DATABASE_URL。
_db_url = os.getenv("IRIP_ALEMBIC_DATABASE_URL") or os.getenv("IRIP_DATABASE_URL")
if _db_url is not None:
    if _db_url.startswith("postgresql+psycopg://"):
        _async_url = _db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        _async_url = _db_url
    config.set_main_option("sqlalchemy.url", _async_url)

target_metadata = Base.metadata

# ---- 导入所有 ORM 模型模块，确保 autogenerate 能发现所有表 ----
# H-02 修复：完整导入所有 packages 下的 entities / ORM 模块，
# 确保 Base.metadata 包含所有表定义，避免 autogenerate 遗漏。
import packages.ai.service  # noqa: F401, E402
import packages.ai.tool_repository  # noqa: F401, E402
import packages.audit.events  # noqa: F401, E402
import packages.auth.entities  # noqa: F401, E402
import packages.auth.scope_grants  # noqa: F401, E402
import packages.common.artifacts  # noqa: F401, E402
import packages.components.flow.flow_runtime  # noqa: F401, E402
import packages.components.registry  # noqa: F401, E402
import packages.connectors.entities  # noqa: F401, E402
import packages.departments.entities  # noqa: F401, E402
import packages.equipment.entities  # noqa: F401, E402
import packages.facts.entities  # noqa: F401, E402
import packages.jobs.entities  # noqa: F401, E402
import packages.jobs.outbox  # noqa: F401, E402
import packages.models.entities  # noqa: F401, E402
import packages.parameters.entities  # noqa: F401, E402
import packages.provenance.entities  # noqa: F401, E402
import packages.standards.methods  # noqa: F401, E402
import packages.standards.objects.object_type_dict  # noqa: F401, E402
import packages.standards.objects  # noqa: F401, E402


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
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running event loop — safe to use asyncio.run()
        asyncio.run(run_migrations_online())
    else:
        # Already inside a running event loop (e.g., pytest-asyncio tests).
        # Run in a separate thread so asyncio.run() can create its own loop.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, run_migrations_online())
            future.result()
