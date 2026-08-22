"""Flow tests for ConclusionService async methods (mock sessions, no DB).

Complements test_conclusion_service.py (which covers source-type derivation
and command validation) by exercising the actual async service methods.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.research.timeline import conclusion_service as mod
from packages.research.timeline.conclusion_service import ConclusionService
from packages.research.timeline.contracts import (
    CandidateSelection,
    CreateManualConclusionCommand,
    ReviseConclusionCommand,
    SaveCandidatesCommand,
)

_AUTO = object()


class _CM:
    def __init__(self, session: MagicMock) -> None:
        self._session = session

    async def __aenter__(self) -> MagicMock:
        return self._session

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _make_service(
    monkeypatch: pytest.MonkeyPatch,
    session: MagicMock,
    actor_id: object | None = _AUTO,
) -> ConclusionService:
    actor = uuid4() if actor_id is _AUTO else actor_id
    service = ConclusionService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=actor,  # type: ignore[arg-type]
    )

    @asynccontextmanager
    async def _scoped(self: ConclusionService):  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr(ConclusionService, "_scoped_session", _scoped)
    cm = _CM(session)
    service._factory = lambda: cm  # type: ignore[method-assign]
    return service


def _patch_repos(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    candidate_repo = MagicMock()
    concl_repo = MagicMock()
    audit = MagicMock()
    audit.record = AsyncMock()
    monkeypatch.setattr(mod, "CandidateRepository", candidate_repo)
    monkeypatch.setattr(mod, "ConclusionRepository", concl_repo)
    monkeypatch.setattr(mod, "AuditRecorder", audit)
    return candidate_repo, concl_repo, audit


def _candidate(status: str = "pending", **kwargs: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "id": uuid4(),
        "status": status,
        "saved_conclusion_id": None,
        "statement": "原始陈述",
        "scope": None,
        "limitations": None,
        "turn_id": uuid4(),
        "evidence_refs": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _conclusion(**kwargs: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "id": uuid4(),
        "workspace_id": uuid4(),
        "source_type": "ai_original",
        "evidence_status": "data_supported",
        "status": "active",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _revision() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), revision_number=1, statement="s", evidence_refs=[])


class TestRequireActor:
    def test_actor_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _make_service(monkeypatch, MagicMock(), actor_id=None)
        with pytest.raises(AppError) as exc_info:
            service._require_actor()
        assert exc_info.value.code == "forbidden"


class TestSaveCandidates:
    async def test_candidate_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        cand_repo, _, _ = _patch_repos(monkeypatch)
        cand_repo.get_candidate = AsyncMock(return_value=None)

        cmd = SaveCandidatesCommand(
            workspace_id=uuid4(),
            turn_id=uuid4(),
            selections=(CandidateSelection(candidate_id=uuid4()),),
            idempotency_key="k",
        )
        with pytest.raises(AppError) as exc_info:
            await service.save_candidates(cmd)
        assert exc_info.value.code == "not_found"

    async def test_rejected_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        cand_repo, _, _ = _patch_repos(monkeypatch)
        candidate = _candidate(status="rejected")
        cand_repo.get_candidate = AsyncMock(return_value=candidate)

        cmd = SaveCandidatesCommand(
            workspace_id=uuid4(),
            turn_id=uuid4(),
            selections=(CandidateSelection(candidate_id=candidate.id),),
            idempotency_key="k",
        )
        with pytest.raises(AppError) as exc_info:
            await service.save_candidates(cmd)
        assert exc_info.value.code == "state_conflict"

    async def test_already_saved_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        cand_repo, concl_repo, _ = _patch_repos(monkeypatch)
        cid = uuid4()
        candidate = _candidate(status="saved", saved_conclusion_id=cid)
        cand_repo.get_candidate = AsyncMock(return_value=candidate)
        concl_repo.get_conclusion = AsyncMock(return_value=_conclusion(id=cid))
        concl_repo.get_latest_revision = AsyncMock(return_value=_revision())
        concl_repo.insert_conclusion = AsyncMock()

        cmd = SaveCandidatesCommand(
            workspace_id=uuid4(),
            turn_id=uuid4(),
            selections=(CandidateSelection(candidate_id=candidate.id),),
            idempotency_key="k",
        )
        results = await service.save_candidates(cmd)
        assert len(results) == 1
        concl_repo.insert_conclusion.assert_not_called()

    async def test_success_creates_conclusion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        cand_repo, concl_repo, audit = _patch_repos(monkeypatch)
        candidate = _candidate()
        cand_repo.get_candidate = AsyncMock(return_value=candidate)
        cand_repo.update_candidate_status = AsyncMock()
        concl = _conclusion()
        rev = _revision()
        concl_repo.insert_conclusion = AsyncMock(return_value=concl)
        concl_repo.insert_revision = AsyncMock(return_value=rev)
        concl_repo.set_current_revision = AsyncMock()

        cmd = SaveCandidatesCommand(
            workspace_id=uuid4(),
            turn_id=uuid4(),
            selections=(CandidateSelection(candidate_id=candidate.id),),
            idempotency_key="k",
        )
        results = await service.save_candidates(cmd)
        assert len(results) == 1
        assert results[0].conclusion_id == concl.id
        concl_repo.insert_conclusion.assert_awaited_once()
        concl_repo.insert_revision.assert_awaited_once()
        cand_repo.update_candidate_status.assert_awaited_once()
        audit.record.assert_awaited_once()


class TestRejectCandidate:
    async def test_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        cand_repo, _, _ = _patch_repos(monkeypatch)
        cand_repo.get_candidate = AsyncMock(return_value=None)
        with pytest.raises(AppError):
            await service.reject_candidate(uuid4(), uuid4())

    async def test_non_pending_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        cand_repo, _, _ = _patch_repos(monkeypatch)
        cand_repo.get_candidate = AsyncMock(return_value=_candidate(status="saved"))
        with pytest.raises(AppError):
            await service.reject_candidate(uuid4(), uuid4())

    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        cand_repo, _, audit = _patch_repos(monkeypatch)
        candidate = _candidate()
        cand_repo.get_candidate = AsyncMock(return_value=candidate)
        cand_repo.update_candidate_status = AsyncMock()
        await service.reject_candidate(uuid4(), candidate.id)
        cand_repo.update_candidate_status.assert_awaited_once()
        audit.record.assert_awaited_once()


class TestCreateManual:
    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        _, concl_repo, audit = _patch_repos(monkeypatch)
        concl = _conclusion(source_type="manual", evidence_status="manual_unverified")
        rev = _revision()
        concl_repo.insert_conclusion = AsyncMock(return_value=concl)
        concl_repo.insert_revision = AsyncMock(return_value=rev)
        concl_repo.set_current_revision = AsyncMock()

        cmd = CreateManualConclusionCommand(
            workspace_id=uuid4(), statement="手工结论", idempotency_key="k"
        )
        result = await service.create_manual(cmd)
        assert result.source_type == "manual"
        assert result.evidence_status == "manual_unverified"
        audit.record.assert_awaited_once()


class TestRevise:
    async def test_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        _, concl_repo, _ = _patch_repos(monkeypatch)
        concl_repo.get_conclusion = AsyncMock(return_value=None)
        cmd = ReviseConclusionCommand(
            workspace_id=uuid4(),
            conclusion_id=uuid4(),
            statement="新",
            expected_lock_version=0,
        )
        with pytest.raises(AppError):
            await service.revise(cmd)

    async def test_foreign_workspace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        _, concl_repo, _ = _patch_repos(monkeypatch)
        concl_repo.get_conclusion = AsyncMock(return_value=_conclusion(workspace_id=uuid4()))
        cmd = ReviseConclusionCommand(
            workspace_id=uuid4(),
            conclusion_id=uuid4(),
            statement="新",
            expected_lock_version=0,
        )
        with pytest.raises(AppError):
            await service.revise(cmd)

    async def test_success_with_existing_revision(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        _, concl_repo, audit = _patch_repos(monkeypatch)
        wid = uuid4()
        concl = _conclusion(workspace_id=wid)
        concl_repo.get_conclusion = AsyncMock(return_value=concl)
        concl_repo.update_conclusion_lock = AsyncMock()
        latest = SimpleNamespace(revision_number=2, evidence_refs=["r"])
        concl_repo.get_latest_revision = AsyncMock(return_value=latest)
        rev = _revision()
        concl_repo.insert_revision = AsyncMock(return_value=rev)
        concl_repo.set_current_revision = AsyncMock()

        cmd = ReviseConclusionCommand(
            workspace_id=wid,
            conclusion_id=concl.id,
            statement="新",
            expected_lock_version=0,
        )
        result = await service.revise(cmd)
        assert result.revision_number == 3
        audit.record.assert_awaited_once()

    async def test_success_without_latest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        _, concl_repo, _ = _patch_repos(monkeypatch)
        wid = uuid4()
        concl = _conclusion(workspace_id=wid)
        concl_repo.get_conclusion = AsyncMock(return_value=concl)
        concl_repo.update_conclusion_lock = AsyncMock()
        concl_repo.get_latest_revision = AsyncMock(return_value=None)
        rev = _revision()
        concl_repo.insert_revision = AsyncMock(return_value=rev)
        concl_repo.set_current_revision = AsyncMock()

        cmd = ReviseConclusionCommand(
            workspace_id=wid,
            conclusion_id=concl.id,
            statement="新",
            expected_lock_version=0,
        )
        result = await service.revise(cmd)
        assert result.revision_number == 1


class TestArchive:
    async def test_not_found_or_foreign(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        _, concl_repo, _ = _patch_repos(monkeypatch)
        concl_repo.get_conclusion = AsyncMock(return_value=None)
        with pytest.raises(AppError):
            await service.archive(uuid4(), uuid4(), 0)

    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        _, concl_repo, audit = _patch_repos(monkeypatch)
        wid = uuid4()
        concl_repo.get_conclusion = AsyncMock(return_value=_conclusion(workspace_id=wid))
        concl_repo.archive_conclusion = AsyncMock()
        await service.archive(wid, uuid4(), 0)
        concl_repo.archive_conclusion.assert_awaited_once()
        audit.record.assert_awaited_once()


class TestSaveFromBlock:
    async def test_empty_statement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        service = _make_service(monkeypatch, session)
        with pytest.raises(AppError, match="不能为空"):
            await service.save_from_block(uuid4(), uuid4(), "  ")

    async def test_turn_foreign(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        session.get = AsyncMock(return_value=None)
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        service = _make_service(monkeypatch, session)
        with pytest.raises(AppError):
            await service.save_from_block(uuid4(), uuid4(), "语句")

    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        turn = SimpleNamespace(id=uuid4(), workspace_id=uuid4())
        session.get = AsyncMock(return_value=turn)
        service = _make_service(monkeypatch, session)
        result = await service.save_from_block(
            turn.workspace_id, turn.id, "语句", block_type="table"
        )
        assert result["status"] == "saved"
        assert result["statement"] == "语句"


class TestListConclusions:
    async def test_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        result = MagicMock()
        result.scalars.return_value = []
        session.execute = AsyncMock(return_value=result)
        service = _make_service(monkeypatch, session)
        out = await service.list_conclusions(uuid4())
        assert out == {"items": []}

    async def test_with_revision(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        rev = SimpleNamespace(revision_number=5, statement="s")
        concl = SimpleNamespace(
            id=uuid4(),
            workspace_id=uuid4(),
            source_type="manual",
            evidence_status="manual_unverified",
            status="active",
            current_revision_id=uuid4(),
        )
        result = MagicMock()
        result.scalars.return_value = [concl]
        session.execute = AsyncMock(return_value=result)
        session.get = AsyncMock(return_value=rev)
        service = _make_service(monkeypatch, session)
        out = await service.list_conclusions(concl.workspace_id)
        assert len(out["items"]) == 1
        assert out["items"][0]["revision_number"] == 5


class TestDeleteConclusion:
    async def test_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()
        service = _make_service(monkeypatch, session)
        with pytest.raises(AppError):
            await service.delete_conclusion(uuid4(), uuid4())

    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = SimpleNamespace(id=uuid4(), status="active")
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()
        service = _make_service(monkeypatch, session)
        out = await service.delete_conclusion(uuid4(), uuid4())
        assert out["status"] == "archived"
