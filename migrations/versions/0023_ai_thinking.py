"""AI 配置增加思考模式开关。

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ai_config",
        sa.Column(
            "thinking_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_config", "thinking_enabled")
