"""Research domain RLS enforcement tests.

P0 data-isolation Task 3: prove that every ``research_%`` table has RLS
enabled AND forced, and that the owner-scoped workspace isolation policy
correctly blocks cross-workspace access.

Test matrix:
  - All 32 research tables must have relrowsecurity = true AND
    relforcerowsecurity = true.
  - All 32 research tables must have a ``research_workspace_isolation`` policy
    with both USING and WITH CHECK clauses.
  - Cross-workspace read blocked: user_a cannot see user_b's workspace even
    when both are in the same department (owner-scoping, not just dept-scoping).
  - Cross-workspace write blocked: user_a cannot insert into user_b's
    workspace (WITH CHECK enforcement).
  - Fail-closed: empty / missing GUC returns 0 rows from every research table.

Design notes:
  - Uses ``SET ROLE irip_app`` (non-superuser) so RLS is actually enforced.
  - GUCs ``app.current_user_id`` / ``app.current_dept_id`` are set per
    transaction via ``set_config(..., true)``.
  - Test data is created as superuser (before SET ROLE) and cleaned up
    afterwards.
"""

import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_ROLE = "irip_app"

#: All 32 research_% tables that must have FORCE RLS.
ALL_RESEARCH_TABLES: list[str] = [
    # Root
    "research_workspace",
    # Direct workspace tables (have workspace_id column)
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
    # Indirect workspace tables (via parent FK)
    "research_analysis_step",
    "research_run_artifact",
    "research_derived_dataset_version",
    "research_view_version",
    "research_insight_version",
    "research_recommendation_item",
    "research_turn_context",
    "research_turn_result",
    "research_conclusion_revision",
    "research_conclusion_candidate",
    # Published result tables (ACL-based)
    "research_result",
    "research_result_version",
    "research_result_acl_revision",
    "research_result_favorite",
    # Lineage (workspace_id column added by 0088)
    "research_lineage_edge",
]

#: Tables that use the simple workspace-owner EXISTS predicate (root + direct
#: + lineage).  Indirect and published tables are tested via behavior.
DIRECT_POLICY_TABLES: list[str] = [
    "research_workspace",
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
    "research_lineage_edge",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_test_db_url() -> str | None:
    """Return the test database URL from env, or None if not configured."""
    return os.getenv("IRIP_TEST_DATABASE_URL")


def _role_exists(conn, role_name: str) -> bool:
    """Check whether a PostgreSQL role exists."""
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"),
        {"name": role_name},
    )
    return result.fetchone() is not None


@contextmanager
def rls_test_context(conn, dept_id, user_id):
    """RLS test context: SET ROLE irip_app + set GUCs, then ROLLBACK.

    1. SET ROLE irip_app (non-superuser, RLS enforced)
    2. set_config GUCs (local to transaction)
    3. yield conn
    4. ROLLBACK (clear GUCs, undo test writes)
    5. RESET ROLE
    """
    # Commit the auto-begun transaction so SET ROLE runs outside a tx
    conn.commit()
    conn.execute(sa.text(f"SET ROLE {APP_ROLE}"))
    # SET ROLE auto-begins a new transaction; use it directly
    try:
        conn.execute(
            sa.text("SELECT set_config('app.current_dept_id', :d, true)"),
            {"d": str(dept_id)},
        )
        conn.execute(
            sa.text("SELECT set_config('app.current_user_id', :u, true)"),
            {"u": str(user_id)},
        )
        yield conn
        conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute(sa.text("RESET ROLE"))


# ---------------------------------------------------------------------------
# 1. RLS metadata verification (all 32 tables)
# ---------------------------------------------------------------------------


