"""authorization and audit: role, scope_grant, audit_event REVOKE.

创建 RBAC 授权模块两张表 + 审计仅追加约束（docs/arch-v0.md §3.1 第 258-292 行）：
- role: 内置 7 个角色（id UUID PK, code TEXT UNIQUE, display_name, permissions JSONB）；
- scope_grant: 对象级授权（user_id/role_id 二选一, organization_id, object_root_id,
  resource_type, action, effective_from/effective_to）；
- audit_event: T03（迁移 0001）已建根表骨架（含全部字段），
  此处仅补充 irip_app 角色的 GRANT/REVOKE 保证仅追加。

安全约束（架构文档第 292 行）：
  应用角色 irip_app 对 audit_event 仅 INSERT + SELECT，
  REVOKE UPDATE, DELETE。

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 role、scope_grant 表 + 审计仅追加约束。"""
    # ---- role（内置角色表）----
    op.create_table(
        "role",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("code", sa.TEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column("permissions", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.UniqueConstraint("code", name="uq_role_code"),
    )

    # ---- scope_grant（对象级授权）----
    op.create_table(
        "scope_grant",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.UUID, nullable=True),
        sa.Column("role_id", sa.UUID, nullable=True),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("object_root_id", sa.UUID, nullable=True),
        sa.Column("resource_type", sa.TEXT, nullable=False),
        sa.Column("action", sa.TEXT, nullable=False),
        sa.Column(
            "effective_from", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column(
            "effective_to", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_scope_grant_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["role.id"], name="fk_scope_grant_role_id"
        ),
        sa.CheckConstraint(
            "(user_id IS NOT NULL AND role_id IS NULL) "
            "OR (user_id IS NULL AND role_id IS NOT NULL)",
            name="ck_scope_grant_user_or_role",
        ),
    )
    # 索引（架构文档第 278 行）
    op.create_index(
        "ix_scope_grant_user_resource_action",
        "scope_grant",
        ["user_id", "resource_type", "action"],
    )
    op.create_index(
        "ix_scope_grant_role_resource_action",
        "scope_grant",
        ["role_id", "resource_type", "action"],
    )

    # ---- 审计仅追加约束 ----
    # 创建应用角色 irip_app（NOLOGIN，仅用于权限隔离）
    op.execute(
        "DO $$ BEGIN "
        "CREATE ROLE irip_app NOLOGIN; "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    # 授予 schema 访问
    op.execute("GRANT USAGE ON SCHEMA public TO irip_app")
    # 审计表：仅 INSERT + SELECT（REVOKE UPDATE, DELETE）
    op.execute("GRANT INSERT, SELECT ON audit_event TO irip_app")
    op.execute("REVOKE UPDATE, DELETE ON audit_event FROM irip_app")
    # 授权表：CRUD
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON role, scope_grant TO irip_app"
    )
    # 认证表：CRUD
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON app_user, refresh_session TO irip_app"
    )

    # ---- 种子数据：7 个内置角色 ----
    from packages.auth.permissions import BUILTIN_ROLES

    import json

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
                permissions=json.dumps(permissions),
            )
        )


def downgrade() -> None:
    """回滚：删除表与索引，撤销 irip_app 权限。"""
    # 撤销 irip_app 权限
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON app_user, refresh_session FROM irip_app")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON role, scope_grant FROM irip_app")
    op.execute("REVOKE INSERT, SELECT ON audit_event FROM irip_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM irip_app")
    op.execute("DROP ROLE IF EXISTS irip_app")

    # 删除 scope_grant
    op.drop_index(
        "ix_scope_grant_role_resource_action", table_name="scope_grant"
    )
    op.drop_index(
        "ix_scope_grant_user_resource_action", table_name="scope_grant"
    )
    op.drop_table("scope_grant")

    # 删除 role
    op.drop_table("role")
