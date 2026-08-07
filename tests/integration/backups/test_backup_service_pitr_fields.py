"""BackupRecordService PITR 字段集成测试。

验证 packages/backups/service.py 的 PITR 升级变更：
- create() 带 backup_method 参数；
- mark_succeeded() 带 PITR 元数据（backup_timestamp、wal_start_lsn、wal_end_lsn）；
- mark_restored() 记录 recovery_target_time。

需要真实数据库（IRIP_TEST_DATABASE_URL）。
对应 docs/arch-db-backup-pitr-upgrade.md §3.4 / T01。
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.backups.entities import (
    BackupMethod,
    BackupRecord,
    BackupStatus,
    BackupType,
)
from packages.backups.service import BackupRecordService
from packages.common.database import session_scope
from packages.common.errors import AppError

pytestmark = pytest.mark.integration


# ---- 辅助函数 ----


async def _insert_record(
    factory: async_sessionmaker[AsyncSession],
    record: BackupRecord,
) -> BackupRecord:
    """直接插入 BackupRecord。"""
    async with session_scope(factory) as session:
        session.add(record)
        await session.flush()
    return record


async def _get_record(
    factory: async_sessionmaker[AsyncSession],
    record_id,
) -> BackupRecord | None:
    """按 ID 查询记录。"""
    async with factory() as session:
        return await session.scalar(sa.select(BackupRecord).where(BackupRecord.id == record_id))


# ============================================================
# 1. create() 带 backup_method 参数
# ============================================================


class TestCreateWithBackupMethod:
    """BackupRecordService.create() 带 backup_method 参数测试。"""

    async def test_create_default_backup_method_is_pitr(
        self,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """create() 不传 backup_method 时默认为 'pitr'。"""
        service = BackupRecordService(rls_session_factory)

        record = await service.create(
            backup_type=BackupType.DAILY.value,
            department_id=org_id,
            file_path="/backups/pitr-test",
        )

        assert record.backup_method == BackupMethod.PITR.value
        assert record.backup_method == "pitr"

    async def test_create_with_explicit_pitr_method(
        self,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """create() 显式传 backup_method='pitr'。"""
        service = BackupRecordService(rls_session_factory)

        record = await service.create(
            backup_type=BackupType.DAILY.value,
            department_id=org_id,
            file_path="/backups/pitr-explicit",
            backup_method=BackupMethod.PITR.value,
        )

        assert record.backup_method == "pitr"

    async def test_create_with_pg_dump_method(
        self,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """create() 显式传 backup_method='pg_dump'。"""
        service = BackupRecordService(rls_session_factory)

        record = await service.create(
            backup_type=BackupType.DAILY.value,
            department_id=org_id,
            file_path="/backups/pgdump-test",
            backup_method=BackupMethod.PG_DUMP.value,
        )

        assert record.backup_method == "pg_dump"

    async def test_create_persisted_backup_method(
        self,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """create() 的 backup_method 持久化到数据库。"""
        service = BackupRecordService(rls_session_factory)

        record = await service.create(
            backup_type=BackupType.MILESTONE.value,
            department_id=org_id,
            file_path="/backups/persisted-method",
            name="milestone-test",
            backup_method=BackupMethod.PG_DUMP.value,
        )

        # 从数据库重新查询
        found = await _get_record(rls_session_factory, record.id)
        assert found is not None
        assert found.backup_method == "pg_dump"


# ============================================================
# 2. mark_succeeded() 带 PITR 元数据
# ============================================================


class TestMarkSucceededPitrMetadata:
    """mark_succeeded() 带 PITR 元数据测试。"""

    async def test_mark_succeeded_with_backup_timestamp(
        self,
        rls_session_factory,
        backup_factory,
        org_id,
        cleanup_backup_records,
    ):
        """mark_succeeded 写入 backup_timestamp。"""
        service = BackupRecordService(rls_session_factory)

        record = backup_factory(BackupType.DAILY.value, status=BackupStatus.PENDING.value)
        await _insert_record(rls_session_factory, record)

        backup_ts = datetime.now(UTC)
        updated = await service.mark_succeeded(
            record.id,
            sha256="abc123",
            file_size=1024,
            backup_timestamp=backup_ts,
        )

        assert updated.backup_timestamp is not None
        # 容忍微秒级差异
        assert abs((updated.backup_timestamp - backup_ts).total_seconds()) < 1

    async def test_mark_succeeded_with_wal_lsn(
        self,
        rls_session_factory,
        backup_factory,
        org_id,
        cleanup_backup_records,
    ):
        """mark_succeeded 写入 wal_start_lsn 和 wal_end_lsn。"""
        service = BackupRecordService(rls_session_factory)

        record = backup_factory(BackupType.DAILY.value, status=BackupStatus.PENDING.value)
        await _insert_record(rls_session_factory, record)

        updated = await service.mark_succeeded(
            record.id,
            sha256="sha-test",
            file_size=2048,
            wal_start_lsn="0/2000000",
            wal_end_lsn="0/2000123",
        )

        assert updated.wal_start_lsn == "0/2000000"
        assert updated.wal_end_lsn == "0/2000123"

    async def test_mark_succeeded_with_all_pitr_metadata(
        self,
        rls_session_factory,
        backup_factory,
        org_id,
        cleanup_backup_records,
    ):
        """mark_succeeded 写入全部 PITR 元数据。"""
        service = BackupRecordService(rls_session_factory)

        record = backup_factory(BackupType.DAILY.value, status=BackupStatus.PENDING.value)
        await _insert_record(rls_session_factory, record)

        backup_ts = datetime.now(UTC)
        updated = await service.mark_succeeded(
            record.id,
            sha256="full-pitr-sha",
            file_size=4096,
            migration_version="0061",
            application_version="0.1.0",
            backup_timestamp=backup_ts,
            wal_start_lsn="0/1000000",
            wal_end_lsn="0/2000000",
        )

        assert updated.status == BackupStatus.SUCCEEDED.value
        assert updated.sha256 == "full-pitr-sha"
        assert updated.file_size == 4096
        assert updated.migration_version == "0061"
        assert updated.backup_timestamp is not None
        assert updated.wal_start_lsn == "0/1000000"
        assert updated.wal_end_lsn == "0/2000000"

    async def test_mark_succeeded_without_pitr_metadata(
        self,
        rls_session_factory,
        backup_factory,
        org_id,
        cleanup_backup_records,
    ):
        """mark_succeeded 不传 PITR 元数据时不报错（v1 兼容）。"""
        service = BackupRecordService(rls_session_factory)

        record = backup_factory(BackupType.DAILY.value, status=BackupStatus.PENDING.value)
        await _insert_record(rls_session_factory, record)

        updated = await service.mark_succeeded(
            record.id,
            sha256="v1-sha",
            file_size=512,
        )

        assert updated.status == BackupStatus.SUCCEEDED.value
        # PITR 字段保持 None/默认
        assert updated.backup_timestamp is None
        assert updated.wal_start_lsn is None
        assert updated.wal_end_lsn is None

    async def test_mark_succeeded_pitr_metadata_persisted(
        self,
        rls_session_factory,
        backup_factory,
        org_id,
        cleanup_backup_records,
    ):
        """mark_succeeded 的 PITR 元数据持久化到数据库。"""
        service = BackupRecordService(rls_session_factory)

        record = backup_factory(BackupType.DAILY.value, status=BackupStatus.PENDING.value)
        await _insert_record(rls_session_factory, record)

        backup_ts = datetime.now(UTC)
        await service.mark_succeeded(
            record.id,
            sha256="persist-sha",
            backup_timestamp=backup_ts,
            wal_start_lsn="0/AAAA0000",
            wal_end_lsn="0/BBBB0000",
        )

        found = await _get_record(rls_session_factory, record.id)
        assert found is not None
        assert found.wal_start_lsn == "0/AAAA0000"
        assert found.wal_end_lsn == "0/BBBB0000"
        assert found.backup_timestamp is not None


# ============================================================
# 3. mark_restored() 记录 recovery_target_time
# ============================================================


class TestMarkRestored:
    """mark_restored() 记录恢复目标时间测试。"""

    async def test_mark_restored_with_recovery_target_time(
        self,
        rls_session_factory,
        backup_factory,
        org_id,
        cleanup_backup_records,
    ):
        """mark_restored 写入 recovery_target_time。"""
        service = BackupRecordService(rls_session_factory)

        record = backup_factory(BackupType.DAILY.value, status=BackupStatus.SUCCEEDED.value)
        await _insert_record(rls_session_factory, record)

        target_time = datetime.now(UTC)
        updated = await service.mark_restored(record.id, recovery_target_time=target_time)

        assert updated.recovery_target_time is not None
        assert abs((updated.recovery_target_time - target_time).total_seconds()) < 1

    async def test_mark_restored_with_none_recovery_target_time(
        self,
        rls_session_factory,
        backup_factory,
        org_id,
        cleanup_backup_records,
    ):
        """mark_restored 传 None 时 recovery_target_time 为 None。"""
        service = BackupRecordService(rls_session_factory)

        record = backup_factory(BackupType.DAILY.value, status=BackupStatus.SUCCEEDED.value)
        await _insert_record(rls_session_factory, record)

        updated = await service.mark_restored(record.id, recovery_target_time=None)

        assert updated.recovery_target_time is None

    async def test_mark_restored_nonexistent_raises(
        self,
        rls_session_factory,
        cleanup_backup_records,
    ):
        """标记不存在的记录 → AppError(not_found)。"""
        service = BackupRecordService(rls_session_factory)

        with pytest.raises(AppError) as exc_info:
            await service.mark_restored(uuid4(), recovery_target_time=datetime.now(UTC))

        assert exc_info.value.code == "not_found"

    async def test_mark_restored_recovery_target_time_persisted(
        self,
        rls_session_factory,
        backup_factory,
        org_id,
        cleanup_backup_records,
    ):
        """mark_restored 的 recovery_target_time 持久化到数据库。"""
        service = BackupRecordService(rls_session_factory)

        record = backup_factory(BackupType.DAILY.value, status=BackupStatus.SUCCEEDED.value)
        await _insert_record(rls_session_factory, record)

        target_time = datetime.now(UTC)
        await service.mark_restored(record.id, recovery_target_time=target_time)

        found = await _get_record(rls_session_factory, record.id)
        assert found is not None
        assert found.recovery_target_time is not None
        assert abs((found.recovery_target_time - target_time).total_seconds()) < 1
