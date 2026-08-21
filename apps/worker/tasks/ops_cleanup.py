"""运营维护任务：数据保留策略清理。

P2-I6: 定期清理超过 N 天的审计日志，防止表无限增长。

配置：
- IRIP_AUDIT_RETENTION_DAYS: 审计日志保留天数（默认 90 天）
- 仅超级用户权限可执行 DELETE（audit_event 表对 irip_app 角色仅追加）
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from celery import shared_task

from apps.worker.tasks import get_system_guc
from packages.common.clock import SystemClock
from packages.common.database import get_database_url, session_scope

logger = logging.getLogger(__name__)

_DEFAULT_RETENTION_DAYS = 90

_superuser_factory: Any | None = None


def _get_superuser_factory() -> Any:
    """获取超级用户 session factory 单例。

    使用 IRIP_ALEMBIC_DATABASE_URL（superuser 连接）绕过 RLS，
    因为 audit_event 表对 irip_app 角色仅追加（REVOKE UPDATE, DELETE）。
    """
    global _superuser_factory
    if _superuser_factory is not None:
        return _superuser_factory

    alembic_url = os.getenv("IRIP_ALEMBIC_DATABASE_URL", "") or get_database_url()
    if not alembic_url:
        raise RuntimeError("无法获取超级用户连接：IRIP_ALEMBIC_DATABASE_URL 未配置")

    async_url = alembic_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(async_url, pool_size=2, max_overflow=2)
    _superuser_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _superuser_factory


@shared_task(name="ops.audit_retention_cleanup", soft_time_limit=300, time_limit=600)
def audit_retention_cleanup() -> dict[str, Any]:
    """清理超过保留期的审计日志。

    删除 occurred_at 早于 (now - IRIP_AUDIT_RETENTION_DAYS) 的审计事件。
    使用超级用户连接绕过 RLS（audit_event 表对 app 角色仅追加）。

    Returns:
        dict: 清理结果摘要。
    """
    retention_days = int(os.getenv("IRIP_AUDIT_RETENTION_DAYS", str(_DEFAULT_RETENTION_DAYS)))
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    factory = _get_superuser_factory()
    clock = SystemClock()
    now = clock.now()

    logger.info(
        "Starting audit retention cleanup: retention_days=%d, cutoff=%s",
        retention_days,
        cutoff.isoformat(),
    )

    try:
        import asyncio

        async def _cleanup() -> int:
            sys_dept, sys_user = get_system_guc()
            async with session_scope(factory) as session:
                from packages.common.tenant_guc import set_dept_guc, set_user_guc

                await set_dept_guc(session, sys_dept)
                await set_user_guc(session, sys_user)

                result = await session.execute(
                    sa.text("DELETE FROM audit_event WHERE occurred_at < :cutoff"),
                    {"cutoff": cutoff},
                )
                return result.rowcount or 0  # type: ignore[attr-defined]

        deleted_count = asyncio.run(_cleanup())

        logger.info("Audit retention cleanup complete: deleted %d events", deleted_count)
        return {
            "status": "ok",
            "deleted_count": deleted_count,
            "retention_days": retention_days,
            "cutoff": cutoff.isoformat(),
            "executed_at": now.isoformat(),
        }
    except Exception as exc:
        logger.error("Audit retention cleanup failed: %s", exc, exc_info=True)
        raise
