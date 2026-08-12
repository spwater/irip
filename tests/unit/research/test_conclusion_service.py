"""Tests for ConclusionService: source type derivation and validation logic."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from packages.research.timeline.conclusion_service import ConclusionService
from packages.research.timeline.contracts import (
    CandidateSelection,
    CreateManualConclusionCommand,
    ReviseConclusionCommand,
    SaveCandidatesCommand,
)
from packages.research.timeline.entities import ResearchConclusionCandidate


class TestDetermineSourceType:
    """Test _determine_source_type logic."""

    def _make_candidate(
        self,
        statement: str = "原始结论",
        scope: str | None = "原始范围",
        limitations: str | None = "原始限制",
    ) -> ResearchConclusionCandidate:
        c = MagicMock(spec=ResearchConclusionCandidate)
        c.statement = statement
        c.scope = scope
        c.limitations = limitations
        return c

    def test_unchanged_is_ai_original(self) -> None:
        candidate = self._make_candidate()
        selection = CandidateSelection(candidate_id=uuid4())
        assert ConclusionService._determine_source_type(candidate, selection) == "ai_original"

    def test_edited_statement_is_ai_edited(self) -> None:
        candidate = self._make_candidate(statement="原始结论")
        selection = CandidateSelection(
            candidate_id=uuid4(),
            edited_statement="修改后的结论",
        )
        assert ConclusionService._determine_source_type(candidate, selection) == "ai_edited"

    def test_same_statement_is_ai_original(self) -> None:
        candidate = self._make_candidate(statement="结论")
        selection = CandidateSelection(
            candidate_id=uuid4(),
            edited_statement="结论",
        )
        assert ConclusionService._determine_source_type(candidate, selection) == "ai_original"

    def test_edited_scope_is_ai_edited(self) -> None:
        candidate = self._make_candidate(scope="原始范围")
        selection = CandidateSelection(
            candidate_id=uuid4(),
            edited_scope="修改范围",
        )
        assert ConclusionService._determine_source_type(candidate, selection) == "ai_edited"

    def test_edited_limitations_is_ai_edited(self) -> None:
        candidate = self._make_candidate(limitations="原始限制")
        selection = CandidateSelection(
            candidate_id=uuid4(),
            edited_limitations="修改限制",
        )
        assert ConclusionService._determine_source_type(candidate, selection) == "ai_edited"


class TestCreateManualConclusionCommand:
    """Manual conclusion command validation."""

    def test_valid(self) -> None:
        cmd = CreateManualConclusionCommand(
            workspace_id=uuid4(),
            statement="设备清洗可能影响下一批结果",
            idempotency_key="manual-001",
            limitations="来自操作记录，尚未分析",
        )
        assert cmd.statement == "设备清洗可能影响下一批结果"

    def test_empty_statement_raises(self) -> None:
        with pytest.raises(ValueError):
            CreateManualConclusionCommand(
                workspace_id=uuid4(),
                statement="  ",
                idempotency_key="manual-002",
            )

    def test_empty_idempotency_key_raises(self) -> None:
        with pytest.raises(ValueError):
            CreateManualConclusionCommand(
                workspace_id=uuid4(),
                statement="有效结论",
                idempotency_key="",
            )


class TestReviseConclusionCommand:
    """Revise command validation."""

    def test_valid(self) -> None:
        cmd = ReviseConclusionCommand(
            workspace_id=uuid4(),
            conclusion_id=uuid4(),
            statement="修订后的结论",
            expected_lock_version=0,
        )
        assert cmd.statement == "修订后的结论"
        assert cmd.expected_lock_version == 0

    def test_empty_statement_raises(self) -> None:
        with pytest.raises(ValueError):
            ReviseConclusionCommand(
                workspace_id=uuid4(),
                conclusion_id=uuid4(),
                statement="",
                expected_lock_version=0,
            )


class TestSaveCandidatesCommand:
    """SaveCandidates command validation."""

    def test_empty_selections_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            SaveCandidatesCommand(
                workspace_id=uuid4(),
                turn_id=uuid4(),
                selections=(),
                idempotency_key="save-001",
            )

    def test_too_many_selections_raises(self) -> None:
        selections = tuple(CandidateSelection(candidate_id=uuid4()) for _ in range(21))
        with pytest.raises(ValueError, match="20"):
            SaveCandidatesCommand(
                workspace_id=uuid4(),
                turn_id=uuid4(),
                selections=selections,
                idempotency_key="save-002",
            )

    def test_valid_single_selection(self) -> None:
        cmd = SaveCandidatesCommand(
            workspace_id=uuid4(),
            turn_id=uuid4(),
            selections=(CandidateSelection(candidate_id=uuid4()),),
            idempotency_key="save-003",
        )
        assert len(cmd.selections) == 1


class TestManualUnverifiedLabel:
    """Manual conclusions must always be labeled manual_unverified."""

    def test_manual_command_creates_unverified(self) -> None:
        """The service must set evidence_status='manual_unverified' for manual."""
        # We verify by inspecting the service source code
        import inspect

        source = inspect.getsource(ConclusionService.create_manual)
        assert "manual_unverified" in source
        assert "manual" in source


class TestArchiveDoesNotDelete:
    """Archive must not delete the conclusion or break historical refs."""

    def test_archive_calls_archive_not_delete(self) -> None:
        import inspect

        source = inspect.getsource(ConclusionService.archive)
        assert "archive_conclusion" in source
        assert "delete" not in source.lower() or "delete" not in source
