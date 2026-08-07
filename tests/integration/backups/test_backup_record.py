"""BackupRecordService 集成测试。

覆盖 packages/backups/service.py 的全部公共方法：
- create: 创建备份记录 + 保留策略计算（daily 14d / milestone NULL / pre_restore 7d）
- list_daily / list_milestone: 按类型查询
- mark_succeeded / mark_failed: 状态更新
- delete_expired: 过期清理

RLS: backup_record 表启用了 FORCE RLS，测试通过 rls_session_factory
自动设置 app.current_dept_id 绕过 RLS。

前置：测试数据库已启动并执行 alembic upgrade head（含迁移 0060）。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.backups.entities import (
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
    """直接插入 BackupRecord（通过 session_scope，RLS GUC 已由 factory 设置）。"""
    async with session_scope(factory) as session:
        session.add(record)
        await session.flush()
    return record


async def _count_records(
    factory: async_sessionmaker[AsyncSession],
) -> int:
    """统计当前 RLS 可见的 backup_record 数量。"""
    async with factory() as session:
        result = await session.execute(sa.select(sa.func.count()).select_from(BackupRecord))
        return result.scalar() or 0


# ============================================================
# 1. test_create_backup_record — 创建记录 + 保留策略
# ============================================================


class TestCreateBackupRecord:
    """BackupRecordService.create() 测试。"""

    async def test_create_daily_record(
        self,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """创建 daily 备份记录：status=pending, expires_at ≈ now+14d, backup_date=今天。"""
        service = BackupRecordService(rls_session_factory)

        record = await service.create(
            backup_type=BackupType.DAILY.value,
            department_id=org_id,
            file_path="/backups/daily-test",
        )

        assert record.id is not None
        assert record.backup_type == BackupType.DAILY.value
        assert record.status == BackupStatus.PENDING.value
        assert record.file_path == "/backups/daily-test"
        assert record.expires_at is not None
        # 过期时间应在 13~15 天范围内（容忍微秒级差异）
        now = datetime.now(UTC)
        delta = record.expires_at - now
        assert timedelta(days=13) < delta < timedelta(days=15)
        assert record.backup_date == now.date()
        assert record.name is None  # daily 无 name
        assert record.department_id == org_id

    async def test_create_milestone_record(
        self,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """创建 milestone 备份记录：expires_at=None（永久保留），有 name+description。"""
        service = BackupRecordService(rls_session_factory)

        record = await service.create(
            backup_type=BackupType.MILESTONE.value,
            department_id=org_id,
            file_path="/backups/milestone-test",
            name="v1.0-release",
            description="Initial release checkpoint",
        )

        assert record.backup_type == BackupType.MILESTONE.value
        assert record.name == "v1.0-release"
        assert record.description == "Initial release checkpoint"
        assert record.expires_at is None  # 永久保留
        assert record.backup_date is None  # milestone 无 backup_date

    async def test_create_pre_restore_record(
        self,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """创建 pre_restore 备份记录：expires_at ≈ now+7d。"""
        service = BackupRecordService(rls_session_factory)

        record = await service.create(
            backup_type=BackupType.PRE_RESTORE.value,
            department_id=org_id,
            file_path="/backups/pre-restore-test",
        )

        assert record.backup_type == BackupType.PRE_RESTORE.value
        assert record.expires_at is not None
        now = datetime.now(UTC)
        delta = record.expires_at - now
        assert timedelta(days=6) < delta < timedelta(days=8)

    async def test_create_milestone_without_name_raises(
        self,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """milestone 类型未提供 name → AppError(validation_failed)。"""
        service = BackupRecordService(rls_session_factory)

        with pytest.raises(AppError) as exc_info:
            await service.create(
                backup_type=BackupType.MILESTONE.value,
                department_id=org_id,
                file_path="/backups/milestone-no-name",
            )

        assert exc_info.value.code == "validation_failed"
        assert "名称" in exc_info.value.message

    async def test_create_with_explicit_expires_at(
        self,
        rls_session_factory,
        org_id,
        cleanup_backup_records,
    ):
        """显式传入 expires_at 时不按类型自动计算。"""
        service = BackupRecordService(rls_session_factory)
        custom_expiry = datetime.now(UTC) + timedelta(days=30)

        record = await service.create(
            backup_type=BackupType.DAILY.value,
            department_id=org_id,
            file_path="/backups/custom-expiry",
            expires_at=custom_expiry,
        )

        # 显式 expires_at 应被尊重（容忍时间戳微秒差异）
        assert abs((record.expires_at - custom_expiry).total_seconds()) < 1


# ============================================================
# 2. test_list_daily_backups — 列出每日备份
# ============================================================


class TestListDailyBackups:
    """list_daily() 测试。"""

    async def test_list_daily_returns_only_daily(
        self,
        rls_session_factory,
        backup_factory,
        cleanup_backup_records,
    ):
        """list_daily 只返回 daily 类型记录，不包含 milestone/pre_restore。"""
        service = BackupRecordService(rls_session_factory)

        # 插入 daily + milestone + pre_restore
        await _insert_record(rls_session_factory, backup_factory(BackupType.DAILY.value))
        await _insert_record(
            rls_session_factory,
            backup_factory(BackupType.MILESTONE.value, name="milestone-1"),
        )
        await _insert_record(rls_session_factory, backup_factory(BackupType.PRE_RESTORE.value))

        records = await service.list_daily()

        assert len(records) == 1
        assert all(r.backup_type == BackupType.DAILY.value for r in records)

    async def test_list_daily_ordered_by_created_at_desc(
        self,
        rls_session_factory,
        backup_factory,
        cleanup_backup_records,
    ):
        """list_daily 按 created_at DESC 排列。"""
        service = BackupRecordService(rls_session_factory)

        now = datetime.now(UTC)
        old = backup_factory(BackupType.DAILY.value, created_at=now - timedelta(days=2))
        mid = backup_factory(BackupType.DAILY.value, created_at=now - timedelta(days=1))
        new = backup_factory(BackupType.DAILY.value, created_at=now)

        await _insert_record(rls_session_factory, old)
        await _insert_record(rls_session_factory, mid)
        await _insert_record(rls_session_factory, new)

        records = await service.list_daily()

        assert len(records) == 3
        # 最新的在前
        assert records[0].created_at >= records[1].created_at
        assert records[1].created_at >= records[2].created_at

    async def test_list_daily_respects_limit(
        self,
        rls_session_factory,
        backup_factory,
        cleanup_backup_records,
    ):
        """list_daily(limit=N) 最多返回 N 条。"""
        service = BackupRecordService(rls_session_factory)

        for _ in range(5):
            await _insert_record(rls_session_factory, backup_factory(BackupType.DAILY.value))

        records = await service.list_daily(limit=2)
        assert len(records) == 2


# ============================================================
# 3. test_list_milestone_backups — 列出里程碑备份
# ============================================================


class TestListMilestoneBackups:
    """list_milestone() 测试。"""

    async def test_list_milestone_returns_only_milestone(
        self,
        rls_session_factory,
        backup_factory,
        cleanup_backup_records,
    ):
        """list_milestone 只返回 milestone 类型记录。"""
        service = BackupRecordService(rls_session_factory)

        await _insert_record(rls_session_factory, backup_factory(BackupType.DAILY.value))
        await _insert_record(
            rls_session_factory,
            backup_factory(BackupType.MILESTONE.value, name="release-1"),
        )
        await _insert_record(
            rls_session_factory,
            backup_factory(BackupType.MILESTONE.value, name="release-2"),
        )

        records = await service.list_milestone()

        assert len(records) == 2
        assert all(r.backup_type == BackupType.MILESTONE.value for r in records)
        names = {r.name for r in records}
        assert names == {"release-1", "release-2"}


# ============================================================
# 4. test_delete_expired — 过期清理
# ============================================================


class TestDeleteExpired:
    """delete_expired() 测试。"""

    async def test_delete_expired_removes_past_expirations(
        self,
        rls_session_factory,
        backup_factory,
        cleanup_backup_records,
    ):
        """expires_at < now 的记录被清理。"""
        service = BackupRecordService(rls_session_factory)

        now = datetime.now(UTC)
        # 过期的 daily
        expired = backup_factory(
            BackupType.DAILY.value,
            created_at=now - timedelta(days=20),
            expires_at=now - timedelta(days=5),
            file_path="/backups/expired-dir",
        )
        # 未过期的 daily
        active = backup_factory(
            BackupType.DAILY.value,
            created_at=now,
            expires_at=now + timedelta(days=14),
        )
        # milestone（永久保留，expires_at=None，不应被清理）
        milestone = backup_factory(
            BackupType.MILESTONE.value,
            name="permanent",
            expires_at=None,
        )

        await _insert_record(rls_session_factory, expired)
        await _insert_record(rls_session_factory, active)
        await _insert_record(rls_session_factory, milestone)

        cleaned = await service.delete_expired()

        assert cleaned == 1
        total = await _count_records(rls_session_factory)
        assert total == 2  # active + milestone 保留

    async def test_delete_expired_preserves_milestone(
        self,
        rls_session_factory,
        backup_factory,
        cleanup_backup_records,
    ):
        """milestone（expires_at=None）永不被清理。"""
        service = BackupRecordService(rls_session_factory)

        await _insert_record(
            rls_session_factory,
            backup_factory(BackupType.MILESTONE.value, name="forever", expires_at=None),
        )

        cleaned = await service.delete_expired()
        assert cleaned == 0

    async def test_delete_expired_no_records(
        self,
        rls_session_factory,
        cleanup_backup_records,
    ):
        """无记录时返回 0。"""
        service = BackupRecordService(rls_session_factory)
        cleaned = await service.delete_expired()
        assert cleaned == 0


# ============================================================
# 5. test_mark_succeeded / test_mark_failed — 状态更新
# ============================================================


class TestMarkSucceeded:
    """mark_succeeded() 测试。"""

    async def test_mark_succeeded_updates_status_and_metadata(
        self,
        rls_session_factory,
        backup_factory,
        cleanup_backup_records,
    ):
        """mark_succeeded 更新 status=succeeded + completed_at + sha256 + file_size。"""
        service = BackupRecordService(rls_session_factory)

        record = backup_factory(BackupType.DAILY.value, status=BackupStatus.PENDING.value)
        await _insert_record(rls_session_factory, record)

        updated = await service.mark_succeeded(
            record.id,
            sha256="abc123def456",
            file_size=1024,
            migration_version="0060",
            application_version="1.0.0",
        )

        assert updated.status == BackupStatus.SUCCEEDED.value
        assert updated.completed_at is not None
        assert updated.sha256 == "abc123def456"
        assert updated.file_size == 1024
        assert updated.migration_version == "0060"
        assert updated.application_version == "1.0.0"

    async def test_mark_succeeded_nonexistent_raises(
        self,
        rls_session_factory,
        cleanup_backup_records,
    ):
        """标记不存在的记录 → AppError(not_found)。"""
        service = BackupRecordService(rls_session_factory)

        with pytest.raises(AppError) as exc_info:
            await service.mark_succeeded(uuid4(), sha256="abc")

        assert exc_info.value.code == "not_found"


class TestMarkFailed:
    """mark_failed() 测试。"""

    async def test_mark_failed_updates_status_and_error(
        self,
        rls_session_factory,
        backup_factory,
        cleanup_backup_records,
    ):
        """mark_failed 更新 status=failed + completed_at + error_message。"""
        service = BackupRecordService(rls_session_factory)

        record = backup_factory(BackupType.DAILY.value, status=BackupStatus.PENDING.value)
        await _insert_record(rls_session_factory, record)

        updated = await service.mark_failed(record.id, "pg_dump failed: connection refused")

        assert updated.status == BackupStatus.FAILED.value
        assert updated.completed_at is not None
        assert updated.error_message == "pg_dump failed: connection refused"

    async def test_mark_failed_truncates_long_error(
        self,
        rls_session_factory,
        backup_factory,
        cleanup_backup_records,
    ):
        """error_message 超过 2000 字符时截断。"""
        service = BackupRecordService(rls_session_factory)

        record = backup_factory(BackupType.DAILY.value, status=BackupStatus.PENDING.value)
        await _insert_record(rls_session_factory, record)

        long_error = "x" * 3000
        updated = await service.mark_failed(record.id, long_error)

        assert updated.error_message is not None
        assert len(updated.error_message) == 2000

    async def test_mark_failed_nonexistent_raises(
        self,
        rls_session_factory,
        cleanup_backup_records,
    ):
        """标记不存在的记录 → AppError(not_found)。"""
        service = BackupRecordService(rls_session_factory)

        with pytest.raises(AppError) as exc_info:
            await service.mark_failed(uuid4(), "error")

        assert exc_info.value.code == "not_found"


# ============================================================
# 附加：get / get_by_job_id / delete
# ============================================================


class TestGetAndDelete:
    """get / get_by_job_id / delete 测试。"""

    async def test_get_returns_record(
        self,
        rls_session_factory,
        backup_factory,
        cleanup_backup_records,
    ):
        """get() 按 ID 查询成功。"""
        service = BackupRecordService(rls_session_factory)
        record = backup_factory(BackupType.DAILY.value)
        await _insert_record(rls_session_factory, record)

        found = await service.get(record.id)
        assert found.id == record.id
        assert found.backup_type == BackupType.DAILY.value

    async def test_get_nonexistent_raises_not_found(
        self,
        rls_session_factory,
        cleanup_backup_records,
    ):
        """get() 不存在的 ID → AppError(not_found)。"""
        service = BackupRecordService(rls_session_factory)

        with pytest.raises(AppError) as exc_info:
            await service.get(uuid4())

        assert exc_info.value.code == "not_found"

    async def test_delete_removes_record(
        self,
        rls_session_factory,
        backup_factory,
        cleanup_backup_records,
    ):
        """delete() 删除记录。"""
        service = BackupRecordService(rls_session_factory)
        record = backup_factory(BackupType.DAILY.value)
        await _insert_record(rls_session_factory, record)

        await service.delete(record.id)

        total = await _count_records(rls_session_factory)
        assert total == 0
