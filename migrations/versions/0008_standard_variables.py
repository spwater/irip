"""standard_variables: variable + variable_version + variable_alias.

增量迁移（IRIP Task 10）：
- 创建 variable 表：标准变量主表，code 组织内唯一，含状态机字段；
- 创建 variable_version 表：不可变版本表，每次提交审核创建一行，发布后锁定；
- 创建 variable_alias 表：别名表，(variable_id, alias) 唯一；
- 索引：ix_variable_organization_code (organization_id, code)、
  ix_variable_version_variable_id、ix_variable_alias_variable_alias；
- irip_app GRANT 三表权限。

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 variable + variable_version + variable_alias 表。"""

    # ---- variable 表 ----
    op.create_table(
        "variable",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("code", sa.TEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column("data_type", sa.TEXT, nullable=False),
        sa.Column("canonical_unit", sa.TEXT, nullable=True),
        sa.Column("quantity_kind", sa.TEXT, nullable=True),
        sa.Column("valid_range", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "version_count",
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
        sa.UniqueConstraint("organization_id", "code", name="uq_variable_org_code"),
    )
    op.create_index(
        "ix_variable_organization_code",
        "variable",
        ["organization_id", "code"],
        unique=True,
    )
    op.create_index(
        "ix_variable_organization_id", "variable", ["organization_id"]
    )

    # ---- variable_version 表 ----
    op.create_table(
        "variable_version",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("variable_id", sa.UUID, nullable=False),
        sa.Column("version", sa.INTEGER, nullable=False),
        sa.Column("code", sa.TEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column("data_type", sa.TEXT, nullable=False),
        sa.Column("canonical_unit", sa.TEXT, nullable=True),
        sa.Column("quantity_kind", sa.TEXT, nullable=True),
        sa.Column("valid_range", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("status", sa.TEXT, nullable=False),
        sa.Column(
            "published_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("published_by", sa.UUID, nullable=True),
        sa.Column(
            "deprecated_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("deprecated_by", sa.UUID, nullable=True),
        sa.Column("rejection_reason", sa.TEXT, nullable=True),
        sa.Column(
            "created_at",
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
            ["variable_id"],
            ["variable.id"],
            name="fk_variable_version_variable_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_variable_version_variable_id",
        "variable_version",
        ["variable_id"],
    )

    # ---- variable_alias 表 ----
    op.create_table(
        "variable_alias",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("variable_id", sa.UUID, nullable=False),
        sa.Column("alias", sa.TEXT, nullable=False),
        sa.Column(
            "language",
            sa.TEXT,
            server_default=sa.text("'zh'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["variable_id"],
            ["variable.id"],
            name="fk_variable_alias_variable_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("variable_id", "alias", name="uq_variable_alias"),
    )
    op.create_index(
        "ix_variable_alias_variable_alias",
        "variable_alias",
        ["variable_id", "alias"],
        unique=True,
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON variable, variable_version, variable_alias TO irip_app"
    )


def downgrade() -> None:
    """回滚：撤销权限、删除三张表。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON variable, variable_version, variable_alias FROM irip_app"
    )

    op.drop_index(
        "ix_variable_alias_variable_alias", table_name="variable_alias"
    )
    op.drop_table("variable_alias")

    op.drop_index(
        "ix_variable_version_variable_id", table_name="variable_version"
    )
    op.drop_table("variable_version")

    op.drop_index("ix_variable_organization_id", table_name="variable")
    op.drop_index("ix_variable_organization_code", table_name="variable")
    op.drop_table("variable")
