"""0060: 创建 backup_record 备份元数据表

IRIP 平台治理 — 数据库备份与恢复功能（docs/arch-db-backup.md）。

新增 backup_record 表，记录每次备份的类型、状态、路径、校验和与保留策略：
- daily: 每日自动快照，expires_at = created_at + 14 days，到期自动清理；
- milestone: 里程碑手动备份，expires_at = NULL，永久保留；
- pre_restore: 回滚前自动备份，expires_at = created_at + 7 days。

同时为该表启用 RLS（租户隔离，参考迁移 0032）。

Revision ID: 0060
Revises: 0059
Create Date: 2026-08-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 backup_record 表 + 索引 + RLS 策略。"""
    op.create_table(
        "backup_record",
        sa.Column("id", sa.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", sa.UUID, sa.ForeignKey("job.id", ondelete="CASCADE"), nullable=True),
        sa.Column("backup_type", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("backup_date", sa.Date, nullable=True),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("file_size", sa.BigInteger, nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("migration_version", sa.String(100), nullable=True),
        sa.Column("application_version", sa.String(50), nullable=True),
        sa.Column("created_by", sa.UUID, sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.CheckConstraint(
            "backup_type IN ('daily', 'milestone', 'pre_restore')",
            name="chk_backup_record_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="chk_backup_record_status",
        ),
    )

    op.create_index("idx_backup_record_type", "backup_record", ["backup_type"])
    op.create_index("idx_backup_record_date", "backup_record", ["backup_date"])
    op.create_index(
        "idx_backup_record_expires",
        "backup_record",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )

    # RLS 租户隔离（参考迁移 0032）
    op.execute('ALTER TABLE "backup_record" ENABLE ROW LEVEL SECURITY;')
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'public'
                  AND tablename = 'backup_record'
                  AND policyname = 'tenant_isolation'
            ) THEN
                CREATE POLICY tenant_isolation ON "backup_record"
                USING (
                    organization_id = current_setting('app.current_org_id', true)::uuid
                );
            END IF;
        END
        $$;
        """
    )
    op.execute('ALTER TABLE "backup_record" FORCE ROW LEVEL SECURITY;')


def downgrade() -> None:
    """回滚：删除 RLS 策略 + 索引 + 表。"""
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "backup_record";')
    op.execute('ALTER TABLE "backup_record" NO FORCE ROW LEVEL SECURITY;')
    op.execute('ALTER TABLE "backup_record" DISABLE ROW LEVEL SECURITY;')

    op.drop_index("idx_backup_record_expires", table_name="backup_record")
    op.drop_index("idx_backup_record_date", table_name="backup_record")
    op.drop_index("idx_backup_record_type", table_name="backup_record")

    op.drop_table("backup_record")
