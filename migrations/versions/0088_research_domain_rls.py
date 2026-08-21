"""0088: Force owner-scoped RLS on all research_% tables.

P0 data-isolation Task 3: enable and FORCE row-level security on every
``research_%`` table so that cross-workspace access is blocked at the
database layer.

Policy model:
  - **Root table** (research_workspace): predicate checks
    ``owner_user_id = current_user AND department_id IN current_visible_dept_ids()``.
  - **Direct tables** (17 tables with ``workspace_id`` column, including
    research_lineage_edge after this migration): predicate uses an EXISTS
    subquery joining to research_workspace on workspace_id.
  - **Indirect tables** (10 tables linked via a parent FK): predicate joins
    through the parent table to research_workspace.
  - **Published result tables** (4 tables): research_result uses an ACL-aware
    read predicate (owner OR published-with-ACL) and owner-only write;
    version / acl_revision / favorite inherit from research_result.

All policies have both USING and WITH CHECK.  All tables use ENABLE + FORCE
ROW LEVEL SECURITY so that even the table owner is subject to the policy.

research_lineage_edge previously had no workspace_id column.  This migration
adds it, backfills from known research namespaces, verifies no unresolved
rows remain, sets NOT NULL + FK, and then applies the workspace-owner policy.

The migration is idempotent: every DROP POLICY IF EXISTS / CREATE POLICY
sequence can be re-run safely.

Revision ID: 0088
Revises: 0087
Create Date: 2026-08-21
"""

from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Predicate fragments (reused across all policies)
# ---------------------------------------------------------------------------

#: Current user UUID from GUC, fail-closed to NULL when missing/empty.
_UID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"

#: Visible department set from GUC (used as ``... IN (SELECT <_DEPT>)``).
_DEPT = "current_visible_dept_ids()"

#: Workspace owner check (used inside EXISTS subqueries where ``w`` is the
#: research_workspace alias).
_WS_OWNER = f"w.owner_user_id = {_UID} AND w.department_id IN (SELECT {_DEPT})"


def _direct_predicate(table: str) -> str:
    """Predicate for tables that have a ``workspace_id`` column.

    The row is visible/writable iff the referenced workspace is owned by the
    current user and belongs to a visible department.
    """
    return (
        f"EXISTS (SELECT 1 FROM research_workspace w "
        f"WHERE w.id = {table}.workspace_id AND {_WS_OWNER})"
    )


def _indirect_predicate(table: str, parent: str, parent_fk: str, fk_col: str) -> str:
    """Predicate for tables linked to workspace via a parent FK.

    Joins ``{table}.{fk_col} -> {parent}.{parent_fk} -> research_workspace``.
    """
    return (
        f"EXISTS (SELECT 1 FROM {parent} p "
        f"JOIN research_workspace w ON w.id = p.workspace_id "
        f"WHERE p.{parent_fk} = {table}.{fk_col} AND {_WS_OWNER})"
    )


def _result_acl_read(ref: str) -> str:
    """ACL-aware read predicate for research_result, parameterised by table ref.

    Visible when:
      - Owner is the current user, OR
      - status = 'published' AND
        - acl_type = 'all' (visible to everyone), OR
        - acl_type = 'tree' AND workspace dept is visible, OR
        - acl_type = 'explicit' AND current user is in the explicit list.
    """
    return (
        f"({ref}.owner_user_id = {_UID} "
        f"OR ("
        f"  {ref}.status = 'published' "
        f"  AND ("
        f"    {ref}.current_acl_type = 'all' "
        f"    OR ("
        f"      {ref}.current_acl_type = 'tree' "
        f"      AND EXISTS (SELECT 1 FROM research_workspace w "
        f"                 WHERE w.id = {ref}.workspace_id "
        f"                 AND w.department_id IN (SELECT {_DEPT}))"
        f"    ) "
        f"    OR ("
        f"      {ref}.current_acl_type = 'explicit' "
        f"      AND EXISTS (SELECT 1 FROM jsonb_array_elements_text("
        f"                 {ref}.current_explicit_user_ids) AS uid "
        f"                 WHERE uid = NULLIF("
        f"                   current_setting('app.current_user_id', true), ''))"
        f"    )"
        f"  )"
        f"))"
    )


def _enable_policy(table: str, using: str, with_check: str | None = None) -> None:
    """Enable + FORCE RLS and (re)create the research_workspace_isolation policy.

    Idempotent: DROP IF EXISTS before CREATE.
    """
    wc = with_check if with_check is not None else using
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS research_workspace_isolation ON {table}")
    op.execute(
        f"CREATE POLICY research_workspace_isolation ON {table} "
        f"USING ({using}) WITH CHECK ({wc})"
    )


