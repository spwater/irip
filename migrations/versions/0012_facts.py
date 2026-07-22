"""facts: fact, fact_revision, raw_observation, normalized_observation,
fact_artifact, fact_revision_link.

增量迁移（IRIP Task 15）：
- 创建 fact 表：事实主表，含状态、当前修订号、幂等键；
- 创建 fact_revision 表：不可变修订表，含全文搜索 tsvector 生成列 + GIN 索引；
- 创建 raw_observation 表：原始观察值（来源数据原始字段值）；
- 创建 normalized_observation 表：标准化观察值（归一化到 L1 变量，必须引用 raw）；
- 创建 fact_artifact 表：事实-工件角色化链接；
- 创建 fact_revision_link 表：修订链链接（supersedes / corrects）；
- irip_app GRANT 六表权限。

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建事实层六张表 + tsvector 生成列 + GIN 索引。"""

    # ---- fact 表 ----
    op.create_table(
        "fact",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("template_version_id", sa.UUID, nullable=False),
        sa.Column("fact_type", sa.TEXT, nullable=False),
        sa.Column("object_id", sa.UUID, nullable=False),
        sa.Column(
            "current_revision",
            sa.INTEGER,
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.TEXT,
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "lock_version",
            sa.INTEGER,
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.TEXT, nullable=True),
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
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["fact_template_version.id"],
            name="fk_fact_template_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["industrial_object.id"],
            name="fk_fact_object_id",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_fact_created_by",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_fact_org_idempotency",
        ),
    )
    op.create_index(
        "ix_fact_organization_id",
        "fact",
        ["organization_id"],
    )
    op.create_index(
        "ix_fact_fact_type",
        "fact",
        ["fact_type"],
    )
    op.create_index(
        "ix_fact_object_id",
        "fact",
        ["object_id"],
    )

    # ---- fact_revision 表 ----
    op.create_table(
        "fact_revision",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("fact_id", sa.UUID, nullable=False),
        sa.Column("revision", sa.INTEGER, nullable=False),
        sa.Column("template_version_id", sa.UUID, nullable=False),
        sa.Column("fact_type", sa.TEXT, nullable=False),
        sa.Column("object_id", sa.UUID, nullable=False),
        sa.Column("subject_id", sa.TEXT, nullable=False),
        sa.Column("method_version_id", sa.UUID, nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revision_reason", sa.TEXT, nullable=True),
        sa.Column("revision_summary", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.UUID, nullable=True),
        sa.ForeignKeyConstraint(
            ["fact_id"],
            ["fact.id"],
            name="fk_fact_revision_fact_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["fact_template_version.id"],
            name="fk_fact_revision_template_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["industrial_object.id"],
            name="fk_fact_revision_object_id",
        ),
        sa.ForeignKeyConstraint(
            ["method_version_id"],
            ["method_version.id"],
            name="fk_fact_revision_method_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_fact_revision_created_by",
        ),
        sa.UniqueConstraint(
            "fact_id", "revision", name="uq_fact_revision_fact_revision"
        ),
    )
    op.create_index(
        "ix_fact_revision_fact_id",
        "fact_revision",
        ["fact_id"],
    )
    # search_vector 生成列：从 subject_id 和 fact_type 生成 tsvector
    op.execute(
        "ALTER TABLE fact_revision ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS ("
        "to_tsvector('simple', coalesce(subject_id, '') || ' ' "
        "|| coalesce(fact_type, ''))"
        ") STORED"
    )
    # GIN 索引在 search_vector 列上（全文搜索）
    op.execute(
        "CREATE INDEX idx_fact_revision_search "
        "ON fact_revision USING GIN(search_vector)"
    )

    # ---- raw_observation 表 ----
    op.create_table(
        "raw_observation",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("fact_revision_id", sa.UUID, nullable=False),
        sa.Column("source_path", sa.TEXT, nullable=False),
        sa.Column("source_value", sa.TEXT, nullable=False),
        sa.Column("source_unit", sa.TEXT, nullable=True),
        sa.Column("source_name", sa.TEXT, nullable=True),
        sa.Column("artifact_id", sa.UUID, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["fact_revision_id"],
            ["fact_revision.id"],
            name="fk_raw_observation_fact_revision_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifact.id"],
            name="fk_raw_observation_artifact_id",
        ),
    )
    op.create_index(
        "ix_raw_observation_fact_revision_id",
        "raw_observation",
        ["fact_revision_id"],
    )

    # ---- normalized_observation 表 ----
    op.create_table(
        "normalized_observation",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("fact_revision_id", sa.UUID, nullable=False),
        sa.Column("variable_version_id", sa.UUID, nullable=False),
        sa.Column("raw_observation_id", sa.UUID, nullable=False),
        sa.Column("value", sa.TEXT, nullable=False),
        sa.Column("unit", sa.TEXT, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["fact_revision_id"],
            ["fact_revision.id"],
            name="fk_normalized_observation_fact_revision_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variable_version_id"],
            ["variable_version.id"],
            name="fk_normalized_observation_variable_version_id",
        ),
        sa.ForeignKeyConstraint(
            ["raw_observation_id"],
            ["raw_observation.id"],
            name="fk_normalized_observation_raw_observation_id",
        ),
    )
    op.create_index(
        "ix_normalized_observation_fact_revision_id",
        "normalized_observation",
        ["fact_revision_id"],
    )
    op.create_index(
        "ix_normalized_observation_variable_version_id",
        "normalized_observation",
        ["variable_version_id"],
    )

    # ---- fact_artifact 表 ----
    op.create_table(
        "fact_artifact",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("fact_revision_id", sa.UUID, nullable=False),
        sa.Column("artifact_id", sa.UUID, nullable=False),
        sa.Column("role", sa.TEXT, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["fact_revision_id"],
            ["fact_revision.id"],
            name="fk_fact_artifact_fact_revision_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["artifact.id"],
            name="fk_fact_artifact_artifact_id",
        ),
    )
    op.create_index(
        "ix_fact_artifact_fact_revision_id",
        "fact_artifact",
        ["fact_revision_id"],
    )

    # ---- fact_revision_link 表 ----
    op.create_table(
        "fact_revision_link",
        sa.Column(
            "id",
            sa.UUID,
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("from_revision_id", sa.UUID, nullable=False),
        sa.Column("to_revision_id", sa.UUID, nullable=False),
        sa.Column("link_type", sa.TEXT, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["from_revision_id"],
            ["fact_revision.id"],
            name="fk_fact_revision_link_from_revision_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_revision_id"],
            ["fact_revision.id"],
            name="fk_fact_revision_link_to_revision_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_fact_revision_link_from_revision_id",
        "fact_revision_link",
        ["from_revision_id"],
    )

    # ---- irip_app 权限 ----
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON fact, fact_revision, raw_observation, "
        "normalized_observation, fact_artifact, fact_revision_link "
        "TO irip_app"
    )


