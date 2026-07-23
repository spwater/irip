"""fact 和 fact_revision 的 template_version_id 改为 nullable（去模板依赖）。

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # fact 表：template_version_id 改为 nullable，去掉 FK 约束
    op.drop_constraint("fk_fact_template_version_id", "fact", type_="foreignkey")
    op.alter_column(
        "fact",
        "template_version_id",
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=True,
    )

    # fact_revision 表：template_version_id 改为 nullable，去掉 FK 约束
    op.drop_constraint("fk_fact_revision_template_version_id", "fact_revision", type_="foreignkey")
    op.alter_column(
        "fact_revision",
        "template_version_id",
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    # 恢复 NOT NULL
    op.alter_column(
        "fact_revision",
        "template_version_id",
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_fact_revision_template_version_id",
        "fact_revision",
        "fact_template_version",
        ["template_version_id"],
        ["id"],
    )
    op.alter_column(
        "fact",
        "template_version_id",
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_fact_template_version_id",
        "fact",
        "fact_template_version",
        ["template_version_id"],
        ["id"],
    )
