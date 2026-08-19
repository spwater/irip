"""0085: component_version.equipment_id 从 TEXT 改为 UUID

修复类型不一致：component_version.equipment_id 原为 TEXT 字符串，
与 equipment.id（UUID）join 时需要 sa.cast 硬拼，类型错配导致
设备名快照静默丢失。

升级策略：
  1. 将列类型从 TEXT 改为 UUID（USING 列名::uuid）
  2. 无效文本值（非合法 UUID）置 NULL（USING NULLIF ... 或 CASE）
  3. 加 FK 约束 → equipment.id ON DELETE SET NULL

降级策略：
  1. 删 FK
  2. 列类型回 TEXT（USING 列名::text）

Rev ID: 0085_component_version_equipment_id_to_guid
Revises: 0084_research_timeline_reset
Create Date: 2026-08-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0085_component_version_equipment"
down_revision = "0084_research_timeline_reset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """TEXT → UUID，清理无效值，加 FK。"""
    # 1. 将非法 UUID 文本值置 NULL，再改类型
    op.execute(
        """
        UPDATE component_version
        SET equipment_id = NULL
        WHERE equipment_id IS NOT NULL
          AND equipment_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        """
    )
    # 2. 改列类型 TEXT → UUID
    op.alter_column(
        "component_version",
        "equipment_id",
        existing_type=sa.Text(),
        type_=sa.UUID(),
        postgresql_using="equipment_id::uuid",
    )
    # 3. 加 FK 约束
    op.create_foreign_key(
        "fk_component_version_equipment_id",
        "component_version",
        "equipment",
        ["equipment_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """UUID → TEXT，删 FK。"""
    op.drop_constraint(
        "fk_component_version_equipment_id",
        "component_version",
        type_="foreignkey",
    )
    op.alter_column(
        "component_version",
        "equipment_id",
        existing_type=sa.UUID(),
        type_=sa.Text(),
        postgresql_using="equipment_id::text",
    )
