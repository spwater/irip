"""备份记录业务服务：CRUD + 保留策略清理。

BackupRecordService 封装 backup_record 表的全部业务操作：
- create(): 创建备份记录，自动计算 expires_at（按类型保留策略）；
- get(): 按 ID 查询，不存在抛 AppError(not_found)；
- list_by_type(): 按备份类型分页查询；
- list_daily() / list_milestone(): 便捷查询方法；
- get_by_job_id(): 按关联作业 ID 查询；
- mark_succeeded() / mark_failed(): 更新备份状态与结果元数据；
- delete(): 删除单条记录（调用方负责删除文件）；
- delete_expired(): 清理过期记录（删除文件 + 删除记录）。

保留策略：
- daily: expires_at = created_at + 14 days；
- milestone: expires_at = NULL（永久保留）；
- pre_restore: expires_at = created_at + 7 days。
"""

import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.backups.entities import (
    DAILY_RETENTION_DAYS,
    PRE_RESTORE_RETENTION_DAYS,
    BackupMethod,
    BackupRecord,
    BackupStatus,
    BackupType,
)
from packages.common.database import ScopedSessionMixin, scoped_session
from packages.common.errors import AppError
from packages.common.ids import new_id

logger = logging.getLogger(__name__)


class BackupRecordService(ScopedSessionMixin):
    """备份记录业务服务。

    依赖注入 async_sessionmaker，所有写操作走 session_scope 事务。

    Attributes:
        _factory: 异步会话工厂。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """初始化备份记录服务。

        Args:
            session_factory: 异步会话工厂。
        """
        self._factory: async_sessionmaker[AsyncSession] = session_factory

    async def create(
        self,
        *,
        backup_type: str,
        department_id: UUID,
        file_path: str,
        job_id: UUID | None = None,
        name: str | None = None,
        description: str | None = None,
        created_by: UUID | None = None,
        expires_at: datetime | None = None,
        backup_method: str = BackupMethod.PITR.value,
    ) -> BackupRecord:
        """创建备份记录。

        若未显式传入 expires_at，则按类型自动计算：
        - daily: created_at + 14 days；
        - milestone: NULL（永久保留）；
        - pre_restore: created_at + 7 days。

        Args:
            backup_type: 备份类型（daily / milestone / pre_restore）。
            department_id: 所属部门 ID。
            file_path: 备份文件路径。
            job_id: 关联作业 ID（系统自动可为 NULL）。
            name: 里程碑名称（milestone 必填）。
            description: 里程碑描述。
            created_by: 创建者用户 ID（系统自动为 NULL）。
            expires_at: 显式过期时间（留空则按类型自动计算）。
            backup_method: 备份方法（默认 pitr，存量 pg_dump）。

        Returns:
            BackupRecord: 新建的备份记录。

        Raises:
            AppError: code="validation_failed"，当 milestone 未提供 name 时。
        """
        if backup_type == BackupType.MILESTONE.value and not name:
            raise AppError(
                code="validation_failed",
                message="里程碑备份必须提供名称",
                retryable=False,
                fields={"name": "required"},
            )

        record_id: UUID = new_id()
        now: datetime = datetime.now(UTC)

        # 按类型计算过期时间（若未显式传入）
        if expires_at is None:
            if backup_type == BackupType.DAILY.value:
                expires_at = now + timedelta(days=DAILY_RETENTION_DAYS)
            elif backup_type == BackupType.PRE_RESTORE.value:
                expires_at = now + timedelta(days=PRE_RESTORE_RETENTION_DAYS)
            # milestone: expires_at 保持 None（永久保留）

        # daily 备份记录 backup_date 便于按天列表
        backup_date = now.date() if backup_type == BackupType.DAILY.value else None

        record = BackupRecord(
            id=record_id,
            job_id=job_id,
            backup_type=backup_type,
            name=name,
            description=description,
            backup_date=backup_date,
            file_path=file_path,
            status=BackupStatus.PENDING.value,
            created_by=created_by,
            created_at=now,
            expires_at=expires_at,
            department_id=department_id,
            backup_method=backup_method,
        )

        async with scoped_session(self._factory, department_id, created_by) as session:
            session.add(record)
            await session.flush()

        return record

    async def get(self, record_id: UUID) -> BackupRecord:
        """按 ID 查询备份记录。

        Args:
            record_id: 备份记录 UUID。

        Returns:
            BackupRecord: 备份记录。

        Raises:
            AppError: code="not_found"，当记录不存在时。
        """
        async with self._scoped_session() as session:
            record: BackupRecord | None = await session.scalar(
                sa.select(BackupRecord).where(BackupRecord.id == record_id)
            )
            if record is None:
                raise AppError(
                    code="not_found",
                    message=f"备份记录不存在: {record_id}",
                    retryable=False,
                    fields={"record_id": str(record_id)},
                )
            return record

    async def get_by_job_id(self, job_id: UUID) -> BackupRecord | None:
        """按关联作业 ID 查询备份记录。

        Args:
            job_id: 异步作业 UUID。

        Returns:
            BackupRecord | None: 备份记录，不存在时返回 None。
        """
        async with self._scoped_session() as session:
            return await session.scalar(  # type: ignore[no-any-return]
                sa.select(BackupRecord).where(BackupRecord.job_id == job_id)
            )

    async def list_by_type(
        self,
        *,
        backup_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> tuple[list[BackupRecord], bool]:
        """按类型分页查询备份记录。

        按 created_at DESC 排列，使用 id 作为游标（UUID 降序比较）。

        Args:
            backup_type: 备份类型筛选（None = 全部）。
            status: 状态筛选（None = 全部）。
            limit: 每页数量。
            cursor: 分页游标（上一页最后一条记录的 id）。

        Returns:
            tuple: (records, has_more)，has_more 表示是否还有更多数据。
        """
        async with self._scoped_session() as session:
            stmt = sa.select(BackupRecord).order_by(
                BackupRecord.created_at.desc(), BackupRecord.id.desc()
            )
            if backup_type is not None:
                stmt = stmt.where(BackupRecord.backup_type == backup_type)
            if status is not None:
                stmt = stmt.where(BackupRecord.status == status)
            if cursor is not None:
                stmt = stmt.where(BackupRecord.id < cursor)
            stmt = stmt.limit(limit + 1)
            result = await session.execute(stmt)
            rows: list[BackupRecord] = list(result.scalars().all())

        has_more: bool = len(rows) > limit
        return rows[:limit], has_more

    async def list_daily(self, limit: int = 14) -> list[BackupRecord]:
        """列出每日快照（最近 N 天，默认 14）。

        便捷方法，等价于 list_by_type(backup_type="daily", limit=14)。

        Args:
            limit: 返回数量上限（默认 14，对应 14 天保留窗口）。

        Returns:
            list[BackupRecord]: 每日快照列表（按 created_at DESC）。
        """
        records, _ = await self.list_by_type(backup_type=BackupType.DAILY.value, limit=limit)
        return records

    async def list_milestone(self, limit: int = 100) -> list[BackupRecord]:
        """列出里程碑备份。

        便捷方法，等价于 list_by_type(backup_type="milestone", limit=100)。

        Args:
            limit: 返回数量上限。

        Returns:
            list[BackupRecord]: 里程碑备份列表（按 created_at DESC）。
        """
        records, _ = await self.list_by_type(backup_type=BackupType.MILESTONE.value, limit=limit)
        return records

    async def mark_succeeded(
        self,
        record_id: UUID,
        *,
        sha256: str | None = None,
        file_size: int | None = None,
        migration_version: str | None = None,
        application_version: str | None = None,
        backup_timestamp: datetime | None = None,
        wal_start_lsn: str | None = None,
        wal_end_lsn: str | None = None,
    ) -> BackupRecord:
        """标记备份成功，更新校验和与版本元数据。

        Args:
            record_id: 备份记录 UUID。
            sha256: 数据库 dump SHA-256 校验和（v1）或 base.tar.gz SHA-256（v2）。
            file_size: 备份文件大小（字节）。
            migration_version: Alembic 迁移版本。
            application_version: IRIP 应用版本。
            backup_timestamp: 联合时间戳（PITR 备份时填入）。
            wal_start_lsn: pg_basebackup 开始时的 WAL LSN（PITR 备份时填入）。
            wal_end_lsn: pg_basebackup 结束时的 WAL LSN（PITR 备份时填入）。

        Returns:
            BackupRecord: 更新后的备份记录。

        Raises:
            AppError: code="not_found"，当记录不存在时。
        """
        now: datetime = datetime.now(UTC)
        async with self._scoped_session() as session:
            record: BackupRecord | None = await session.scalar(
                sa.select(BackupRecord).where(BackupRecord.id == record_id)
            )
            if record is None:
                raise AppError(
                    code="not_found",
                    message=f"备份记录不存在: {record_id}",
                    retryable=False,
                    fields={"record_id": str(record_id)},
                )
            record.status = BackupStatus.SUCCEEDED.value
            record.completed_at = now
            if sha256 is not None:
                record.sha256 = sha256
            if file_size is not None:
                record.file_size = file_size
            if migration_version is not None:
                record.migration_version = migration_version
            if application_version is not None:
                record.application_version = application_version
            if backup_timestamp is not None:
                record.backup_timestamp = backup_timestamp
            if wal_start_lsn is not None:
                record.wal_start_lsn = wal_start_lsn
            if wal_end_lsn is not None:
                record.wal_end_lsn = wal_end_lsn
            await session.flush()
            return record

    async def mark_restored(
        self,
        record_id: UUID,
        recovery_target_time: datetime | None = None,
    ) -> BackupRecord:
        """记录恢复操作的目标时间点（恢复完成后调用）。

        更新 backup_record 的 recovery_target_time 字段，记录恢复时使用的
        目标时间（PITR 恢复时为 recovery_target_time 或 backup_timestamp）。

        Args:
            record_id: 备份记录 UUID。
            recovery_target_time: 恢复目标时间（None 表示恢复到备份时间点）。

        Returns:
            BackupRecord: 更新后的备份记录。

        Raises:
            AppError: code="not_found"，当记录不存在时。
        """
        async with self._scoped_session() as session:
            record: BackupRecord | None = await session.scalar(
                sa.select(BackupRecord).where(BackupRecord.id == record_id)
            )
            if record is None:
                raise AppError(
                    code="not_found",
                    message=f"备份记录不存在: {record_id}",
                    retryable=False,
                    fields={"record_id": str(record_id)},
                )
            record.recovery_target_time = recovery_target_time
            await session.flush()
            return record

    async def mark_failed(
        self,
        record_id: UUID,
        error_message: str,
    ) -> BackupRecord:
        """标记备份失败，记录错误信息。

        Args:
            record_id: 备份记录 UUID。
            error_message: 失败原因。

        Returns:
            BackupRecord: 更新后的备份记录。

        Raises:
            AppError: code="not_found"，当记录不存在时。
        """
        now: datetime = datetime.now(UTC)
        async with self._scoped_session() as session:
            record: BackupRecord | None = await session.scalar(
                sa.select(BackupRecord).where(BackupRecord.id == record_id)
            )
            if record is None:
                raise AppError(
                    code="not_found",
                    message=f"备份记录不存在: {record_id}",
                    retryable=False,
                    fields={"record_id": str(record_id)},
                )
            record.status = BackupStatus.FAILED.value
            record.completed_at = now
            record.error_message = error_message[:2000] if error_message else None
            await session.flush()
            return record

    async def delete(self, record_id: UUID) -> None:
        """删除单条备份记录。

        仅删除数据库记录，不删除文件系统文件（调用方负责删除文件）。

        Args:
            record_id: 备份记录 UUID。
        """
        async with self._scoped_session() as session:
            await session.execute(sa.delete(BackupRecord).where(BackupRecord.id == record_id))
            await session.flush()

    async def delete_expired(
        self,
        *,
        dept_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> int:
        """清理过期备份记录（删除文件 + 删除记录）。

        查询 expires_at < now() 的记录，逐条删除文件系统目录和数据库记录。
        单条清理失败时记录日志但继续处理下一条（不中断批次）。

        阶段2 多租户升级：Beat 定时任务调用时传 dept_id（system 哨兵）+
        user_id（system_service 用户），在查询和删除前设置 GUC，确保 RLS 不拦截。
        user_id 必须是 system_service 用户（挂 root 部门）才能通过
        current_visible_dept_ids() 看到 system 哨兵部门的 backup_record。

        Args:
            dept_id: 挂载部门 ID（Beat 任务传 system 哨兵 ID；
                None 时由调用方在外层 session_scope 设 GUC）。
            user_id: 系统服务用户 ID（Beat 任务传 IRIP_SYSTEM_SERVICE_USER_ID；
                None 时 user GUC 设空串，RLS fail-closed 返回空集）。

        Returns:
            int: 实际清理的记录数量。
        """
        now: datetime = datetime.now(UTC)
        cleaned: int = 0

        # 查询过期记录（设 GUC 否则 RLS 拦截）
        async with scoped_session(self._factory, dept_id, user_id) as session:
            stmt = sa.select(BackupRecord).where(
                BackupRecord.expires_at.is_not(None),
                BackupRecord.expires_at < now,
            )
            result = await session.execute(stmt)
            expired_records: list[BackupRecord] = list(result.scalars().all())

        for record in expired_records:
            try:
                # 删除文件系统目录
                backup_dir: Path = Path(record.file_path)
                if backup_dir.exists():  # noqa: ASYNC240
                    shutil.rmtree(backup_dir, ignore_errors=True)
                    logger.info(
                        "Cleaned up expired backup %s: removed dir %s",
                        record.id,
                        backup_dir,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to remove backup dir %s for record %s: %s",
                    record.file_path,
                    record.id,
                    exc,
                )

            # 删除数据库记录（独立事务 + GUC，单条失败不影响其他记录）
            try:
                async with scoped_session(self._factory, dept_id, user_id) as session:
                    await session.execute(
                        sa.delete(BackupRecord).where(BackupRecord.id == record.id)
                    )
                    await session.flush()
                cleaned += 1
            except Exception as exc:
                logger.warning("Failed to delete backup record %s: %s", record.id, exc)

        logger.info("Retention cleanup: removed %d expired backups", cleaned)
        return cleaned
