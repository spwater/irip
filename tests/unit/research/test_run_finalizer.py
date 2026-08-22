"""Tests for TimelineRunFinalizer: atomic run completion and failure.

Covers the CAS status transitions, TurnResult idempotency, Turn status
transition, and CandidateExtractionJob creation + outbox enqueue — all
through mocked sessions and repositories (no real DB required).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.research.execution.entities_trusted import ResearchAnalysisRun
from packages.research.timeline.entities import ResearchTurn
from packages.research.timeline.run_finalizer import TimelineRunFinalizer


def _make_finalizer(
    monkeypatch: pytest.MonkeyPatch,
    session: MagicMock,
    actor_id: uuid4 | None = None,
) -> TimelineRunFinalizer:
    """Build a finalizer whose ``_scoped_session`` yields the given mock session."""
    finalizer = TimelineRunFinalizer(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=actor_id or uuid4(),
    )

    @asynccontextmanager
    async def _scoped(self: TimelineRunFinalizer):  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr(TimelineRunFinalizer, "_scoped_session", _scoped)
    return finalizer


def _make_session(
    run: object | None = None,
    turn: object | None = None,
    existing_result: object | None = None,
) -> MagicMock:
    """Build a mock session whose ``get`` dispatches by entity type."""
    session = MagicMock()

    async def get_side_effect(entity: type, _ident: object) -> object | None:
        if entity is ResearchAnalysisRun:
            return run
        if entity is ResearchTurn:
            return turn
        return None

    session.get = AsyncMock(side_effect=get_side_effect)

    result = MagicMock()
    result.scalar_one_or_none.return_value = existing_result
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


class TestComplete:
    """Branch coverage for ``TimelineRunFinalizer.complete``."""

    async def test_run_not_found_raises_async(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session(run=None)
        finalizer = _make_finalizer(monkeypatch, session)

        with pytest.raises(ValueError, match="not found"):
            await finalizer.complete(uuid4(), uuid4(), uuid4(), "analysis")

    async def test_idempotent_skip_when_already_succeeded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id, workspace_id, turn_id = uuid4(), uuid4(), uuid4()
        run = SimpleNamespace(id=run_id, status="succeeded")
        session = _make_session(run=run)
        finalizer = _make_finalizer(monkeypatch, session)

        with monkeypatch.context() as m:
            enqueue = AsyncMock()
            m.setattr(
                "packages.research.timeline.run_finalizer.OutboxDispatcher.enqueue", enqueue
            )
            result = await finalizer.complete(run_id, workspace_id, turn_id, "analysis")

        assert result["status"] == "succeeded"
        assert result["run_id"] == str(run_id)
        session.add.assert_not_called()
        enqueue.assert_not_called()

    async def test_invalid_state_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run_id = uuid4()
        run = SimpleNamespace(id=run_id, status="failed")
        session = _make_session(run=run)
        finalizer = _make_finalizer(monkeypatch, session)

        with pytest.raises(ValueError, match="invalid state"):
            await finalizer.complete(run_id, uuid4(), uuid4(), "analysis")

    async def test_full_success_writes_result_and_enqueues(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id, workspace_id, turn_id = uuid4(), uuid4(), uuid4()
        run = SimpleNamespace(id=run_id, status="queued")
        turn = SimpleNamespace(id=turn_id, status="queued")
        session = _make_session(run=run, turn=turn, existing_result=None)
        finalizer = _make_finalizer(monkeypatch, session)

        extraction_job = MagicMock()
        extraction_job.id = uuid4()

        get_extraction = AsyncMock(return_value=None)
        insert_extraction = AsyncMock(return_value=extraction_job)
        enqueue = AsyncMock()

        monkeypatch.setattr(
            "packages.research.timeline.repository.TimelineRepository.get_extraction_by_run",
            get_extraction,
        )
        monkeypatch.setattr(
            "packages.research.timeline.repository.TimelineRepository.insert_extraction_job",
            insert_extraction,
        )
        monkeypatch.setattr(
            "packages.research.timeline.run_finalizer.OutboxDispatcher.enqueue", enqueue
        )

        result = await finalizer.complete(run_id, workspace_id, turn_id, "analysis text")

        assert result == {
            "run_id": str(run_id),
            "turn_id": str(turn_id),
            "status": "succeeded",
        }
        assert run.status == "succeeded"
        assert turn.status == "succeeded"
        session.add.assert_called_once()
        insert_extraction.assert_awaited_once_with(
            session, workspace_id=workspace_id, turn_id=turn_id, run_id=run_id
        )
        enqueue.assert_awaited_once()
        _, kwargs = enqueue.await_args
        assert kwargs["aggregate_id"] == extraction_job.id
        assert kwargs["aggregate_type"] == "research_candidate_extraction"

    async def test_existing_result_skips_insert(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id, workspace_id, turn_id = uuid4(), uuid4(), uuid4()
        run = SimpleNamespace(id=run_id, status="running")
        turn = SimpleNamespace(id=turn_id, status="running")
        existing = MagicMock()
        session = _make_session(run=run, turn=turn, existing_result=existing)
        finalizer = _make_finalizer(monkeypatch, session)

        extraction_job = MagicMock()
        extraction_job.id = uuid4()
        monkeypatch.setattr(
            "packages.research.timeline.repository.TimelineRepository.get_extraction_by_run",
            AsyncMock(return_value=extraction_job),
        )
        monkeypatch.setattr(
            "packages.research.timeline.repository.TimelineRepository.insert_extraction_job",
            AsyncMock(),
        )
        enqueue = AsyncMock()
        monkeypatch.setattr(
            "packages.research.timeline.run_finalizer.OutboxDispatcher.enqueue", enqueue
        )

        result = await finalizer.complete(run_id, workspace_id, turn_id, "analysis")

        assert result["status"] == "succeeded"
        # result already exists -> no new ResearchTurnResult added
        session.add.assert_not_called()
        # but extraction (existing) is enqueued
        enqueue.assert_awaited_once()

    async def test_turn_missing_is_tolerated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run_id, workspace_id, turn_id = uuid4(), uuid4(), uuid4()
        run = SimpleNamespace(id=run_id, status="queued")
        session = _make_session(run=run, turn=None, existing_result=None)
        finalizer = _make_finalizer(monkeypatch, session)

        extraction_job = MagicMock()
        extraction_job.id = uuid4()
        monkeypatch.setattr(
            "packages.research.timeline.repository.TimelineRepository.get_extraction_by_run",
            AsyncMock(return_value=extraction_job),
        )
        monkeypatch.setattr(
            "packages.research.timeline.repository.TimelineRepository.insert_extraction_job",
            AsyncMock(),
        )
        monkeypatch.setattr(
            "packages.research.timeline.run_finalizer.OutboxDispatcher.enqueue", AsyncMock()
        )

        result = await finalizer.complete(run_id, workspace_id, turn_id, "analysis")
        assert result["status"] == "succeeded"

    async def test_turn_already_succeeded_not_modified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id, workspace_id, turn_id = uuid4(), uuid4(), uuid4()
        run = SimpleNamespace(id=run_id, status="queued")
        turn = SimpleNamespace(id=turn_id, status="run_failed")
        session = _make_session(run=run, turn=turn, existing_result=None)
        finalizer = _make_finalizer(monkeypatch, session)

        extraction_job = MagicMock()
        extraction_job.id = uuid4()
        monkeypatch.setattr(
            "packages.research.timeline.repository.TimelineRepository.get_extraction_by_run",
            AsyncMock(return_value=extraction_job),
        )
        monkeypatch.setattr(
            "packages.research.timeline.run_finalizer.OutboxDispatcher.enqueue", AsyncMock()
        )

        await finalizer.complete(run_id, workspace_id, turn_id, "analysis")
        # turn status is run_failed which is a terminal "already done" marker;
        # finalizer must not overwrite it
        assert turn.status == "run_failed"


class TestFail:
    """Branch coverage for ``TimelineRunFinalizer.fail``."""

    async def test_fail_marks_run_and_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run_id, turn_id = uuid4(), uuid4()
        run = SimpleNamespace(id=run_id, status="running", error_summary=None)
        turn = SimpleNamespace(id=turn_id, status="running")
        session = _make_session(run=run, turn=turn)
        finalizer = _make_finalizer(monkeypatch, session)

        result = await finalizer.fail(run_id, turn_id, "boom" * 200)

        assert result == {"run_id": str(run_id), "turn_id": str(turn_id), "status": "failed"}
        assert run.status == "failed"
        assert run.error_summary == ("boom" * 200)[:500]
        assert turn.status == "run_failed"

    async def test_fail_run_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run_id, turn_id = uuid4(), uuid4()
        session = _make_session(run=None, turn=None)
        finalizer = _make_finalizer(monkeypatch, session)

        result = await finalizer.fail(run_id, turn_id, "err")
        assert result["status"] == "failed"

    async def test_fail_run_already_succeeded_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id, turn_id = uuid4(), uuid4()
        run = SimpleNamespace(id=run_id, status="succeeded", error_summary=None)
        turn = SimpleNamespace(id=turn_id, status="running")
        session = _make_session(run=run, turn=turn)
        finalizer = _make_finalizer(monkeypatch, session)

        await finalizer.fail(run_id, turn_id, "err")
        assert run.status == "succeeded"
        assert run.error_summary is None

    async def test_fail_turn_already_run_failed_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id, turn_id = uuid4(), uuid4()
        run = SimpleNamespace(id=run_id, status="running")
        turn = SimpleNamespace(id=turn_id, status="run_failed")
        session = _make_session(run=run, turn=turn)
        finalizer = _make_finalizer(monkeypatch, session)

        await finalizer.fail(run_id, turn_id, "err")
        assert turn.status == "run_failed"
