"""Tests for turn_plan_adapter: generate_plan_for_turn and confirm_plan_for_turn."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.research.timeline import turn_plan_adapter as mod
from packages.research.timeline.contracts import PlanVersionRef
from packages.research.timeline.turn_plan_adapter import (
    confirm_plan_for_turn,
    generate_plan_for_turn,
)


def _make_turn(status: str = "question_draft") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        workspace_id=uuid4(),
        status=status,
        evidence_snapshot_id=uuid4(),
    )


class TestGeneratePlanForTurn:
    async def test_turn_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mod.TimelineRepository, "get_turn", AsyncMock(return_value=None))
        with pytest.raises(AppError) as exc_info:
            await generate_plan_for_turn(MagicMock(), uuid4(), MagicMock())
        assert exc_info.value.code == "not_found"

    async def test_turn_cannot_plan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = _make_turn(status="running")
        monkeypatch.setattr(
            mod.TimelineRepository, "get_turn", AsyncMock(return_value=turn)
        )
        with pytest.raises(AppError) as exc_info:
            await generate_plan_for_turn(MagicMock(), turn.id, MagicMock())
        assert exc_info.value.code == "state_conflict"

    async def test_generates_and_binds_plan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = _make_turn(status="question_draft")
        plan_ref = PlanVersionRef(
            plan_id=uuid4(), turn_id=turn.id, version_number=1, status="confirmed"
        )
        plan_service = MagicMock()
        plan_service.generate_plan = AsyncMock(return_value=plan_ref)

        repo = MagicMock()
        repo.get_turn = AsyncMock(return_value=turn)
        repo.lock_turn_inputs = AsyncMock()
        repo.update_turn_status = AsyncMock()
        monkeypatch.setattr(mod, "TimelineRepository", repo)

        builder = MagicMock()
        builder.build = AsyncMock(return_value=MagicMock())
        builder.build_conclusion_inputs = AsyncMock(return_value=[])
        builder.render_context_for_model = MagicMock(return_value="ctx")
        monkeypatch.setattr(mod, "TurnContextBuilder", builder)

        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock())

        result = await generate_plan_for_turn(session, turn.id, plan_service)

        assert result is plan_ref
        plan_service.generate_plan.assert_awaited_once_with(
            workspace_id=turn.workspace_id, snapshot_id=turn.evidence_snapshot_id
        )
        repo.lock_turn_inputs.assert_awaited_once()
        repo.update_turn_status.assert_awaited_once_with(
            session, turn.id, expected_status="planning", new_status="plan_review"
        )
        session.execute.assert_awaited_once()


class TestConfirmPlanForTurn:
    async def test_plan_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        session.get = AsyncMock(return_value=None)
        with pytest.raises(AppError) as exc_info:
            await confirm_plan_for_turn(session, uuid4(), uuid4())
        assert exc_info.value.code == "not_found"

    async def test_plan_not_for_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        plan = SimpleNamespace(id=uuid4(), turn_id=uuid4(), status="draft", version_number=1)
        session = MagicMock()
        session.get = AsyncMock(return_value=plan)
        with pytest.raises(AppError) as exc_info:
            await confirm_plan_for_turn(session, uuid4(), plan.id)
        assert exc_info.value.code == "not_found"

    async def test_plan_not_draft(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn_id = uuid4()
        plan = SimpleNamespace(id=uuid4(), turn_id=turn_id, status="confirmed", version_number=1)
        session = MagicMock()
        session.get = AsyncMock(return_value=plan)
        with pytest.raises(AppError) as exc_info:
            await confirm_plan_for_turn(session, turn_id, plan.id)
        assert exc_info.value.code == "state_conflict"

    async def test_confirm_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn_id = uuid4()
        plan = SimpleNamespace(id=uuid4(), turn_id=turn_id, status="draft", version_number=3)
        session = MagicMock()
        session.get = AsyncMock(return_value=plan)
        session.execute = AsyncMock(return_value=MagicMock())

        repo = MagicMock()
        repo.update_turn_status = AsyncMock()
        monkeypatch.setattr(mod, "TimelineRepository", repo)

        result = await confirm_plan_for_turn(session, turn_id, plan.id)

        assert result.plan_id == plan.id
        assert result.status == "confirmed"
        assert result.version_number == 3
        repo.update_turn_status.assert_awaited_once_with(
            session, turn_id, expected_status="plan_review", new_status="plan_confirmed"
        )
