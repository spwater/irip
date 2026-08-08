"""0080: 拆分 thinking_enabled 为三个模型独立控制

Revision ID: 0080
Revises: 0079
Create Date: 2026-08-07
"""

from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增三个独立的思考模式列，迁移旧值。"""
    op.execute(
        "ALTER TABLE ai_config "
        "ADD COLUMN IF NOT EXISTS model_thinking_enabled boolean "
        "DEFAULT false"
    )
    op.execute(
        "ALTER TABLE ai_config "
        "ADD COLUMN IF NOT EXISTS assistant_thinking_enabled boolean "
        "DEFAULT false"
    )
    op.execute(
        "ALTER TABLE ai_config "
        "ADD COLUMN IF NOT EXISTS research_thinking_enabled boolean "
        "DEFAULT false"
    )
    # 迁移旧值：thinking_enabled=true 时三个都设为 true
    op.execute(
        "UPDATE ai_config SET "
        "model_thinking_enabled = thinking_enabled, "
        "assistant_thinking_enabled = thinking_enabled, "
        "research_thinking_enabled = thinking_enabled "
        "WHERE thinking_enabled = true"
    )
    # 删除旧列
    op.execute("ALTER TABLE ai_config DROP COLUMN IF EXISTS thinking_enabled")


def downgrade() -> None:
    """恢复单一 thinking_enabled 列。"""
    op.execute(
        "ALTER TABLE ai_config ADD COLUMN IF NOT EXISTS thinking_enabled boolean DEFAULT false"
    )
    op.execute(
        "UPDATE ai_config SET thinking_enabled = true "
        "WHERE model_thinking_enabled = true OR "
        "assistant_thinking_enabled = true OR "
        "research_thinking_enabled = true"
    )
    op.execute("ALTER TABLE ai_config DROP COLUMN IF EXISTS model_thinking_enabled")
    op.execute("ALTER TABLE ai_config DROP COLUMN IF EXISTS assistant_thinking_enabled")
    op.execute("ALTER TABLE ai_config DROP COLUMN IF EXISTS research_thinking_enabled")
