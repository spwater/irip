"""0040: ai_tool 表新增 category 列 — 工具分类字段。

为 ai_tool 表新增 category TEXT NOT NULL DEFAULT 'ai_tool' 列，
用于区分 AI 工具（category=ai_tool）和内置解析器插件（category=ingestion）。
AI 对话仅暴露 category=ai_tool 的工具，ingestion 类工具由组件系统调用。

Revision ID: 0040
Revises: 0039
Create Date: 2026-07-29
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 category 列。"""
    op.execute(
        "ALTER TABLE ai_tool "
        "ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'ai_tool'"
    )
    # xrd_converter 标记为 ingestion 分类
    op.execute(
        "UPDATE ai_tool SET category = 'ingestion' WHERE name = 'xrd_converter'"
    )
    op.execute(
        "UPDATE ai_tool SET category = 'ingestion' WHERE name = 'llm_converter'"
    )


def downgrade() -> None:
    """删除 category 列。"""
    op.execute("ALTER TABLE ai_tool DROP COLUMN IF EXISTS category")