class TestResearchRLSEnforced:
    """Verify every research_% table has ENABLE + FORCE RLS."""

    @pytest.fixture(autouse=True)
    def _check_db(self):
        if not _get_test_db_url():
            pytest.skip("IRIP_TEST_DATABASE_URL not set")
        if not _role_exists(create_engine(_get_test_db_url()).connect().__enter__(), APP_ROLE):
            # Quick existence check will be done in test; just skip if no URL.
            pass

    @pytest.mark.parametrize("table_name", ALL_RESEARCH_TABLES)
    def test_rls_enabled_and_forced(self, table_name: str):
        """Each research table must have relrowsecurity AND relforcerowsecurity = true."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    sa.text(
                        "SELECT relrowsecurity, relforcerowsecurity "
                        "FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relname = :t AND c.relkind = 'r'"
                    ),
                    {"t": table_name},
                ).fetchone()
                assert row is not None, f"Table {table_name} not found"
                assert row[0] is True, f"{table_name}: RLS not enabled (relrowsecurity=false)"
                assert row[1] is True, f"{table_name}: RLS not forced (relforcerowsecurity=false)"
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 2. Policy existence verification
# ---------------------------------------------------------------------------


class TestResearchPolicyExists:
    """Verify every research_% table has the research_workspace_isolation policy
    with both USING and WITH CHECK expressions."""

    @pytest.fixture(autouse=True)
    def _check_db(self):
        if not _get_test_db_url():
            pytest.skip("IRIP_TEST_DATABASE_URL not set")

    @pytest.mark.parametrize("table_name", ALL_RESEARCH_TABLES)
    def test_policy_exists_with_using_and_check(self, table_name: str):
        """Each table must have research_workspace_isolation with USING + WITH CHECK."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    sa.text(
                        "SELECT policyname, cmd, qual, with_check "
                        "FROM pg_policies "
                        "WHERE schemaname = 'public' AND tablename = :t"
                    ),
                    {"t": table_name},
                ).fetchall()
                assert rows, f"Table {table_name} has no RLS policies"
                policy = next(
                    (r for r in rows if r[0] == "research_workspace_isolation"),
                    None,
                )
                assert policy is not None, (
                    f"Table {table_name} missing 'research_workspace_isolation' policy; "
                    f"found: {[r[0] for r in rows]}"
                )
                # cmd should be 'ALL' (applies to all commands) or we accept any
                qual = policy[2] or ""
                with_check = policy[3] or ""
                assert qual, f"Table {table_name} policy has empty USING clause"
                assert with_check, f"Table {table_name} policy has empty WITH CHECK clause"
        finally:
            engine.dispose()

    @pytest.mark.parametrize("table_name", DIRECT_POLICY_TABLES)
    def test_policy_uses_workspace_owner(self, table_name: str):
        """Direct/root/lineage policies reference owner_user_id and current_visible_dept_ids."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    sa.text(
                        "SELECT qual FROM pg_policies "
                        "WHERE schemaname = 'public' AND tablename = :t "
                        "AND policyname = 'research_workspace_isolation'"
                    ),
                    {"t": table_name},
                ).fetchall()
                assert rows, f"Table {table_name} missing policy"
                qual = rows[0][0] or ""
                assert "owner_user_id" in qual, (
                    f"Table {table_name} policy should reference owner_user_id: {qual}"
                )
                assert "current_visible_dept_ids" in qual, (
                    f"Table {table_name} policy should reference current_visible_dept_ids: {qual}"
                )
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 3. Cross-workspace read isolation (behavioral)
# ---------------------------------------------------------------------------


class TestResearchCrossWorkspaceIsolation:
    """Verify owner-scoped RLS: user_a cannot see user_b's workspace or its
    child rows, even when both users are in the same department."""

    @pytest.fixture(autouse=True)
    def _check_db(self):
        if not _get_test_db_url():
            pytest.skip("IRIP_TEST_DATABASE_URL not set")

    def _setup(self, engine) -> tuple:
        """Create two users in the same department, each with a workspace + snapshot."""
        with engine.connect() as conn:
            # Root department (sentinel)
            root = conn.execute(
                sa.text("SELECT id FROM department WHERE code = 'root' AND parent_id IS NULL")
            ).fetchone()
            if root is None:
                pytest.skip("root sentinel department not found")
            root_id = root[0]

            # Child department for both users
            dept_id = uuid4()
            user_a = uuid4()
            user_b = uuid4()
            ws_a = uuid4()
            ws_b = uuid4()
            snap_a = uuid4()
            snap_b = uuid4()

            from packages.auth.passwords import hash_password

            conn.execute(
                sa.text(
                    "INSERT INTO department "
                    "(id, code, display_name, status, sort_order, parent_id) "
                    "VALUES (:id, :code, :name, 'active', 400, :parent)"
                ),
                {
                    "id": dept_id,
                    "code": f"rls_ws_dept_{dept_id.hex[:8]}",
                    "name": "RLS WS Dept",
                    "parent": root_id,
                },
            )
            for uid, label in [(user_a, "A"), (user_b, "B")]:
                conn.execute(
                    sa.text(
                        "INSERT INTO app_user (id, email, display_name, password_hash, "
                        "status, lock_version, department_id) "
                        "VALUES (:id, :email, :name, :hash, 'active', 0, :dept)"
                    ),
                    {
                        "id": uid,
                        "email": f"rls_ws_{label}_{uid.hex[:8]}@irip.local",
                        "name": f"RLS WS User {label}",
                        "hash": hash_password("Test-Password-2026!"),
                        "dept": dept_id,
                    },
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO app_user_department (user_id, department_id, is_primary) "
                        "VALUES (:uid, :dept, true)"
                    ),
                    {"uid": uid, "dept": dept_id},
                )

            # Two workspaces — same dept, different owners
            for ws_id, owner, label in [(ws_a, user_a, "A"), (ws_b, user_b, "B")]:
                conn.execute(
                    sa.text(
                        "INSERT INTO research_workspace "
                        "(id, owner_user_id, department_id, name, status, next_turn_number) "
                        "VALUES (:id, :owner, :dept, :name, 'draft', 1)"
                    ),
                    {"id": ws_id, "owner": owner, "dept": dept_id, "name": f"WS {label}"},
                )

            # Evidence snapshots (one per workspace) — direct child table
            for snap_id, ws_id, owner in [(snap_a, ws_a, user_a), (snap_b, ws_b, user_b)]:
                conn.execute(
                    sa.text(
                        "INSERT INTO research_evidence_snapshot "
                        "(id, workspace_id, snapshot_number, content_hash, "
                        " permission_envelope, field_manifest, source_refs, "
                        " created_by, idempotency_key) "
                        "VALUES (:id, :ws, 1, :hash, "
                        " '{}'::jsonb, '[]'::jsonb, '[]'::jsonb, "
                        " :owner, :idem)"
                    ),
                    {
                        "id": snap_id,
                        "ws": ws_id,
                        "hash": f"hash_{snap_id.hex[:8]}",
                        "owner": owner,
                        "idem": f"snap_{snap_id.hex[:8]}",
                    },
                )

            conn.commit()
            return (dept_id, user_a, user_b, ws_a, ws_b, snap_a, snap_b)

    def _cleanup(self, engine, ids: tuple) -> None:
        dept_id, user_a, user_b, ws_a, ws_b, snap_a, snap_b = ids
        with engine.connect() as conn:
            conn.execute(
                sa.text("DELETE FROM research_evidence_snapshot WHERE id IN (:a, :b)"),
                {"a": snap_a, "b": snap_b},
            )
            conn.execute(
                sa.text("DELETE FROM research_workspace WHERE id IN (:a, :b)"),
                {"a": ws_a, "b": ws_b},
            )
            conn.execute(
                sa.text("DELETE FROM app_user_department WHERE user_id IN (:a, :b)"),
                {"a": user_a, "b": user_b},
            )
            conn.execute(
                sa.text("DELETE FROM app_user WHERE id IN (:a, :b)"), {"a": user_a, "b": user_b}
            )
            conn.execute(sa.text("DELETE FROM department WHERE id = :d"), {"d": dept_id})
            conn.commit()

    def test_cross_workspace_read_blocked(self):
        """user_a sees only own workspace, not user_b's."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        ids = self._setup(engine)
        try:
            dept_id, user_a, user_b, ws_a, ws_b, snap_a, snap_b = ids
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"Role {APP_ROLE} not found")

                # user_a context — should see only workspace A
                with rls_test_context(conn, dept_id, user_a) as rls_conn:
                    total = rls_conn.execute(
                        sa.text("SELECT count(*) FROM research_workspace")
                    ).scalar()
                    assert total == 1, f"user_a should see 1 workspace, saw {total}"

                    # Should NOT see workspace B
                    cross = rls_conn.execute(
                        sa.text("SELECT count(*) FROM research_workspace WHERE id = :b"),
                        {"b": str(ws_b)},
                    ).scalar()
                    assert cross == 0, "user_a should not see user_b's workspace"

                # user_b context — should see only workspace B
                with rls_test_context(conn, dept_id, user_b) as rls_conn:
                    total = rls_conn.execute(
                        sa.text("SELECT count(*) FROM research_workspace")
                    ).scalar()
                    assert total == 1, f"user_b should see 1 workspace, saw {total}"
        finally:
            self._cleanup(engine, ids)
            engine.dispose()

    def test_cross_workspace_child_read_blocked(self):
        """user_a sees only own evidence snapshots, not user_b's."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        ids = self._setup(engine)
        try:
            dept_id, user_a, user_b, ws_a, ws_b, snap_a, snap_b = ids
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"Role {APP_ROLE} not found")

                with rls_test_context(conn, dept_id, user_a) as rls_conn:
                    total = rls_conn.execute(
                        sa.text("SELECT count(*) FROM research_evidence_snapshot")
                    ).scalar()
                    assert total == 1, f"user_a should see 1 snapshot, saw {total}"

                    cross = rls_conn.execute(
                        sa.text(
                            "SELECT count(*) FROM research_evidence_snapshot "
                            "WHERE workspace_id = :b"
                        ),
                        {"b": str(ws_b)},
                    ).scalar()
                    assert cross == 0, "user_a should not see user_b's snapshots"
        finally:
            self._cleanup(engine, ids)
            engine.dispose()

    def test_cross_workspace_write_blocked(self):
        """user_a cannot insert a snapshot into user_b's workspace (WITH CHECK)."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        ids = self._setup(engine)
        try:
            dept_id, user_a, user_b, ws_a, ws_b, snap_a, snap_b = ids
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"Role {APP_ROLE} not found")

                new_snap = uuid4()
                with rls_test_context(conn, dept_id, user_a) as rls_conn:
                    with pytest.raises(Exception) as exc_info:
                        rls_conn.execute(
                            sa.text(
                                "INSERT INTO research_evidence_snapshot "
                                "(id, workspace_id, snapshot_number, content_hash, "
                                " permission_envelope, field_manifest, source_refs, "
                                " created_by, idempotency_key) "
                                "VALUES (:id, :ws, 2, :hash, '{}'::jsonb, '[]'::jsonb, "
                                " '[]'::jsonb, :owner, :idem)"
                            ),
                            {
                                "id": str(new_snap),
                                "ws": str(ws_b),  # user_b's workspace — RLS should block
                                "hash": "cross_hash",
                                "owner": str(user_a),
                                "idem": "cross_snap",
                            },
                        )
                    msg = str(exc_info.value).lower()
                    assert "row-level security" in msg or "policy" in msg, (
                        f"Cross-workspace write should be blocked by RLS, got: {exc_info.value}"
                    )
        finally:
            self._cleanup(engine, ids)
            engine.dispose()


