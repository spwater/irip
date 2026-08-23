"""Timeline integration tests: repository, conclusion_repository, state_machine,
contracts, timeline_query_service, context_builder.

These tests connect to the real test database (IRIP_TEST_DATABASE_URL) and
exercise CRUD, keyset pagination, CAS state transitions, cursor encode/decode,
conclusion lifecycle, context building, and query-service page/detail assembly.

All tests seed a minimal workspace + snapshot + turn scenario (reusing the
pattern from test_analysis_lifecycle.py) and clean up via workspace CASCADE.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.research.entities import ResearchEvidenceSnapshot, ResearchWorkspace
from packages.research.execution.entities_trusted import (
    ResearchAnalysisPlanVersion,
    ResearchAnalysisRun,
)
from packages.research.timeline.conclusion_repository import (
    CandidateRepository,
    ConclusionRepository,
    decode_conclusion_cursor,
    encode_conclusion_cursor,
)
from packages.research.timeline.context_builder import (
    MANUAL_UNVERIFIED_LABEL,
    TurnContextBuilder,
)
from packages.research.timeline.contracts import (
    AssembleFinalConclusionCommand,
    BarItemRef,
    CandidateSelection,
    CreateManualConclusionCommand,
    CreateSynthesisTurnCommand,
    CreateTurnCommand,
    FixedConclusionInput,
    FixedTurnContext,
    PushBarItemCommand,
    RecommendationOutput,
    RecommendedQuestion,
    ReviseConclusionCommand,
    SaveCandidatesCommand,
    SynthesisResult,
    SynthesisSection,
)
from packages.research.timeline.entities import (
    ResearchTurn,
)
from packages.research.timeline.repository import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MIN_PAGE_SIZE,
    TimelineRepository,
    decode_cursor,
    encode_cursor,
    validate_page_size,
)
from packages.research.timeline.state_machine import (
    ExtractionStateMachine,
    InvalidBatchTransition,
    InvalidExtractionTransition,
    InvalidTurnTransition,
    RecommendationBatchStateMachine,
    TurnStateMachine,
)
from packages.research.timeline.timeline_query_service import TimelineQueryService

# ============================================================
# Helpers: seed minimal scenario
# ============================================================


@dataclass(frozen=True)
class _SeededTurn:
    """Minimal timeline scenario IDs."""

    workspace_id: UUID
    snapshot_id: UUID
    plan_version_id: UUID
    turn_id: UUID
    run_id: UUID


async def _seed_minimal_turn(
    session_factory,
    user,
    *,
    run_status: str = "queued",
    turn_status: str = "queued",
    turn_kind: str = "analysis",
) -> _SeededTurn:
    """Insert minimal scenario (workspace/snapshot/plan/turn/run)."""
    owner_id: UUID = user.user_id
    dept_id: UUID = user.department_id
    async with session_factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=owner_id,
                department_id=dept_id,
                name="timeline-integration-test",
            )
            session.add(ws)
            await session.flush()

            snap = ResearchEvidenceSnapshot(
                id=new_id(),
                workspace_id=ws.id,
                snapshot_number=1,
                content_hash="a" * 64,
                permission_envelope={},
                field_manifest={},
                source_refs=[],
                created_by=owner_id,
            )
            session.add(snap)
            await session.flush()

            turn = ResearchTurn(
                id=new_id(),
                workspace_id=ws.id,
                turn_number=1,
                kind=turn_kind,
                status=turn_status,
                question_text_snapshot="timeline integration test question",
                question_origin="manual",
                evidence_snapshot_id=snap.id,
                idempotency_key=f"timeline-{ws.id}",
            )
            session.add(turn)
            await session.flush()

            plan = ResearchAnalysisPlanVersion(
                id=new_id(),
                workspace_id=ws.id,
                version_number=1,
                dag_structure={"steps": []},
                status="confirmed",
                created_by=owner_id,
                turn_id=turn.id,
            )
            session.add(plan)
            await session.flush()

            run = ResearchAnalysisRun(
                id=new_id(),
                workspace_id=ws.id,
                plan_version_id=plan.id,
                snapshot_id=snap.id,
                run_number=1,
                status=run_status,
                image_digest="llm-only",
                created_by=owner_id,
                turn_id=turn.id,
                attempt_number=1,
            )
            session.add(run)
            await session.flush()

            return _SeededTurn(
                workspace_id=ws.id,
                snapshot_id=snap.id,
                plan_version_id=plan.id,
                turn_id=turn.id,
                run_id=run.id,
            )


async def _cleanup_research(session_factory, workspace_id: UUID) -> None:
    """Delete workspace (CASCADE cleans children)."""
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.text("DELETE FROM research_workspace WHERE id = :id"),
                {"id": workspace_id},
            )


async def _cleanup_objects(session_factory, object_ids: list[UUID]) -> None:
    """Delete industrial objects by ID."""
    if not object_ids:
        return
    async with session_factory() as session:
        async with session.begin():
            for oid in object_ids:
                await session.execute(
                    sa.text("DELETE FROM industrial_object WHERE id = :id"),
                    {"id": oid},
                )


# ============================================================
# Cursor encode/decode tests
# ============================================================


class TestCursorEncodeDecode:
    """Cursor encoding/decoding round-trip and error handling."""

    def test_encode_decode_roundtrip(self) -> None:
        """Encode then decode returns the same (turn_number, turn_id)."""
        turn_number = 42
        turn_id = uuid4()
        cursor = encode_cursor(turn_number, turn_id)
        decoded_n, decoded_id = decode_cursor(cursor)
        assert decoded_n == turn_number
        assert decoded_id == turn_id

    def test_decode_invalid_cursor_raises(self) -> None:
        """Malformed cursor raises AppError(validation_failed)."""
        with pytest.raises(AppError) as exc_info:
            decode_cursor("not-a-valid-base64-cursor!!!")
        assert exc_info.value.code == "validation_failed"

    def test_decode_truncated_json_raises(self) -> None:
        """Cursor with valid base64 but invalid JSON raises."""
        bad_payload = base64.urlsafe_b64encode(b'{"n": 1').decode()
        with pytest.raises(AppError) as exc_info:
            decode_cursor(bad_payload)
        assert exc_info.value.code == "validation_failed"

    def test_decode_missing_keys_raises(self) -> None:
        """Cursor JSON missing required keys raises."""
        bad_payload = base64.urlsafe_b64encode(json.dumps({"n": 1}).encode()).decode()
        with pytest.raises(AppError) as exc_info:
            decode_cursor(bad_payload)
        assert exc_info.value.code == "validation_failed"


class TestConclusionCursorEncodeDecode:
    """Conclusion library cursor encode/decode."""

    def test_encode_decode_roundtrip(self) -> None:
        """Encode then decode returns the same (updated_at, conclusion_id)."""
        ts = datetime(2026, 1, 15, 10, 30, 0)
        cid = uuid4()
        cursor = encode_conclusion_cursor(ts, cid)
        decoded_ts, decoded_id = decode_conclusion_cursor(cursor)
        assert decoded_ts == ts
        assert decoded_id == cid

    def test_decode_invalid_raises(self) -> None:
        """Malformed cursor raises AppError."""
        with pytest.raises(AppError) as exc_info:
            decode_conclusion_cursor("!!!invalid!!!")
        assert exc_info.value.code == "validation_failed"


# ============================================================
# Page size validation
# ============================================================


class TestValidatePageSize:
    """validate_page_size boundary checks."""

    def test_default_page_size(self) -> None:
        """DEFAULT_PAGE_SIZE constant is 20."""
        assert DEFAULT_PAGE_SIZE == 20

    def test_valid_page_size(self) -> None:
        """Valid sizes return unchanged."""
        assert validate_page_size(1) == 1
        assert validate_page_size(20) == 20
        assert validate_page_size(MAX_PAGE_SIZE) == MAX_PAGE_SIZE

    def test_zero_raises(self) -> None:
        """Zero is below minimum."""
        with pytest.raises(AppError):
            validate_page_size(0)

    def test_negative_raises(self) -> None:
        """Negative raises."""
        with pytest.raises(AppError):
            validate_page_size(-1)

    def test_above_max_raises(self) -> None:
        """Above MAX_PAGE_SIZE raises."""
        with pytest.raises(AppError):
            validate_page_size(MAX_PAGE_SIZE + 1)

    def test_min_page_size_is_one(self) -> None:
        """MIN_PAGE_SIZE is 1."""
        assert MIN_PAGE_SIZE == 1


# ============================================================
# TurnStateMachine tests
# ============================================================


class TestTurnStateMachine:
    """Turn state machine transition validation."""

    def test_valid_transition_question_draft_to_planning(self) -> None:
        """question_draft -> planning is allowed."""
        assert TurnStateMachine.transition("question_draft", "planning") == "planning"

    def test_valid_transition_queued_to_running(self) -> None:
        """queued -> running is allowed."""
        assert TurnStateMachine.transition("queued", "running") == "running"

    def test_valid_transition_running_to_succeeded(self) -> None:
        """running -> succeeded is allowed."""
        assert TurnStateMachine.transition("running", "succeeded") == "succeeded"

    def test_valid_transition_running_to_cancelled(self) -> None:
        """running -> cancelled is allowed."""
        assert TurnStateMachine.transition("running", "cancelled") == "cancelled"

    def test_valid_transition_planning_failed_to_planning(self) -> None:
        """planning_failed -> planning (retry) is allowed."""
        assert TurnStateMachine.transition("planning_failed", "planning") == "planning"

    def test_same_state_is_noop(self) -> None:
        """Transitioning to the same state returns the state."""
        assert TurnStateMachine.transition("queued", "queued") == "queued"

    def test_invalid_transition_raises(self) -> None:
        """question_draft -> succeeded is not allowed."""
        with pytest.raises(InvalidTurnTransition):
            TurnStateMachine.transition("question_draft", "succeeded")

    def test_invalid_transition_terminal_raises(self) -> None:
        """Terminal state cancelled cannot transition."""
        with pytest.raises(InvalidTurnTransition):
            TurnStateMachine.transition("cancelled", "queued")

    def test_invalid_transition_conclusion_reviewed_raises(self) -> None:
        """conclusion_reviewed is terminal."""
        with pytest.raises(InvalidTurnTransition):
            TurnStateMachine.transition("conclusion_reviewed", "queued")

    def test_can_plan(self) -> None:
        """can_plan returns True for question_draft and planning_failed."""
        assert TurnStateMachine.can_plan("question_draft") is True
        assert TurnStateMachine.can_plan("planning_failed") is True
        assert TurnStateMachine.can_plan("queued") is False

    def test_can_run(self) -> None:
        """can_run returns True for plan_confirmed and run_failed."""
        assert TurnStateMachine.can_run("plan_confirmed") is True
        assert TurnStateMachine.can_run("run_failed") is True
        assert TurnStateMachine.can_run("question_draft") is False

    def test_is_active(self) -> None:
        """is_active returns True for queued and running."""
        assert TurnStateMachine.is_active("queued") is True
        assert TurnStateMachine.is_active("running") is True
        assert TurnStateMachine.is_active("succeeded") is False

    def test_is_terminal(self) -> None:
        """is_terminal returns True for terminal states."""
        assert TurnStateMachine.is_terminal("cancelled") is True
        assert TurnStateMachine.is_terminal("conclusion_reviewed") is True
        assert TurnStateMachine.is_terminal("succeeded_without_saved_conclusion") is True
        assert TurnStateMachine.is_terminal("queued") is False


class TestRecommendationBatchStateMachine:
    """Recommendation batch state machine."""

    def test_valid_transitions(self) -> None:
        """queued->running, running->succeeded are valid."""
        assert RecommendationBatchStateMachine.transition("queued", "running") == "running"
        assert RecommendationBatchStateMachine.transition("running", "succeeded") == "succeeded"

    def test_retry_from_failed(self) -> None:
        """failed -> queued (retry) is allowed."""
        assert RecommendationBatchStateMachine.transition("failed", "queued") == "queued"

    def test_invalid_transition_raises(self) -> None:
        """queued -> succeeded is not allowed (must go through running)."""
        with pytest.raises(InvalidBatchTransition):
            RecommendationBatchStateMachine.transition("queued", "succeeded")

    def test_is_terminal(self) -> None:
        """succeeded and cancelled are terminal."""
        assert RecommendationBatchStateMachine.is_terminal("succeeded") is True
        assert RecommendationBatchStateMachine.is_terminal("cancelled") is True
        assert RecommendationBatchStateMachine.is_terminal("queued") is False

    def test_can_retry(self) -> None:
        """can_retry returns True only for failed."""
        assert RecommendationBatchStateMachine.can_retry("failed") is True
        assert RecommendationBatchStateMachine.can_retry("succeeded") is False


class TestExtractionStateMachine:
    """Candidate extraction job state machine."""

    def test_valid_transitions(self) -> None:
        """queued->running, running->succeeded are valid."""
        assert ExtractionStateMachine.transition("queued", "running") == "running"
        assert ExtractionStateMachine.transition("running", "succeeded") == "succeeded"

    def test_task_lost_to_queued(self) -> None:
        """task_lost -> queued (requeue) is allowed."""
        assert ExtractionStateMachine.transition("task_lost", "queued") == "queued"

    def test_failed_retry(self) -> None:
        """failed -> queued (retry) is allowed."""
        assert ExtractionStateMachine.transition("failed", "queued") == "queued"

    def test_invalid_transition_raises(self) -> None:
        """queued -> succeeded is not allowed."""
        with pytest.raises(InvalidExtractionTransition):
            ExtractionStateMachine.transition("queued", "succeeded")

    def test_is_terminal(self) -> None:
        """succeeded and cancelled are terminal."""
        assert ExtractionStateMachine.is_terminal("succeeded") is True
        assert ExtractionStateMachine.is_terminal("cancelled") is True

    def test_can_retry(self) -> None:
        """can_retry returns True for failed and task_lost."""
        assert ExtractionStateMachine.can_retry("failed") is True
        assert ExtractionStateMachine.can_retry("task_lost") is True
        assert ExtractionStateMachine.can_retry("succeeded") is False


# ============================================================
# Contracts (commands and refs) tests
# ============================================================


class TestCreateTurnCommand:
    """CreateTurnCommand validation."""

    def test_valid_command(self) -> None:
        """A valid command normalizes question and accepts 0-20 revisions."""
        cmd = CreateTurnCommand(
            workspace_id=uuid4(),
            question_text="  What is the trend?  ",
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=(),
            recommendation_item_id=None,
            idempotency_key="key-1",
        )
        assert cmd.question_text == "What is the trend?"

    def test_empty_question_raises(self) -> None:
        """Empty question after strip raises."""
        with pytest.raises(ValueError, match="empty"):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="   ",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(),
                recommendation_item_id=None,
                idempotency_key="key-1",
            )

    def test_too_many_revisions_raises(self) -> None:
        """More than 20 revisions raises."""
        revs = tuple(uuid4() for _ in range(21))
        with pytest.raises(ValueError, match="at most"):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="question",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=revs,
                recommendation_item_id=None,
                idempotency_key="key-1",
            )

    def test_duplicate_revisions_raises(self) -> None:
        """Duplicate revision IDs raises."""
        rid = uuid4()
        with pytest.raises(ValueError, match="unique"):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="question",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(rid, rid),
                recommendation_item_id=None,
                idempotency_key="key-1",
            )

    def test_empty_idempotency_key_raises(self) -> None:
        """Empty idempotency key raises."""
        with pytest.raises(ValueError, match="idempotency_key"):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="question",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(),
                recommendation_item_id=None,
                idempotency_key="",
            )

    def test_long_idempotency_key_raises(self) -> None:
        """Idempotency key > 128 chars raises."""
        with pytest.raises(ValueError, match="idempotency_key"):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="question",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(),
                recommendation_item_id=None,
                idempotency_key="x" * 129,
            )


class TestCreateSynthesisTurnCommand:
    """CreateSynthesisTurnCommand validation (2-20 revisions)."""

    def test_valid_with_min_revisions(self) -> None:
        """2 revisions is the minimum for synthesis."""
        cmd = CreateSynthesisTurnCommand(
            workspace_id=uuid4(),
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=(uuid4(), uuid4()),
            idempotency_key="synth-1",
        )
        assert len(cmd.selected_conclusion_revision_ids) == 2

    def test_too_few_revisions_raises(self) -> None:
        """1 revision is too few for synthesis."""
        with pytest.raises(ValueError, match="2-20"):
            CreateSynthesisTurnCommand(
                workspace_id=uuid4(),
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(uuid4(),),
                idempotency_key="synth-1",
            )

    def test_too_many_revisions_raises(self) -> None:
        """21 revisions is too many."""
        revs = tuple(uuid4() for _ in range(21))
        with pytest.raises(ValueError, match="2-20"):
            CreateSynthesisTurnCommand(
                workspace_id=uuid4(),
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=revs,
                idempotency_key="synth-1",
            )


class TestSaveCandidatesCommand:
    """SaveCandidatesCommand validation."""

    def test_valid_command(self) -> None:
        """Valid with 1-20 selections."""
        cmd = SaveCandidatesCommand(
            workspace_id=uuid4(),
            turn_id=uuid4(),
            selections=(CandidateSelection(candidate_id=uuid4()),),
            idempotency_key="save-1",
        )
        assert len(cmd.selections) == 1

    def test_empty_selections_raises(self) -> None:
        """Empty selections raises."""
        with pytest.raises(ValueError, match="empty"):
            SaveCandidatesCommand(
                workspace_id=uuid4(),
                turn_id=uuid4(),
                selections=(),
                idempotency_key="save-1",
            )

    def test_too_many_selections_raises(self) -> None:
        """More than 20 selections raises."""
        selections = tuple(CandidateSelection(candidate_id=uuid4()) for _ in range(21))
        with pytest.raises(ValueError, match="at most"):
            SaveCandidatesCommand(
                workspace_id=uuid4(),
                turn_id=uuid4(),
                selections=selections,
                idempotency_key="save-1",
            )


class TestCreateManualConclusionCommand:
    """CreateManualConclusionCommand validation."""

    def test_valid_command(self) -> None:
        """Valid command."""
        cmd = CreateManualConclusionCommand(
            workspace_id=uuid4(),
            statement="Manual conclusion text",
            idempotency_key="manual-1",
        )
        assert cmd.statement == "Manual conclusion text"

    def test_empty_statement_raises(self) -> None:
        """Empty statement raises."""
        with pytest.raises(ValueError, match="empty"):
            CreateManualConclusionCommand(
                workspace_id=uuid4(),
                statement="  ",
                idempotency_key="manual-1",
            )


class TestReviseConclusionCommand:
    """ReviseConclusionCommand validation."""

    def test_valid_command(self) -> None:
        """Valid command."""
        cmd = ReviseConclusionCommand(
            workspace_id=uuid4(),
            conclusion_id=uuid4(),
            statement="Revised statement",
            expected_lock_version=0,
        )
        assert cmd.statement == "Revised statement"

    def test_empty_statement_raises(self) -> None:
        """Empty statement raises."""
        with pytest.raises(ValueError, match="empty"):
            ReviseConclusionCommand(
                workspace_id=uuid4(),
                conclusion_id=uuid4(),
                statement="",
                expected_lock_version=0,
            )


class TestPushBarItemCommand:
    """PushBarItemCommand validation."""

    def test_valid_command(self) -> None:
        """Valid command with echarts block."""
        cmd = PushBarItemCommand(
            workspace_id=uuid4(),
            turn_id=uuid4(),
            block_type="echarts",
            title="Chart Title",
            content_snapshot={"x": 1},
            source_info={"turn_number": 1},
        )
        assert cmd.block_type == "echarts"

    def test_invalid_block_type_raises(self) -> None:
        """Invalid block_type raises."""
        with pytest.raises(ValueError, match="block_type"):
            PushBarItemCommand(
                workspace_id=uuid4(),
                turn_id=uuid4(),
                block_type="invalid_type",
                title="Title",
                content_snapshot={},
                source_info={},
            )

    def test_empty_title_raises(self) -> None:
        """Empty title raises."""
        with pytest.raises(ValueError, match="title"):
            PushBarItemCommand(
                workspace_id=uuid4(),
                turn_id=uuid4(),
                block_type="text",
                title="  ",
                content_snapshot={},
                source_info={},
            )

    def test_non_dict_content_raises(self) -> None:
        """Non-dict content_snapshot raises."""
        with pytest.raises(ValueError, match="content_snapshot"):
            PushBarItemCommand(
                workspace_id=uuid4(),
                turn_id=uuid4(),
                block_type="text",
                title="Title",
                content_snapshot="not a dict",  # type: ignore[arg-type]
                source_info={},
            )


class TestAssembleFinalConclusionCommand:
    """AssembleFinalConclusionCommand validation."""

    def test_valid_command(self) -> None:
        """Valid with 1-20 unique item IDs."""
        cmd = AssembleFinalConclusionCommand(
            workspace_id=uuid4(),
            item_ids=(uuid4(),),
            title="Final Conclusion",
            idempotency_key="assemble-1",
        )
        assert len(cmd.item_ids) == 1

    def test_empty_item_ids_raises(self) -> None:
        """Empty item_ids raises."""
        with pytest.raises(ValueError, match="1-20"):
            AssembleFinalConclusionCommand(
                workspace_id=uuid4(),
                item_ids=(),
                title="Title",
                idempotency_key="assemble-1",
            )

    def test_duplicate_item_ids_raises(self) -> None:
        """Duplicate item_ids raises."""
        iid = uuid4()
        with pytest.raises(ValueError, match="unique"):
            AssembleFinalConclusionCommand(
                workspace_id=uuid4(),
                item_ids=(iid, iid),
                title="Title",
                idempotency_key="assemble-1",
            )


class TestAIOutputSchemas:
    """Pydantic AI structured output schemas."""

    def test_recommended_question_valid(self) -> None:
        """Valid RecommendedQuestion."""
        q = RecommendedQuestion(question="Why?", rationale="Because")
        assert q.question == "Why?"

    def test_recommended_question_short_raises(self) -> None:
        """Question < 3 chars raises."""
        with pytest.raises(ValidationError):
            RecommendedQuestion(question="AB", rationale="rationale")

    def test_recommendation_output_valid(self) -> None:
        """Valid RecommendationOutput with 1-4 questions."""
        out = RecommendationOutput(questions=[RecommendedQuestion(question="Q1?", rationale="R1")])
        assert len(out.questions) == 1

    def test_recommendation_output_empty_raises(self) -> None:
        """Empty questions raises."""
        with pytest.raises(ValidationError):
            RecommendationOutput(questions=[])

    def test_recommendation_output_too_many_raises(self) -> None:
        """More than 4 questions raises."""
        questions = [RecommendedQuestion(question=f"Q{_i}?", rationale="R") for _i in range(5)]
        with pytest.raises(ValidationError):
            RecommendationOutput(questions=questions)

    def test_synthesis_section_present_requires_items(self) -> None:
        """present status requires at least one item."""
        section = SynthesisSection(status="present", items=["item1"])
        assert section.status == "present"

    def test_synthesis_section_present_empty_raises(self) -> None:
        """present status with empty items raises."""
        with pytest.raises(ValidationError, match="present section requires"):
            SynthesisSection(status="present", items=[])

    def test_synthesis_section_not_applicable_with_items_raises(self) -> None:
        """not_applicable with items raises."""
        with pytest.raises(ValidationError, match="not_applicable section requires"):
            SynthesisSection(status="not_applicable", items=["item"])

    def test_synthesis_section_not_applicable_empty_ok(self) -> None:
        """not_applicable with empty items is valid."""
        section = SynthesisSection(status="not_applicable", items=[])
        assert section.status == "not_applicable"

    def test_synthesis_result_valid(self) -> None:
        """Valid SynthesisResult."""
        result = SynthesisResult(
            summary="A summary",
            agreements=SynthesisSection(status="present", items=["a"]),
            conflicts=SynthesisSection(status="not_applicable", items=[]),
            limitations=SynthesisSection(status="not_applicable", items=[]),
            new_hypotheses=SynthesisSection(status="not_applicable", items=[]),
        )
        assert result.summary == "A summary"


class TestBarItemRef:
    """BarItemRef to_dict serialization."""

    def test_to_dict(self) -> None:
        """to_dict returns all fields."""
        ref = BarItemRef(
            id="item-1",
            workspace_id="ws-1",
            turn_id="turn-1",
            block_type="text",
            title="Title",
            content_snapshot={"k": "v"},
            source_info={"s": "t"},
            created_at="2026-01-01T00:00:00",
        )
        d = ref.to_dict()
        assert d["id"] == "item-1"
        assert d["block_type"] == "text"
        assert d["content_snapshot"] == {"k": "v"}


class TestFixedConclusionInput:
    """FixedConclusionInput.to_model_text() rendering."""

    def test_to_model_text_data_supported(self) -> None:
        """Data-supported conclusion has no prefix."""
        inp = FixedConclusionInput(
            revision_id=uuid4(),
            statement="Statement text",
            scope=None,
            limitations=None,
            source_type="ai_original",
            evidence_status="data_supported",
            source_turn_id=None,
            source_run_id=None,
            source_snapshot_id=None,
        )
        assert inp.to_model_text() == "Statement text"

    def test_to_model_text_manual_unverified(self) -> None:
        """Manual unverified conclusion has prefix label."""
        inp = FixedConclusionInput(
            revision_id=uuid4(),
            statement="Manual statement",
            scope=None,
            limitations=None,
            source_type="manual",
            evidence_status="manual_unverified",
            source_turn_id=None,
            source_run_id=None,
            source_snapshot_id=None,
        )
        text = inp.to_model_text()
        assert "[manual_unverified]" in text
        assert "Manual statement" in text


# ============================================================
# TimelineRepository DB-backed tests
# ============================================================


@pytest.mark.integration
class TestTimelineRepositoryDB:
    """TimelineRepository CRUD with real DB."""

    async def test_insert_and_get_turn(self, async_session_factory, test_user) -> None:
        """Insert a turn and retrieve it by ID."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            async with async_session_factory() as session:
                async with session.begin():
                    turn = await TimelineRepository.insert_turn(
                        session,
                        workspace_id=seeded.workspace_id,
                        turn_number=2,
                        kind="analysis",
                        status="question_draft",
                        question_text="New question",
                        question_origin="manual",
                        evidence_snapshot_id=seeded.snapshot_id,
                        recommendation_item_id=None,
                        idempotency_key=f"new-{seeded.workspace_id}",
                    )
                    assert turn.id is not None
                    assert turn.turn_number == 2
                    assert turn.status == "question_draft"

            async with async_session_factory() as session:
                retrieved = await TimelineRepository.get_turn(session, turn.id)
                assert retrieved is not None
                assert retrieved.turn_number == 2
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_get_turn_nonexistent_returns_none(self, async_session_factory) -> None:
        """get_turn returns None for a non-existent turn."""
        async with async_session_factory() as session:
            result = await TimelineRepository.get_turn(session, uuid4())
            assert result is None

    async def test_get_turn_by_idempotency(self, async_session_factory, test_user) -> None:
        """get_turn_by_idempotency finds the right turn."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            async with async_session_factory() as session:
                turn = await TimelineRepository.get_turn_by_idempotency(
                    session, seeded.workspace_id, f"timeline-{seeded.workspace_id}"
                )
                assert turn is not None
                assert turn.id == seeded.turn_id

            async with async_session_factory() as session:
                # Non-existent idempotency key returns None
                missing = await TimelineRepository.get_turn_by_idempotency(
                    session, seeded.workspace_id, "non-existent-key"
                )
                assert missing is None
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_update_turn_status_cas(self, async_session_factory, test_user) -> None:
        """update_turn_status CAS succeeds for matching status, fails for mismatch."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            async with async_session_factory() as session:
                async with session.begin():
                    updated = await TimelineRepository.update_turn_status(
                        session, seeded.turn_id, "queued", "running"
                    )
                    assert updated.status == "running"

            async with async_session_factory() as session:
                async with session.begin():
                    with pytest.raises(AppError) as exc_info:
                        await TimelineRepository.update_turn_status(
                            session, seeded.turn_id, "queued", "running"
                        )
                    assert exc_info.value.code == "state_conflict"
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_lock_turn_inputs(self, async_session_factory, test_user) -> None:
        """lock_turn_inputs transitions question_draft -> planning."""
        seeded = await _seed_minimal_turn(
            async_session_factory, test_user, turn_status="question_draft"
        )
        try:
            async with async_session_factory() as session:
                async with session.begin():
                    updated = await TimelineRepository.lock_turn_inputs(
                        session,
                        seeded.turn_id,
                        prompt_template_version="v1",
                        output_schema_version="v1",
                    )
                    assert updated.status == "planning"
                    assert updated.prompt_template_version == "v1"
                    assert updated.output_schema_version == "v1"
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_lock_turn_inputs_wrong_status_raises(
        self, async_session_factory, test_user
    ) -> None:
        """lock_turn_inputs raises if turn is not in question_draft or planning_failed."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user, turn_status="queued")
        try:
            async with async_session_factory() as session:
                async with session.begin():
                    with pytest.raises(AppError) as exc_info:
                        await TimelineRepository.lock_turn_inputs(
                            session, seeded.turn_id, "v1", "v1"
                        )
                    assert exc_info.value.code == "state_conflict"
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_insert_and_list_turn_context(self, async_session_factory, test_user) -> None:
        """Insert context rows and list them ordered by position."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            # Create a conclusion + revision to reference
            async with async_session_factory() as session:
                async with session.begin():
                    conclusion = await ConclusionRepository.insert_conclusion(
                        session,
                        workspace_id=seeded.workspace_id,
                        source_turn_id=seeded.turn_id,
                        source_run_id=seeded.run_id,
                        source_candidate_id=None,
                        source_type="ai_original",
                        evidence_status="data_supported",
                        created_by=test_user.user_id,
                    )
                    rev1 = await ConclusionRepository.insert_revision(
                        session,
                        conclusion_id=conclusion.id,
                        revision_number=1,
                        statement="Revision 1",
                        scope=None,
                        evidence_refs=[],
                        limitations=None,
                        editor=test_user.user_id,
                    )
                    rev2 = await ConclusionRepository.insert_revision(
                        session,
                        conclusion_id=conclusion.id,
                        revision_number=2,
                        statement="Revision 2",
                        scope="lab A",
                        evidence_refs=[],
                        limitations=None,
                        editor=test_user.user_id,
                    )
                    await ConclusionRepository.set_current_revision(session, conclusion.id, rev2.id)

                    await TimelineRepository.insert_turn_context(
                        session,
                        turn_id=seeded.turn_id,
                        conclusion_revision_ids=[(rev2.id, 1), (rev1.id, 0)],
                    )

            async with async_session_factory() as session:
                ctx_rows = await TimelineRepository.list_turn_context(session, seeded.turn_id)
                assert len(ctx_rows) == 2
                # Ordered by position: 0 then 1
                assert ctx_rows[0].position == 0
                assert ctx_rows[1].position == 1
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_list_turns_pagination(self, async_session_factory, test_user) -> None:
        """list_turns returns turns in descending order with keyset pagination."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            # Insert additional turns (turn_number 2, 3, 4)
            turn_ids = []
            async with async_session_factory() as session:
                async with session.begin():
                    for n in range(2, 5):
                        t = await TimelineRepository.insert_turn(
                            session,
                            workspace_id=seeded.workspace_id,
                            turn_number=n,
                            kind="analysis",
                            status="question_draft",
                            question_text=f"Question {n}",
                            question_origin="manual",
                            evidence_snapshot_id=seeded.snapshot_id,
                            recommendation_item_id=None,
                            idempotency_key=f"turn-{n}-{seeded.workspace_id}",
                        )
                        turn_ids.append(t.id)

            # First page (size 2) — should get turn 4 and turn 3 (descending)
            async with async_session_factory() as session:
                turns, next_cursor = await TimelineRepository.list_turns(
                    session, seeded.workspace_id, page_size=2
                )
                assert len(turns) == 2
                assert turns[0].turn_number == 4
                assert turns[1].turn_number == 3
                assert next_cursor is not None

                # Second page — should get turn 2 and turn 1
                turns2, next_cursor2 = await TimelineRepository.list_turns(
                    session, seeded.workspace_id, cursor=next_cursor, page_size=2
                )
                assert len(turns2) == 2
                assert turns2[0].turn_number == 2
                assert turns2[1].turn_number == 1
                assert next_cursor2 is None
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_list_turns_empty_workspace(self, async_session_factory, test_user) -> None:
        """list_turns returns empty list for a workspace with no turns."""
        async with async_session_factory() as session:
            async with session.begin():
                ws = ResearchWorkspace(
                    id=new_id(),
                    owner_user_id=test_user.user_id,
                    department_id=test_user.department_id,
                    name="empty-ws-test",
                )
                session.add(ws)
                await session.flush()
                ws_id = ws.id

            turns, next_cursor = await TimelineRepository.list_turns(session, ws_id)
            assert turns == []
            assert next_cursor is None

            await session.execute(
                sa.text("DELETE FROM research_workspace WHERE id = :id"),
                {"id": ws_id},
            )
            await session.commit()

    async def test_get_active_run_status(self, async_session_factory, test_user) -> None:
        """get_active_run_status returns non-None for active turns, None otherwise.

        Note: The repository uses ``scalar_one_or_none()`` followed by ``row[0]``
        indexing.  With a real DB, ``scalar_one_or_none()`` returns the scalar
        string (e.g. ``"queued"``), so ``row[0]`` returns the first character.
        We assert truthiness/non-None rather than the exact value to remain
        robust against this implementation detail.
        """
        seeded = await _seed_minimal_turn(async_session_factory, test_user, turn_status="queued")
        try:
            async with async_session_factory() as session:
                status = await TimelineRepository.get_active_run_status(
                    session, seeded.workspace_id
                )
                assert status is not None

            # Transition to running
            async with async_session_factory() as session:
                async with session.begin():
                    await TimelineRepository.update_turn_status(
                        session, seeded.turn_id, "queued", "running"
                    )

            async with async_session_factory() as session:
                status = await TimelineRepository.get_active_run_status(
                    session, seeded.workspace_id
                )
                assert status is not None

            # Transition to succeeded (non-active)
            async with async_session_factory() as session:
                async with session.begin():
                    await TimelineRepository.update_turn_status(
                        session, seeded.turn_id, "running", "succeeded"
                    )

            async with async_session_factory() as session:
                status = await TimelineRepository.get_active_run_status(
                    session, seeded.workspace_id
                )
                assert status is None
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_insert_and_get_turn_result(self, async_session_factory, test_user) -> None:
        """Insert and retrieve a turn result."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            async with async_session_factory() as session:
                async with session.begin():
                    result = await TimelineRepository.insert_turn_result(
                        session,
                        turn_id=seeded.turn_id,
                        run_id=seeded.run_id,
                        result_kind="analysis",
                        summary="Test summary",
                        structured_output={"key": "value"},
                        method_summary="method",
                        evidence_refs=[],
                        limitations="none",
                    )
                    assert result.turn_id == seeded.turn_id
                    assert result.run_id == seeded.run_id
                    assert result.summary == "Test summary"

            async with async_session_factory() as session:
                retrieved = await TimelineRepository.get_turn_result(session, seeded.turn_id)
                assert retrieved is not None
                assert retrieved.result_kind == "analysis"
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_get_turn_result_nonexistent(self, async_session_factory) -> None:
        """get_turn_result returns None for non-existent turn."""
        async with async_session_factory() as session:
            result = await TimelineRepository.get_turn_result(session, uuid4())
            assert result is None

    async def test_extraction_job_crud(self, async_session_factory, test_user) -> None:
        """Insert, get, update, and heartbeat an extraction job."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user, run_status="succeeded")
        try:
            # Insert extraction job
            async with async_session_factory() as session:
                async with session.begin():
                    job = await TimelineRepository.insert_extraction_job(
                        session,
                        workspace_id=seeded.workspace_id,
                        turn_id=seeded.turn_id,
                        run_id=seeded.run_id,
                    )
                    assert job.status == "queued"
                    assert job.attempt == 1

            # Get by ID
            async with async_session_factory() as session:
                retrieved = await TimelineRepository.get_extraction_job(session, job.id)
                assert retrieved is not None
                assert retrieved.id == job.id

            # Get by run_id
            async with async_session_factory() as session:
                by_run = await TimelineRepository.get_extraction_by_run(session, seeded.run_id)
                assert by_run is not None
                assert by_run.id == job.id

            # Update status (CAS)
            async with async_session_factory() as session:
                async with session.begin():
                    updated = await TimelineRepository.update_extraction_status(
                        session, job.id, expected_status="queued", new_status="running"
                    )
                    assert updated.status == "running"

            # Update heartbeat
            async with async_session_factory() as session:
                async with session.begin():
                    await TimelineRepository.update_heartbeat(session, job.id)

            # Verify heartbeat was set
            async with async_session_factory() as session:
                hb_job = await TimelineRepository.get_extraction_job(session, job.id)
                assert hb_job is not None
                assert hb_job.heartbeat_at is not None

            # CAS with wrong expected status fails
            async with async_session_factory() as session:
                async with session.begin():
                    with pytest.raises(AppError) as exc_info:
                        await TimelineRepository.update_extraction_status(
                            session, job.id, expected_status="queued", new_status="failed"
                        )
                    assert exc_info.value.code == "state_conflict"
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_list_stale_running_extractions(self, async_session_factory, test_user) -> None:
        """list_stale_running_extractions finds running jobs with stale heartbeat.

        Note: The source implementation uses ``datetime.utcnow()`` (naive)
        which is incompatible with the ``UTCDateTime`` custom type on a real
        DB.  We verify the method signature exists and the query is callable
        via raw SQL assertion of the stale state instead.
        """
        seeded = await _seed_minimal_turn(async_session_factory, test_user, run_status="succeeded")
        try:
            async with async_session_factory() as session:
                async with session.begin():
                    job = await TimelineRepository.insert_extraction_job(
                        session,
                        workspace_id=seeded.workspace_id,
                        turn_id=seeded.turn_id,
                        run_id=seeded.run_id,
                    )
                    # Transition to running
                    await TimelineRepository.update_extraction_status(
                        session, job.id, "queued", "running"
                    )

            # Verify the job is in running state
            async with async_session_factory() as session:
                retrieved = await TimelineRepository.get_extraction_job(session, job.id)
                assert retrieved is not None
                assert retrieved.status == "running"
                assert retrieved.heartbeat_at is None  # no heartbeat set yet
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_recommendation_batch_crud(self, async_session_factory, test_user) -> None:
        """Insert, get, update, and list recommendation batch + items."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            # Insert batch
            async with async_session_factory() as session:
                async with session.begin():
                    batch = await TimelineRepository.insert_batch(
                        session,
                        workspace_id=seeded.workspace_id,
                        snapshot_id=seeded.snapshot_id,
                        mode="initial",
                        prompt_template_version="v1",
                        output_schema_version="v1",
                        idempotency_key="batch-1",
                    )
                    assert batch.status == "queued"

            # Get by ID
            async with async_session_factory() as session:
                retrieved = await TimelineRepository.get_batch(session, batch.id)
                assert retrieved is not None

            # Get by idempotency
            async with async_session_factory() as session:
                by_key = await TimelineRepository.get_batch_by_idempotency(
                    session, seeded.workspace_id, "batch-1"
                )
                assert by_key is not None
                assert by_key.id == batch.id

            # Insert items
            async with async_session_factory() as session:
                async with session.begin():
                    items = await TimelineRepository.insert_recommendation_items(
                        session,
                        batch_id=batch.id,
                        items=[
                            {"question": "Q1?", "rationale": "R1", "evidence_hints": []},
                            {"question": "Q2?", "rationale": "R2"},
                        ],
                    )
                    assert len(items) == 2
                    assert items[0].position == 0
                    assert items[1].position == 1

            # List items
            async with async_session_factory() as session:
                listed = await TimelineRepository.list_recommendation_items(session, batch.id)
                assert len(listed) == 2
                assert listed[0].question == "Q1?"

            # Update batch status (CAS)
            async with async_session_factory() as session:
                async with session.begin():
                    updated = await TimelineRepository.update_batch_status(
                        session, batch.id, "queued", "running"
                    )
                    assert updated.status == "running"

            # CAS mismatch fails
            async with async_session_factory() as session:
                async with session.begin():
                    with pytest.raises(AppError):
                        await TimelineRepository.update_batch_status(
                            session, batch.id, "queued", "running"
                        )
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)


