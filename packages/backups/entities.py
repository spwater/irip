"""备份记录实体：BackupType / BackupStatus 枚举 + BackupRecord ORM 模型。

对应 backup_record 表（docs/arch-db-backup.md §3.2 / docs/prd-db-backup.md §5.1）。

备份类型与保留策略：
- daily: 每日自动快照，expires_at = created_at + 14 days；
- milestone: 里程碑手动备份，expires_at = NULL（永久保留）；
- pre_restore: 回滚前自动备份，expires_at = created_at + 7 days。

备份状态机：pending → succeeded / failed（终态）。
"""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class BackupType(StrEnum):
    """备份类型枚举。

    Attributes:
        DAILY: 每日自动快照（Celery beat 触发，14 天保留）。
        MILESTONE: 里程碑手动备份（API 触发，永久保留）。
        PRE_RESTORE: 回滚前自动备份（Worker 内联创建，7 天保留）。
    """

    DAILY = "daily"
    MILESTONE = "milestone"
    PRE_RESTORE = "pre_restore"


class BackupStatus(StrEnum):
    """备份状态枚举。

    Attributes:
        PENDING: 备份进行中（已创建记录，Worker 尚未完成）。
        SUCCEEDED: 备份成功完成。
        FAILED: 备份失败。
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BackupMethod(StrEnum):
    """备份方法枚举（PITR 升级）。

    Attributes:
        PITR: 物理基础备份（pg_basebackup + WAL 归档 + mc mirror），format_version=2。
        PG_DUMP: 逻辑备份（pg_dump + S3Repository），format_version=1（存量备份）。
    """

    PITR = "pitr"
    PG_DUMP = "pg_dump"


#: 每日备份保留天数。
DAILY_RETENTION_DAYS: int = 14

#: 回滚前备份保留天数。
PRE_RESTORE_RETENTION_DAYS: int = 7


class BackupRecord(Base):
    """备份记录 ORM 模型（对应 backup_record 表）。

    记录每次备份的类型、状态、文件路径、校验和与保留策略。
    backup_record.id 与 manifest.backup_id、备份子目录名三者一致。

    Attributes:
        id: 备份记录 UUID（PK，与 manifest.backup_id 一致）。
        job_id: 关联的异步作业 UUID（FK→job.id，daily/pre_restore 系统自动时可为 NULL）。
        backup_type: 备份类型（daily / milestone / pre_restore）。
        name: 里程碑名称（仅 milestone 必填，daily/pre_restore 为 NULL）。
        description: 里程碑描述（仅 milestone 有值）。
        backup_date: 快照日期（daily 用，便于按天列表）。
        file_path: 备份文件路径（{IRIP_BACKUP_OUTPUT_DIR}/{backup_id}/）。
        file_size: 备份文件大小（字节）。
        sha256: 数据库 dump SHA-256 校验和。
        status: 备份状态（pending / succeeded / failed）。
        migration_version: 备份时 Alembic 迁移版本。
        application_version: 备份时 IRIP 应用版本。
        created_by: 创建者用户 ID（FK→app_user.id，系统自动时为 NULL）。
        created_at: 创建时间（UTC）。
        completed_at: 完成时间（UTC，pending 时为 NULL）。
        expires_at: 过期时间（NULL = 永久保留）。
        department_id: 所属部门 ID（RLS 租户隔离）。
    """

    __tablename__ = "backup_record"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    job_id: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("job.id", ondelete="CASCADE"), nullable=True
    )
    backup_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    name: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    backup_date: Mapped[date | None] = mapped_column(sa.Date, nullable=True)
    file_path: Mapped[str] = mapped_column(sa.Text, nullable=False)
    file_size: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'pending'")
    )
    migration_version: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    application_version: Mapped[str | None] = mapped_column(sa.String(50), nullable=True)
    # ---- PITR 升级新增字段（0061 迁移）----
    backup_timestamp: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    wal_start_lsn: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    wal_end_lsn: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    recovery_target_time: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    backup_method: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default="pitr"
    )
    created_by: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # ---- 多租户隔离键升级：B 类一列 ----
    department_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("department.id"),
        nullable=False,
        comment="所属部门 ID（备份记录归 system 哨兵部门）",
    )

    def __repr__(self) -> str:
        return (
            f"BackupRecord(id={self.id!r}, backup_type={self.backup_type!r}, "
            f"status={self.status!r})"
        )
