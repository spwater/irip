"""Tests for timeline contracts: AI schemas, commands and refs."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.research.timeline.contracts import (
    MAX_CONCLUSION_REVISIONS,
    CreateManualConclusionCommand,
    CreateSynthesisTurnCommand,
    CreateTurnCommand,
    RecommendationOutput,
    RecommendedQuestion,
    SynthesisResult,
    SynthesisSection,
)


class TestRecommendationOutput:
    """RecommendationOutput schema boundary tests."""

    @pytest.mark.parametrize("count", [1, 2, 3, 4])
    def test_accepts_one_to_four(self, count: int) -> None:
        output = RecommendationOutput(
            questions=[
                RecommendedQuestion(question=f"问题 {i}", rationale="数据可检验")
                for i in range(count)
            ]
        )
        assert len(output.questions) == count

    def test_rejects_zero(self) -> None:
        with pytest.raises(ValidationError):
            RecommendationOutput(questions=[])

    def test_rejects_five(self) -> None:
        with pytest.raises(ValidationError):
            RecommendationOutput(
                questions=[RecommendedQuestion(question=f"Q{i}", rationale="r") for i in range(5)]
            )

    def test_question_min_length(self) -> None:
        with pytest.raises(ValidationError):
            RecommendedQuestion(question="Q", rationale="r")

    def test_rationale_required(self) -> None:
        with pytest.raises(ValidationError):
            RecommendedQuestion(question="A valid question", rationale="")


class TestSynthesisSection:
    """SynthesisSection cross-field validation."""

    def test_present_with_items(self) -> None:
        section = SynthesisSection(status="present", items=["方向一致"])
        assert section.items == ["方向一致"]

    def test_not_applicable_with_empty(self) -> None:
        section = SynthesisSection(status="not_applicable", items=[])
        assert section.items == []

    def test_present_without_items_raises(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisSection(status="present", items=[])

    def test_not_applicable_with_items_raises(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisSection(status="not_applicable", items=["不应该有"])


class TestSynthesisResult:
    """SynthesisResult full schema."""

    def test_valid_with_all_present(self) -> None:
        result = SynthesisResult(
            summary="两轮分析共同支持温度升高与收率上升有关。",
            agreements=SynthesisSection(status="present", items=["方向一致"]),
            conflicts=SynthesisSection(status="present", items=["批次差异"]),
            limitations=SynthesisSection(status="present", items=["样本少"]),
            new_hypotheses=SynthesisSection(status="present", items=["阈值效应"]),
        )
        assert result.summary

    def test_conflicts_not_applicable(self) -> None:
        result = SynthesisResult(
            summary="无冲突的两轮分析。",
            agreements=SynthesisSection(status="present", items=["一致"]),
            conflicts=SynthesisSection(status="not_applicable", items=[]),
            limitations=SynthesisSection(status="present", items=["样本少"]),
            new_hypotheses=SynthesisSection(status="present", items=["新假设"]),
        )
        assert result.conflicts.items == []

    def test_empty_summary_raises(self) -> None:
        with pytest.raises(ValidationError):
            SynthesisResult(
                summary="",
                agreements=SynthesisSection(status="not_applicable", items=[]),
                conflicts=SynthesisSection(status="not_applicable", items=[]),
                limitations=SynthesisSection(status="not_applicable", items=[]),
                new_hypotheses=SynthesisSection(status="not_applicable", items=[]),
            )


class TestCreateTurnCommand:
    """CreateTurnCommand validation."""

    def test_valid_minimal(self) -> None:
        cmd = CreateTurnCommand(
            workspace_id=uuid4(),
            question_text="哪些批次收率偏低？",
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=(),
            recommendation_item_id=None,
            idempotency_key="turn-001",
        )
        # NFKC normalizes fullwidth ? to halfwidth ?
        assert cmd.question_text == "哪些批次收率偏低?"

    def test_strips_whitespace(self) -> None:
        cmd = CreateTurnCommand(
            workspace_id=uuid4(),
            question_text="  哪些批次收率偏低？  ",
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=(),
            recommendation_item_id=None,
            idempotency_key="turn-002",
        )
        # NFKC normalizes fullwidth ? to halfwidth ?
        assert cmd.question_text == "哪些批次收率偏低?"

    def test_empty_question_raises(self) -> None:
        with pytest.raises(ValueError):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="   ",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(),
                recommendation_item_id=None,
                idempotency_key="turn-003",
            )

    def test_zero_revisions_ok(self) -> None:
        cmd = CreateTurnCommand(
            workspace_id=uuid4(),
            question_text="问题",
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=(),
            recommendation_item_id=None,
            idempotency_key="turn-004",
        )
        assert len(cmd.selected_conclusion_revision_ids) == 0

    def test_twenty_revisions_ok(self) -> None:
        ids = tuple(uuid4() for _ in range(MAX_CONCLUSION_REVISIONS))
        cmd = CreateTurnCommand(
            workspace_id=uuid4(),
            question_text="问题",
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=ids,
            recommendation_item_id=None,
            idempotency_key="turn-005",
        )
        assert len(cmd.selected_conclusion_revision_ids) == MAX_CONCLUSION_REVISIONS

    def test_twenty_one_revisions_raises(self) -> None:
        ids = tuple(uuid4() for _ in range(MAX_CONCLUSION_REVISIONS + 1))
        with pytest.raises(ValueError):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="问题",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=ids,
                recommendation_item_id=None,
                idempotency_key="turn-006",
            )

    def test_duplicate_revisions_raises(self) -> None:
        rid = uuid4()
        with pytest.raises(ValueError):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="问题",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(rid, rid),
                recommendation_item_id=None,
                idempotency_key="turn-007",
            )

    def test_empty_idempotency_key_raises(self) -> None:
        with pytest.raises(ValueError):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="问题",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(),
                recommendation_item_id=None,
                idempotency_key="",
            )

    def test_long_idempotency_key_raises(self) -> None:
        with pytest.raises(ValueError):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="问题",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(),
                recommendation_item_id=None,
                idempotency_key="x" * 129,
            )


class TestCreateSynthesisTurnCommand:
    """CreateSynthesisTurnCommand validation."""

    def test_two_revisions_ok(self) -> None:
        cmd = CreateSynthesisTurnCommand(
            workspace_id=uuid4(),
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=(uuid4(), uuid4()),
            idempotency_key="synth-001",
        )
        assert len(cmd.selected_conclusion_revision_ids) == 2

    def test_one_revision_raises(self) -> None:
        with pytest.raises(ValueError):
            CreateSynthesisTurnCommand(
                workspace_id=uuid4(),
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(uuid4(),),
                idempotency_key="synth-002",
            )

    def test_zero_revisions_raises(self) -> None:
        with pytest.raises(ValueError):
            CreateSynthesisTurnCommand(
                workspace_id=uuid4(),
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(),
                idempotency_key="synth-003",
            )

    def test_twenty_revisions_ok(self) -> None:
        ids = tuple(uuid4() for _ in range(20))
        cmd = CreateSynthesisTurnCommand(
            workspace_id=uuid4(),
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=ids,
            idempotency_key="synth-004",
        )
        assert len(cmd.selected_conclusion_revision_ids) == 20


class TestCreateManualConclusionCommand:
    """Manual conclusion command validation."""

    def test_valid(self) -> None:
        cmd = CreateManualConclusionCommand(
            workspace_id=uuid4(),
            statement="设备清洗可能影响下一批结果",
            limitations="来自操作记录",
            idempotency_key="manual-001",
        )
        assert cmd.statement == "设备清洗可能影响下一批结果"

    def test_empty_statement_raises(self) -> None:
        with pytest.raises(ValueError):
            CreateManualConclusionCommand(
                workspace_id=uuid4(),
                statement="  ",
                idempotency_key="manual-002",
            )
