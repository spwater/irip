"""Drop ingestion_job table (0 rows, no ORM model, unused).

Revision ID: 0058
Revises: 0057
Create Date: 2026-08-01

删除原因:
  ingestion_job 表自创建以来始终 0 条数据（migration 0013 创建）。
  没有对应的 ORM 类（无 Python IngestionJob model），仅有一个 FK 约束
  fk_ingestion_job_job_id（job_id → job.id, ON DELETE SET NULL）。
  代码中无任何 INSERT/SELECT/UPDATE 操作引用该表，仅测试清理脚本中
  有一条 DELETE 语句（已在本迁移同步清理）。

  PostgreSQL DROP TABLE 会自动级联删除:
  - FK 约束 fk_ingestion_job_job_id
  - 索引 ix_ingestion_job_organization_id / ix_ingestion_job_file_sha256
  - RLS policy（migration 0032 创建）
  - irip_app / irip_runtime 权限（migration 0013 / 0034 创建）
  无需显式 REVOKE 或 DROP CONSTRAINT。

  注意: process_ingestion_job 是 worker 任务处理函数名，不操作该表，无需修改。
"""

import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除 ingestion_job 表。"""
    op.drop_table("ingestion_job")


def downgrade() -> None:
    """重建 ingestion_job 表（恢复至 0057 后的结构，不含 0056/0057 已删除的列）。"""

    op.create_table(
        "ingestion_job",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("job_id", sa.UUID, nullable=True),
        sa.Column("source_path", sa.TEXT, nullable=False),
        sa.Column("object_id", sa.UUID, nullable=True),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("result", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("file_sha256", sa.TEXT, nullable=True),
        sa.Column(
            "deduplicated",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["job.id"],
            name="fk_ingestion_job_job_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_ingestion_job_organization_id",
        "ingestion_job",
        ["organization_id"],
    )
    op.create_index(
        "ix_ingestion_job_file_sha256",
        "ingestion_job",
        ["file_sha256"],
    )
