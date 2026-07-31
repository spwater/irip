"""Drop fact version chain: remove 7 tables, merge fields into fact.

Revision ID: 0055
Revises: 0054
Create Date: 2025-07-01

Changes:
- DROP 7 tables: fact_revision, raw_observation, normalized_observation,
  quality_assessment, fact_artifact, fact_revision_link, parameter_staleness;
- ALTER fact table: add 11 merged fields + source_artifact_id;
- ALTER fact table: DROP current_revision column;
- CREATE fact_data_index table (FK→fact.id CASCADE).
"""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop version chain tables and merge fields into fact table."""

    # 1. DROP trigger on fact_revision (safety, table will be dropped anyway)
    op.execute("DROP TRIGGER IF EXISTS prevent_modify_fact_revision ON fact_revision")

    # 2. DROP 7 tables (order: dependent tables first, then fact_revision)
    # fact_revision_link, raw_observation, normalized_observation, fact_artifact,
    # quality_assessment, parameter_staleness all depend on fact_revision
    op.drop_table("parameter_staleness")
    op.drop_table("fact_revision_link")
    op.drop_table("normalized_observation")
    op.drop_table("raw_observation")
    op.drop_table("quality_assessment")
    op.drop_table("fact_artifact")
    op.drop_table("fact_revision")

    # 3. ALTER fact table: add merged fields from fact_revision
    op.add_column("fact", sa.Column("subject_id", sa.Text, nullable=False, server_default=""))
    op.add_column("fact", sa.Column("method_version_id", sa.UUID, nullable=True))
    op.add_column("fact", sa.Column("flow_run_id", sa.UUID, nullable=True))
    op.add_column("fact", sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("fact", sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("fact", sa.Column("task_code", sa.Text, nullable=True))
    op.add_column("fact", sa.Column("task_name", sa.Text, nullable=True))
    op.add_column("fact", sa.Column("department_name", sa.Text, nullable=True))
    op.add_column("fact", sa.Column("operator", sa.Text, nullable=True))
    op.add_column("fact", sa.Column("run_operator", sa.Text, nullable=True))
    op.add_column("fact", sa.Column("equipment_name", sa.Text, nullable=True))
    op.add_column("fact", sa.Column("source_artifact_id", sa.UUID, nullable=True))

    # 4. search_vector generated column (tsvector)
    op.execute(
        "ALTER TABLE fact ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS ("
        "to_tsvector('simple', coalesce(subject_id, '') || ' ' || coalesce(fact_type, ''))"
        ") STORED"
    )

    # 5. Add FK constraints for method_version_id and flow_run_id
    op.create_foreign_key(
        "fk_fact_method_version_id",
        "fact",
        "method_version",
        ["method_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_fact_flow_run_id",
        "fact",
        "flow_run",
        ["flow_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_fact_source_artifact_id",
        "fact",
        "artifact",
        ["source_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 6. GIN index for search_vector
    op.create_index(
        "ix_fact_search_vector",
        "fact",
        ["search_vector"],
        postgresql_using="gin",
    )

    # 7. DROP current_revision from fact
    op.drop_column("fact", "current_revision")

    # 8. CREATE fact_data_index table (new — never existed before)
    op.create_table(
        "fact_data_index",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column(
            "fact_id",
            sa.UUID,
            sa.ForeignKey("fact.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_index", sa.Integer, nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("value_text", sa.Text, nullable=True),
        sa.Column("value_number", sa.Float, nullable=True),
    )


def downgrade() -> None:
    """Restore fact version chain (not fully reversible — data lost)."""

    # Drop fact_data_index table (created in upgrade, never existed before)
    op.drop_table("fact_data_index")

    # Restore current_revision
    op.add_column(
        "fact",
        sa.Column("current_revision", sa.Integer, nullable=False, server_default=sa.text("1")),
    )

    # Drop search_vector index and column
    op.drop_index("ix_fact_search_vector", table_name="fact")
    op.drop_column("fact", "search_vector")

    # Drop FK constraints
    op.drop_constraint("fk_fact_source_artifact_id", "fact", type_="foreignkey")
    op.drop_constraint("fk_fact_flow_run_id", "fact", type_="foreignkey")
    op.drop_constraint("fk_fact_method_version_id", "fact", type_="foreignkey")

    # Drop merged columns
    op.drop_column("fact", "source_artifact_id")
    op.drop_column("fact", "equipment_name")
    op.drop_column("fact", "run_operator")
    op.drop_column("fact", "operator")
    op.drop_column("fact", "department_name")
    op.drop_column("fact", "task_name")
    op.drop_column("fact", "task_code")
    op.drop_column("fact", "ended_at")
    op.drop_column("fact", "started_at")
    op.drop_column("fact", "flow_run_id")
    op.drop_column("fact", "method_version_id")
    op.drop_column("fact", "subject_id")

    # Recreate 7 tables (minimal structure, no data)
    op.create_table(
        "fact_revision",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("fact_id", sa.UUID, sa.ForeignKey("fact.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("template_version_id", sa.UUID, nullable=True),
        sa.Column("fact_type", sa.Text, nullable=False),
        sa.Column("object_id", sa.UUID, sa.ForeignKey("industrial_object.id"), nullable=False),
        sa.Column("subject_id", sa.Text, nullable=False),
        sa.Column("method_version_id", sa.UUID, sa.ForeignKey("method_version.id"), nullable=True),
        sa.Column("task_code", sa.Text, nullable=True),
        sa.Column("task_name", sa.Text, nullable=True),
        sa.Column("department_name", sa.Text, nullable=True),
        sa.Column("operator", sa.Text, nullable=True),
        sa.Column("run_operator", sa.Text, nullable=True),
        sa.Column("equipment_name", sa.Text, nullable=True),
        sa.Column("flow_run_id", sa.UUID, sa.ForeignKey("flow_run.id", ondelete="SET NULL"), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revision_reason", sa.Text, nullable=True),
        sa.Column("revision_summary", sa.JSON, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.UUID, sa.ForeignKey("app_user.id"), nullable=True),
        sa.UniqueConstraint("fact_id", "revision", name="uq_fact_revision_fact_revision"),
    )

    op.create_table(
        "raw_observation",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("fact_revision_id", sa.UUID, sa.ForeignKey("fact_revision.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_path", sa.Text, nullable=False),
        sa.Column("source_value", sa.Text, nullable=False),
        sa.Column("source_unit", sa.Text, nullable=True),
        sa.Column("source_name", sa.Text, nullable=True),
        sa.Column("artifact_id", sa.UUID, sa.ForeignKey("artifact.id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "normalized_observation",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("fact_revision_id", sa.UUID, sa.ForeignKey("fact_revision.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variable_version_id", sa.UUID, sa.ForeignKey("variable_version.id"), nullable=False),
        sa.Column("raw_observation_id", sa.UUID, sa.ForeignKey("raw_observation.id"), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("unit", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "fact_artifact",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("fact_revision_id", sa.UUID, sa.ForeignKey("fact_revision.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.UUID, sa.ForeignKey("artifact.id"), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "fact_revision_link",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("from_revision_id", sa.UUID, sa.ForeignKey("fact_revision.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_revision_id", sa.UUID, sa.ForeignKey("fact_revision.id", ondelete="CASCADE"), nullable=False),
        sa.Column("link_type", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "quality_assessment",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("fact_revision_id", sa.UUID, sa.ForeignKey("fact_revision.id"), nullable=False),
        sa.Column("overall_status", sa.Text, nullable=False),
        sa.Column("summary", sa.JSON, nullable=True),
        sa.Column("results", sa.JSON, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "parameter_staleness",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("parameter_version_id", sa.UUID, sa.ForeignKey("parameter_version.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_revision_id", sa.UUID, sa.ForeignKey("fact_revision.id"), nullable=False),
        sa.Column("review_state", sa.Text, nullable=False, server_default=sa.text("'current'")),
        sa.Column("last_checked_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
