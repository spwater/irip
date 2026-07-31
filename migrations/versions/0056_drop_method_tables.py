"""Drop method + method_version tables and method_version_id columns.

Revision ID: 0056
Revises: 0055
Create Date: 2025-07-31

Changes:
- DROP FK constraint + column fact.method_version_id (FK→method_version);
- DROP column ingestion_job.method_version_id (plain UUID, no FK);
- DROP TABLE method_version (has FK→method.id, must drop before method);
- DROP TABLE method.

method / method_version 两张表实际未被业务使用（converter 接口已隐含
方法信息），仅含测试数据。本迁移物理删除两张表及 fact / ingestion_job
上的 method_version_id 列。
"""

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除 method / method_version 表及 method_version_id 列。"""

    # 1. 删除 fact.method_version_id 的 FK 约束（0055 中创建）
    op.drop_constraint("fk_fact_method_version_id", "fact", type_="foreignkey")

    # 2. 删除 fact.method_version_id 列
    op.drop_column("fact", "method_version_id")

    # 3. 删除 ingestion_job.method_version_id 列（无 FK 约束，0013 创建时为普通 UUID）
    op.drop_column("ingestion_job", "method_version_id")

    # 4. 删除 method_version 表（有 FK→method.id，必须先于 method 删除）
    op.drop_table("method_version")

    # 5. 删除 method 表
    op.drop_table("method")


def downgrade() -> None:
    """重建 method / method_version 表及 method_version_id 列（不含历史数据）。"""

    # 1. 重建 method 表
    op.create_table(
        "method",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'draft'")),
        sa.Column("version_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lock_version", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("organization_id", "code", name="uq_method_org_code"),
    )

    # 2. 重建 method_version 表
    op.create_table(
        "method_version",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("method_id", sa.UUID, sa.ForeignKey("method.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("published_by", sa.UUID, nullable=True),
        sa.Column("deprecated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deprecated_by", sa.UUID, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lock_version", sa.Integer, nullable=False, server_default=sa.text("0")),
    )

    # 3. 恢复 ingestion_job.method_version_id 列
    op.add_column("ingestion_job", sa.Column("method_version_id", sa.UUID, nullable=True))

    # 4. 恢复 fact.method_version_id 列 + FK 约束
    op.add_column("fact", sa.Column("method_version_id", sa.UUID, nullable=True))
    op.create_foreign_key(
        "fk_fact_method_version_id",
        "fact",
        "method_version",
        ["method_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
