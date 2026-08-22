"""Tests for AnalysisService: run submission orchestration and AI config load."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.research.timeline import analysis_service as mod
from packages.research.timeline.analysis_service import AnalysisService

_AUTO = object()


def _make_service(
    monkeypatch: pytest.MonkeyPatch,
    session: MagicMock,
    actor_id: object | None = _AUTO,
) -> AnalysisService:
    actor = uuid4() if actor_id is _AUTO else actor_id
    service = AnalysisService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=actor,  # type: ignore[arg-type]
    )

    @asynccontextmanager
    async def _scoped(self: AnalysisService):  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr(AnalysisService, "_scoped_session", _scoped)
    return service


def _make_session(*, plan: object | None = None, attempt_count: int = 0) -> MagicMock:
    session = MagicMock()
    plan_result = MagicMock()
    plan_result.scalar_one_or_none.return_value = plan
    attempt_result = MagicMock()
    attempt_result.scalar_one.return_value = attempt_count
    session.execute = AsyncMock(side_effect=[plan_result, attempt_result])
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


class TestRequireActor:
    def test_actor_missing_raises_forbidden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _make_service(monkeypatch, MagicMock(), actor_id=None)
        with pytest.raises(AppError) as exc_info:
            service._require_actor()
        assert exc_info.value.code == "forbidden"

    def test_actor_present_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        actor = uuid4()
        service = _make_service(monkeypatch, MagicMock(), actor_id=actor)
        assert service._require_actor() == actor


class TestSubmitRun:
    async def test_turn_cannot_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = SimpleNamespace(id=uuid4(), status="question_draft")
        monkeypatch.setattr(
            mod, "require_owned_turn",
            AsyncMock(return_value=turn),
        )
        service = _make_service(monkeypatch, _make_session())

        with pytest.raises(AppError) as exc_info:
            await service.submit_run(uuid4(), turn.id)
        assert exc_info.value.code == "state_conflict"

    async def test_no_confirmed_plan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = SimpleNamespace(id=uuid4(), status="plan_confirmed")
        monkeypatch.setattr(
            mod, "require_owned_turn",
            AsyncMock(return_value=turn),
        )
        service = _make_service(monkeypatch, _make_session(plan=None))

        with pytest.raises(AppError) as exc_info:
            await service.submit_run(uuid4(), turn.id)
        assert exc_info.value.code == "state_conflict"

    async def test_active_run_busy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = SimpleNamespace(id=uuid4(), status="plan_confirmed")
        plan = SimpleNamespace(id=uuid4())
        active_run = SimpleNamespace(id=uuid4())
        monkeypatch.setattr(
            mod, "require_owned_turn",
            AsyncMock(return_value=turn),
        )
        monkeypatch.setattr(
            "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.get_active_run_for_workspace",
            AsyncMock(return_value=active_run),
        )
        service = _make_service(monkeypatch, _make_session(plan=plan))

        with pytest.raises(AppError) as exc_info:
            await service.submit_run(uuid4(), turn.id)
        assert exc_info.value.code == "analysis_busy"

    async def test_submit_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = SimpleNamespace(
            id=uuid4(), status="plan_confirmed", evidence_snapshot_id=uuid4()
        )
        plan = SimpleNamespace(id=uuid4())
        run_id = uuid4()
        monkeypatch.setattr(
            mod, "require_owned_turn",
            AsyncMock(return_value=turn),
        )
        monkeypatch.setattr(
            "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.get_active_run_for_workspace",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.get_next_run_number",
            AsyncMock(return_value=4),
        )
        monkeypatch.setattr("packages.common.ids.new_id", lambda: run_id)
        enqueue = AsyncMock()
        monkeypatch.setattr("packages.jobs.outbox.OutboxDispatcher.enqueue", enqueue)

        session = _make_session(plan=plan, attempt_count=0)
        service = _make_service(monkeypatch, session)

        result = await service.submit_run(uuid4(), turn.id)

        assert result == {"run_id": str(run_id), "turn_id": str(turn.id), "status": "queued"}
        session.add.assert_called_once()
        enqueue.assert_awaited_once()
        assert turn.status == "queued"


class TestLoadAIConfig:
    async def test_no_row_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        result = MagicMock()
        result.first.return_value = None
        session.execute = AsyncMock(return_value=result)
        service = _make_service(monkeypatch, session)

        assert await service._load_ai_config() is None

    async def test_row_decrypts_and_maps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        result = MagicMock()
        result.first.return_value = ("http://x", "encrypted-key", "m", "rm", True)
        session.execute = AsyncMock(return_value=result)
        service = _make_service(monkeypatch, session)

        crypto = MagicMock()
        crypto.decrypt = MagicMock(return_value="decrypted-key")
        monkeypatch.setattr(
            "packages.common.crypto.EnvelopeCrypto.from_env",
            classmethod(lambda cls: crypto),
        )

        config = await service._load_ai_config()
        assert config == {
            "base_url": "http://x",
            "api_key": "decrypted-key",
            "model_name": "m",
            "research_model_name": "rm",
            "research_thinking_enabled": True,
        }
