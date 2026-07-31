"""Drop 10 standards empty tables + fact/ingestion_job template_version_id columns.

Revision ID: 0057
Revises: 0056
Create Date: 2025-07-31

Changes:
- DROP COLUMN fact.template_version_id (nullable, no FK after 0025);
- DROP COLUMN ingestion_job.template_version_id (plain UUID, no FK);
- DROP COLUMN ingestion_job.mapping_profile_version_id (plain UUID, no FK);
- DROP TABLE variable_version (FK→variable.id);
- DROP TABLE variable_alias (FK→variable.id);
- DROP TABLE equipment_variable (FK→equipment.id + FK→variable.id);
- DROP TABLE fact_template_version (FK→fact_template.id);
- DROP TABLE fact_template;
- DROP TABLE standard_package_version (FK→standard_package.id);
- DROP TABLE standard_package;
- DROP TABLE mapping_profile_version (FK→mapping_profile.id);
- DROP TABLE mapping_profile.

上述 10 张表（variable / variable_version / variable_alias /
standard_package / standard_package_version / fact_template /
fact_template_version / equipment_variable / mapping_profile /
mapping_profile_version）全部 0 条数据。代码中所有引用已在迁移执行前清理
（packages/standards/variables|templates|packages、connectors/mapping 等）。
DROP TABLE 会自动清理关联的 RLS policy（0032 创建）与 irip_runtime 权限
（0034 创建），无需显式 REVOKE。
"""

