"""AI 大模型配置表。

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ai_config",
        sa.Column("id", sa.Integer, primary_key=True, server_default=sa.text("1")),
        sa.Column("base_url", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("api_key", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("model_name", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_by", sa.UUID(as_uuid=True), nullable=True),
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ai_config TO irip_app;")


def downgrade() -> None:
    op.drop_table("ai_config")
