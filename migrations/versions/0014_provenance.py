"""provenance: evidence_set, evidence_set_version, transformation_recipe,
transformation_recipe_version, derivation_run, provenance_edge.

增量迁移（IRIP Task 17）：
- 创建 evidence_set 表：证据集稳定身份（draft/frozen）；
- 创建 evidence_set_version 表：证据集不可变冻结快照（members JSONB）；
- 创建 transformation_recipe 表：推导配方稳定身份（draft/published/deprecated）；
- 创建 transformation_recipe_version 表：推导配方不可变发布版本；
- 创建 derivation_run 表：一次配方在证据集上的执行记录；
- 创建 provenance_edge 表：溯源图边（连接事实修订、观察值、推导运行、参数版本）；
- irip_app GRANT 六表权限。

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建溯源与推导层六张表。"""

    # ---- evidence_set 表 ----
    op.create_table(
        "evidence_set",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("name", sa.TEXT, nullable=False),
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
    )
    op.create_index(
        "ix_evidence_set_organization_id",
        "evidence_set",
        ["organization_id"],
    )

    # ---- evidence_set_version 表 ----
    op.create_table(
        "evidence_set_version",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("evidence_set_id", sa.UUID, nullable=False),
        sa.Column("version", sa.INTEGER, nullable=False),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'frozen'"),
            nullable=False,
        ),
        sa.Column("members", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column(
            "member_count",
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
            "frozen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["evidence_set_id"],
            ["evidence_set.id"],
            name="fk_evidence_set_version_evidence_set_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "evidence_set_id",
            "version",
            name="uq_evidence_set_version_set_version",
        ),
    )
    op.create_index(
        "ix_evidence_set_version_evidence_set_id",
        "evidence_set_version",
        ["evidence_set_id"],
    )

    # ---- transformation_recipe 表 ----
    op.create_table(
        "transformation_recipe",
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
            "organization_id",
            "code",
            name="uq_transformation_recipe_org_code",
        ),
    )
    op.create_index(
        "ix_transformation_recipe_organization_id",
        "transformation_recipe",
        ["organization_id"],
    )

    # ---- transformation_recipe_version 表 ----
    op.create_table(
        "transformation_recipe_version",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("recipe_id", sa.UUID, nullable=False),
        sa.Column("version", sa.INTEGER, nullable=False),
        sa.Column("component_name", sa.TEXT, nullable=False),
        sa.Column("component_version", sa.TEXT, nullable=False),
        sa.Column("parameters", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column("random_seed", sa.INTEGER, nullable=False),
        sa.Column(
            "output_definitions",
            sa.dialects.postgresql.JSONB,
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'published'"),
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
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["transformation_recipe.id"],
            name="fk_recipe_version_recipe_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "recipe_id",
            "version",
            name="uq_recipe_version_recipe_version",
        ),
    )
    op.create_index(
        "ix_recipe_version_recipe_id",
        "transformation_recipe_version",
        ["recipe_id"],
    )

    # ---- derivation_run 表 ----
    op.create_table(
        "derivation_run",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("evidence_set_version_id", sa.UUID, nullable=False),
        sa.Column("recipe_version_id", sa.UUID, nullable=False),
        sa.Column("job_id", sa.UUID, nullable=True),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("output_digest", sa.TEXT, nullable=True),
        sa.Column("outputs", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error", sa.TEXT, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["evidence_set_version_id"],
            ["evidence_set_version.id"],
            name="fk_derivation_run_evidence_set_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["recipe_version_id"],
            ["transformation_recipe_version.id"],
            name="fk_derivation_run_recipe_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["job.id"],
            name="fk_derivation_run_job_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_derivation_run_organization_id",
        "derivation_run",
        ["organization_id"],
    )
    op.create_index(
        "ix_derivation_run_evidence_set_version_id",
        "derivation_run",
        ["evidence_set_version_id"],
    )
    op.create_index(
        "ix_derivation_run_recipe_version_id",
        "derivation_run",
        ["recipe_version_id"],
    )

    # ---- provenance_edge 表 ----
    op.create_table(
        "provenance_edge",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("derivation_run_id", sa.UUID, nullable=False),
        sa.Column("source_type", sa.TEXT, nullable=False),
        sa.Column("source_id", sa.UUID, nullable=False),
        sa.Column("target_type", sa.TEXT, nullable=False),
        sa.Column("target_id", sa.UUID, nullable=False),
        sa.Column("edge_type", sa.TEXT, nullable=False),
        sa.Column("metadata", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["derivation_run_id"],
            ["derivation_run.id"],
            name="fk_provenance_edge_derivation_run_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_provenance_edge_source",
        "provenance_edge",
        ["source_type", "source_id"],
    )
    op.create_index(
        "ix_provenance_edge_target",
        "provenance_edge",
        ["target_type", "target_id"],
    )
    op.create_index(
        "ix_provenance_edge_derivation_run_id",
        "provenance_edge",
        ["derivation_run_id"],
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON evidence_set, evidence_set_version, "
        "transformation_recipe, transformation_recipe_version, "
        "derivation_run, provenance_edge TO irip_app"
    )


def downgrade() -> None:
    """回滚：撤销权限、删除六张表。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON evidence_set, evidence_set_version, "
        "transformation_recipe, transformation_recipe_version, "
        "derivation_run, provenance_edge FROM irip_app"
    )

    op.drop_index(
        "ix_provenance_edge_derivation_run_id", table_name="provenance_edge"
    )
    op.drop_index("ix_provenance_edge_target", table_name="provenance_edge")
    op.drop_index("ix_provenance_edge_source", table_name="provenance_edge")
    op.drop_table("provenance_edge")

    op.drop_index(
        "ix_derivation_run_recipe_version_id", table_name="derivation_run"
    )
    op.drop_index(
        "ix_derivation_run_evidence_set_version_id",
        table_name="derivation_run",
    )
    op.drop_index(
        "ix_derivation_run_organization_id", table_name="derivation_run"
    )
    op.drop_table("derivation_run")

    op.drop_index(
        "ix_recipe_version_recipe_id",
        table_name="transformation_recipe_version",
    )
    op.drop_table("transformation_recipe_version")

    op.drop_index(
        "ix_transformation_recipe_organization_id",
        table_name="transformation_recipe",
    )
    op.drop_table("transformation_recipe")

    op.drop_index(
        "ix_evidence_set_version_evidence_set_id",
        table_name="evidence_set_version",
    )
    op.drop_table("evidence_set_version")

    op.drop_index(
        "ix_evidence_set_organization_id", table_name="evidence_set"
    )
    op.drop_table("evidence_set")
