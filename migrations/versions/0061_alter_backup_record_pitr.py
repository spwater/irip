"""0061: backup_record 表新增 PITR 字段

IRIP 平台数据库备份升级 — PG PITR + WAL 归档 + MinIO mc mirror 联合备份
（docs/arch-db-backup-pitr-upgrade.md §3.1）。

为 backup_record 表新增 5 个字段：
- backup_timestamp: 联合时间戳（PG basebackup + MinIO mirror 共用）
- wal_start_lsn: pg_basebackup 开始时的 WAL LSN
- wal_end_lsn: pg_basebackup 结束时的 WAL LSN
- recovery_target_time: 恢复时填写的目标时间点
- backup_method: 备份方法（'pitr' / 'pg_dump'）

存量记录回填 backup_method='pg_dump'，新记录默认 'pitr'。

Revision ID: 0061
Revises: 0060
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 PITR 字段 + 回填存量记录 + 设置默认值 + 索引。"""
    # 1. 新增字段（先添加为 nullable，回填后再设约束）
    op.add_column(
        "backup_record",
        sa.Column("backup_timestamp", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "backup_record",
        sa.Column("wal_start_lsn", sa.String(64), nullable=True),
    )
    op.add_column(
        "backup_record",
        sa.Column("wal_end_lsn", sa.String(64), nullable=True),
    )
    op.add_column(
        "backup_record",
        sa.Column("recovery_target_time", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "backup_record",
        sa.Column("backup_method", sa.String(20), nullable=True),
    )

    # 2. 回填存量记录为 pg_dump
    op.execute(
        "UPDATE backup_record SET backup_method = 'pg_dump' WHERE backup_method IS NULL"
    )

    # 3. 设置默认值 + NOT NULL
    op.alter_column(
        "backup_record",
        "backup_method",
        server_default="pitr",
        nullable=False,
    )

    # 4. 新增索引（按备份方法查询）
    op.create_index("idx_backup_record_method", "backup_record", ["backup_method"])


def downgrade() -> None:
    """回滚：删除索引 + 字段。"""
    op.drop_index("idx_backup_record_method", table_name="backup_record")
    op.drop_column("backup_record", "backup_method")
    op.drop_column("backup_record", "recovery_target_time")
    op.drop_column("backup_record", "wal_end_lsn")
    op.drop_column("backup_record", "wal_start_lsn")
    op.drop_column("backup_record", "backup_timestamp")
