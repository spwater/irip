"""Add assistant_model_name column to ai_config table.

Revision ID: 0042
Revises: 0041
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_config",
        sa.Column("assistant_model_name", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_config", "assistant_model_name")
