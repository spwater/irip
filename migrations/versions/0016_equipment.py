"""equipment + equipment_variable: 设备仪器管理。

增量迁移（IRIP Task — 设备仪器管理）：
- 创建 equipment 表：设备仪器主表，code 组织内唯一，含部门外键；
- 创建 equipment_variable 表：设备-物理量多对多关联表，复合主键；
- 索引：ix_equipment_organization_id, ix_equipment_department_id,
  ix_equipment_variable_variable_id；
- irip_app GRANT 两表权限。

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 equipment + equipment_variable 表。"""

    # ---- equipment 表 ----
    op.create_table(
        "equipment",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("code", sa.TEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column("description", sa.TEXT, nullable=True),
        sa.Column("department_id", sa.UUID, nullable=False),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "sort_order",
            sa.INTEGER,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "lock_version",
            sa.INTEGER,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["department.id"],
            name="fk_equipment_department_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "code", name="uq_equipment_org_code"),
    )
    op.create_index("ix_equipment_organization_id", "equipment", ["organization_id"])
    op.create_index("ix_equipment_department_id", "equipment", ["department_id"])

    # ---- equipment_variable 关联表 ----
    op.create_table(
        "equipment_variable",
        sa.Column("equipment_id", sa.UUID, nullable=False),
        sa.Column("variable_id", sa.UUID, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["equipment_id"],
            ["equipment.id"],
            name="fk_equipment_variable_equipment_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variable_id"],
            ["variable.id"],
            name="fk_equipment_variable_variable_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("equipment_id", "variable_id"),
    )
    op.create_index(
        "ix_equipment_variable_variable_id", "equipment_variable", ["variable_id"]
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON equipment, equipment_variable TO irip_app"
    )

    # ---- re-seed 7 个内置角色（ON CONFLICT DO UPDATE，写入 equipment 权限）----
    import json

    from packages.auth.permissions import BUILTIN_ROLES

    for code, info in BUILTIN_ROLES.items():
        display_name = info["display_name"]
        permissions = info["permissions"]
        op.execute(
            sa.text(
                "INSERT INTO role (code, display_name, permissions) "
                "VALUES (:code, :display_name, CAST(:permissions AS jsonb)) "
                "ON CONFLICT (code) DO UPDATE SET "
                "display_name = EXCLUDED.display_name, "
                "permissions = EXCLUDED.permissions"
            ).bindparams(
                code=code,
                display_name=display_name,
                permissions=json.dumps([str(p) for p in permissions]),
            )
        )


def downgrade() -> None:
    """回滚：撤销权限、删除两张表。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON equipment, equipment_variable FROM irip_app"
    )

    op.drop_index(
        "ix_equipment_variable_variable_id", table_name="equipment_variable"
    )
    op.drop_table("equipment_variable")

    op.drop_index("ix_equipment_department_id", table_name="equipment")
    op.drop_index("ix_equipment_organization_id", table_name="equipment")
    op.drop_table("equipment")
