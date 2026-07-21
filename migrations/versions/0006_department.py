"""department + app_user_department + scope_grant.department_id + re-seed roles.

增量迁移（docs/arch-department.md §3.1–§3.4）：
- P0: 创建 department 表（实验室/机构主表）；
- P1: 创建 app_user_department 关联表（用户-实验室多对多）；
- P1: scope_grant 新增 department_id 列（NULL = 全组织，非 NULL = 特定实验室）；
- irip_app GRANT 新表权限；
- re-seed 7 个内置角色（ON CONFLICT DO UPDATE，写入 department 权限）。

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-21
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 department + app_user_department 表，扩展 scope_grant，re-seed 角色。"""

    # ---- P0: department 表 ----
    op.create_table(
        "department",
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
        sa.UniqueConstraint("organization_id", "code", name="uq_department_org_code"),
    )
    op.create_index("ix_department_organization_id", "department", ["organization_id"])
    op.create_index("ix_department_status", "department", ["status"])

    # ---- P1: app_user_department 关联表 ----
    op.create_table(
        "app_user_department",
        sa.Column("user_id", sa.UUID, nullable=False),
        sa.Column("department_id", sa.UUID, nullable=False),
        sa.Column(
            "is_primary",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_app_user_department_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["department.id"],
            name="fk_app_user_department_department_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "department_id", name="uq_user_department"),
    )
    op.create_index(
        "ix_user_department_department_id", "app_user_department", ["department_id"]
    )
    op.create_index(
        "ix_user_department_user_id", "app_user_department", ["user_id"]
    )

    # ---- P1: scope_grant 新增 department_id 列 ----
    # NULL = 全组织范围（兼容现有行为）；非 NULL = 仅该实验室范围
    op.add_column(
        "scope_grant",
        sa.Column("department_id", sa.UUID, nullable=True),
    )
    op.create_foreign_key(
        "fk_scope_grant_department_id",
        "scope_grant",
        "department",
        ["department_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_scope_grant_department_id", "scope_grant", ["department_id"]
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON department, app_user_department TO irip_app"
    )
    # scope_grant 新列已有 irip_app 权限（0003 已 GRANT），无需追加

    # ---- re-seed 7 个内置角色（ON CONFLICT DO UPDATE）----
    # BUILTIN_ROLES 此时已含 department 权限（代码先于迁移修改）
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
    """回滚：撤销权限、删除 scope_grant 新列、删除关联表、删除 department 表。"""
    # 撤销 irip_app 权限
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON department, app_user_department FROM irip_app"
    )

    # 删除 scope_grant.department_id
    op.drop_index("ix_scope_grant_department_id", table_name="scope_grant")
    op.drop_constraint("fk_scope_grant_department_id", "scope_grant", type_="foreignkey")
    op.drop_column("scope_grant", "department_id")

    # 删除 app_user_department
    op.drop_index("ix_user_department_user_id", table_name="app_user_department")
    op.drop_index("ix_user_department_department_id", table_name="app_user_department")
    op.drop_table("app_user_department")

    # 删除 department
    op.drop_index("ix_department_status", table_name="department")
    op.drop_index("ix_department_organization_id", table_name="department")
    op.drop_table("department")
