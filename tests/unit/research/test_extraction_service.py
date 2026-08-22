"""Tests for CandidateExtractionService: enqueue / execute / retry (mock, no DB)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.research.timeline import extraction_service as mod
from packages.research.timeline.extraction_service import (
    MAX_CANDIDATES,
    CandidateExtractionService,
)


class _CM:
    def __init__(self, session: MagicMock) -> None:
        self._session = session

    async def __aenter__(self) -> MagicMock:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _factory(session: MagicMock):
    cm = _CM(session)
    return lambda: cm


def _make_service(session: MagicMock, gateway: object | None = None) -> CandidateExtractionService:
    return CandidateExtractionService(_factory(session), model_gateway=gateway)


def _fake_job(status: str = "queued", turn_id: uuid4 | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), status=status, turn_id=turn_id or uuid4(), run_id=uuid4()
    )


class TestEnqueueForCompletedRun:
    async def test_idempotent_returns_existing(self) -> None:
        session = MagicMock()
        existing = _fake_job(status="succeeded")
        with patch.object(
            mod.TimelineRepository, "get_extraction_by_run",
            new=AsyncMock(return_value=existing),
        ) as get_ext:
            ref = await CandidateExtractionService.enqueue_for_completed_run(session, uuid4())
        assert ref.extraction_id == existing.id
        assert ref.status == "succeeded"
        get_ext.assert_awaited_once()

    async def test_run_not_found(self) -> None:
        session = MagicMock()
        session.get = AsyncMock(return_value=None)
        with patch.object(
            mod.TimelineRepository, "get_extraction_by_run", new=AsyncMock(return_value=None)
        ):
            with pytest.raises(AppError) as exc_info:
                await CandidateExtractionService.enqueue_for_completed_run(session, uuid4())
        assert exc_info.value.code == "not_found"

    async def test_run_invalid_status(self) -> None:
        session = MagicMock()
        run = SimpleNamespace(status="running")
        session.get = AsyncMock(return_value=run)
        with patch.object(
            mod.TimelineRepository, "get_extraction_by_run", new=AsyncMock(return_value=None)
        ):
            with pytest.raises(AppError) as exc_info:
                await CandidateExtractionService.enqueue_for_completed_run(session, uuid4())
        assert exc_info.value.code == "state_conflict"

    async def test_run_without_turn_id(self) -> None:
        session = MagicMock()
        run = SimpleNamespace(status="succeeded", turn_id=None)
        session.get = AsyncMock(return_value=run)
        with patch.object(
            mod.TimelineRepository, "get_extraction_by_run", new=AsyncMock(return_value=None)
        ):
            with pytest.raises(AppError) as exc_info:
                await CandidateExtractionService.enqueue_for_completed_run(session, uuid4())
        assert exc_info.value.code == "state_conflict"

    async def test_success_inserts_job(self) -> None:
        session = MagicMock()
        run_id = uuid4()
        run = SimpleNamespace(id=run_id, status="succeeded", turn_id=uuid4(), workspace_id=uuid4())
        job = _fake_job()
        session.get = AsyncMock(return_value=run)
        with (
            patch.object(
                mod.TimelineRepository, "get_extraction_by_run",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                mod.TimelineRepository, "insert_extraction_job",
                new=AsyncMock(return_value=job),
            ) as insert,
        ):
            ref = await CandidateExtractionService.enqueue_for_completed_run(session, run_id)
        assert ref.extraction_id == job.id
        assert ref.status == "queued"
        insert.assert_awaited_once()


class TestExecute:
    async def test_job_not_found(self) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        service = _make_service(session)
        with patch.object(
            mod.TimelineRepository, "get_extraction_job", new=AsyncMock(return_value=None)
        ):
            with pytest.raises(AppError) as exc_info:
                await service.execute(uuid4())
        assert exc_info.value.code == "not_found"

    async def test_terminal_returns_current(self) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        job = _fake_job(status="succeeded")
        service = _make_service(session)
        with patch.object(
            mod.TimelineRepository, "get_extraction_job", new=AsyncMock(return_value=job)
        ):
            ref = await service.execute(job.id)
        assert ref.status == "succeeded"

    async def test_cas_conflict_returns_current(self) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        job = _fake_job(status="queued")
        service = _make_service(session)
        with (
            patch.object(
                mod.TimelineRepository, "get_extraction_job", new=AsyncMock(return_value=job)
            ),
            patch.object(
                mod.TimelineRepository, "update_extraction_status",
                new=AsyncMock(side_effect=AppError(code="x", message="conflict")),
            ),
        ):
            ref = await service.execute(job.id)
        assert ref.status == "queued"

    async def test_no_gateway_zero_candidates(self) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        job = _fake_job(status="queued")
        service = _make_service(session, gateway=None)
        with (
            patch.object(
                mod.TimelineRepository, "get_extraction_job", new=AsyncMock(return_value=job)
            ),
            patch.object(mod.TimelineRepository, "update_extraction_status", new=AsyncMock()),
            patch.object(mod.TimelineRepository, "update_heartbeat", new=AsyncMock()),
            patch.object(
                mod.TimelineRepository, "get_turn_result",
                new=AsyncMock(return_value=SimpleNamespace(summary=None)),
            ),
        ):
            ref = await service.execute(job.id)
        assert ref.status == "succeeded"

    async def test_gateway_string_parses_and_truncates(self) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        job = _fake_job(status="queued")
        import json

        candidates = [{"statement": f"c{i}"} for i in range(MAX_CANDIDATES + 5)]
        gateway = MagicMock()
        gateway.call = AsyncMock(return_value=json.dumps({"candidates": candidates}))
        service = _make_service(session, gateway=gateway)
        with (
            patch.object(
                mod.TimelineRepository, "get_extraction_job", new=AsyncMock(return_value=job)
            ),
            patch.object(mod.TimelineRepository, "update_extraction_status", new=AsyncMock()),
            patch.object(mod.TimelineRepository, "update_heartbeat", new=AsyncMock()),
            patch.object(
                mod.TimelineRepository, "get_turn_result",
                new=AsyncMock(return_value=SimpleNamespace(summary="s")),
            ),
            patch.object(mod.CandidateRepository, "insert_candidates", new=AsyncMock()) as insert,
        ):
            ref = await service.execute(job.id)
        assert ref.status == "succeeded"
        # truncated to MAX_CANDIDATES
        assert insert.await_args.kwargs["candidates"].__len__() == MAX_CANDIDATES

    async def test_gateway_dict_payload(self) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        job = _fake_job(status="queued")
        gateway = MagicMock()
        gateway.call = AsyncMock(return_value={"candidates": [{"statement": "x"}]})
        service = _make_service(session, gateway=gateway)
        with (
            patch.object(
                mod.TimelineRepository, "get_extraction_job", new=AsyncMock(return_value=job)
            ),
            patch.object(mod.TimelineRepository, "update_extraction_status", new=AsyncMock()),
            patch.object(mod.TimelineRepository, "update_heartbeat", new=AsyncMock()),
            patch.object(
                mod.TimelineRepository, "get_turn_result",
                new=AsyncMock(return_value=SimpleNamespace(summary=None)),
            ),
            patch.object(mod.CandidateRepository, "insert_candidates", new=AsyncMock()),
        ):
            ref = await service.execute(job.id)
        assert ref.status == "succeeded"

    async def test_gateway_object_content(self) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        job = _fake_job(status="queued")
        gateway = MagicMock()
        gateway.call = AsyncMock(
            return_value=SimpleNamespace(content="{\"candidates\": []}")
        )
        service = _make_service(session, gateway=gateway)
        with (
            patch.object(
                mod.TimelineRepository, "get_extraction_job", new=AsyncMock(return_value=job)
            ),
            patch.object(mod.TimelineRepository, "update_extraction_status", new=AsyncMock()),
            patch.object(mod.TimelineRepository, "update_heartbeat", new=AsyncMock()),
            patch.object(
                mod.TimelineRepository, "get_turn_result", new=AsyncMock(return_value=None)
            ),
        ):
            ref = await service.execute(job.id)
        assert ref.status == "succeeded"

    async def test_model_call_failure_marks_failed(self) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        job = _fake_job(status="queued")
        gateway = MagicMock()
        gateway.call = AsyncMock(side_effect=RuntimeError("boom"))
        service = _make_service(session, gateway=gateway)
        update_status = AsyncMock()
        with (
            patch.object(
                mod.TimelineRepository, "get_extraction_job", new=AsyncMock(return_value=job)
            ),
            patch.object(mod.TimelineRepository, "update_extraction_status", update_status),
            patch.object(mod.TimelineRepository, "update_heartbeat", new=AsyncMock()),
            patch.object(
                mod.TimelineRepository, "get_turn_result", new=AsyncMock(return_value=None)
            ),
        ):
            ref = await service.execute(job.id)
        assert ref.status == "failed"
        assert update_status.await_count == 2  # queued->running then running->failed
        session.commit.assert_awaited_once()


class TestRetry:
    async def test_job_not_found(self) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        service = _make_service(session)
        with patch.object(
            mod.TimelineRepository, "get_extraction_job", new=AsyncMock(return_value=None)
        ):
            with pytest.raises(AppError) as exc_info:
                await service.retry(uuid4())
        assert exc_info.value.code == "not_found"

    async def test_cannot_retry(self) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        job = _fake_job(status="succeeded")
        service = _make_service(session)
        with patch.object(
            mod.TimelineRepository, "get_extraction_job", new=AsyncMock(return_value=job)
        ):
            with pytest.raises(AppError) as exc_info:
                await service.retry(job.id)
        assert exc_info.value.code == "state_conflict"

    async def test_retry_success(self) -> None:
        session = MagicMock()
        session.commit = AsyncMock()
        job = _fake_job(status="failed")
        job.attempt = 2
        service = _make_service(session)
        update = AsyncMock()
        with (
            patch.object(
                mod.TimelineRepository, "get_extraction_job", new=AsyncMock(return_value=job)
            ),
            patch.object(mod.TimelineRepository, "update_extraction_status", update),
        ):
            ref = await service.retry(job.id)
        assert ref.status == "queued"
        update.assert_awaited_once()
        session.commit.assert_awaited_once()
