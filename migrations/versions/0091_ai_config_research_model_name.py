"""0091: add research_model_name column to ai_config

Revision ID: 0091
Revises: 0090
Create Date: 2026-08-12

补齐 ``ai_config.research_model_name``：该列在 ORM 模型
（``packages/ai/config_store.py`` 的 ``_ai_config_table``）与多处 SQL
（``apps/worker/research_timeline_tasks.py``、``packages/research/timeline/*``）
中被引用，但历史迁移从未建立该列，导致 api 启动时
``UndefinedColumn: column ai_config.research_model_name does not exist``。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0091"
down_revision: str | None = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """新增 research_model_name 列（可空，兼容旧无该列的数据行）。"""
    op.add_column("ai_config", sa.Column("research_model_name", sa.Text, nullable=True))


def downgrade() -> None:
    """回滚：删除 research_model_name 列。"""
    op.drop_column("ai_config", "research_model_name")
