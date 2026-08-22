"""Tests for TurnService async flows (create/plan/confirm/delete).

Exercises ownership checks, idempotency, context insertion, auditing, and
outbox enqueue through mocked sessions and repositories (no real DB).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.research.entities import ResearchEvidenceSnapshot
from packages.research.timeline import turn_service as mod
from packages.research.timeline.contracts import (
    CreateSynthesisTurnCommand,
    CreateTurnCommand,
)
from packages.research.timeline.entities import (
    ResearchConclusion,
    ResearchConclusionRevision,
)
from packages.research.timeline.turn_service import TurnService

#: Sentinel meaning "generate a random actor id" (distinct from explicit ``None``).
_AUTO = object()


def _make_service(
    monkeypatch: pytest.MonkeyPatch,
    session: MagicMock,
    actor_id: uuid4 | None | object = _AUTO,
) -> TurnService:
    """Build a TurnService whose ``_scoped_session`` yields the mock session."""
    actor = uuid4() if actor_id is _AUTO else actor_id
    service = TurnService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=actor,  # type: ignore[arg-type]
    )

    @asynccontextmanager
    async def _scoped(self: TurnService):  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr(TurnService, "_scoped_session", _scoped)
    return service


def _patch_repos(
    monkeypatch: pytest.MonkeyPatch,
    *,
    workspace: object | None = None,
    turn_number: int = 1,
    idempotency_existing: object | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Patch WorkspaceRepository / TimelineRepository / AuditRecorder at module level."""
    workspace_repo = MagicMock()
    workspace_repo.get_workspace = AsyncMock(return_value=workspace)
    workspace_repo.allocate_turn_number = AsyncMock(return_value=turn_number)

    timeline_repo = MagicMock()
    timeline_repo.get_turn_by_idempotency = AsyncMock(return_value=idempotency_existing)
    timeline_repo.insert_turn = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    timeline_repo.insert_turn_context = AsyncMock()
    timeline_repo.lock_turn_inputs = AsyncMock()
    timeline_repo.update_turn_status = AsyncMock()

    audit = MagicMock()
    audit.record = AsyncMock()

    monkeypatch.setattr(mod, "WorkspaceRepository", workspace_repo)
    monkeypatch.setattr(mod, "TimelineRepository", timeline_repo)
    monkeypatch.setattr(mod, "AuditRecorder", audit)
    return workspace_repo, timeline_repo, audit


def _make_cmd(revision_ids: tuple = ()) -> CreateTurnCommand:
    return CreateTurnCommand(
        workspace_id=uuid4(),
        question_text="问题",
        evidence_snapshot_id=uuid4(),
        selected_conclusion_revision_ids=revision_ids,
        recommendation_item_id=None,
        idempotency_key="key-1",
    )


def _make_session(
    *,
    snapshot: object | None = None,
    revisions: dict | None = None,
    conclusions: dict | None = None,
) -> MagicMock:
    from packages.research.execution.entities_trusted import ResearchAnalysisPlanVersion

    session = MagicMock()

    def get_side_effect(entity: type, ident: object) -> object | None:
        if entity is ResearchEvidenceSnapshot:
            return snapshot
        if entity is ResearchAnalysisPlanVersion:
            return getattr(session, "_plan", None)
        if entity is ResearchConclusionRevision:
            return (revisions or {}).get(ident)
        if entity is ResearchConclusion:
            return (conclusions or {}).get(ident)
        return None

    session.get = AsyncMock(side_effect=get_side_effect)
    session.execute = AsyncMock(return_value=MagicMock())
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    return session


class TestRequireActor:
    async def test_actor_required_raises_forbidden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _make_service(monkeypatch, MagicMock(), actor_id=None)
        with pytest.raises(AppError) as exc_info:
            service._require_actor()
        assert exc_info.value.code == "forbidden"

    async def test_actor_present_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        actor = uuid4()
        service = _make_service(monkeypatch, MagicMock(), actor_id=actor)
        assert service._require_actor() == actor

    def test_session_factory_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        factory = MagicMock()
        service = TurnService(factory, uuid4(), uuid4())
        assert service.session_factory is factory


