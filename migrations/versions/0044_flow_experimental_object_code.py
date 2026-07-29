"""Add experimental_object_code column to flow_definition table.

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "flow_definition",
        sa.Column("experimental_object_code", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("flow_definition", "experimental_object_code")
