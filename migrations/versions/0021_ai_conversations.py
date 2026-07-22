"""ai_conversation + ai_message: AI 助手对话持久化。

增量迁移（IRIP V3-T01 — AI 助手全栈）：
- 创建 ai_conversation 表：对话主表，按 (organization_id, user_id) 归属，
  含 title/provider_mode/created_at/updated_at；
- 创建 ai_message 表：消息表，按 conversation_id 外键关联，
  含 role(user/assistant/tool)/content/tool_calls_json(JSONB)/
  citations_json(JSONB)/uncertainty/created_at；
- 索引：ix_ai_conversation_user_id, ix_ai_message_conversation_id；
- irip_app GRANT 两表权限；
- re-seed 7 个内置角色（添加 assistant:use 权限）。

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-23
"""

from collections.abc import Sequence
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建两张表，授权，re-seed roles。"""

    # ---- ai_conversation 表 ----
    op.create_table(
        "ai_conversation",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("user_id", sa.UUID, nullable=False),
        sa.Column("title", sa.TEXT, server_default=sa.text("''"), nullable=False),
        sa.Column(
            "provider_mode",
            sa.TEXT,
            server_default=sa.text("'offline'"),
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
    )
    op.create_index(
        "ix_ai_conversation_user_id",
        "ai_conversation",
        ["user_id"],
    )
    op.create_index(
        "ix_ai_conversation_org_user",
        "ai_conversation",
        ["organization_id", "user_id"],
    )

    # ---- ai_message 表 ----
    op.create_table(
        "ai_message",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("conversation_id", sa.UUID, nullable=False),
        sa.Column("role", sa.TEXT, nullable=False),
        sa.Column(
            "content", sa.TEXT, server_default=sa.text("''"), nullable=False
        ),
        sa.Column(
            "tool_calls_json",
            JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "citations_json",
            JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("uncertainty", sa.TEXT, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversation.id"],
            name="fk_ai_message_conversation_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_ai_message_conversation_id",
        "ai_message",
        ["conversation_id"],
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON ai_conversation, ai_message TO irip_app"
    )

    # ---- re-seed 7 个内置角色（ON CONFLICT DO UPDATE，写入 assistant:use 权限）----
    from packages.auth.permissions import BUILTIN_ROLES

    for code, info in BUILTIN_ROLES.items():
        display_name = info["display_name"]
        permissions = info["permissions"]
        op.execute(
            sa.text(
                "INSERT INTO role (code, display_name, permissions) "
                "VALUES (:code, :display_name, "
                "CAST(:permissions AS jsonb)) "
                "ON CONFLICT (code) DO UPDATE SET "
                "display_name = EXCLUDED.display_name, "
                "permissions = EXCLUDED.permissions"
            ).bindparams(
                code=code,
                display_name=display_name,
                permissions=json.dumps([str(p) for p in permissions]),
            )
        )


def downgrade() -> None:
    """回滚：撤销权限、删除两张表。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON ai_conversation, ai_message FROM irip_app"
    )

    op.drop_index(
        "ix_ai_message_conversation_id",
        table_name="ai_message",
    )
    op.drop_table("ai_message")

    op.drop_index(
        "ix_ai_conversation_org_user",
        table_name="ai_conversation",
    )
    op.drop_index(
        "ix_ai_conversation_user_id",
        table_name="ai_conversation",
    )
    op.drop_table("ai_conversation")
