"""0051: 为 app_user 表添加 token_version 列（H-06）

T04 在 AppUser ORM 中新增了 token_version 字段用于 JWT 撤销，
但缺少对应的数据库迁移。此迁移补建该列。

Revision ID: 0051
Revises: 0050
Create Date: 2026-07-31
"""

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为 app_user 表添加 token_version 列。"""
    op.execute(
        "ALTER TABLE app_user ADD COLUMN IF NOT EXISTS token_version "
        "INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    """回滚：移除 token_version 列。"""
    op.execute("ALTER TABLE app_user DROP COLUMN IF EXISTS token_version")