# ---------------------------------------------------------------------------
# Table groups
# ---------------------------------------------------------------------------

#: Direct workspace tables (have workspace_id column, excluding research_result
#: which uses ACL and research_lineage_edge which gets workspace_id in Step 1).
DIRECT_TABLES: list[str] = [
    "research_ai_conversation",
    "research_analysis_plan_version",
    "research_analysis_run",
    "research_candidate_extraction_job",
    "research_conclusion",
    "research_conclusion_bar_item",
    "research_derived_dataset",
    "research_evidence_snapshot",
    "research_insight",
    "research_insight_candidate",
    "research_knowledge_reference",
    "research_memory_document",
    "research_recommendation_batch",
    "research_turn",
    "research_view",
    "research_workspace_evidence_ref",
]

#: Indirect workspace tables: (table, parent_table, parent_fk_column, fk_column).
INDIRECT_TABLES: list[tuple[str, str, str, str]] = [
    ("research_analysis_step", "research_analysis_run", "id", "run_id"),
    ("research_run_artifact", "research_analysis_run", "id", "run_id"),
    ("research_derived_dataset_version", "research_derived_dataset", "id", "dataset_id"),
    ("research_view_version", "research_view", "id", "view_id"),
    ("research_insight_version", "research_insight", "id", "insight_id"),
    ("research_recommendation_item", "research_recommendation_batch", "id", "batch_id"),
    ("research_turn_context", "research_turn", "id", "turn_id"),
    ("research_turn_result", "research_turn", "id", "turn_id"),
    ("research_conclusion_revision", "research_conclusion", "id", "conclusion_id"),
    ("research_conclusion_candidate", "research_turn", "id", "turn_id"),
]

#: All 32 research tables (for downgrade).
ALL_TABLES: list[str] = (
    ["research_workspace"]
    + DIRECT_TABLES
    + [t[0] for t in INDIRECT_TABLES]
    + [
        "research_result",
        "research_result_version",
        "research_result_acl_revision",
        "research_result_favorite",
    ]
    + ["research_lineage_edge"]
)

#: Research namespaces that can be resolved to a workspace_id, used for
#: backfilling research_lineage_edge.workspace_id.  Each entry maps a
#: namespace to a SQL subquery that returns workspace_id for a given object
#: id (bound as ``e.source_id`` or ``e.target_id``).
_NAMESPACE_RESOLUTIONS: list[tuple[str, str]] = [
    ("research:workspace", "e.{side}"),
    ("research:evidence_snapshot", "(SELECT workspace_id FROM research_evidence_snapshot WHERE id = e.{side})"),
    ("research:analysis_run", "(SELECT workspace_id FROM research_analysis_run WHERE id = e.{side})"),
    ("research:analysis_step", "(SELECT r.workspace_id FROM research_analysis_step s JOIN research_analysis_run r ON r.id = s.run_id WHERE s.id = e.{side})"),
    ("research:derived_dataset", "(SELECT workspace_id FROM research_derived_dataset WHERE id = e.{side})"),
    ("research:derived_dataset_version", "(SELECT d.workspace_id FROM research_derived_dataset_version v JOIN research_derived_dataset d ON d.id = v.dataset_id WHERE v.id = e.{side})"),
    ("research:dataset_version", "(SELECT d.workspace_id FROM research_derived_dataset_version v JOIN research_derived_dataset d ON d.id = v.dataset_id WHERE v.id = e.{side})"),
    ("research:view", "(SELECT workspace_id FROM research_view WHERE id = e.{side})"),
    ("research:view_version", "(SELECT vw.workspace_id FROM research_view_version vv JOIN research_view vw ON vw.id = vv.view_id WHERE vv.id = e.{side})"),
    ("research:insight", "(SELECT workspace_id FROM research_insight WHERE id = e.{side})"),
    ("research:insight_version", "(SELECT i.workspace_id FROM research_insight_version iv JOIN research_insight i ON i.id = iv.insight_id WHERE iv.id = e.{side})"),
    ("research:knowledge_reference", "(SELECT workspace_id FROM research_knowledge_reference WHERE id = e.{side})"),
    ("research:result_version", "(SELECT r.workspace_id FROM research_result_version rv JOIN research_result r ON r.id = rv.result_id WHERE rv.id = e.{side})"),
]


