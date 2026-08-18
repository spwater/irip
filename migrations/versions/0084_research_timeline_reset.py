"""0084: Research Workspace timeline reset and schema

One-time destructive reset of old Research Workspace business data followed
by creation of the new timeline domain tables.

**This migration is not reversible.** Deleted business data cannot be restored
by downgrade(). Only schema changes are rolled back.

Delete order follows FK dependencies (children first):
  research_knowledge_reference
  research_result_favorite
  research_result_acl_revision
  research_lineage_edge
  research_result_version
  research_result
  research_insight_candidate
  research_insight_version
  research_insight
  research_view_version
  research_view
  research_derived_dataset_version
  research_derived_dataset
  research_ai_conversation
  research_memory_document
  research_run_artifact
  research_analysis_step
  research_analysis_run
  research_analysis_plan_version
  research_evidence_snapshot
  research_workspace_evidence_ref
  research_question_version
  research_workspace

New tables (9):
  research_recommendation_batch
  research_recommendation_item
  research_turn
  research_turn_context
  research_turn_result
  research_candidate_extraction_job
  research_conclusion_candidate
  research_conclusion
  research_conclusion_revision

Modified existing tables:
  research_workspace: drop current_question_version/forked_from_id,
    add latest_snapshot_id (nullable FK, ON DELETE SET NULL DEFERRABLE),
    add next_turn_number (NOT NULL default 1)
  research_evidence_snapshot: add idempotency_key (NOT NULL),
    add UNIQUE(workspace_id, idempotency_key)
  research_analysis_plan_version: add turn_id (NOT NULL FK),
    change unique index from workspace-level to (turn_id, version_number)
  research_analysis_run: add turn_id (NOT NULL FK),
    add attempt_number (NOT NULL default 1),
    add UNIQUE(turn_id, attempt_number),
    keep active Run partial unique index

Revision ID: 0084
Revises: 0083
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


# ============================================================
# Tables to delete (children-first FK order)
# ============================================================

_DELETE_TABLES: list[str] = [
    "research_knowledge_reference",
    "research_result_favorite",
    "research_result_acl_revision",
    "research_lineage_edge",
    "research_result_version",
    "research_result",
    "research_insight_candidate",
    "research_insight_version",
    "research_insight",
    "research_view_version",
    "research_view",
    "research_derived_dataset_version",
    "research_derived_dataset",
    "research_ai_conversation",
    "research_memory_document",
    "research_run_artifact",
    "research_analysis_step",
    "research_analysis_run",
    "research_analysis_plan_version",
    "research_evidence_snapshot",
    "research_workspace_evidence_ref",
    "research_question_version",
    "research_workspace",
]

# Tables that must NOT be deleted (retention guard)
_RETAIN_TABLES: list[str] = [
    "fact",
    "app_user",
    "department",
    "audit_event",
    "ai_config",
    "job",
    "outbox_event",
]


def upgrade() -> None:
    """Destructive reset + new timeline schema."""

    # ============================================================
    # 1. Delete old Research business data (children first)
    # ============================================================
    for table in _DELETE_TABLES:
        op.execute(f"DELETE FROM {table}")

    # Flush any pending trigger events from DEFERRABLE constraints
    # before ALTER TABLE (0083 made research FKs DEFERRABLE INITIALLY DEFERRED)
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")

    # ============================================================
    # 2. Drop old question version table
    # ============================================================
    op.drop_table("research_question_version")

    # ============================================================
    # 3. Modify research_workspace
    # ============================================================
    op.drop_column("research_workspace", "current_question_version")
    op.drop_column("research_workspace", "forked_from_id")
    op.add_column(
        "research_workspace",
        sa.Column("latest_snapshot_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "research_workspace",
        sa.Column("next_turn_number", sa.Integer, nullable=False, server_default=sa.text("1")),
    )
    op.create_foreign_key(
        "research_workspace_latest_snapshot_fkey",
        "research_workspace",
        "research_evidence_snapshot",
        ["latest_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )

    # ============================================================
    # 4. Modify research_evidence_snapshot: add idempotency_key
    # ============================================================
    op.add_column(
        "research_evidence_snapshot",
        sa.Column("idempotency_key", sa.Text, nullable=False, server_default=sa.text("''")),
    )
    op.create_unique_constraint(
        "uq_research_evidence_snapshot_idempotency",
        "research_evidence_snapshot",
        ["workspace_id", "idempotency_key"],
    )

    # ============================================================
    # 5. Modify research_analysis_plan_version: add turn_id
    # ============================================================
    # Drop old workspace-level unique index (if exists — name may vary)
    op.execute(
        "DROP INDEX IF EXISTS ix_research_analysis_plan_version_workspace_version"
    )
    op.add_column(
        "research_analysis_plan_version",
        sa.Column(
            "turn_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("'00000000-0000-0000-0000-000000000000'"),
        ),
    )
    # Create turn-level unique index
    op.create_unique_constraint(
        "uq_plan_version_turn_number",
        "research_analysis_plan_version",
        ["turn_id", "version_number"],
    )

    # ============================================================
    # 6. Modify research_analysis_run: add turn_id + attempt_number
    # ============================================================
    op.add_column(
        "research_analysis_run",
        sa.Column(
            "turn_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("'00000000-0000-0000-0000-000000000000'"),
        ),
    )
    op.add_column(
        "research_analysis_run",
        sa.Column("attempt_number", sa.Integer, nullable=False, server_default=sa.text("1")),
    )
    op.create_unique_constraint(
        "uq_run_turn_attempt",
        "research_analysis_run",
        ["turn_id", "attempt_number"],
    )
    # Active Run partial unique index now scoped to turn_id
    # (Keep existing workspace-level index for backward compat during transition)
    # op.drop_index("ix_research_analysis_run_active", table_name="research_analysis_run")
    # op.create_index(
    #     "ix_research_analysis_run_active",
    #     "research_analysis_run",
    #     ["workspace_id"],
    #     unique=True,
    #     postgresql_where=sa.text("status IN ('queued', 'planning', 'running')"),
    # )

    # ============================================================
    # 7. Create new timeline tables
    # ============================================================

    # --- research_recommendation_batch ---
    op.create_table(
        "research_recommendation_batch",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_evidence_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.Text, nullable=False),  # initial | followup
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'queued'")),
        sa.Column("prompt_template_version", sa.Text, nullable=False),
        sa.Column("output_schema_version", sa.Text, nullable=False),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("error_code", sa.Text, nullable=True),
        sa.Column("attempt", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_recommendation_batch_idempotency"
        ),
    )
    op.create_index(
        "ix_recommendation_batch_workspace", "research_recommendation_batch", ["workspace_id"]
    )
    op.create_index(
        "ix_recommendation_batch_snapshot", "research_recommendation_batch", ["snapshot_id"]
    )

    # --- research_recommendation_item ---
    op.create_table(
        "research_recommendation_item",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_recommendation_batch.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("evidence_hints", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("batch_id", "position", name="uq_recommendation_item_batch_position"),
    )

    # --- research_turn ---
    op.create_table(
        "research_turn",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_number", sa.Integer, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),  # analysis | synthesis
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'question_draft'")),
        sa.Column("question_text_snapshot", sa.Text, nullable=False),
        sa.Column(
            "question_origin", sa.Text, nullable=False
        ),  # initial_ai | followup_ai | ai_edited | manual | synthesis
        sa.Column("recommendation_item_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "evidence_snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_evidence_snapshot.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("prompt_template_version", sa.Text, nullable=True),
        sa.Column("output_schema_version", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.Text, nullable=False),
        sa.Column("lock_version", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("workspace_id", "turn_number", name="uq_turn_workspace_number"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_turn_idempotency"),
    )
    op.create_index("ix_research_turn_workspace", "research_turn", ["workspace_id"])
    op.create_index("ix_research_turn_status", "research_turn", ["status"])

    # --- research_turn_context ---
    op.create_table(
        "research_turn_context",
        sa.Column(
            "turn_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_turn.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "conclusion_revision_id",
            UUID(as_uuid=True),
            primary_key=True,
        ),  # FK added later after research_conclusion_revision exists
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # --- research_turn_result ---
    op.create_table(
        "research_turn_result",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "turn_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_turn.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_analysis_run.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("result_kind", sa.Text, nullable=False),  # analysis | synthesis | partial
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("structured_output", JSONB, nullable=True),
        sa.Column("method_summary", sa.Text, nullable=True),
        sa.Column("evidence_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("limitations", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # --- research_candidate_extraction_job ---
    op.create_table(
        "research_candidate_extraction_job",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "turn_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_turn.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_analysis_run.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'queued'")),
        sa.Column("attempt", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_code", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_extraction_job_status", "research_candidate_extraction_job", ["status"])

    # --- research_conclusion_candidate ---
    op.create_table(
        "research_conclusion_candidate",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "extraction_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_candidate_extraction_job.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "turn_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_turn.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("statement", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=True),
        sa.Column("evidence_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("method_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("confidence_level", sa.Text, nullable=True),
        sa.Column("limitations", sa.Text, nullable=True),
        sa.Column(
            "status", sa.Text, nullable=False, server_default=sa.text("'pending'")
        ),  # pending | saved | rejected
        sa.Column("saved_conclusion_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("extraction_id", "ordinal", name="uq_candidate_extraction_ordinal"),
    )
    op.create_index("ix_conclusion_candidate_turn", "research_conclusion_candidate", ["turn_id"])
    op.create_index("ix_conclusion_candidate_status", "research_conclusion_candidate", ["status"])

    # --- research_conclusion ---
    op.create_table(
        "research_conclusion",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("current_revision_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "source_turn_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "research_turn.id", ondelete="SET NULL", deferrable=True, initially="DEFERRED"
            ),
            nullable=True,
        ),
        sa.Column(
            "source_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "research_analysis_run.id",
                ondelete="SET NULL",
                deferrable=True,
                initially="DEFERRED",
            ),
            nullable=True,
        ),
        sa.Column("source_candidate_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.Text, nullable=False),  # ai_original | ai_edited | manual
        sa.Column("evidence_status", sa.Text, nullable=False),  # data_supported | manual_unverified
        sa.Column(
            "status", sa.Text, nullable=False, server_default=sa.text("'active'")
        ),  # active | archived
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("lock_version", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_conclusion_workspace", "research_conclusion", ["workspace_id"])
    op.create_index("ix_conclusion_status", "research_conclusion", ["status"])
    # FK from current_revision_id to research_conclusion_revision.id is added
    # after the revision table is created (circular dependency).

    # --- research_conclusion_revision ---
    op.create_table(
        "research_conclusion_revision",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conclusion_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_conclusion.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer, nullable=False),
        sa.Column("statement", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=True),
        sa.Column("evidence_refs", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("limitations", sa.Text, nullable=True),
        sa.Column("editor", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "conclusion_id", "revision_number", name="uq_conclusion_revision_number"
        ),
    )

    # Now add FK from research_conclusion.current_revision_id to research_conclusion_revision.id
    op.create_foreign_key(
        "research_conclusion_current_revision_fkey",
        "research_conclusion",
        "research_conclusion_revision",
        ["current_revision_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )

    # Add FK from research_turn_context.conclusion_revision_id (deferred to here
    # because research_conclusion_revision is created after research_turn_context)
    op.create_foreign_key(
        "research_turn_context_conclusion_revision_fkey",
        "research_turn_context",
        "research_conclusion_revision",
        ["conclusion_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # ============================================================
    # 8. Keep research_insight_candidate table empty for compilation
    # ============================================================
    # (Already deleted data above; table structure retained for backward compat)

    # ============================================================
    # 9. Grant permissions to irip_app on new tables
    # ============================================================
    new_tables = [
        "research_recommendation_batch",
        "research_recommendation_item",
        "research_turn",
        "research_turn_context",
        "research_turn_result",
        "research_candidate_extraction_job",
        "research_conclusion_candidate",
        "research_conclusion",
        "research_conclusion_revision",
    ]
    for table in new_tables:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO irip_app")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO irip_runtime")


def downgrade() -> None:
    """Rollback schema only. Deleted business data is NOT restored.

    This downgrade reverses structural changes but cannot recover the
    old Research Workspace business data that was deleted in upgrade().
    The data loss is permanent and intentional per the design document.
    """

    # Drop new tables (reverse order)
    op.drop_constraint(
        "research_conclusion_current_revision_fkey", "research_conclusion", type_="foreignkey"
    )
    # 关键修复：research_turn_context.conclusion_revision_id 有 FK 指向
    # research_conclusion_revision（upgrade 第 537 行添加），必须先删该 FK
    # 否则 drop research_conclusion_revision 会触发 DependentObjectsStillExist。
    op.drop_constraint(
        "research_turn_context_conclusion_revision_fkey",
        "research_turn_context",
        type_="foreignkey",
    )
    op.drop_table("research_conclusion_revision")
    op.drop_table("research_conclusion")
    op.drop_table("research_conclusion_candidate")
    op.drop_table("research_candidate_extraction_job")
    op.drop_table("research_turn_result")
    op.drop_table("research_turn_context")
    op.drop_table("research_turn")
    op.drop_table("research_recommendation_item")
    op.drop_table("research_recommendation_batch")

    # Revert research_analysis_run changes
    op.drop_constraint("uq_run_turn_attempt", "research_analysis_run", type_="unique")
    op.drop_column("research_analysis_run", "attempt_number")
    op.drop_column("research_analysis_run", "turn_id")

    # Revert research_analysis_plan_version changes
    op.drop_constraint(
        "uq_plan_version_turn_number", "research_analysis_plan_version", type_="unique"
    )
    op.drop_column("research_analysis_plan_version", "turn_id")
    # Recreate old workspace-level unique index
    # 注意：0083 及之前此对象是纯 INDEX（非 constraint，见升级前的 pg_indexes），
    # 故用 create_index 而非 create_unique_constraint。否则 rebuild 成 constraint 后，
    # 再次 upgrade 的 DROP INDEX 会报 DependentObjectsStillExist（constraint 依赖同名 index）。
    op.create_index(
        "ix_research_analysis_plan_version_workspace_version",
        "research_analysis_plan_version",
        ["workspace_id", "version_number"],
        unique=True,
    )

    # Revert research_evidence_snapshot changes
    op.drop_constraint(
        "uq_research_evidence_snapshot_idempotency", "research_evidence_snapshot", type_="unique"
    )
    op.drop_column("research_evidence_snapshot", "idempotency_key")

    # Revert research_workspace changes
    op.drop_constraint(
        "research_workspace_latest_snapshot_fkey", "research_workspace", type_="foreignkey"
    )
    op.drop_column("research_workspace", "next_turn_number")
    op.drop_column("research_workspace", "latest_snapshot_id")
    op.add_column(
        "research_workspace", sa.Column("forked_from_id", UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "research_workspace",
        sa.Column(
            "current_question_version", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
    )

    # Recreate research_question_version table
    op.create_table(
        "research_question_version",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("sub_questions", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False),
    )
