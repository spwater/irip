"""mapping_profiles: mapping_profile, mapping_profile_version, secret.

增量迁移（IRIP Task 13）：
- 创建 mapping_profile 表：映射配置主表，name 组织内唯一，含状态机字段；
- 创建 mapping_profile_version 表：不可变版本表，存储规则快照（JSONB），
  发布后锁定不可修改；
- 创建 secret 表：密钥表，按 id 引用存储外部数据源凭据
  （MVP 明文，TODO 加密）；
- 索引：UNIQUE(org, name) for mapping_profile；FK indexes；
- irip_app GRANT 三表权限。

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 mapping_profile / mapping_profile_version / secret 三张表。"""

    # ---- mapping_profile 表 ----
    op.create_table(
        "mapping_profile",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("name", sa.TEXT, nullable=False),
        sa.Column("source_kind", sa.TEXT, nullable=False),
        sa.Column("source_config", sa.dialects.postgresql.JSONB, nullable=False),
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
        sa.Column("created_by", sa.UUID, nullable=True),
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_mapping_profile_org_name"
        ),
    )
    op.create_index(
        "ix_mapping_profile_organization_name",
        "mapping_profile",
        ["organization_id", "name"],
        unique=True,
    )
    op.create_index(
        "ix_mapping_profile_organization_id",
        "mapping_profile",
        ["organization_id"],
    )

    # ---- mapping_profile_version 表 ----
    op.create_table(
        "mapping_profile_version",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("profile_id", sa.UUID, nullable=False),
        sa.Column("version", sa.INTEGER, nullable=False),
        sa.Column(
            "rules",
            sa.dialects.postgresql.JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "published_at", sa.TIMESTAMP(timezone=True), nullable=True
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
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["mapping_profile.id"],
            name="fk_mapping_profile_version_profile_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_mapping_profile_version_profile_id",
        "mapping_profile_version",
        ["profile_id"],
    )

    # ---- secret 表 ----
    op.create_table(
        "secret",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("kind", sa.TEXT, nullable=False),
        sa.Column("value", sa.TEXT, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_secret_organization_id",
        "secret",
        ["organization_id"],
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mapping_profile, mapping_profile_version, secret TO irip_app"
    )


def downgrade() -> None:
    """回滚：撤销权限、删除三张表。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON mapping_profile, mapping_profile_version, secret FROM irip_app"
    )

    op.drop_index("ix_secret_organization_id", table_name="secret")
    op.drop_table("secret")

    op.drop_index(
        "ix_mapping_profile_version_profile_id",
        table_name="mapping_profile_version",
    )
    op.drop_table("mapping_profile_version")

    op.drop_index(
        "ix_mapping_profile_organization_id", table_name="mapping_profile"
    )
    op.drop_index(
        "ix_mapping_profile_organization_name", table_name="mapping_profile"
    )
    op.drop_table("mapping_profile")
