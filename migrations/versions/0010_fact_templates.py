"""fact_templates: method, method_version, fact_template, fact_template_version,
standard_package, standard_package_version.

增量迁移（IRIP Task 12）：
- 创建 method 表：方法主表，code 组织内唯一，含状态机字段；
- 创建 method_version 表：方法不可变版本表；
- 创建 fact_template 表：事实模板主表，code 组织内唯一；
- 创建 fact_template_version 表：模板版本表，存储观测要求 / 必要条件 / 质量规则引用；
- 创建 standard_package 表：标准包主表，code 组织内唯一；
- 创建 standard_package_version 表：包版本表，存储变量/方法/模板/质量规则引用；
- 索引：UNIQUE(org, code) for method, fact_template, standard_package；FK indexes；
- irip_app GRANT 六表权限。

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 method / fact_template / standard_package 六张表。"""

    # ---- method 表 ----
    op.create_table(
        "method",
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
        sa.UniqueConstraint("organization_id", "code", name="uq_method_org_code"),
    )
    op.create_index(
        "ix_method_organization_code",
        "method",
        ["organization_id", "code"],
        unique=True,
    )
    op.create_index(
        "ix_method_organization_id", "method", ["organization_id"]
    )

    # ---- method_version 表 ----
    op.create_table(
        "method_version",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("method_id", sa.UUID, nullable=False),
        sa.Column("version", sa.INTEGER, nullable=False),
        sa.Column("code", sa.TEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column("description", sa.TEXT, nullable=True),
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
            ["method_id"],
            ["method.id"],
            name="fk_method_version_method_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_method_version_method_id",
        "method_version",
        ["method_id"],
    )

    # ---- fact_template 表 ----
    op.create_table(
        "fact_template",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("code", sa.TEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column("fact_type", sa.TEXT, nullable=False),
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
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_fact_template_org_code"
        ),
    )
    op.create_index(
        "ix_fact_template_organization_code",
        "fact_template",
        ["organization_id", "code"],
        unique=True,
    )
    op.create_index(
        "ix_fact_template_organization_id",
        "fact_template",
        ["organization_id"],
    )

    # ---- fact_template_version 表 ----
    op.create_table(
        "fact_template_version",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("template_id", sa.UUID, nullable=False),
        sa.Column("version", sa.INTEGER, nullable=False),
        sa.Column("code", sa.TEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column("fact_type", sa.TEXT, nullable=False),
        sa.Column(
            "required_conditions",
            sa.dialects.postgresql.JSONB,
            nullable=True,
        ),
        sa.Column(
            "observations",
            sa.dialects.postgresql.JSONB,
            nullable=True,
        ),
        sa.Column(
            "required_artifact_roles",
            sa.dialects.postgresql.JSONB,
            nullable=True,
        ),
        sa.Column(
            "quality_rule_codes",
            sa.dialects.postgresql.JSONB,
            nullable=True,
        ),
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
            ["template_id"],
            ["fact_template.id"],
            name="fk_fact_template_version_template_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_fact_template_version_template_id",
        "fact_template_version",
        ["template_id"],
    )

    # ---- standard_package 表 ----
    op.create_table(
        "standard_package",
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
        sa.UniqueConstraint(
            "organization_id",
            "code",
            name="uq_standard_package_org_code",
        ),
    )
    op.create_index(
        "ix_standard_package_organization_code",
        "standard_package",
        ["organization_id", "code"],
        unique=True,
    )
    op.create_index(
        "ix_standard_package_organization_id",
        "standard_package",
        ["organization_id"],
    )

    # ---- standard_package_version 表 ----
    op.create_table(
        "standard_package_version",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("package_id", sa.UUID, nullable=False),
        sa.Column("version", sa.INTEGER, nullable=False),
        sa.Column("code", sa.TEXT, nullable=False),
        sa.Column("display_name", sa.TEXT, nullable=False),
        sa.Column("description", sa.TEXT, nullable=True),
        sa.Column(
            "variable_refs",
            sa.dialects.postgresql.JSONB,
            nullable=True,
        ),
        sa.Column(
            "method_refs",
            sa.dialects.postgresql.JSONB,
            nullable=True,
        ),
        sa.Column(
            "template_refs",
            sa.dialects.postgresql.JSONB,
            nullable=True,
        ),
        sa.Column(
            "quality_rule_refs",
            sa.dialects.postgresql.JSONB,
            nullable=True,
        ),
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
            ["package_id"],
            ["standard_package.id"],
            name="fk_standard_package_version_package_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_standard_package_version_package_id",
        "standard_package_version",
        ["package_id"],
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON method, method_version, "
        "fact_template, fact_template_version, "
        "standard_package, standard_package_version TO irip_app"
    )


def downgrade() -> None:
    """回滚：撤销权限、删除六张表。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON method, method_version, "
        "fact_template, fact_template_version, "
        "standard_package, standard_package_version FROM irip_app"
    )

    op.drop_index(
        "ix_standard_package_version_package_id",
        table_name="standard_package_version",
    )
    op.drop_table("standard_package_version")

    op.drop_index(
        "ix_standard_package_organization_id",
        table_name="standard_package",
    )
    op.drop_index(
        "ix_standard_package_organization_code",
        table_name="standard_package",
    )
    op.drop_table("standard_package")

    op.drop_index(
        "ix_fact_template_version_template_id",
        table_name="fact_template_version",
    )
    op.drop_table("fact_template_version")

    op.drop_index(
        "ix_fact_template_organization_id",
        table_name="fact_template",
    )
    op.drop_index(
        "ix_fact_template_organization_code",
        table_name="fact_template",
    )
    op.drop_table("fact_template")

    op.drop_index(
        "ix_method_version_method_id", table_name="method_version"
    )
    op.drop_table("method_version")

    op.drop_index("ix_method_organization_id", table_name="method")
    op.drop_index("ix_method_organization_code", table_name="method")
    op.drop_table("method")
