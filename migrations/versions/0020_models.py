"""model + model_version: 模型生命周期。

增量迁移（IRIP V2-T04 — 模型生命周期）：
- 创建 model 表：模型主表，组织内按 (organization_id, code) 唯一，
  含 status/current_version_id（发布指针）/lock_version/created_at/updated_at；
- 创建 model_version 表：模型版本表，按 (model_id, version) 唯一，
  含 contract_json(JSONB)/model_artifact_id/metrics_json(JSONB)/
  applicability_domain_json(JSONB)/code_hash/dependency_hash/model_hash/
  status/created_at/published_at；
- 索引：ix_model_organization_id, ix_model_version_model_id；
- irip_app GRANT 两表权限；
- re-seed 7 个内置角色（添加 model:manage, model:read 权限）。

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-22
"""

from collections.abc import Sequence
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建两张表，授权，re-seed roles。"""

    # ---- model 表 ----
    op.create_table(
        "model",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("code", sa.TEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("current_version_id", sa.UUID, nullable=True),
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
            "organization_id", "code", name="uq_model_org_code"
        ),
    )
    op.create_index(
        "ix_model_organization_id",
        "model",
        ["organization_id"],
    )

    # ---- model_version 表 ----
    op.create_table(
        "model_version",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("model_id", sa.UUID, nullable=False),
        sa.Column("version", sa.INTEGER, nullable=False),
        sa.Column(
            "contract_json",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("model_artifact_id", sa.UUID, nullable=True),
        sa.Column(
            "metrics_json",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "applicability_domain_json",
            JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.TEXT, nullable=True),
        sa.Column("dependency_hash", sa.TEXT, nullable=True),
        sa.Column("model_hash", sa.TEXT, nullable=True),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "published_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["model.id"],
            name="fk_model_version_model_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "model_id", "version", name="uq_model_version_model_ver"
        ),
    )
    op.create_index(
        "ix_model_version_model_id",
        "model_version",
        ["model_id"],
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON model, model_version TO irip_app"
    )

    # ---- re-seed 7 个内置角色（ON CONFLICT DO UPDATE，写入 model 权限）----
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
        "ON model, model_version FROM irip_app"
    )

    op.drop_index(
        "ix_model_version_model_id",
        table_name="model_version",
    )
    op.drop_table("model_version")

    op.drop_index(
        "ix_model_organization_id",
        table_name="model",
    )
    op.drop_table("model")
