"""0050: 为 component 表添加 active_version_id 列（H-02）

packages/components/registry.py 的 Component ORM 已定义 active_version_id
列（指向当前活跃版本的 UUID），但缺少对应的数据库迁移。
此迁移补建该列及外键约束和索引。

Revision ID: 0050
Revises: 0049
Create Date: 2026-07-31
"""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为 component 表添加 active_version_id 列。"""
    op.execute(
        "ALTER TABLE component ADD COLUMN IF NOT EXISTS active_version_id UUID"
    )

    # 外键约束：active_version_id 引用 component_version.id
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS ("
        "SELECT 1 FROM information_schema.table_constraints "
        "WHERE constraint_name = 'fk_component_active_version_id') THEN "
        "ALTER TABLE component "
        "ADD CONSTRAINT fk_component_active_version_id "
        "FOREIGN KEY (active_version_id) REFERENCES component_version(id); "
        "END IF; END $$;"
    )

    # 索引：加速按 active_version_id 查询
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_component_active_version_id "
        "ON component (active_version_id)"
    )


def downgrade() -> None:
    """回滚：移除 active_version_id 列。"""
    op.drop_index("ix_component_active_version_id", table_name="component")
    op.drop_constraint("fk_component_active_version_id", "component", type_="foreignkey")
    op.drop_column("component", "active_version_id")
