"""AI 对话增加置顶和归档字段。

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ai_conversation",
        sa.Column(
            "pinned",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "ai_conversation",
        sa.Column(
            "archived",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # 置顶对话排序优先：建索引加速排序
    op.create_index(
        "ix_ai_conversation_pinned_archived",
        "ai_conversation",
        ["archived", "pinned", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_conversation_pinned_archived", table_name="ai_conversation")
    op.drop_column("ai_conversation", "archived")
    op.drop_column("ai_conversation", "pinned")
