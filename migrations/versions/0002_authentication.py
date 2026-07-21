"""authentication: app_user + refresh_session.

创建认证模块两张表（docs/arch-v0.md §3.1 第 232-256 行）：
- app_user: 用户主表（CITEXT 邮箱、Argon2id 密码哈希、状态、乐观锁）；
- refresh_session: 家族化刷新会话（SHA-256 摘要、旋转链、家族撤销）。

索引（架构文档第 256 行）：
  ix_app_user_email                    — CITEXT UNIQUE 自带索引
  ix_refresh_session_family_id         — (family_id)
  ix_refresh_session_user_id_revoked_at — (user_id, revoked_at)
  ix_refresh_session_expires_at         — (expires_at)

需 citext 扩展支持大小写不敏感邮箱查询。

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import CITEXT

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 app_user 和 refresh_session 表。"""
    # ---- citext 扩展（大小写不敏感文本）----
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # ---- app_user（用户主表）----
    op.create_table(
        "app_user",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=True),
        sa.Column("email", CITEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column("password_hash", sa.TEXT, nullable=False),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'active'"),
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
        sa.Column(
            "lock_version",
            sa.INTEGER,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.UniqueConstraint("email", name="uq_app_user_email"),
    )

    # ---- refresh_session（家族化刷新会话）----
    op.create_table(
        "refresh_session",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("family_id", sa.UUID, nullable=False),
        sa.Column("user_id", sa.UUID, nullable=False),
        sa.Column("token_digest", sa.TEXT, nullable=False),
        sa.Column(
            "issued_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("replaced_by", sa.UUID, nullable=True),
        sa.Column("created_ip", sa.TEXT, nullable=True),
        sa.Column("user_agent", sa.TEXT, nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_refresh_session_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by"], ["refresh_session.id"],
            name="fk_refresh_session_replaced_by",
        ),
        sa.UniqueConstraint("token_digest", name="uq_refresh_session_token_digest"),
    )

    # ---- 索引 ----
    op.create_index(
        "ix_refresh_session_family_id", "refresh_session", ["family_id"]
    )
    op.create_index(
        "ix_refresh_session_user_id_revoked_at",
        "refresh_session",
        ["user_id", "revoked_at"],
    )
    op.create_index(
        "ix_refresh_session_expires_at", "refresh_session", ["expires_at"]
    )


def downgrade() -> None:
    """回滚：删除表与索引，移除 citext 扩展。"""
    op.drop_index("ix_refresh_session_expires_at", table_name="refresh_session")
    op.drop_index(
        "ix_refresh_session_user_id_revoked_at", table_name="refresh_session"
    )
    op.drop_index("ix_refresh_session_family_id", table_name="refresh_session")
    op.drop_table("refresh_session")
    op.drop_table("app_user")
    op.execute("DROP EXTENSION IF EXISTS citext")