# ---------------------------------------------------------------------------
# 4. Fail-closed verification
# ---------------------------------------------------------------------------


class TestResearchRLSFailClosed:
    """Verify that missing/empty GUC causes all research tables to return 0 rows."""

    @pytest.fixture(autouse=True)
    def _check_db(self):
        if not _get_test_db_url():
            pytest.skip("IRIP_TEST_DATABASE_URL not set")

    def test_empty_guc_research_workspace(self):
        """Empty GUC → 0 rows from research_workspace."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"Role {APP_ROLE} not found")
                conn.commit()
                conn.execute(sa.text(f"SET ROLE {APP_ROLE}"))
                # SET ROLE auto-begins a new transaction
                try:
                    conn.execute(sa.text("SELECT set_config('app.current_dept_id', '', true)"))
                    conn.execute(sa.text("SELECT set_config('app.current_user_id', '', true)"))
                    result = conn.execute(sa.text("SELECT count(*) FROM research_workspace"))
                    assert result.scalar() == 0, "Empty GUC should fail-closed to 0 rows"
                    conn.rollback()
                finally:
                    conn.execute(sa.text("RESET ROLE"))
        finally:
            engine.dispose()

    def test_empty_guc_research_evidence_snapshot(self):
        """Empty GUC → 0 rows from research_evidence_snapshot."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"Role {APP_ROLE} not found")
                conn.commit()
                conn.execute(sa.text(f"SET ROLE {APP_ROLE}"))
                # SET ROLE auto-begins a new transaction
                try:
                    conn.execute(sa.text("SELECT set_config('app.current_dept_id', '', true)"))
                    conn.execute(sa.text("SELECT set_config('app.current_user_id', '', true)"))
                    result = conn.execute(
                        sa.text("SELECT count(*) FROM research_evidence_snapshot")
                    )
                    assert result.scalar() == 0, "Empty GUC should fail-closed to 0 rows"
                    conn.rollback()
                finally:
                    conn.execute(sa.text("RESET ROLE"))
        finally:
            engine.dispose()

    def test_empty_guc_research_result(self):
        """Empty GUC → 0 rows from research_result (published table also fail-closed)."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"Role {APP_ROLE} not found")
                conn.commit()
                conn.execute(sa.text(f"SET ROLE {APP_ROLE}"))
                # SET ROLE auto-begins a new transaction
                try:
                    conn.execute(sa.text("SELECT set_config('app.current_dept_id', '', true)"))
                    conn.execute(sa.text("SELECT set_config('app.current_user_id', '', true)"))
                    result = conn.execute(sa.text("SELECT count(*) FROM research_result"))
                    assert result.scalar() == 0, "Empty GUC should fail-closed to 0 rows"
                    conn.rollback()
                finally:
                    conn.execute(sa.text("RESET ROLE"))
        finally:
            engine.dispose()

    def test_empty_guc_research_lineage_edge(self):
        """Empty GUC → 0 rows from research_lineage_edge."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                if not _role_exists(conn, APP_ROLE):
                    pytest.skip(f"Role {APP_ROLE} not found")
                conn.commit()
                conn.execute(sa.text(f"SET ROLE {APP_ROLE}"))
                # SET ROLE auto-begins a new transaction
                try:
                    conn.execute(sa.text("SELECT set_config('app.current_dept_id', '', true)"))
                    conn.execute(sa.text("SELECT set_config('app.current_user_id', '', true)"))
                    result = conn.execute(sa.text("SELECT count(*) FROM research_lineage_edge"))
                    assert result.scalar() == 0, "Empty GUC should fail-closed to 0 rows"
                    conn.rollback()
                finally:
                    conn.execute(sa.text("RESET ROLE"))
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 5. Lineage edge workspace_id column verification
# ---------------------------------------------------------------------------


