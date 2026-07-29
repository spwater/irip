"""Add equipment_name column to fact_revision table.

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fact_revision",
        sa.Column("equipment_name", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fact_revision", "equipment_name")
