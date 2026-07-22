"""parameters: parameter, parameter_version, parameter_candidate,
parameter_staleness.

增量迁移（IRIP Task 18）：
- 创建 parameter 表：参数稳定身份（draft/pending_review/published/
  rejected/expired/deprecated）；
- 创建 parameter_version 表：参数不可变发布版本（value/unit/confidence/
  conditions AST/derivation_run 引用）；
- 创建 parameter_candidate 表：推导产出的候选（pending_review/
  approved/rejected），含提交人/审核人；
- 创建 parameter_staleness 表：事实修订 → 参数版本依赖跟踪
  （current/review_required）；
- irip_app GRANT 四表权限。

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建参数层四张表。"""

    # ---- parameter 表 ----
    op.create_table(
        "parameter",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("variable_code", sa.TEXT, nullable=False),
        sa.Column("object_id", sa.UUID, nullable=False),
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
            "organization_id",
            "variable_code",
            "object_id",
            name="uq_parameter_org_var_obj",
        ),
    )
    op.create_index(
        "ix_parameter_organization_id",
        "parameter",
        ["organization_id"],
    )

    # ---- parameter_version 表 ----
    op.create_table(
        "parameter_version",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("parameter_id", sa.UUID, nullable=False),
        sa.Column("version", sa.INTEGER, nullable=False),
        sa.Column("value", sa.TEXT, nullable=False),
        sa.Column("unit", sa.TEXT, nullable=True),
        sa.Column("confidence", sa.TEXT, nullable=True),
        sa.Column(
            "confidence_interval", sa.dialects.postgresql.JSONB, nullable=True
        ),
        sa.Column(
            "conditions", sa.dialects.postgresql.JSONB, nullable=True
        ),
        sa.Column("derivation_run_id", sa.UUID, nullable=False),
        sa.Column("evidence_set_version_id", sa.UUID, nullable=False),
        sa.Column("recipe_version_id", sa.UUID, nullable=False),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'published'"),
            nullable=False,
        ),
        sa.Column(
            "published_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_by", sa.UUID, nullable=False),
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
            ["parameter_id"],
            ["parameter.id"],
            name="fk_parameter_version_parameter_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["derivation_run_id"],
            ["derivation_run.id"],
            name="fk_parameter_version_derivation_run_id",
        ),
        sa.UniqueConstraint(
            "parameter_id",
            "version",
            name="uq_parameter_version_param_version",
        ),
    )
    op.create_index(
        "ix_parameter_version_parameter_id",
        "parameter_version",
        ["parameter_id"],
    )

    # ---- parameter_candidate 表 ----
    op.create_table(
        "parameter_candidate",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("parameter_id", sa.UUID, nullable=False),
        sa.Column("derivation_run_id", sa.UUID, nullable=False),
        sa.Column("value", sa.TEXT, nullable=False),
        sa.Column("unit", sa.TEXT, nullable=True),
        sa.Column("confidence", sa.TEXT, nullable=True),
        sa.Column(
            "confidence_interval", sa.dialects.postgresql.JSONB, nullable=True
        ),
        sa.Column(
            "conditions", sa.dialects.postgresql.JSONB, nullable=True
        ),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'pending_review'"),
            nullable=False,
        ),
        sa.Column("submitted_by", sa.UUID, nullable=False),
        sa.Column(
            "submitted_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reviewed_by", sa.UUID, nullable=True),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("review_decision", sa.TEXT, nullable=True),
        sa.Column("review_comment", sa.TEXT, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parameter_id"],
            ["parameter.id"],
            name="fk_parameter_candidate_parameter_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["derivation_run_id"],
            ["derivation_run.id"],
            name="fk_parameter_candidate_derivation_run_id",
        ),
        sa.UniqueConstraint(
            "parameter_id",
            "derivation_run_id",
            name="uq_parameter_candidate_param_deriv",
        ),
    )
    op.create_index(
        "ix_parameter_candidate_parameter_id",
        "parameter_candidate",
        ["parameter_id"],
    )

    # ---- parameter_staleness 表 ----
    op.create_table(
        "parameter_staleness",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("parameter_version_id", sa.UUID, nullable=False),
        sa.Column("fact_revision_id", sa.UUID, nullable=False),
        sa.Column(
            "review_state",
            sa.TEXT,
            server_default=sa.text("'current'"),
            nullable=False,
        ),
        sa.Column(
            "last_checked_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parameter_version_id"],
            ["parameter_version.id"],
            name="fk_parameter_staleness_parameter_version_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["fact_revision_id"],
            ["fact_revision.id"],
            name="fk_parameter_staleness_fact_revision_id",
        ),
    )
    op.create_index(
        "ix_parameter_staleness_parameter_version_id",
        "parameter_staleness",
        ["parameter_version_id"],
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON parameter, parameter_version, "
        "parameter_candidate, parameter_staleness TO irip_app"
    )


def downgrade() -> None:
    """回滚：撤销权限、删除四张表。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON parameter, parameter_version, "
        "parameter_candidate, parameter_staleness FROM irip_app"
    )

    op.drop_index(
        "ix_parameter_staleness_parameter_version_id",
        table_name="parameter_staleness",
    )
    op.drop_table("parameter_staleness")

    op.drop_index(
        "ix_parameter_candidate_parameter_id",
        table_name="parameter_candidate",
    )
    op.drop_table("parameter_candidate")

    op.drop_index(
        "ix_parameter_version_parameter_id",
        table_name="parameter_version",
    )
    op.drop_table("parameter_version")

    op.drop_index(
        "ix_parameter_organization_id", table_name="parameter"
    )
    op.drop_table("parameter")