def _build_backfill_sql() -> str:
    """Build the comprehensive backfill UPDATE for research_lineage_edge."""
    source_cases = " ".join(
        f"WHEN e.source_namespace = '{ns}' THEN {sql.format(side='source_id')}"
        for ns, sql in _NAMESPACE_RESOLUTIONS
    )
    target_cases = " ".join(
        f"WHEN e.target_namespace = '{ns}' THEN {sql.format(side='target_id')}"
        for ns, sql in _NAMESPACE_RESOLUTIONS
    )
    return (
        "UPDATE research_lineage_edge e "
        "SET workspace_id = COALESCE("
        f"  CASE {source_cases} END,"
        f"  CASE {target_cases} END"
        ") "
        "WHERE e.workspace_id IS NULL"
    )


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Enable and FORCE owner-scoped RLS on all research_% tables."""

    # ====================================================================
    # Step 1: research_lineage_edge — add workspace_id column
    # ====================================================================

    # 1a. Add nullable column (no default).
    op.execute(
        "ALTER TABLE research_lineage_edge ADD COLUMN IF NOT EXISTS workspace_id uuid"
    )

    # 1b. Deterministic backfill from known research namespaces.
    op.execute(_build_backfill_sql())

    # 1c. Verify no unresolved rows remain.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM research_lineage_edge WHERE workspace_id IS NULL) THEN
                RAISE EXCEPTION '0088 backfill failed: research_lineage_edge has rows '
                    'with unresolved workspace_id. IDs: %',
                    (SELECT string_agg(id::text, ', ')
                     FROM research_lineage_edge WHERE workspace_id IS NULL);
            END IF;
        END $$;
        """
    )

    # 1d. Set NOT NULL.
    op.execute(
        "ALTER TABLE research_lineage_edge ALTER COLUMN workspace_id SET NOT NULL"
    )

    # 1e. Add FK constraint + index.
    op.execute(
        "ALTER TABLE research_lineage_edge "
        "ADD CONSTRAINT fk_lineage_edge_workspace_id "
        "FOREIGN KEY (workspace_id) REFERENCES research_workspace(id) ON DELETE CASCADE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lineage_edge_workspace_id "
        "ON research_lineage_edge (workspace_id)"
    )

    # ====================================================================
    # Step 2: RLS on root table — research_workspace
    # ====================================================================
    ws_pred = f"owner_user_id = {_UID} AND department_id IN (SELECT {_DEPT})"
    _enable_policy("research_workspace", ws_pred)

    # ====================================================================
    # Step 3: RLS on direct workspace tables (16 + lineage = 17)
    # ====================================================================
    for table in DIRECT_TABLES:
        _enable_policy(table, _direct_predicate(table))

    # research_lineage_edge now has workspace_id — treat as direct table.
    _enable_policy("research_lineage_edge", _direct_predicate("research_lineage_edge"))

    # ====================================================================
    # Step 4: RLS on indirect workspace tables (10)
    # ====================================================================
    for table, parent, parent_fk, fk_col in INDIRECT_TABLES:
        _enable_policy(table, _indirect_predicate(table, parent, parent_fk, fk_col))

    # ====================================================================
    # Step 5: RLS on published result tables (4)
    # ====================================================================

    # research_result: read = owner OR published+ACL, write = owner only.
    _enable_policy(
        "research_result",
        _result_acl_read("research_result"),
        f"owner_user_id = {_UID}",
    )

    # research_result_version: read inherits result visibility, write = owner.
    _enable_policy(
        "research_result_version",
        f"EXISTS (SELECT 1 FROM research_result r "
        f"WHERE r.id = research_result_version.result_id "
        f"AND {_result_acl_read('r')})",
        f"EXISTS (SELECT 1 FROM research_result r "
        f"WHERE r.id = research_result_version.result_id "
        f"AND r.owner_user_id = {_UID})",
    )

    # research_result_acl_revision: owner-only (read + write).
    _enable_policy(
        "research_result_acl_revision",
        f"EXISTS (SELECT 1 FROM research_result r "
        f"WHERE r.id = research_result_acl_revision.result_id "
        f"AND r.owner_user_id = {_UID})",
    )

    # research_result_favorite: user owns the favorite AND result is visible.
    _enable_policy(
        "research_result_favorite",
        f"user_id = {_UID} "
        f"AND EXISTS (SELECT 1 FROM research_result r "
        f"WHERE r.id = research_result_favorite.result_id "
        f"AND {_result_acl_read('r')})",
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Remove all research RLS policies and the lineage workspace_id column."""

    # Drop policies and disable RLS on all 32 tables.
    for table in ALL_TABLES:
        op.execute(f"DROP POLICY IF EXISTS research_workspace_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Drop lineage workspace_id column.
    op.execute(
        "ALTER TABLE research_lineage_edge "
        "DROP CONSTRAINT IF EXISTS fk_lineage_edge_workspace_id"
    )
    op.execute("DROP INDEX IF EXISTS ix_lineage_edge_workspace_id")
    op.execute(
        "ALTER TABLE research_lineage_edge DROP COLUMN IF EXISTS workspace_id"
    )
