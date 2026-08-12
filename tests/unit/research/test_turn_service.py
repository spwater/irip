"""Tests for TurnService: origin derivation, idempotency, and validation."""

from uuid import uuid4

import pytest

from packages.research.timeline.contracts import (
    CreateSynthesisTurnCommand,
    CreateTurnCommand,
)
from packages.research.timeline.turn_service import TurnService


class TestDeriveOrigin:
    """Test question_origin derivation logic."""

    def test_with_recommendation_item(self) -> None:
        assert TurnService._derive_origin(uuid4(), "问题") == "initial_ai"

    def test_without_recommendation_item(self) -> None:
        assert TurnService._derive_origin(None, "问题") == "manual"

    def test_empty_recommendation_item_id(self) -> None:
        # None means manual
        assert TurnService._derive_origin(None, "任何问题") == "manual"


class TestCreateTurnCommandValidation:
    """Test that CreateTurnCommand validates inputs correctly."""

    def test_zero_revisions_ok(self) -> None:
        cmd = CreateTurnCommand(
            workspace_id=uuid4(),
            question_text="问题",
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=(),
            recommendation_item_id=None,
            idempotency_key="t1",
        )
        assert len(cmd.selected_conclusion_revision_ids) == 0

    def test_twenty_revisions_ok(self) -> None:
        ids = tuple(uuid4() for _ in range(20))
        cmd = CreateTurnCommand(
            workspace_id=uuid4(),
            question_text="问题",
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=ids,
            recommendation_item_id=None,
            idempotency_key="t2",
        )
        assert len(cmd.selected_conclusion_revision_ids) == 20

    def test_twenty_one_revisions_rejected(self) -> None:
        ids = tuple(uuid4() for _ in range(21))
        with pytest.raises(ValueError, match="at most"):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="问题",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=ids,
                recommendation_item_id=None,
                idempotency_key="t3",
            )

    def test_duplicate_revisions_rejected(self) -> None:
        rid = uuid4()
        with pytest.raises(ValueError, match="unique"):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="问题",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(rid, rid),
                recommendation_item_id=None,
                idempotency_key="t4",
            )

    def test_empty_idempotency_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="idempotency_key"):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="问题",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(),
                recommendation_item_id=None,
                idempotency_key="",
            )

    def test_long_idempotency_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="idempotency_key"):
            CreateTurnCommand(
                workspace_id=uuid4(),
                question_text="问题",
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(),
                recommendation_item_id=None,
                idempotency_key="x" * 129,
            )


class TestCreateSynthesisTurnCommandValidation:
    """Test synthesis command validation."""

    def test_two_revisions_ok(self) -> None:
        cmd = CreateSynthesisTurnCommand(
            workspace_id=uuid4(),
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=(uuid4(), uuid4()),
            idempotency_key="s1",
        )
        assert len(cmd.selected_conclusion_revision_ids) == 2

    def test_one_revision_rejected(self) -> None:
        with pytest.raises(ValueError, match="2-20"):
            CreateSynthesisTurnCommand(
                workspace_id=uuid4(),
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(uuid4(),),
                idempotency_key="s2",
            )

    def test_zero_revisions_rejected(self) -> None:
        with pytest.raises(ValueError, match="2-20"):
            CreateSynthesisTurnCommand(
                workspace_id=uuid4(),
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=(),
                idempotency_key="s3",
            )

    def test_twenty_revisions_ok(self) -> None:
        ids = tuple(uuid4() for _ in range(20))
        cmd = CreateSynthesisTurnCommand(
            workspace_id=uuid4(),
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=ids,
            idempotency_key="s4",
        )
        assert len(cmd.selected_conclusion_revision_ids) == 20

    def test_twenty_one_revisions_rejected(self) -> None:
        ids = tuple(uuid4() for _ in range(21))
        with pytest.raises(ValueError, match="2-20"):
            CreateSynthesisTurnCommand(
                workspace_id=uuid4(),
                evidence_snapshot_id=uuid4(),
                selected_conclusion_revision_ids=ids,
                idempotency_key="s5",
            )
