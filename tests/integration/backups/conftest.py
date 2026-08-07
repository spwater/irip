"""备份功能集成测试 fixtures。

提供 RLS 感知的会话工厂和测试数据辅助：
- rls_session_factory: 每次会话自动 SET LOCAL app.current_dept_id，绕过 RLS；
- backup_factory: 构造 BackupRecord 实例的工厂函数；
- cleanup_backup_records: 测试后清理 backup_record 表。

RLS 约定：backup_record 表启用了 FORCE ROW LEVEL SECURITY（迁移 0048），
策略 tenant_isolation: department_id = current_setting('app.current_dept_id', true)::uuid。
测试中的 async_session_factory（NullPool + create_async_engine）不会自动设置 GUC，
因此需要通过 engine 事件监听器在连接级别设置默认 GUC。
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from packages.backups.entities import (
    DAILY_RETENTION_DAYS,
    PRE_RESTORE_RETENTION_DAYS,
    BackupRecord,
    BackupStatus,
    BackupType,
)
from packages.common.ids import new_id


def _to_async_url(url: str) -> str:
    """将同步 psycopg URL 转换为异步 psycopg_async URL。"""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    return url


@pytest.fixture
def org_id() -> UUID:
    """测试用组织 ID（固定值，便于 RLS GUC 设置）。"""
    return UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def rls_session_factory(sync_engine, org_id: UUID) -> async_sessionmaker[AsyncSession]:
    """提供 RLS 感知的异步会话工厂。

    每次获取连接时自动设置 app.current_dept_id = org_id，
    使 RLS 策略能正确过滤租户数据。
    """
    import os

    url = os.getenv("IRIP_TEST_DATABASE_URL")
    async_url = _to_async_url(url)
    engine = create_async_engine(async_url, poolclass=NullPool)

    org_id_str = str(org_id)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_tenant_guc(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        """连接级别设置租户 GUC。"""
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET app.current_dept_id = '{org_id_str}'")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    # 引擎在 session 结束时自动释放


@pytest.fixture
def backup_factory(org_id: UUID):
    """构造 BackupRecord 测试实例的工厂函数。

    返回一个函数 create_backup_record(backup_type, **kwargs) -> BackupRecord，
    使用默认值填充必填字段。
    """

    def _create(
        backup_type: str = BackupType.DAILY.value,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str = BackupStatus.PENDING.value,
        file_path: str | None = None,
        file_size: int | None = None,
        sha256: str | None = None,
        created_at: datetime | None = None,
        expires_at: datetime | None = None,
        backup_date=None,
        department_id: UUID | None = None,
    ) -> BackupRecord:
        """创建 BackupRecord 实例（未持久化）。"""
        now = created_at or datetime.now(UTC)
        if expires_at is None and backup_type == BackupType.DAILY.value:
            expires_at = now + timedelta(days=DAILY_RETENTION_DAYS)
        elif expires_at is None and backup_type == BackupType.PRE_RESTORE.value:
            expires_at = now + timedelta(days=PRE_RESTORE_RETENTION_DAYS)
        # milestone: expires_at = None（永久保留）

        return BackupRecord(
            id=new_id(),
            job_id=None,
            backup_type=backup_type,
            name=name,
            description=description,
            backup_date=backup_date or (now.date() if backup_type == BackupType.DAILY.value else None),
            file_path=file_path or f"/backups/{new_id().hex}",
            file_size=file_size,
            sha256=sha256,
            status=status,
            created_by=None,
            created_at=now,
            expires_at=expires_at,
            department_id=department_id or org_id,
        )

    return _create


@pytest.fixture
def cleanup_backup_records(sync_engine, org_id: UUID):
    """测试后清理 backup_record 表中指定组织的数据。"""
    yield
    with sync_engine.connect() as conn:
        conn.execute(
            sa.text("DELETE FROM backup_record WHERE department_id = :oid"),
            {"oid": str(org_id)},
        )
        conn.commit()
