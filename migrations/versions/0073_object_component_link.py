"""0073: 实验对象可选关联数据接口（component_id）

在 industrial_object 表新增 component_id 列（nullable，FK→component.id，ON DELETE SET NULL）。
实验对象可在新建/编辑页面选择一个数据接口（可选），选择框旁有 + 按钮可弹窗新建接口。

component 表的 id 是组件定义级别的 UUID 主键，可作为合法 FK 目标。
删除组件时，关联的实验对象的 component_id 自动置 NULL（ON DELETE SET NULL）。

Revision ID: 0073
Revises: 0072
Create Date: 2026-09-03
"""

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 industrial_object.component_id 列（nullable FK→component.id）。"""
    op.execute(
        "ALTER TABLE industrial_object ADD COLUMN IF NOT EXISTS component_id uuid "
        "REFERENCES component(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_industrial_object_component_id "
        "ON industrial_object (component_id)"
    )


def downgrade() -> None:
    """删除 industrial_object.component_id 列。"""
    op.execute("DROP INDEX IF EXISTS ix_industrial_object_component_id")
    op.execute("ALTER TABLE industrial_object DROP COLUMN IF EXISTS component_id")