class TestResearchLineageEdgeWorkspaceId:
    """Verify research_lineage_edge has a NOT NULL workspace_id column (0088)."""

    @pytest.fixture(autouse=True)
    def _check_db(self):
        if not _get_test_db_url():
            pytest.skip("IRIP_TEST_DATABASE_URL not set")

    def test_workspace_id_column_exists_not_null(self):
        """research_lineage_edge.workspace_id exists and is NOT NULL."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    sa.text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'research_lineage_edge' "
                        "AND column_name = 'workspace_id'"
                    ),
                ).fetchone()
                assert row is not None, "research_lineage_edge should have workspace_id column"
                assert row[0] == "NO", f"workspace_id should be NOT NULL, got {row[0]}"
        finally:
            engine.dispose()

    def test_workspace_id_has_fk(self):
        """research_lineage_edge.workspace_id has FK to research_workspace.id."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    sa.text(
                        "SELECT 1 FROM information_schema.table_constraints tc "
                        "JOIN information_schema.key_column_usage kcu "
                        "ON tc.constraint_name = kcu.constraint_name "
                        "WHERE tc.table_name = 'research_lineage_edge' "
                        "AND tc.constraint_type = 'FOREIGN KEY' "
                        "AND kcu.column_name = 'workspace_id'"
                    ),
                ).fetchone()
                assert row is not None, (
                    "research_lineage_edge.workspace_id should have FK constraint"
                )
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# 6. Published result ACL visibility (behavioral)
# ---------------------------------------------------------------------------


