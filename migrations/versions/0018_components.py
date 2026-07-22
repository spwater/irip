"""component + component_version: 组件注册表。

增量迁移（IRIP V2-T01 — 组件系统基础设施）：
- 创建 component 表：组件主表，组织内按 (organization_id, name) 唯一，
  含 kind/status/lock_version/created_at/updated_at；
- 创建 component_version 表：组件版本表，按 (component_id, version) 唯一，
  含 manifest_yaml/manifest_sha256/runtime/port_schemas/status/published_at；
- 索引：ix_component_organization_id, ix_component_version_component_id；
- irip_app GRANT 两表权限；
- re-seed 7 个内置角色（添加 component:manage, component:read 权限）。

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 component + component_version 表，授权，re-seed roles。"""

    # ---- component 表 ----
    op.create_table(
        "component",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("name", sa.TEXT, nullable=False),
        sa.Column("kind", sa.TEXT, nullable=False),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "lock_version",
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
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_component_org_name"
        ),
    )
    op.create_index(
        "ix_component_organization_id", "component", ["organization_id"]
    )

    # ---- component_version 表 ----
    op.create_table(
        "component_version",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("component_id", sa.UUID, nullable=False),
        sa.Column("version", sa.TEXT, nullable=False),
        sa.Column("manifest_yaml", sa.TEXT, nullable=False),
        sa.Column("manifest_sha256", sa.TEXT, nullable=False),
        sa.Column("runtime", sa.TEXT, nullable=False),
        sa.Column(
            "port_schemas",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'published'"),
            nullable=False,
        ),
        sa.Column(
            "published_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["component_id"],
            ["component.id"],
            name="fk_component_version_component_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "component_id",
            "version",
            name="uq_component_version_comp_ver",
        ),
    )
    op.create_index(
        "ix_component_version_component_id",
        "component_version",
        ["component_id"],
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON component, component_version TO irip_app"
    )

    # ---- re-seed 7 个内置角色（ON CONFLICT DO UPDATE，写入 component 权限）----
    import json

    from packages.auth.permissions import BUILTIN_ROLES

    for code, info in BUILTIN_ROLES.items():
        display_name = info["display_name"]
        permissions = info["permissions"]
        op.execute(
            sa.text(
                "INSERT INTO role (code, display_name, permissions) "
                "VALUES (:code, :display_name, "
                "CAST(:permissions AS jsonb)) "
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
        "ON component, component_version FROM irip_app"
    )

    op.drop_index(
        "ix_component_version_component_id",
        table_name="component_version",
    )
    op.drop_table("component_version")

    op.drop_index("ix_component_organization_id", table_name="component")
    op.drop_table("component")