import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除 10 张空表及 fact / ingestion_job 上的相关列。"""

    # 1. 删除 fact.template_version_id 列（0025 已去掉 FK 约束，列已 nullable）
    op.drop_column("fact", "template_version_id")

    # 2. 删除 ingestion_job.template_version_id 列（0013 创建时为普通 UUID，无 FK）
    op.drop_column("ingestion_job", "template_version_id")

    # 3. 删除 ingestion_job.mapping_profile_version_id 列（0013 创建时为普通 UUID，无 FK）
    op.drop_column("ingestion_job", "mapping_profile_version_id")

    # 4. 删除 variable_version 表（FK→variable.id，必须先于 variable 删除）
    op.drop_index("ix_variable_version_variable_id", table_name="variable_version")
    op.drop_table("variable_version")

    # 5. 删除 variable_alias 表（FK→variable.id，必须先于 variable 删除）
    op.drop_index("ix_variable_alias_variable_alias", table_name="variable_alias")
    op.drop_table("variable_alias")

    # 6. 删除 equipment_variable 表（FK→equipment.id + FK→variable.id）
    op.drop_index(
        "ix_equipment_variable_variable_id", table_name="equipment_variable"
    )
    op.drop_table("equipment_variable")

    # 7. 删除 variable 表
    op.drop_index("ix_variable_organization_id", table_name="variable")
    op.drop_index("ix_variable_organization_code", table_name="variable")
    op.drop_table("variable")

    # 8. 删除 fact_template_version 表（FK→fact_template.id，必须先于 fact_template 删除）
    op.drop_index(
        "ix_fact_template_version_template_id", table_name="fact_template_version"
    )
    op.drop_table("fact_template_version")

    # 9. 删除 fact_template 表
    op.drop_index("ix_fact_template_organization_id", table_name="fact_template")
    op.drop_index(
        "ix_fact_template_organization_code", table_name="fact_template"
    )
    op.drop_table("fact_template")

    # 10. 删除 standard_package_version 表（FK→standard_package.id）
    op.drop_index(
        "ix_standard_package_version_package_id",
        table_name="standard_package_version",
    )
    op.drop_table("standard_package_version")

    # 11. 删除 standard_package 表
    op.drop_index("ix_standard_package_organization_id", table_name="standard_package")
    op.drop_index(
        "ix_standard_package_organization_code", table_name="standard_package"
    )
    op.drop_table("standard_package")

    # 12. 删除 mapping_profile_version 表（FK→mapping_profile.id）
    op.drop_index(
        "ix_mapping_profile_version_profile_id",
        table_name="mapping_profile_version",
    )
    op.drop_table("mapping_profile_version")

    # 13. 删除 mapping_profile 表
    op.drop_index("ix_mapping_profile_organization_id", table_name="mapping_profile")
    op.drop_index(
        "ix_mapping_profile_organization_name", table_name="mapping_profile"
    )
    op.drop_table("mapping_profile")


def downgrade() -> None:
    """重建 10 张表及 fact / ingestion_job 上的相关列（不含历史数据）。"""

    # 1. 重建 mapping_profile 表
    op.create_table(
        "mapping_profile",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("source_kind", sa.Text, nullable=False),
        sa.Column("source_config", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column(
            "status", sa.Text, nullable=False, server_default=sa.text("'draft'")
        ),
        sa.Column(
            "lock_version", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
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
        "ix_mapping_profile_organization_id", "mapping_profile", ["organization_id"]
    )

    # 2. 重建 mapping_profile_version 表
    op.create_table(
        "mapping_profile_version",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column(
            "profile_id",
            sa.UUID,
            sa.ForeignKey("mapping_profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column(
            "rules",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "status", sa.Text, nullable=False, server_default=sa.text("'draft'")
        ),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "lock_version", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_mapping_profile_version_profile_id",
        "mapping_profile_version",
        ["profile_id"],
    )

    # 3. 重建 standard_package 表
    op.create_table(
        "standard_package",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "status", sa.Text, nullable=False, server_default=sa.text("'draft'")
        ),
        sa.Column(
            "version_count", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "lock_version", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.UniqueConstraint(
            "organization_id", "code", name="uq_standard_package_org_code"
        ),
    )
    op.create_index(
        "ix_standard_package_organization_code",
        "standard_package",
        ["organization_id", "code"],
        unique=True,
    )
    op.create_index(
        "ix_standard_package_organization_id", "standard_package", ["organization_id"]
    )

    # 4. 重建 standard_package_version 表
    op.create_table(
        "standard_package_version",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column(
            "package_id",
            sa.UUID,
            sa.ForeignKey("standard_package.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("variable_refs", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("method_refs", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("template_refs", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("quality_rule_refs", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("published_by", sa.UUID, nullable=True),
        sa.Column("deprecated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deprecated_by", sa.UUID, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "lock_version", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_index(
        "ix_standard_package_version_package_id",
        "standard_package_version",
        ["package_id"],
    )

    # 5. 重建 fact_template 表
    op.create_table(
        "fact_template",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("fact_type", sa.Text, nullable=False),
        sa.Column(
            "status", sa.Text, nullable=False, server_default=sa.text("'draft'")
        ),
        sa.Column(
            "version_count", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "lock_version", sa.Integer, nullable=False, server_default=sa.text("0")
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
        "ix_fact_template_organization_id", "fact_template", ["organization_id"]
    )

    # 6. 重建 fact_template_version 表
    op.create_table(
        "fact_template_version",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column(
            "template_id",
            sa.UUID,
            sa.ForeignKey("fact_template.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("fact_type", sa.Text, nullable=False),
        sa.Column("required_conditions", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("observations", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column(
            "required_artifact_roles", sa.dialects.postgresql.JSONB, nullable=True
        ),
        sa.Column("quality_rule_codes", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("published_by", sa.UUID, nullable=True),
        sa.Column("deprecated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deprecated_by", sa.UUID, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "lock_version", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_index(
        "ix_fact_template_version_template_id",
        "fact_template_version",
        ["template_id"],
    )

    # 7. 重建 variable 表
    op.create_table(
        "variable",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("data_type", sa.Text, nullable=False),
        sa.Column("canonical_unit", sa.Text, nullable=True),
        sa.Column("quantity_kind", sa.Text, nullable=True),
        sa.Column("valid_range", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column(
            "status", sa.Text, nullable=False, server_default=sa.text("'draft'")
        ),
        sa.Column(
            "version_count", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "lock_version", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.UniqueConstraint("organization_id", "code", name="uq_variable_org_code"),
    )
    op.create_index(
        "ix_variable_organization_code",
        "variable",
        ["organization_id", "code"],
        unique=True,
    )
    op.create_index("ix_variable_organization_id", "variable", ["organization_id"])

    # 8. 重建 equipment_variable 表
    op.create_table(
        "equipment_variable",
        sa.Column(
            "equipment_id",
            sa.UUID,
            sa.ForeignKey("equipment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "variable_id",
            sa.UUID,
            sa.ForeignKey("variable.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("equipment_id", "variable_id"),
    )
    op.create_index(
        "ix_equipment_variable_variable_id", "equipment_variable", ["variable_id"]
    )

    # 9. 重建 variable_alias 表
    op.create_table(
        "variable_alias",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column(
            "variable_id",
            sa.UUID,
            sa.ForeignKey("variable.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.Text, nullable=False),
        sa.Column(
            "language", sa.Text, nullable=False, server_default=sa.text("'zh'")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("variable_id", "alias", name="uq_variable_alias"),
    )
    op.create_index(
        "ix_variable_alias_variable_alias",
        "variable_alias",
        ["variable_id", "alias"],
        unique=True,
    )

    # 10. 重建 variable_version 表
    op.create_table(
        "variable_version",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column(
            "variable_id",
            sa.UUID,
            sa.ForeignKey("variable.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("data_type", sa.Text, nullable=False),
        sa.Column("canonical_unit", sa.Text, nullable=True),
        sa.Column("quantity_kind", sa.Text, nullable=True),
        sa.Column("valid_range", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("published_by", sa.UUID, nullable=True),
        sa.Column("deprecated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deprecated_by", sa.UUID, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "lock_version", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_index(
        "ix_variable_version_variable_id", "variable_version", ["variable_id"]
    )

    # 11. 恢复 ingestion_job.mapping_profile_version_id 列
    op.add_column(
        "ingestion_job", sa.Column("mapping_profile_version_id", sa.UUID, nullable=True)
    )

    # 12. 恢复 ingestion_job.template_version_id 列
    op.add_column(
        "ingestion_job", sa.Column("template_version_id", sa.UUID, nullable=True)
    )

    # 13. 恢复 fact.template_version_id 列（nullable，不恢复 FK）
    op.add_column("fact", sa.Column("template_version_id", sa.UUID, nullable=True))