class TestCreateAnalysisTurn:
    async def test_workspace_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        _patch_repos(monkeypatch, workspace=None)

        with pytest.raises(AppError) as exc_info:
            await service.create_analysis_turn(_make_cmd())
        assert exc_info.value.code == "not_found"

    async def test_snapshot_foreign(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cmd = _make_cmd()
        snapshot = SimpleNamespace(workspace_id=uuid4())  # different workspace
        session = _make_session(snapshot=snapshot)
        service = _make_service(monkeypatch, session)
        _patch_repos(monkeypatch, workspace=SimpleNamespace())

        with pytest.raises(AppError) as exc_info:
            await service.create_analysis_turn(cmd)
        assert exc_info.value.code == "not_found"

    async def test_revision_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cmd = _make_cmd(revision_ids=(uuid4(),))
        snapshot = SimpleNamespace(workspace_id=cmd.workspace_id)
        session = _make_session(snapshot=snapshot, revisions={})
        service = _make_service(monkeypatch, session)
        _patch_repos(monkeypatch, workspace=SimpleNamespace())

        with pytest.raises(AppError) as exc_info:
            await service.create_analysis_turn(cmd)
        assert exc_info.value.code == "not_found"

    async def test_conclusion_foreign(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cmd = _make_cmd(revision_ids=(uuid4(),))
        rid = cmd.selected_conclusion_revision_ids[0]
        snapshot = SimpleNamespace(workspace_id=cmd.workspace_id)
        revision = SimpleNamespace(id=rid, conclusion_id=uuid4())
        conclusion = SimpleNamespace(workspace_id=uuid4())  # foreign
        session = _make_session(
            snapshot=snapshot,
            revisions={rid: revision},
            conclusions={revision.conclusion_id: conclusion},
        )
        service = _make_service(monkeypatch, session)
        _patch_repos(monkeypatch, workspace=SimpleNamespace())

        with pytest.raises(AppError) as exc_info:
            await service.create_analysis_turn(cmd)
        assert exc_info.value.code == "not_found"

    async def test_idempotency_returns_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cmd = _make_cmd()
        snapshot = SimpleNamespace(workspace_id=cmd.workspace_id)
        session = _make_session(snapshot=snapshot)
        service = _make_service(monkeypatch, session)

        existing = SimpleNamespace(
            id=uuid4(),
            workspace_id=cmd.workspace_id,
            turn_number=5,
            kind="analysis",
            status="question_draft",
            question_text_snapshot=cmd.question_text,
            question_origin="manual",
            evidence_snapshot_id=cmd.evidence_snapshot_id,
        )
        _patch_repos(monkeypatch, workspace=SimpleNamespace(), idempotency_existing=existing)

        result = await service.create_analysis_turn(cmd)
        assert result.turn_id == existing.id
        assert result.turn_number == 5

    async def test_success_with_revisions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rid = uuid4()
        cmd = _make_cmd(revision_ids=(rid,))
        snapshot = SimpleNamespace(workspace_id=cmd.workspace_id)
        revision = SimpleNamespace(id=rid, conclusion_id=uuid4())
        conclusion = SimpleNamespace(workspace_id=cmd.workspace_id)
        session = _make_session(
            snapshot=snapshot,
            revisions={rid: revision},
            conclusions={revision.conclusion_id: conclusion},
        )
        service = _make_service(monkeypatch, session)
        _ws, timeline, audit = _patch_repos(monkeypatch, workspace=SimpleNamespace(), turn_number=9)

        result = await service.create_analysis_turn(cmd)

        assert result.turn_number == 9
        timeline.insert_turn.assert_awaited_once()
        timeline.insert_turn_context.assert_awaited_once()
        audit.record.assert_awaited_once()

    async def test_success_without_revisions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cmd = _make_cmd(revision_ids=())
        snapshot = SimpleNamespace(workspace_id=cmd.workspace_id)
        session = _make_session(snapshot=snapshot)
        service = _make_service(monkeypatch, session)
        _ws, timeline, audit = _patch_repos(monkeypatch, workspace=SimpleNamespace())

        result = await service.create_analysis_turn(cmd)

        assert result.kind == "analysis"
        timeline.insert_turn_context.assert_not_called()
        audit.record.assert_awaited_once()


class TestCreateSynthesisTurn:
    async def test_workspace_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cmd = CreateSynthesisTurnCommand(
            workspace_id=uuid4(),
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=(uuid4(), uuid4()),
            idempotency_key="s1",
        )
        session = _make_session()
        service = _make_service(monkeypatch, session)
        _patch_repos(monkeypatch, workspace=None)

        with pytest.raises(AppError) as exc_info:
            await service.create_synthesis_turn(cmd)
        assert exc_info.value.code == "not_found"

    async def test_success_generates_synthesis_question(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rids = (uuid4(), uuid4())
        cmd = CreateSynthesisTurnCommand(
            workspace_id=uuid4(),
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=rids,
            idempotency_key="s1",
        )
        snapshot = SimpleNamespace(workspace_id=cmd.workspace_id)
        revisions = {r: SimpleNamespace(id=r, conclusion_id=uuid4()) for r in rids}
        conclusions = {
            v.conclusion_id: SimpleNamespace(workspace_id=cmd.workspace_id)
            for v in revisions.values()
        }
        session = _make_session(snapshot=snapshot, revisions=revisions, conclusions=conclusions)
        service = _make_service(monkeypatch, session)
        _ws, timeline, audit = _patch_repos(monkeypatch, workspace=SimpleNamespace())

        result = await service.create_synthesis_turn(cmd)

        assert result.kind == "synthesis"
        assert "综合所选的 2 条结论" in result.question_text
        timeline.insert_turn.assert_awaited_once()
        _, kwargs = timeline.insert_turn.await_args
        assert kwargs["kind"] == "synthesis"
        assert kwargs["question_origin"] == "synthesis"
        audit.record.assert_awaited_once()

    async def test_idempotency_returns_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rids = (uuid4(), uuid4())
        cmd = CreateSynthesisTurnCommand(
            workspace_id=uuid4(),
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=rids,
            idempotency_key="s1",
        )
        snapshot = SimpleNamespace(workspace_id=cmd.workspace_id)
        revisions = {r: SimpleNamespace(id=r, conclusion_id=uuid4()) for r in rids}
        conclusions = {
            v.conclusion_id: SimpleNamespace(workspace_id=cmd.workspace_id)
            for v in revisions.values()
        }
        session = _make_session(snapshot=snapshot, revisions=revisions, conclusions=conclusions)
        service = _make_service(monkeypatch, session)

        existing = SimpleNamespace(
            id=uuid4(),
            workspace_id=cmd.workspace_id,
            turn_number=2,
            kind="synthesis",
            status="question_draft",
            question_text_snapshot="q",
            question_origin="synthesis",
            evidence_snapshot_id=cmd.evidence_snapshot_id,
        )
        _patch_repos(monkeypatch, workspace=SimpleNamespace(), idempotency_existing=existing)

        result = await service.create_synthesis_turn(cmd)
        assert result.turn_id == existing.id

    async def test_snapshot_foreign(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cmd = CreateSynthesisTurnCommand(
            workspace_id=uuid4(),
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=(uuid4(), uuid4()),
            idempotency_key="s1",
        )
        snapshot = SimpleNamespace(workspace_id=uuid4())  # foreign
        session = _make_session(snapshot=snapshot)
        service = _make_service(monkeypatch, session)
        _patch_repos(monkeypatch, workspace=SimpleNamespace())

        with pytest.raises(AppError) as exc_info:
            await service.create_synthesis_turn(cmd)
        assert exc_info.value.code == "not_found"

    async def test_conclusion_foreign(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rids = (uuid4(), uuid4())
        cmd = CreateSynthesisTurnCommand(
            workspace_id=uuid4(),
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=rids,
            idempotency_key="s1",
        )
        snapshot = SimpleNamespace(workspace_id=cmd.workspace_id)
        revisions = {r: SimpleNamespace(id=r, conclusion_id=uuid4()) for r in rids}
        conclusions = {
            v.conclusion_id: SimpleNamespace(workspace_id=uuid4())  # foreign
            for v in revisions.values()
        }
        session = _make_session(snapshot=snapshot, revisions=revisions, conclusions=conclusions)
        service = _make_service(monkeypatch, session)
        _patch_repos(monkeypatch, workspace=SimpleNamespace())

        with pytest.raises(AppError) as exc_info:
            await service.create_synthesis_turn(cmd)
        assert exc_info.value.code == "not_found"

    async def test_revision_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rids = (uuid4(), uuid4())
        cmd = CreateSynthesisTurnCommand(
            workspace_id=uuid4(),
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=rids,
            idempotency_key="s1",
        )
        snapshot = SimpleNamespace(workspace_id=cmd.workspace_id)
        session = _make_session(snapshot=snapshot, revisions={})
        service = _make_service(monkeypatch, session)
        _patch_repos(monkeypatch, workspace=SimpleNamespace())

        with pytest.raises(AppError) as exc_info:
            await service.create_synthesis_turn(cmd)
        assert exc_info.value.code == "not_found"

    async def test_conclusion_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rids = (uuid4(), uuid4())
        cmd = CreateSynthesisTurnCommand(
            workspace_id=uuid4(),
            evidence_snapshot_id=uuid4(),
            selected_conclusion_revision_ids=rids,
            idempotency_key="s1",
        )
        snapshot = SimpleNamespace(workspace_id=cmd.workspace_id)
        revisions = {r: SimpleNamespace(id=r, conclusion_id=uuid4()) for r in rids}
        # conclusions omitted entirely -> session.get(ResearchConclusion) returns None
        session = _make_session(snapshot=snapshot, revisions=revisions)
        service = _make_service(monkeypatch, session)
        _patch_repos(monkeypatch, workspace=SimpleNamespace())

        with pytest.raises(AppError) as exc_info:
            await service.create_synthesis_turn(cmd)
        assert exc_info.value.code == "not_found"


class TestStartPlanning:
    async def test_turn_cannot_plan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = SimpleNamespace(id=uuid4(), status="running", turn_number=1)
        monkeypatch.setattr(
            "packages.research.timeline.access.require_owned_turn",
            AsyncMock(return_value=turn),
        )
        monkeypatch.setattr(mod, "TimelineRepository", MagicMock())
        monkeypatch.setattr(mod, "AuditRecorder", MagicMock())

        service = _make_service(monkeypatch, _make_session())
        with pytest.raises(AppError) as exc_info:
            await service.start_planning(uuid4(), turn.id)
        assert exc_info.value.code == "state_conflict"

    async def test_success_locks_and_enqueues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = SimpleNamespace(id=uuid4(), status="question_draft", turn_number=3)
        monkeypatch.setattr(
            "packages.research.timeline.access.require_owned_turn",
            AsyncMock(return_value=turn),
        )
        timeline = MagicMock()
        timeline.lock_turn_inputs = AsyncMock()
        monkeypatch.setattr(mod, "TimelineRepository", timeline)
        audit = MagicMock()
        audit.record = AsyncMock()
        monkeypatch.setattr(mod, "AuditRecorder", audit)
        enqueue = AsyncMock()
        monkeypatch.setattr("packages.jobs.outbox.OutboxDispatcher.enqueue", enqueue)

        session = _make_session()
        service = _make_service(monkeypatch, session)

        result = await service.start_planning(uuid4(), turn.id)

        assert result.plan_id == turn.id  # placeholder stub
        assert result.status == "planning"
        timeline.lock_turn_inputs.assert_awaited_once()
        audit.record.assert_awaited_once()
        enqueue.assert_awaited_once()


class TestConfirmPlan:
    async def test_plan_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = SimpleNamespace(id=uuid4(), turn_number=1)
        monkeypatch.setattr(
            "packages.research.timeline.access.require_owned_turn",
            AsyncMock(return_value=turn),
        )
        session = _make_session()
        session._plan = None  # type: ignore[attr-defined]

        service = _make_service(monkeypatch, session)
        with pytest.raises(AppError) as exc_info:
            await service.confirm_plan(uuid4(), turn.id, uuid4())
        assert exc_info.value.code == "not_found"

    async def test_plan_not_draft(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = SimpleNamespace(id=uuid4(), turn_number=1)
        monkeypatch.setattr(
            "packages.research.timeline.access.require_owned_turn",
            AsyncMock(return_value=turn),
        )
        plan = SimpleNamespace(
            id=uuid4(), turn_id=turn.id, status="confirmed", version_number=1
        )
        session = _make_session()
        session._plan = plan  # type: ignore[attr-defined]

        service = _make_service(monkeypatch, session)
        with pytest.raises(AppError) as exc_info:
            await service.confirm_plan(uuid4(), turn.id, plan.id)
        assert exc_info.value.code == "state_conflict"

    async def test_success_confirms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = SimpleNamespace(id=uuid4(), turn_number=4)
        plan = SimpleNamespace(id=uuid4(), turn_id=turn.id, status="draft", version_number=2)
        monkeypatch.setattr(
            "packages.research.timeline.access.require_owned_turn",
            AsyncMock(return_value=turn),
        )
        update_plan_status = AsyncMock()
        monkeypatch.setattr(
            "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.update_plan_status",
            update_plan_status,
        )
        timeline = MagicMock()
        timeline.update_turn_status = AsyncMock()
        monkeypatch.setattr(mod, "TimelineRepository", timeline)
        audit = MagicMock()
        audit.record = AsyncMock()
        monkeypatch.setattr(mod, "AuditRecorder", audit)

        session = _make_session()
        session._plan = plan  # type: ignore[attr-defined]
        service = _make_service(monkeypatch, session)

        result = await service.confirm_plan(uuid4(), turn.id, plan.id)

        assert result.status == "confirmed"
        assert result.version_number == 2
        update_plan_status.assert_awaited_once()
        timeline.update_turn_status.assert_awaited_once_with(
            session, turn.id, expected_status="plan_review", new_status="plan_confirmed"
        )
        audit.record.assert_awaited_once()


class TestDeleteTurn:
    async def test_delete_calls_session_delete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        turn = SimpleNamespace(id=uuid4(), turn_number=1)
        monkeypatch.setattr(
            "packages.research.timeline.access.require_owned_turn",
            AsyncMock(return_value=turn),
        )

        session = _make_session()
        service = _make_service(monkeypatch, session)

        await service.delete_turn(uuid4(), turn.id)
        session.delete.assert_awaited_once_with(turn)


class TestDeriveOrigin:
    def test_with_recommendation_item_is_initial_ai(self) -> None:
        assert TurnService._derive_origin(uuid4(), "问题") == "initial_ai"

    def test_without_recommendation_item_is_manual(self) -> None:
        assert TurnService._derive_origin(None, "问题") == "manual"
