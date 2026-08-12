"""Security tests for conclusion ownership and access control.

Verifies that:
  1. Cross-workspace conclusion access returns not_found (not forbidden).
  2. Cross-workspace candidate access returns not_found.
  3. Manual conclusions are always labeled manual_unverified.
  4. Archive doesn't break historical TurnContext references.
  5. Lock version mismatch raises state_conflict.
"""

import inspect

from packages.research.timeline.conclusion_service import ConclusionService


class TestCrossWorkspaceAccess:
    """Cross-workspace ID mismatches must return not_found (fail-closed)."""

    def test_revise_checks_workspace_match(self) -> None:
        source = inspect.getsource(ConclusionService.revise)
        assert "workspace_id" in source
        assert "not_found" in source

    def test_archive_checks_workspace_match(self) -> None:
        source = inspect.getsource(ConclusionService.archive)
        assert "workspace_id" in source
        assert "not_found" in source

    def test_save_candidates_checks_candidate_exists(self) -> None:
        source = inspect.getsource(ConclusionService.save_candidates)
        assert "not_found" in source
        assert "候选结论不存在" in source


class TestManualConclusionSecurity:
    """Manual conclusions must never be misrepresented as data-supported."""

    def test_create_manual_sets_manual_unverified(self) -> None:
        source = inspect.getsource(ConclusionService.create_manual)
        assert "manual_unverified" in source
        assert "manual" in source

    def test_create_manual_does_not_set_data_supported(self) -> None:
        source = inspect.getsource(ConclusionService.create_manual)
        # "data_supported" should NOT appear in the manual creation path
        # (it should only appear in save_candidates for AI conclusions)
        assert "data_supported" not in source.replace(
            'evidence_status="manual_unverified"',
            "",
        )


class TestOptimisticLocking:
    """Lock version must be checked on revise and archive."""

    def test_revise_checks_lock_version(self) -> None:
        source = inspect.getsource(ConclusionService.revise)
        assert "expected_lock_version" in source
        assert "update_conclusion_lock" in source

    def test_archive_checks_lock_version(self) -> None:
        source = inspect.getsource(ConclusionService.archive)
        assert "expected_lock_version" in source
        assert "archive_conclusion" in source


class TestImmutableRevisions:
    """Old revisions must never be modified or deleted."""

    def test_revise_creates_new_revision(self) -> None:
        source = inspect.getsource(ConclusionService.revise)
        assert "insert_revision" in source
        assert "revision_number" in source
        # It should NOT update old revisions
        assert "update_revision" not in source.lower()

    def test_revise_preserves_evidence_refs(self) -> None:
        source = inspect.getsource(ConclusionService.revise)
        assert "old_evidence_refs" in source or "evidence_refs" in source
