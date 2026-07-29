"""Add equipment_id column to component_version table.

Revision ID: 0043
Revises: 0042
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "component_version",
        sa.Column("equipment_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("component_version", "equipment_id")