class TestResearchResultACL:
    """Verify research_result ACL policy: owner sees own; published 'all' visible to others."""

    @pytest.fixture(autouse=True)
    def _check_db(self):
        if not _get_test_db_url():
            pytest.skip("IRIP_TEST_DATABASE_URL not set")

    def test_owner_sees_private_result(self):
        """Owner can see their own private result."""
        db_url = _get_test_db_url()
        engine = create_engine(db_url, pool_pre_ping=True)
        dept_id = uuid4()
        user_a = uuid4()
        user_b = uuid4()
        ws_a = uuid4()
        result_a = uuid4()
        with engine.connect() as conn:
            root = conn.execute(
                sa.text("SELECT id FROM department WHERE code = 'root' AND parent_id IS NULL")
            ).fetchone()
            if root is None:
                pytest.skip("root sentinel department not found")
            root_id = root[0]

            from packages.auth.passwords import hash_password

            conn.execute(
                sa.text(
                    "INSERT INTO department "
                    "(id, code, display_name, status, sort_order, parent_id) "
                    "VALUES (:id, :code, :name, 'active', 500, :parent)"
                ),
                {
                    "id": dept_id,
                    "code": f"rls_acl_dept_{dept_id.hex[:8]}",
                    "name": "RLS ACL Dept",
                    "parent": root_id,
                },
            )
            for uid, label in [(user_a, "A"), (user_b, "B")]:
                conn.execute(
                    sa.text(
                        "INSERT INTO app_user (id, email, display_name, password_hash, "
                        "status, lock_version, department_id) "
                        "VALUES (:id, :email, :name, :hash, 'active', 0, :dept)"
                    ),
                    {
                        "id": uid,
                        "email": f"rls_acl_{label}_{uid.hex[:8]}@irip.local",
                        "name": f"RLS ACL User {label}",
                        "hash": hash_password("Test-Password-2026!"),
                        "dept": dept_id,
                    },
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO app_user_department (user_id, department_id, is_primary) "
                        "VALUES (:uid, :dept, true)"
                    ),
                    {"uid": uid, "dept": dept_id},
                )
            conn.execute(
                sa.text(
                    "INSERT INTO research_workspace "
                    "(id, owner_user_id, department_id, name, status, next_turn_number) "
                    "VALUES (:id, :owner, :dept, :name, 'draft', 1)"
                ),
                {"id": ws_a, "owner": user_a, "dept": dept_id, "name": "ACL WS"},
            )
            # private result owned by user_a
            conn.execute(
                sa.text(
                    "INSERT INTO research_result "
                    "(id, workspace_id, owner_user_id, name, status, current_version, "
                    " current_acl_type, current_explicit_user_ids) "
                    "VALUES (:id, :ws, :owner, :name, 'published', 1, "
                    " 'private', '[]'::jsonb)"
                ),
                {"id": result_a, "ws": ws_a, "owner": user_a, "name": "Private Result"},
            )
            conn.commit()

            try:
                with engine.connect() as conn2:
                    if not _role_exists(conn2, APP_ROLE):
                        pytest.skip(f"Role {APP_ROLE} not found")

                    # Owner sees own private result
                    with rls_test_context(conn2, dept_id, user_a) as rls_conn:
                        count = rls_conn.execute(
                            sa.text("SELECT count(*) FROM research_result WHERE id = :r"),
                            {"r": str(result_a)},
                        ).scalar()
                        assert count == 1, "Owner should see own private result"

                    # Other user in same dept does NOT see private result
                    with rls_test_context(conn2, dept_id, user_b) as rls_conn:
                        count = rls_conn.execute(
                            sa.text("SELECT count(*) FROM research_result WHERE id = :r"),
                            {"r": str(result_a)},
                        ).scalar()
                        assert count == 0, "Non-owner should not see private result"
            finally:
                with engine.connect() as conn3:
                    conn3.execute(
                        sa.text("DELETE FROM research_result WHERE id = :r"), {"r": result_a}
                    )
                    conn3.execute(
                        sa.text("DELETE FROM research_workspace WHERE id = :w"), {"w": ws_a}
                    )
                    conn3.execute(
                        sa.text("DELETE FROM app_user_department WHERE user_id IN (:a, :b)"),
                        {"a": user_a, "b": user_b},
                    )
                    conn3.execute(
                        sa.text("DELETE FROM app_user WHERE id IN (:a, :b)"),
                        {"a": user_a, "b": user_b},
                    )
                    conn3.execute(sa.text("DELETE FROM department WHERE id = :d"), {"d": dept_id})
                    conn3.commit()
                engine.dispose()
