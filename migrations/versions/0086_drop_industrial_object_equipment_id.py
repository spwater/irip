"""0086: Drop industrial_object.equipment_id column

设备仪器关联已从实验对象（IndustrialObject）移至数据接口
（component_version.equipment_id），实验对象不再需要此列。

Rev ID: 0086
Revises: 0085
Create Date: 2026-08-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("industrial_object", "equipment_id")


def downgrade() -> None:
    op.add_column(
        "industrial_object",
        sa.Column("equipment_id", sa.UUID(), nullable=True),
    )
