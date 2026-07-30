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
    op.add_column(
        "component",
        sa.Column(
            "active_version_id",
            sa.UUID(),
            nullable=True,
        ),
    )

    # 外键约束：active_version_id 引用 component_version.id
    op.create_foreign_key(
        "fk_component_active_version_id",
        "component",
        "component_version",
        ["active_version_id"],
        ["id"],
    )

    # 索引：加速按 active_version_id 查询
    op.create_index(
        "ix_component_active_version_id",
        "component",
        ["active_version_id"],
    )


def downgrade() -> None:
    """回滚：移除 active_version_id 列。"""
    op.drop_index("ix_component_active_version_id", table_name="component")
    op.drop_constraint("fk_component_active_version_id", "component", type_="foreignkey")
    op.drop_column("component", "active_version_id")