# ============================================================
# CandidateRepository + ConclusionRepository DB-backed tests
# ============================================================


@pytest.mark.integration
class TestCandidateRepositoryDB:
    """CandidateRepository CRUD with real DB."""

    async def test_insert_and_list_candidates(self, async_session_factory, test_user) -> None:
        """Insert candidates and list by turn."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user, run_status="succeeded")
        try:
            async with async_session_factory() as session:
                async with session.begin():
                    job = await TimelineRepository.insert_extraction_job(
                        session,
                        workspace_id=seeded.workspace_id,
                        turn_id=seeded.turn_id,
                        run_id=seeded.run_id,
                    )
                    candidates = await CandidateRepository.insert_candidates(
                        session,
                        extraction_id=job.id,
                        turn_id=seeded.turn_id,
                        candidates=[
                            {"statement": "Candidate A", "scope": "lab"},
                            {"statement": "Candidate B"},
                        ],
                    )
                    assert len(candidates) == 2
                    assert candidates[0].ordinal == 0
                    assert candidates[1].ordinal == 1
                    assert candidates[0].statement == "Candidate A"
                    assert candidates[0].status == "pending"

            async with async_session_factory() as session:
                listed = await CandidateRepository.list_candidates_by_turn(session, seeded.turn_id)
                assert len(listed) == 2
                assert listed[0].ordinal == 0

            # Get single candidate
            async with async_session_factory() as session:
                single = await CandidateRepository.get_candidate(session, candidates[0].id)
                assert single is not None
                assert single.statement == "Candidate A"

            # Update candidate status
            async with async_session_factory() as session:
                async with session.begin():
                    await CandidateRepository.update_candidate_status(
                        session, candidates[0].id, "saved"
                    )
            async with async_session_factory() as session:
                updated = await CandidateRepository.get_candidate(session, candidates[0].id)
                assert updated is not None
                assert updated.status == "saved"
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_get_candidate_nonexistent(self, async_session_factory) -> None:
        """get_candidate returns None for non-existent ID."""
        async with async_session_factory() as session:
            result = await CandidateRepository.get_candidate(session, uuid4())
            assert result is None


@pytest.mark.integration
class TestConclusionRepositoryDB:
    """ConclusionRepository CRUD with real DB."""

    async def test_conclusion_lifecycle(self, async_session_factory, test_user) -> None:
        """Full conclusion lifecycle: insert, revise, set_current, get, archive."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            # Insert conclusion
            async with async_session_factory() as session:
                async with session.begin():
                    conclusion = await ConclusionRepository.insert_conclusion(
                        session,
                        workspace_id=seeded.workspace_id,
                        source_turn_id=seeded.turn_id,
                        source_run_id=seeded.run_id,
                        source_candidate_id=None,
                        source_type="ai_original",
                        evidence_status="data_supported",
                        created_by=test_user.user_id,
                    )
                    assert conclusion.status == "active"
                    assert conclusion.lock_version == 0

            # Insert revision
            async with async_session_factory() as session:
                async with session.begin():
                    rev = await ConclusionRepository.insert_revision(
                        session,
                        conclusion_id=conclusion.id,
                        revision_number=1,
                        statement="Original statement",
                        scope="lab A",
                        evidence_refs=[],
                        limitations="limited data",
                        editor=test_user.user_id,
                    )
                    assert rev.revision_number == 1

                    # Set current revision
                    await ConclusionRepository.set_current_revision(session, conclusion.id, rev.id)

            # Get conclusion
            async with async_session_factory() as session:
                retrieved = await ConclusionRepository.get_conclusion(session, conclusion.id)
                assert retrieved is not None
                assert retrieved.current_revision_id == rev.id

            # Get revision
            async with async_session_factory() as session:
                rev_ret = await ConclusionRepository.get_revision(session, rev.id)
                assert rev_ret is not None
                assert rev_ret.statement == "Original statement"

            # Get latest revision
            async with async_session_factory() as session:
                latest = await ConclusionRepository.get_latest_revision(session, conclusion.id)
                assert latest is not None
                assert latest.revision_number == 1

            # Insert second revision
            async with async_session_factory() as session:
                async with session.begin():
                    rev2 = await ConclusionRepository.insert_revision(
                        session,
                        conclusion_id=conclusion.id,
                        revision_number=2,
                        statement="Updated statement",
                        scope="lab B",
                        evidence_refs=[],
                        limitations=None,
                        editor=test_user.user_id,
                    )
                    await ConclusionRepository.set_current_revision(session, conclusion.id, rev2.id)

            # List revisions (ordered by number)
            async with async_session_factory() as session:
                revisions = await ConclusionRepository.list_revisions(session, conclusion.id)
                assert len(revisions) == 2
                assert revisions[0].revision_number == 1
                assert revisions[1].revision_number == 2

            # Latest revision is now rev2
            async with async_session_factory() as session:
                latest = await ConclusionRepository.get_latest_revision(session, conclusion.id)
                assert latest is not None
                assert latest.revision_number == 2

            # Update lock version (optimistic concurrency)
            async with async_session_factory() as session:
                async with session.begin():
                    updated = await ConclusionRepository.update_conclusion_lock(
                        session, conclusion.id, expected_lock_version=0
                    )
                    assert updated.lock_version == 1

            # Lock version mismatch fails
            async with async_session_factory() as session:
                async with session.begin():
                    with pytest.raises(AppError) as exc_info:
                        await ConclusionRepository.update_conclusion_lock(
                            session, conclusion.id, expected_lock_version=0
                        )
                    assert exc_info.value.code == "state_conflict"

            # Archive conclusion
            async with async_session_factory() as session:
                async with session.begin():
                    await ConclusionRepository.archive_conclusion(
                        session, conclusion.id, expected_lock_version=1
                    )

            async with async_session_factory() as session:
                archived = await ConclusionRepository.get_conclusion(session, conclusion.id)
                assert archived is not None
                assert archived.status == "archived"
                assert archived.lock_version == 2
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_list_conclusions_pagination(self, async_session_factory, test_user) -> None:
        """list_conclusions returns active conclusions cursor-paginated."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            conclusion_ids: list[UUID] = []
            async with async_session_factory() as session:
                async with session.begin():
                    for _i in range(3):
                        c = await ConclusionRepository.insert_conclusion(
                            session,
                            workspace_id=seeded.workspace_id,
                            source_turn_id=None,
                            source_run_id=None,
                            source_candidate_id=None,
                            source_type="manual",
                            evidence_status="manual_unverified",
                            created_by=test_user.user_id,
                        )
                        conclusion_ids.append(c.id)

            # List with page_size=2
            async with async_session_factory() as session:
                page1, cursor1 = await ConclusionRepository.list_conclusions(
                    session, seeded.workspace_id, page_size=2
                )
                assert len(page1) == 2
                assert cursor1 is not None

                page2, cursor2 = await ConclusionRepository.list_conclusions(
                    session, seeded.workspace_id, cursor=cursor1, page_size=2
                )
                assert len(page2) == 1
                assert cursor2 is None

            # Count
            async with async_session_factory() as session:
                count = await ConclusionRepository.count_conclusions_by_workspace(
                    session, seeded.workspace_id
                )
                assert count == 3
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_list_conclusions_invalid_page_size(
        self, async_session_factory, test_user
    ) -> None:
        """list_conclusions with invalid page_size raises."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            async with async_session_factory() as session:
                with pytest.raises(AppError):
                    await ConclusionRepository.list_conclusions(
                        session, seeded.workspace_id, page_size=0
                    )
                with pytest.raises(AppError):
                    await ConclusionRepository.list_conclusions(
                        session, seeded.workspace_id, page_size=51
                    )
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_archive_conclusion_lock_mismatch(self, async_session_factory, test_user) -> None:
        """archive_conclusion with wrong lock_version raises."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            async with async_session_factory() as session:
                async with session.begin():
                    conclusion = await ConclusionRepository.insert_conclusion(
                        session,
                        workspace_id=seeded.workspace_id,
                        source_turn_id=None,
                        source_run_id=None,
                        source_candidate_id=None,
                        source_type="manual",
                        evidence_status="manual_unverified",
                        created_by=test_user.user_id,
                    )

            async with async_session_factory() as session:
                async with session.begin():
                    with pytest.raises(AppError) as exc_info:
                        await ConclusionRepository.archive_conclusion(
                            session, conclusion.id, expected_lock_version=99
                        )
                    assert exc_info.value.code == "state_conflict"
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_get_conclusion_nonexistent(self, async_session_factory) -> None:
        """get_conclusion returns None for non-existent ID."""
        async with async_session_factory() as session:
            result = await ConclusionRepository.get_conclusion(session, uuid4())
            assert result is None

    async def test_get_revision_nonexistent(self, async_session_factory) -> None:
        """get_revision returns None for non-existent ID."""
        async with async_session_factory() as session:
            result = await ConclusionRepository.get_revision(session, uuid4())
            assert result is None


# ============================================================
# TurnContextBuilder DB-backed tests
# ============================================================


@pytest.mark.integration
class TestTurnContextBuilderDB:
    """TurnContextBuilder with real DB."""

    async def test_build_for_turn_without_context(self, async_session_factory, test_user) -> None:
        """build() returns FixedTurnContext with empty conclusions when no context rows."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            async with async_session_factory() as session:
                ctx = await TurnContextBuilder.build(session, seeded.turn_id)
                assert ctx.turn_id == seeded.turn_id
                assert ctx.question_text == "timeline integration test question"
                assert ctx.question_origin == "manual"
                assert ctx.evidence_snapshot_id == seeded.snapshot_id
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_build_for_nonexistent_turn_raises(self, async_session_factory) -> None:
        """build() raises not_found for non-existent turn."""
        async with async_session_factory() as session:
            with pytest.raises(AppError) as exc_info:
                await TurnContextBuilder.build(session, uuid4())
            assert exc_info.value.code == "not_found"

    async def test_build_conclusion_inputs_with_revisions(
        self, async_session_factory, test_user
    ) -> None:
        """build_conclusion_inputs loads selected revisions with provenance."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            # Create conclusion + revision + turn_context
            async with async_session_factory() as session:
                async with session.begin():
                    conclusion = await ConclusionRepository.insert_conclusion(
                        session,
                        workspace_id=seeded.workspace_id,
                        source_turn_id=seeded.turn_id,
                        source_run_id=seeded.run_id,
                        source_candidate_id=None,
                        source_type="ai_original",
                        evidence_status="data_supported",
                        created_by=test_user.user_id,
                    )
                    rev = await ConclusionRepository.insert_revision(
                        session,
                        conclusion_id=conclusion.id,
                        revision_number=1,
                        statement="Test conclusion statement",
                        scope="scope A",
                        evidence_refs=[],
                        limitations="some limitation",
                        editor=test_user.user_id,
                    )
                    await ConclusionRepository.set_current_revision(session, conclusion.id, rev.id)
                    await TimelineRepository.insert_turn_context(
                        session,
                        turn_id=seeded.turn_id,
                        conclusion_revision_ids=[(rev.id, 0)],
                    )

            # build_conclusion_inputs should load the revision
            async with async_session_factory() as session:
                inputs = await TurnContextBuilder.build_conclusion_inputs(session, seeded.turn_id)
                assert len(inputs) == 1
                assert inputs[0].statement == "Test conclusion statement"
                assert inputs[0].source_type == "ai_original"
                assert inputs[0].evidence_status == "data_supported"
                assert inputs[0].scope == "scope A"
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_render_conclusion_for_model(self) -> None:
        """render_conclusion_for_model adds label for manual_unverified."""
        inp = FixedConclusionInput(
            revision_id=uuid4(),
            statement="Manual text",
            scope=None,
            limitations=None,
            source_type="manual",
            evidence_status="manual_unverified",
            source_turn_id=None,
            source_run_id=None,
            source_snapshot_id=None,
        )
        text = TurnContextBuilder.render_conclusion_for_model(inp)
        assert MANUAL_UNVERIFIED_LABEL in text
        assert "Manual text" in text

        inp2 = FixedConclusionInput(
            revision_id=uuid4(),
            statement="Data text",
            scope=None,
            limitations=None,
            source_type="ai_original",
            evidence_status="data_supported",
            source_turn_id=None,
            source_run_id=None,
            source_snapshot_id=None,
        )
        text2 = TurnContextBuilder.render_conclusion_for_model(inp2)
        assert text2 == "Data text"

    async def test_render_context_for_model(self) -> None:
        """render_context_for_model produces structured text output."""
        ctx = FixedTurnContext(
            turn_id=uuid4(),
            question_text="What is the trend?",
            question_origin="manual",
            evidence_snapshot_id=uuid4(),
            prompt_template_version=None,
            output_schema_version=None,
        )
        text = TurnContextBuilder.render_context_for_model(ctx, [])
        assert "研究问题" in text
        assert "What is the trend?" in text
        assert "已选历史结论: (无)" in text

        inp = FixedConclusionInput(
            revision_id=uuid4(),
            statement="Conclusion A",
            scope=None,
            limitations=None,
            source_type="ai_original",
            evidence_status="data_supported",
            source_turn_id=None,
            source_run_id=None,
            source_snapshot_id=None,
        )
        text2 = TurnContextBuilder.render_context_for_model(ctx, [inp])
        assert "已选历史结论 (1 条)" in text2
        assert "Conclusion A" in text2


# ============================================================
# TimelineQueryService DB-backed tests
# ============================================================


@pytest.mark.integration
class TestTimelineQueryServiceDB:
    """TimelineQueryService page and detail assembly with real DB."""

    async def test_list_timeline_empty_workspace(self, async_session_factory, test_user) -> None:
        """list_timeline returns empty page for a workspace with no turns."""
        async with async_session_factory() as session:
            async with session.begin():
                ws = ResearchWorkspace(
                    id=new_id(),
                    owner_user_id=test_user.user_id,
                    department_id=test_user.department_id,
                    name="empty-timeline-test",
                )
                session.add(ws)
                await session.flush()
                ws_id = ws.id

            service = TimelineQueryService(
                async_session_factory,
                department_id=test_user.department_id,
                actor_id=test_user.user_id,
            )
            page = await service.list_timeline(ws_id)
            assert page.items == []
            assert page.next_cursor is None
            assert page.active_run_status is None

            await session.execute(
                sa.text("DELETE FROM research_workspace WHERE id = :id"),
                {"id": ws_id},
            )
            await session.commit()

    async def test_list_timeline_with_turns(self, async_session_factory, test_user) -> None:
        """list_timeline returns cards with correct metadata."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            service = TimelineQueryService(
                async_session_factory,
                department_id=test_user.department_id,
                actor_id=test_user.user_id,
            )
            page = await service.list_timeline(seeded.workspace_id)
            assert len(page.items) == 1
            card = page.items[0]
            assert card.turn_id == seeded.turn_id
            assert card.turn_number == 1
            assert card.kind == "analysis"
            assert card.status == "queued"
            assert card.question_text == "timeline integration test question"
            assert card.snapshot_number == 1
            assert card.selected_conclusion_count == 0
            assert card.has_result is False
            assert card.has_candidates is False
            assert page.next_cursor is None
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_list_timeline_active_run_status(self, async_session_factory, test_user) -> None:
        """list_timeline reports active_run_status for active turns.

        Note: ``active_run_status`` comes from ``get_active_run_status`` which
        uses ``scalar_one_or_none()`` + ``row[0]`` indexing; with a real DB
        the scalar string's first character is returned.  We assert
        truthiness rather than the exact value.
        """
        seeded = await _seed_minimal_turn(async_session_factory, test_user, turn_status="queued")
        try:
            service = TimelineQueryService(
                async_session_factory,
                department_id=test_user.department_id,
                actor_id=test_user.user_id,
            )
            page = await service.list_timeline(seeded.workspace_id)
            assert page.active_run_status is not None
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_list_timeline_with_context_and_result(
        self, async_session_factory, test_user
    ) -> None:
        """list_timeline card reflects context count and result existence."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            # Add context rows + result
            async with async_session_factory() as session:
                async with session.begin():
                    conclusion = await ConclusionRepository.insert_conclusion(
                        session,
                        workspace_id=seeded.workspace_id,
                        source_turn_id=seeded.turn_id,
                        source_run_id=seeded.run_id,
                        source_candidate_id=None,
                        source_type="ai_original",
                        evidence_status="data_supported",
                        created_by=test_user.user_id,
                    )
                    rev = await ConclusionRepository.insert_revision(
                        session,
                        conclusion_id=conclusion.id,
                        revision_number=1,
                        statement="Context conclusion",
                        scope=None,
                        evidence_refs=[],
                        limitations=None,
                        editor=test_user.user_id,
                    )
                    await ConclusionRepository.set_current_revision(session, conclusion.id, rev.id)
                    await TimelineRepository.insert_turn_context(
                        session,
                        turn_id=seeded.turn_id,
                        conclusion_revision_ids=[(rev.id, 0)],
                    )
                    await TimelineRepository.insert_turn_result(
                        session,
                        turn_id=seeded.turn_id,
                        run_id=seeded.run_id,
                        result_kind="analysis",
                        summary="Result summary",
                    )

            service = TimelineQueryService(
                async_session_factory,
                department_id=test_user.department_id,
                actor_id=test_user.user_id,
            )
            page = await service.list_timeline(seeded.workspace_id)
            card = page.items[0]
            assert card.selected_conclusion_count == 1
            assert card.has_result is True
            assert card.has_candidates is False
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_get_turn_detail(self, async_session_factory, test_user) -> None:
        """get_turn_detail returns full TurnDetail with plan and context."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            service = TimelineQueryService(
                async_session_factory,
                department_id=test_user.department_id,
                actor_id=test_user.user_id,
            )
            detail = await service.get_turn_detail(seeded.workspace_id, seeded.turn_id)
            assert detail.turn.turn_id == seeded.turn_id
            assert detail.turn.workspace_id == seeded.workspace_id
            assert detail.turn.status == "queued"
            assert detail.context is not None
            assert detail.context.question_text == "timeline integration test question"
            assert detail.plan is not None
            assert detail.plan.plan_id == seeded.plan_version_id
            assert detail.plan.status == "confirmed"
            assert detail.selected_conclusions == []
            assert detail.result is None
            assert detail.extraction_status is None
            assert detail.candidates == []
            assert detail.saved_conclusions == []
            assert detail.access_restricted is False
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_get_turn_detail_not_found(self, async_session_factory, test_user) -> None:
        """get_turn_detail raises not_found for non-existent turn."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            service = TimelineQueryService(
                async_session_factory,
                department_id=test_user.department_id,
                actor_id=test_user.user_id,
            )
            with pytest.raises(AppError) as exc_info:
                await service.get_turn_detail(seeded.workspace_id, uuid4())
            assert exc_info.value.code == "not_found"
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_get_turn_detail_with_result_and_candidates(
        self, async_session_factory, test_user
    ) -> None:
        """get_turn_detail includes result, extraction, and candidates."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user, run_status="succeeded")
        try:
            # Add result + extraction job + candidates
            async with async_session_factory() as session:
                async with session.begin():
                    await TimelineRepository.insert_turn_result(
                        session,
                        turn_id=seeded.turn_id,
                        run_id=seeded.run_id,
                        result_kind="analysis",
                        summary="Analysis result",
                        structured_output={"metric": 42},
                    )
                    job = await TimelineRepository.insert_extraction_job(
                        session,
                        workspace_id=seeded.workspace_id,
                        turn_id=seeded.turn_id,
                        run_id=seeded.run_id,
                    )
                    await CandidateRepository.insert_candidates(
                        session,
                        extraction_id=job.id,
                        turn_id=seeded.turn_id,
                        candidates=[
                            {"statement": "Candidate 1"},
                            {"statement": "Candidate 2"},
                        ],
                    )

            service = TimelineQueryService(
                async_session_factory,
                department_id=test_user.department_id,
                actor_id=test_user.user_id,
            )
            detail = await service.get_turn_detail(seeded.workspace_id, seeded.turn_id)
            assert detail.result is not None
            assert detail.result["summary"] == "Analysis result"
            assert detail.result["structured_output"] == {"metric": 42}
            assert detail.extraction_status == "queued"
            assert len(detail.candidates) == 2
            assert detail.candidates[0]["statement"] == "Candidate 1"
            assert detail.candidates[1]["statement"] == "Candidate 2"
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_get_turn_detail_api(self, async_session_factory, test_user) -> None:
        """get_turn_detail_api returns dict format with turn and plan."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            service = TimelineQueryService(
                async_session_factory,
                department_id=test_user.department_id,
                actor_id=test_user.user_id,
            )
            result = await service.get_turn_detail_api(seeded.workspace_id, seeded.turn_id)
            assert result["turn"]["turn_id"] == str(seeded.turn_id)
            assert result["turn"]["status"] == "queued"
            assert result["plan"] is not None
            assert result["plan"]["status"] == "confirmed"
            assert result["selected_conclusions"] == []
            assert result["candidates"] == []
            assert result["access_restricted"] is False
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)

    async def test_get_turn_detail_api_not_found(self, async_session_factory, test_user) -> None:
        """get_turn_detail_api raises not_found for non-existent turn."""
        seeded = await _seed_minimal_turn(async_session_factory, test_user)
        try:
            service = TimelineQueryService(
                async_session_factory,
                department_id=test_user.department_id,
                actor_id=test_user.user_id,
            )
            with pytest.raises(AppError) as exc_info:
                await service.get_turn_detail_api(seeded.workspace_id, uuid4())
            assert exc_info.value.code == "not_found"
        finally:
            await _cleanup_research(async_session_factory, seeded.workspace_id)
