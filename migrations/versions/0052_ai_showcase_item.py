"""0052: 创建 ai_showcase_item 表（AI 助手分析橱窗）。

增量迁移（irip-ai-showcase — AI 助手分析橱窗及可视化升级）：
- 创建 ai_showcase_item 表：橱窗卡片持久化，按 conversation_id 外键关联 ai_conversation，
  含 sort_order/block_type/title/content_snapshot/source_message_id/source_block_index/
  data_source(JSONB)/created_at/updated_at；
- 唯一索引 uq_showcase_conv_msg_block (conversation_id, source_message_id, source_block_index)：
  防止同一对话内重复加入相同块；
- 排序索引 idx_showcase_conv_sort (conversation_id, sort_order)：加速按序查询；
- irip_app GRANT 权限。

Revision ID: 0052
Revises: 0051
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 ai_showcase_item 表 + 索引 + 权限。"""

    # ---- ai_showcase_item 表 ----
    op.create_table(
        "ai_showcase_item",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("conversation_id", sa.UUID, nullable=False),
        sa.Column("user_id", sa.UUID, nullable=False),
        sa.Column(
            "sort_order",
            sa.Integer,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("block_type", sa.String(32), nullable=False),
        sa.Column(
            "title",
            sa.String(200),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("content_snapshot", sa.TEXT, nullable=False),
        sa.Column("source_message_id", sa.UUID, nullable=False),
        sa.Column(
            "source_block_index",
            sa.Integer,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "data_source",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversation.id"],
            name="fk_ai_showcase_item_conversation_id",
            ondelete="CASCADE",
        ),
    )

    # ---- 唯一索引：防重复加入 ----
    op.create_index(
        "uq_showcase_conv_msg_block",
        "ai_showcase_item",
        ["conversation_id", "source_message_id", "source_block_index"],
        unique=True,
    )

    # ---- 排序索引 ----
    op.create_index(
        "idx_showcase_conv_sort",
        "ai_showcase_item",
        ["conversation_id", "sort_order"],
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ai_showcase_item TO irip_app"
    )


def downgrade() -> None:
    """回滚：撤销权限、删除表和索引。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON ai_showcase_item FROM irip_app"
    )

    op.drop_index(
        "idx_showcase_conv_sort",
        table_name="ai_showcase_item",
    )
    op.drop_index(
        "uq_showcase_conv_msg_block",
        table_name="ai_showcase_item",
    )
    op.drop_table("ai_showcase_item")
