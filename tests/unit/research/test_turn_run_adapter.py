"""Tests for turn_run_adapter: submit_run_for_turn and complete_run_for_turn."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.research.timeline import turn_run_adapter
from packages.research.timeline.turn_run_adapter import (
    complete_run_for_turn,
    submit_run_for_turn,
)


def _make_turn(
    status: str = "plan_confirmed", workspace_id: uuid4 | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        workspace_id=workspace_id or uuid4(),
        status=status,
        evidence_snapshot_id=uuid4(),
        created_by=uuid4(),
    )


def _make_plan(status: str = "confirmed", turn_id: uuid4 | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), status=status, turn_id=turn_id)


class TestSubmitRunForTurn:
    async def test_turn_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            turn_run_adapter.TimelineRepository, "get_turn", AsyncMock(return_value=None)
        )
        with pytest.raises(AppError) as exc_info:
            await submit_run_for_turn(MagicMock(), uuid4(), uuid4(), uuid4(), "key")
        assert exc_info.value.code == "not_found"

    async def test_turn_workspace_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = _make_turn()
        monkeypatch.setattr(
            turn_run_adapter.TimelineRepository, "get_turn", AsyncMock(return_value=turn)
        )
        with pytest.raises(AppError) as exc_info:
            await submit_run_for_turn(MagicMock(), uuid4(), turn.id, uuid4(), "key")
        assert exc_info.value.code == "not_found"

    async def test_turn_cannot_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = _make_turn(status="question_draft")
        monkeypatch.setattr(
            turn_run_adapter.TimelineRepository, "get_turn", AsyncMock(return_value=turn)
        )
        with pytest.raises(AppError) as exc_info:
            await submit_run_for_turn(
                MagicMock(), turn.workspace_id, turn.id, uuid4(), "key"
            )
        assert exc_info.value.code == "state_conflict"

    async def test_active_run_conflict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = _make_turn()
        repo = MagicMock()
        repo.get_turn = AsyncMock(return_value=turn)
        repo.get_active_run_status = AsyncMock(return_value="running")
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)

        with pytest.raises(AppError) as exc_info:
            await submit_run_for_turn(
                MagicMock(), turn.workspace_id, turn.id, uuid4(), "key"
            )
        assert exc_info.value.code == "analysis_busy"

    async def test_plan_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = _make_turn()
        repo = MagicMock()
        repo.get_turn = AsyncMock(return_value=turn)
        repo.get_active_run_status = AsyncMock(return_value=None)
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)

        session = MagicMock()
        session.get = AsyncMock(return_value=None)

        with pytest.raises(AppError) as exc_info:
            await submit_run_for_turn(
                session, turn.workspace_id, turn.id, uuid4(), "key"
            )
        assert exc_info.value.code == "not_found"

    async def test_plan_not_for_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = _make_turn()
        plan = _make_plan(status="confirmed", turn_id=uuid4())
        repo = MagicMock()
        repo.get_turn = AsyncMock(return_value=turn)
        repo.get_active_run_status = AsyncMock(return_value=None)
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)

        session = MagicMock()
        session.get = AsyncMock(return_value=plan)

        with pytest.raises(AppError) as exc_info:
            await submit_run_for_turn(
                session, turn.workspace_id, turn.id, plan.id, "key"
            )
        assert exc_info.value.code == "not_found"

    async def test_plan_not_confirmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = _make_turn()
        plan = _make_plan(status="draft", turn_id=turn.id)
        repo = MagicMock()
        repo.get_turn = AsyncMock(return_value=turn)
        repo.get_active_run_status = AsyncMock(return_value=None)
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)

        session = MagicMock()
        session.get = AsyncMock(return_value=plan)

        with pytest.raises(AppError) as exc_info:
            await submit_run_for_turn(
                session, turn.workspace_id, turn.id, plan.id, "key"
            )
        assert exc_info.value.code == "state_conflict"

    async def test_success_from_plan_confirmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = _make_turn(status="plan_confirmed")
        plan = _make_plan(status="confirmed", turn_id=turn.id)
        repo = MagicMock()
        repo.get_turn = AsyncMock(return_value=turn)
        repo.get_active_run_status = AsyncMock(return_value=None)
        repo.update_turn_status = AsyncMock()
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)
        monkeypatch.setattr(
            "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.get_next_run_number",
            AsyncMock(return_value=7),
        )
        new_run_id = uuid4()
        monkeypatch.setattr("packages.common.ids.new_id", lambda: new_run_id)

        session = MagicMock()
        session.get = AsyncMock(return_value=plan)
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        session.execute = AsyncMock(return_value=count_result)
        session.add = MagicMock()
        session.flush = AsyncMock()

        run_id, attempt = await submit_run_for_turn(
            session, turn.workspace_id, turn.id, plan.id, "key"
        )

        assert run_id == new_run_id
        assert attempt == 1
        repo.update_turn_status.assert_awaited_once()
        _, kwargs = repo.update_turn_status.await_args
        assert kwargs["expected_status"] == "plan_confirmed"
        assert kwargs["new_status"] == "queued"
        session.add.assert_called_once()

    async def test_success_from_run_failed_and_attempt_increment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        turn = _make_turn(status="run_failed")
        plan = _make_plan(status="confirmed", turn_id=turn.id)
        repo = MagicMock()
        repo.get_turn = AsyncMock(return_value=turn)
        repo.get_active_run_status = AsyncMock(return_value=None)
        repo.update_turn_status = AsyncMock()
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)
        monkeypatch.setattr(
            "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.get_next_run_number",
            AsyncMock(return_value=3),
        )
        monkeypatch.setattr("packages.common.ids.new_id", lambda: uuid4())

        session = MagicMock()
        session.get = AsyncMock(return_value=plan)
        count_result = MagicMock()
        count_result.scalar_one.return_value = 2
        session.execute = AsyncMock(return_value=count_result)
        session.add = MagicMock()
        session.flush = AsyncMock()

        _run_id, attempt = await submit_run_for_turn(
            session, turn.workspace_id, turn.id, plan.id, "key"
        )

        assert attempt == 3
        _, kwargs = repo.update_turn_status.await_args
        assert kwargs["expected_status"] == "run_failed"


class TestCompleteRunForTurn:
    async def test_run_missing_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        session.get = AsyncMock(return_value=None)
        repo = MagicMock()
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)

        result = await complete_run_for_turn(session, uuid4(), "succeeded")
        assert result is None
        repo.insert_turn_result.assert_not_called()

    async def test_run_without_turn_id_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = SimpleNamespace(id=uuid4(), turn_id=None, workspace_id=uuid4())
        session = MagicMock()
        session.get = AsyncMock(return_value=run)
        repo = MagicMock()
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)

        result = await complete_run_for_turn(session, run.id, "succeeded")
        assert result is None

    async def test_succeeded_transitions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = SimpleNamespace(
            id=uuid4(),
            turn_id=uuid4(),
            workspace_id=uuid4(),
            error_summary=None,
            coverage_summary=None,
        )
        session = MagicMock()
        session.get = AsyncMock(return_value=run)
        repo = MagicMock()
        repo.insert_turn_result = AsyncMock()
        repo.insert_extraction_job = AsyncMock()
        repo.update_turn_status = AsyncMock()
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)

        await complete_run_for_turn(session, run.id, "succeeded")

        _, kwargs = repo.insert_turn_result.await_args
        assert kwargs["result_kind"] == "analysis"
        repo.insert_extraction_job.assert_awaited_once()
        repo.update_turn_status.assert_awaited_once_with(
            session, run.turn_id, expected_status="running", new_status="succeeded"
        )

    async def test_partially_succeeded_uses_partial_kind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = SimpleNamespace(
            id=uuid4(),
            turn_id=uuid4(),
            workspace_id=uuid4(),
            error_summary="e",
            coverage_summary={},
        )
        session = MagicMock()
        session.get = AsyncMock(return_value=run)
        repo = MagicMock()
        repo.insert_turn_result = AsyncMock()
        repo.insert_extraction_job = AsyncMock()
        repo.update_turn_status = AsyncMock()
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)

        await complete_run_for_turn(session, run.id, "partially_succeeded")

        _, kwargs = repo.insert_turn_result.await_args
        assert kwargs["result_kind"] == "partial"

    async def test_failed_transitions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = SimpleNamespace(id=uuid4(), turn_id=uuid4(), workspace_id=uuid4())
        session = MagicMock()
        session.get = AsyncMock(return_value=run)
        repo = MagicMock()
        repo.update_turn_status = AsyncMock()
        repo.insert_turn_result = AsyncMock()
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)

        await complete_run_for_turn(session, run.id, "failed")

        repo.update_turn_status.assert_awaited_once_with(
            session, run.turn_id, expected_status="running", new_status="run_failed"
        )
        repo.insert_turn_result.assert_not_called()

    async def test_cancelled_transitions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = SimpleNamespace(id=uuid4(), turn_id=uuid4(), workspace_id=uuid4())
        session = MagicMock()
        session.get = AsyncMock(return_value=run)
        repo = MagicMock()
        repo.update_turn_status = AsyncMock()
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)

        await complete_run_for_turn(session, run.id, "cancelled")

        repo.update_turn_status.assert_awaited_once_with(
            session, run.turn_id, expected_status="running", new_status="cancelled"
        )

    async def test_unknown_status_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = SimpleNamespace(id=uuid4(), turn_id=uuid4(), workspace_id=uuid4())
        session = MagicMock()
        session.get = AsyncMock(return_value=run)
        repo = MagicMock()
        repo.update_turn_status = AsyncMock()
        repo.insert_turn_result = AsyncMock()
        monkeypatch.setattr(turn_run_adapter, "TimelineRepository", repo)

        await complete_run_for_turn(session, run.id, "queued")

        repo.update_turn_status.assert_not_called()
        repo.insert_turn_result.assert_not_called()
