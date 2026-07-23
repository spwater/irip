"""0026: 实验对象关联设备字段

Revision ID: 0026_object_equipment_link
Revises: 0025_fact_template_optional
Create Date: 2026-07-23
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE industrial_object
        ADD COLUMN IF NOT EXISTS equipment_id UUID
        REFERENCES equipment(id) ON DELETE SET NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE industrial_object DROP COLUMN IF EXISTS equipment_id"
    )
