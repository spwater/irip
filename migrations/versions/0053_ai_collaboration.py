"""0053: AI 助手协作功能 — conversation_participant 表 + ai_message/app_user 字段扩展。

增量迁移（irip-ai-collab — AI 助手多人协作）：
- 创建 conversation_participant 表（联合主键 conversation_id + user_id，
  FK CASCADE 关联 ai_conversation / app_user，role owner/member）；
- 添加 idx_conv_participant_user 索引（按 user_id 查询用户参与的对话）；
- ai_message 新增 mentions JSONB（@ 人 user_id 数组）+ sender_user_id UUID
  + sender_display_name TEXT + sender_avatar_url TEXT；
- app_user 新增 avatar_url TEXT（头像 URL）；
- irip_app GRANT 权限。

Revision ID: 0053
Revises: 0052
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 conversation_participant 表 + 扩展 ai_message / app_user 字段 + 权限。"""

    # ---- 1. conversation_participant 表 ----
    op.create_table(
        "conversation_participant",
        sa.Column("conversation_id", sa.UUID, nullable=False),
        sa.Column("user_id", sa.UUID, nullable=False),
        sa.Column(
            "role",
            sa.String(20),
            server_default=sa.text("'member'"),
            nullable=False,
        ),
        sa.Column(
            "joined_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversation.id"],
            name="fk_conv_participant_conversation_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_conv_participant_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id", "user_id", name="pk_conversation_participant"),
    )

    # ---- 2. 索引：按 user_id 查询用户参与的对话 ----
    op.create_index(
        "idx_conv_participant_user",
        "conversation_participant",
        ["user_id"],
    )

    # ---- 3. ai_message 新增字段 ----
    op.add_column(
        "ai_message",
        sa.Column(
            "mentions",
            JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "ai_message",
        sa.Column("sender_user_id", sa.UUID, nullable=True),
    )
    op.add_column(
        "ai_message",
        sa.Column("sender_display_name", sa.TEXT, nullable=True),
    )
    op.add_column(
        "ai_message",
        sa.Column("sender_avatar_url", sa.TEXT, nullable=True),
    )

    # ---- 4. app_user 新增头像字段 ----
    op.add_column(
        "app_user",
        sa.Column("avatar_url", sa.TEXT, nullable=True),
    )

    # ---- 5. irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON conversation_participant TO irip_app"
    )


def downgrade() -> None:
    """回滚：撤销权限、删除字段和表。"""

    # ---- 撤销权限 ----
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON conversation_participant FROM irip_app"
    )

    # ---- 删除 app_user 字段 ----
    op.drop_column("app_user", "avatar_url")

    # ---- 删除 ai_message 字段 ----
    op.drop_column("ai_message", "sender_avatar_url")
    op.drop_column("ai_message", "sender_display_name")
    op.drop_column("ai_message", "sender_user_id")
    op.drop_column("ai_message", "mentions")

    # ---- 删除索引和表 ----
    op.drop_index(
        "idx_conv_participant_user",
        table_name="conversation_participant",
    )
    op.drop_table("conversation_participant")