def downgrade() -> None:
    """回滚：撤销权限、删除六张表。"""
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE "
        "ON fact, fact_revision, raw_observation, "
        "normalized_observation, fact_artifact, fact_revision_link "
        "FROM irip_app"
    )

    op.drop_index(
        "ix_fact_revision_link_from_revision_id",
        table_name="fact_revision_link",
    )
    op.drop_table("fact_revision_link")

    op.drop_index(
        "ix_fact_artifact_fact_revision_id", table_name="fact_artifact"
    )
    op.drop_table("fact_artifact")

    op.drop_index(
        "ix_normalized_observation_variable_version_id",
        table_name="normalized_observation",
    )
    op.drop_index(
        "ix_normalized_observation_fact_revision_id",
        table_name="normalized_observation",
    )
    op.drop_table("normalized_observation")

    op.drop_index(
        "ix_raw_observation_fact_revision_id",
        table_name="raw_observation",
    )
    op.drop_table("raw_observation")

    op.execute("DROP INDEX IF EXISTS idx_fact_revision_search")
    op.drop_index("ix_fact_revision_fact_id", table_name="fact_revision")
    op.drop_table("fact_revision")

    op.drop_index("ix_fact_object_id", table_name="fact")
    op.drop_index("ix_fact_fact_type", table_name="fact")
    op.drop_index("ix_fact_organization_id", table_name="fact")
    op.drop_table("fact")
