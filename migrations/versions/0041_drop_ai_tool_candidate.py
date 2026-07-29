"""0041: 删除 ai_tool 表的 candidate 列。

candidate 字段已无实际用途（前端不再展示，后端不再使用候选/只读区分），
统一移除以简化数据模型。

Revision ID: 0041
Revises: 0040
Create Date: 2026-07-29
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除 candidate 列。"""
    op.execute("ALTER TABLE ai_tool DROP COLUMN IF EXISTS candidate")


def downgrade() -> None:
    """恢复 candidate 列。"""
    op.execute(
        "ALTER TABLE ai_tool "
        "ADD COLUMN IF NOT EXISTS candidate BOOLEAN NOT NULL DEFAULT false"
    )
