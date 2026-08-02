"""add meta_prompt column to ai_config

Revision ID: 780b980397b7
Revises: 0066
Create Date: 2026-08-02 18:11:09.589112
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op



revision: str = '780b980397b7'
down_revision: str | None = '0066'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_config", sa.Column("meta_prompt", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("ai_config", "meta_prompt")
