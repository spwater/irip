"""quality_ingestion: quality_assessment, ingestion_job.

增量迁移（IRIP Task 16）：
- 创建 quality_assessment 表：存储事实修订的质量评估结果；
- 创建 ingestion_job 表：存储摄入作业详情（源路径、映射配置、状态、结果）；
- irip_app GRANT 两表权限。

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 quality_assessment / ingestion_job 两张表。"""

    # ---- quality_assessment 表 ----
    op.create_table(
        "quality_assessment",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("fact_revision_id", sa.UUID, nullable=False),
        sa.Column("overall_status", sa.TEXT, nullable=False),
        sa.Column("summary", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("results", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["fact_revision_id"],
            ["fact_revision.id"],
            name="fk_quality_assessment_fact_revision_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_quality_assessment_fact_revision_id",
        "quality_assessment",
        ["fact_revision_id"],
    )

    # ---- ingestion_job 表 ----
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
        sa.Column("mapping_profile_version_id", sa.UUID, nullable=True),
        sa.Column("template_version_id", sa.UUID, nullable=True),
        sa.Column("object_id", sa.UUID, nullable=True),
        sa.Column("method_version_id", sa.UUID, nullable=True),
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

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON quality_assessment, ingestion_job TO irip_app"
    )


def downgrade() -> None:
    """回滚：撤销权限、删除两张表。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON quality_assessment, ingestion_job FROM irip_app"
    )

    op.drop_index(
        "ix_ingestion_job_file_sha256", table_name="ingestion_job"
    )
    op.drop_index(
        "ix_ingestion_job_organization_id", table_name="ingestion_job"
    )
    op.drop_table("ingestion_job")

    op.drop_index(
        "ix_quality_assessment_fact_revision_id",
        table_name="quality_assessment",
    )
    op.drop_table("quality_assessment")
