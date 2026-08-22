"""0092: drop ai_config table

Revision ID: 0092
Revises: 0091
Create Date: 2026-08-22

AI 大模型配置从数据库迁移到 YAML 文件（``config/models.yaml`` +
``config/ai-usage.yaml``），``ai_config`` 表不再使用，予以删除。

downgrade 会重建表结构但无法恢复原有数据。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from packages.common.db_types import GUID, UTCDateTime

revision: str = "0092"
down_revision: str | None = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """删除 ai_config 表。"""
    op.drop_table("ai_config")


def downgrade() -> None:
    """回滚：重建 ai_config 表结构（数据不可恢复）。"""
    op.create_table(
        "ai_config",
        sa.Column("id", sa.Integer, primary_key=True, server_default=sa.text("1")),
        sa.Column("base_url", sa.Text, nullable=False),
        sa.Column("api_key", sa.Text, nullable=False),
        sa.Column("model_name", sa.Text, nullable=False),
        sa.Column("assistant_model_name", sa.Text, nullable=True),
        sa.Column("research_model_name", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("meta_prompt", sa.Text, nullable=True),
        sa.Column(
            "model_thinking_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "assistant_thinking_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "research_thinking_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("updated_at", UTCDateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", GUID, nullable=True),
    )
