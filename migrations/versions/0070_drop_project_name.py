"""0070: 删除 flow_definition.project_name 废弃字段

实验项目提级后，flow_definition.project_name 已被 project_id FK 替代。
存量数据已在 0069 迁移中迁移到 experiment_project 表并回填 project_id。
本迁移彻底删除 project_name 列（连带自动删除其 COMMENT）。

Revision ID: 0070
Revises: 0069
Create Date: 2026-09-15
"""

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """升级：删除 flow_definition.project_name 列。

    DROP COLUMN IF EXISTS 会连带自动删除该列上的 COMMENT。
    """
    op.execute(
        "ALTER TABLE flow_definition DROP COLUMN IF EXISTS project_name"
    )


def downgrade() -> None:
    """回滚：恢复 flow_definition.project_name 列（nullable TEXT）。"""
    op.execute(
        "ALTER TABLE flow_definition ADD COLUMN project_name TEXT"
    )
