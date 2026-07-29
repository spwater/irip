"""Add run_operator column to fact_revision table.

Revision ID: 0045
Revises: 0044
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fact_revision",
        sa.Column("run_operator", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fact_revision", "run_operator")
