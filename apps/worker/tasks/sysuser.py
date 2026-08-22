"""系统服务用户解析（worker 共享模块）。

为 Celery worker 提供系统服务用户 ID 解析能力，消除以 ``department_id``
冒充 ``actor_id`` / ``created_by`` / ``uploaded_by`` 的语义错误与潜在 FK 违约。

Celery worker 没有用户会话，此前直接将 ``department_id``（部门 UUID）传入
需要 ``app_user.id`` 的字段。本模块通过 bootstrap 创建的 ``system@irip.local``
系统服务用户来提供合法的 ``app_user`` 引用。

解析优先级：
  1. 环境变量 ``IRIP_SYSTEM_SERVICE_USER_ID``（bootstrap 输出，推荐方式）；
  2. 回退查询 ``app_user`` 表中 ``system@irip.local`` 用户。

首次解析后缓存到模块级变量，避免重复查询/解析。
"""

from __future__ import annotations

import os
from uuid import UUID

#: 缓存的系统服务用户 ID（首次解析后复用）。
_cached_system_service_user_id: UUID | None = None

#: 系统服务用户邮箱（与 bootstrap 常量一致）。
_SYSTEM_SERVICE_EMAIL: str = "system@irip.local"


def get_system_service_user_id_sync() -> UUID:
    """同步获取系统服务用户 ID（仅从环境变量）。

    适用于无法 await 的上下文。优先从环境变量解析，不查询数据库。

    Returns:
        UUID: 系统服务用户 ID。

    Raises:
        RuntimeError: 当环境变量未设置时。
    """
    global _cached_system_service_user_id
    if _cached_system_service_user_id is not None:
        return _cached_system_service_user_id

    env_id = os.getenv("IRIP_SYSTEM_SERVICE_USER_ID", "")
    if env_id:
        _cached_system_service_user_id = UUID(env_id)
        return _cached_system_service_user_id

    raise RuntimeError(
        "IRIP_SYSTEM_SERVICE_USER_ID not set. "
        "Run bootstrap first, then set this environment variable."
    )


async def get_system_service_user_id() -> UUID:
    """异步获取系统服务用户 ID（环境变量优先，回退查询数据库）。

    首次解析后缓存，后续调用直接返回缓存值。

    Returns:
        UUID: 系统服务用户 ID。

    Raises:
        RuntimeError: 当环境变量未设置且数据库中也不存在系统服务用户时。
    """
    global _cached_system_service_user_id
    if _cached_system_service_user_id is not None:
        return _cached_system_service_user_id

    # 优先从环境变量解析
    env_id = os.getenv("IRIP_SYSTEM_SERVICE_USER_ID", "")
    if env_id:
        _cached_system_service_user_id = UUID(env_id)
        return _cached_system_service_user_id

    # 回退：查询数据库
    import sqlalchemy as sa

    from packages.common.database import build_session_factory, get_database_url, session_scope

    db_url = get_database_url("postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip")
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        async_url = db_url

    factory = build_session_factory(async_url)
    async with session_scope(factory) as session:
        result = await session.execute(
            sa.text("SELECT id FROM app_user WHERE email = :email LIMIT 1"),
            {"email": _SYSTEM_SERVICE_EMAIL},
        )
        row = result.first()
        if row is None:
            raise RuntimeError(
                f"System service user ({_SYSTEM_SERVICE_EMAIL}) not found in DB. "
                "Run bootstrap to create it, or set IRIP_SYSTEM_SERVICE_USER_ID."
            )
        _cached_system_service_user_id = UUID(str(row[0]))

    return _cached_system_service_user_id
