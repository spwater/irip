"""0090: Fail-closed public Result reads (require non-null user GUC).

Remediation B1: 0088 introduced an ACL-aware RLS predicate for
``research_result`` / ``research_result_version`` where a published result
with ``current_acl_type = 'all'`` was visible to *any* session — including
sessions where the ``app.current_user_id`` GUC is missing/empty (i.e. no
valid authenticated context).  The ``all`` branch did not reference the user
GUC at all, so it leaked public results even when the tenant/user context was
absent, violating the plan's §6 risk #2 requirement ("缺失 GUC 时全部
fail-closed，含公开 Result").

This migration strengthens the read predicate by gating the entire
"published + ACL" sub-branch on ``_UID IS NOT NULL`` so that a public result
is only visible when a valid user context exists.  Missing/empty GUC now
evaluates the ``owner`` branch (NULL, false) and the published branch
(gated on non-null user, false) → empty result set → fail-closed.

The policy is re-created idempotently (DROP POLICY IF EXISTS + CREATE POLICY)
for ``research_result`` and ``research_result_version``, mirroring the
predicate shape from 0088 so the two stay consistent.
``research_result_favorite`` already requires ``user_id = current_user`` in
its USING clause, so its ``all`` reference is already fail-closed and needs
no change.  ``research_result_acl_revision`` is owner-only and unaffected.

Revision ID: 0090
Revises: 0089
Create Date: 2026-08-22
"""

from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None

#: Current user UUID from GUC, fail-closed to NULL when missing/empty.
_UID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"

#: Visible department set from GUC.
_DEPT = "current_visible_dept_ids()"


def _result_acl_read(ref: str) -> str:
    """Strengthened ACL-aware read predicate (gated on non-null user GUC).

    Visible when:
      - Owner is the current user, OR
      - the current user GUC is non-null AND status = 'published' AND
        - acl_type = 'all', OR
        - acl_type = 'tree' AND workspace dept is visible, OR
        - acl_type = 'explicit' AND current user is in the explicit list.

    The added ``_UID IS NOT NULL`` guard makes published/public reads
    fail-closed when the user context is absent.
    """
    return (
        f"({ref}.owner_user_id = {_UID} "
        f"OR ("
        f"  {_UID} IS NOT NULL "
        f"  AND {ref}.status = 'published' "
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


def _recreate_policy(table: str, using: str, with_check: str) -> None:
    """Idempotently recreate the research_workspace_isolation policy."""
    op.execute(f"DROP POLICY IF EXISTS research_workspace_isolation ON {table}")
    op.execute(
        f"CREATE POLICY research_workspace_isolation ON {table} "
        f"USING ({using}) WITH CHECK ({with_check})"
    )


def upgrade() -> None:
    """Strengthen research_result / research_result_version public-read RLS."""
    # research_result: read = owner OR (non-null user AND published + ACL),
    # write = owner only.
    _recreate_policy(
        "research_result",
        _result_acl_read("research_result"),
        f"owner_user_id = {_UID}",
    )

    # research_result_version: read inherits the strengthened result visibility,
    # write = owner only.
    _recreate_policy(
        "research_result_version",
        f"EXISTS (SELECT 1 FROM research_result r "
        f"WHERE r.id = research_result_version.result_id "
        f"AND {_result_acl_read('r')})",
        f"EXISTS (SELECT 1 FROM research_result r "
        f"WHERE r.id = research_result_version.result_id "
        f"AND r.owner_user_id = {_UID})",
    )


def downgrade() -> None:
    """Revert to the 0088 predicate (public reads visible without user GUC).

    Intentionally restores the pre-remediation predicate by dropping the
    strengthened policies; the 0088 predicate can be re-applied by running its
    migration.  Kept as a best-effort reverse for local rollback.
    """
    # Drop strengthened policies; re-application of 0088's predicate is left to
    # the operator (downgrade of a security hardening is inherently dangerous).
    op.execute("DROP POLICY IF EXISTS research_workspace_isolation ON research_result")
    op.execute("DROP POLICY IF EXISTS research_workspace_isolation ON research_result_version")
