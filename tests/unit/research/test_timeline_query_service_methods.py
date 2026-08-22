"""Unit tests for TimelineQueryService methods (list_timeline, get_turn_detail, etc.).

Uses mock sessions to exercise the query-building and data-assembly logic
without a real database. Focuses on covering the service's core read methods.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.research.timeline import timeline_query_service as tq_mod
from packages.research.timeline.timeline_query_service import TimelineQueryService

# ============================================================
# Helpers
# ============================================================


def _make_session() -> MagicMock:
    """Create a mock session supporting async execute and get."""
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.get = AsyncMock(return_value=None)
    return session


def _make_service(
    monkeypatch: pytest.MonkeyPatch,
    session: MagicMock,
    actor_id: object | None = None,
) -> TimelineQueryService:
    """Create a TimelineQueryService with patched _scoped_session."""
    service = TimelineQueryService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=actor_id if actor_id is not None else uuid4(),
    )

    @asynccontextmanager
    async def _scoped(self: TimelineQueryService):  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr(TimelineQueryService, "_scoped_session", _scoped)
    return service


def _turn(**overrides: object) -> SimpleNamespace:
    """Create a turn SimpleNamespace."""
    defaults: dict[str, object] = {
        "id": uuid4(),
        "workspace_id": uuid4(),
        "turn_number": 1,
        "kind": "initial_ai",
        "status": "completed",
        "question_text_snapshot": "哪些批次收率偏低?",
        "question_origin": "manual",
        "evidence_snapshot_id": uuid4(),
        "prompt_template_version": "v1",
        "output_schema_version": "v1",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_execute_result(rows: list | None = None, scalar: object | None = None):
    """Create a mock result object for session.execute()."""
    result = MagicMock()
    if rows is not None:
        # For .scalars().all() pattern
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = rows
        result.scalars.return_value = scalars_mock
        # For iteration: for row in result
        result.__iter__ = MagicMock(return_value=iter(rows))
    if scalar is not None:
        result.scalar_one_or_none.return_value = scalar
    return result


# ============================================================
# list_timeline tests
# ============================================================


class TestListTimelineEmpty:
    """list_timeline returns empty page when no turns."""

    async def test_empty_turns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)

        # Patch require_owned_workspace to no-op
        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        # Patch TimelineRepository.list_turns to return empty
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "list_turns",
            AsyncMock(return_value=([], None)),
        )

        page = await service.list_timeline(uuid4())
        assert page.items == []
        assert page.next_cursor is None
        assert page.active_run_status is None


class TestListTimelineWithTurns:
    """list_timeline with actual turns — exercises batch-load logic."""

    async def test_with_turns_builds_cards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)

        wid = uuid4()
        turn1 = _turn(id=uuid4(), workspace_id=wid, turn_number=1)
        turn2 = _turn(id=uuid4(), workspace_id=wid, turn_number=2)

        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "list_turns",
            AsyncMock(return_value=([turn1, turn2], None)),
        )

        # Mock the 4 batch-load queries (ctx_counts, result_rows, cand_rows, snap_rows)
        # Each session.execute call returns a different mock result
        ctx_result = MagicMock()
        ctx_result.__iter__ = MagicMock(return_value=iter([(turn1.id, 2), (turn2.id, 0)]))

        result_result = MagicMock()
        result_result.__iter__ = MagicMock(return_value=iter([(turn1.id,)]))

        cand_result = MagicMock()
        cand_result.__iter__ = MagicMock(return_value=iter([]))

        snap_result = MagicMock()
        snap_result.__iter__ = MagicMock(
            return_value=iter([(turn1.evidence_snapshot_id, 1), (turn2.evidence_snapshot_id, 2)])
        )

        session.execute = AsyncMock(
            side_effect=[ctx_result, result_result, cand_result, snap_result]
        )

        # Patch get_active_run_status
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_active_run_status",
            AsyncMock(return_value="running"),
        )

        page = await service.list_timeline(wid)
        assert len(page.items) == 2
        assert page.items[0].turn_number == 1
        assert page.items[0].selected_conclusion_count == 2
        assert page.items[0].snapshot_number == 1
        assert page.items[0].has_result is True
        assert page.items[0].has_candidates is False
        assert page.active_run_status == "running"

    async def test_with_turns_no_next_cursor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)

        wid = uuid4()
        turn1 = _turn(id=uuid4(), workspace_id=wid)

        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "list_turns",
            AsyncMock(return_value=([turn1], None)),
        )

        ctx_result = MagicMock()
        ctx_result.__iter__ = MagicMock(return_value=iter([]))
        result_result = MagicMock()
        result_result.__iter__ = MagicMock(return_value=iter([]))
        cand_result = MagicMock()
        cand_result.__iter__ = MagicMock(return_value=iter([]))
        snap_result = MagicMock()
        snap_result.__iter__ = MagicMock(return_value=iter([]))

        session.execute = AsyncMock(
            side_effect=[ctx_result, result_result, cand_result, snap_result]
        )

        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_active_run_status",
            AsyncMock(return_value=None),
        )

        page = await service.list_timeline(wid)
        assert len(page.items) == 1
        assert page.next_cursor is None
        assert page.active_run_status is None


# ============================================================
# get_turn_detail tests
# ============================================================


class TestGetTurnDetail:
    """get_turn_detail — tests for not_found and success paths."""

    async def test_turn_not_found_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)

        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn",
            AsyncMock(return_value=None),
        )

        with pytest.raises(AppError) as exc_info:
            await service.get_turn_detail(uuid4(), uuid4())
        assert exc_info.value.code == "not_found"

    async def test_turn_wrong_workspace_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)

        wid = uuid4()
        turn = _turn(workspace_id=uuid4())  # different workspace

        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn",
            AsyncMock(return_value=turn),
        )

        with pytest.raises(AppError) as exc_info:
            await service.get_turn_detail(wid, turn.id)
        assert exc_info.value.code == "not_found"

    async def test_success_no_context_no_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)

        wid = uuid4()
        turn = _turn(workspace_id=wid)

        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn",
            AsyncMock(return_value=turn),
        )
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "list_turn_context",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn_result",
            AsyncMock(return_value=None),
        )

        # Extraction query
        extraction_result = MagicMock()
        extraction_result.scalar_one_or_none.return_value = None
        # Candidates query
        cand_result = MagicMock()
        cand_scalars = MagicMock()
        cand_scalars.all.return_value = []
        cand_result.scalars.return_value = cand_scalars
        # Saved conclusions query
        saved_result = MagicMock()
        saved_scalars = MagicMock()
        saved_scalars.all.return_value = []
        saved_result.scalars.return_value = saved_scalars

        session.execute = AsyncMock(side_effect=[extraction_result, cand_result, saved_result])

        # Patch _load_plan_ref
        monkeypatch.setattr(
            TimelineQueryService,
            "_load_plan_ref",
            AsyncMock(return_value=None),
        )

        detail = await service.get_turn_detail(wid, turn.id)
        assert detail.turn.turn_id == turn.id
        assert detail.selected_conclusions == []
        assert detail.result is None
        assert detail.extraction_status is None
        assert detail.candidates == []
        assert detail.saved_conclusions == []
        assert detail.access_restricted is False

    async def test_success_with_context_and_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)

        wid = uuid4()
        turn = _turn(workspace_id=wid)

        # Context row
        rev_id = uuid4()
        ctx_row = SimpleNamespace(
            turn_id=turn.id,
            conclusion_revision_id=rev_id,
            position=0,
        )

        # Revision
        concl_id = uuid4()
        revision = SimpleNamespace(
            id=rev_id,
            conclusion_id=concl_id,
            statement="结论陈述",
            scope="scope1",
            limitations="limit1",
        )

        # Conclusion
        conclusion = SimpleNamespace(
            id=concl_id,
            source_type="ai_original",
            evidence_status="data_supported",
            source_turn_id=uuid4(),
            source_run_id=uuid4(),
        )

        # Result row
        result_row = SimpleNamespace(
            result_kind="analysis",
            summary="摘要",
            structured_output={"k": "v"},
            method_summary="方法",
            evidence_refs=[],
            limitations=None,
        )

        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn",
            AsyncMock(return_value=turn),
        )
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "list_turn_context",
            AsyncMock(return_value=[ctx_row]),
        )
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn_result",
            AsyncMock(return_value=result_row),
        )

        # Mock session.execute: revisions, conclusions, extraction, candidates, saved
        rev_result = MagicMock()
        rev_result.scalars.return_value = [revision]

        concl_result = MagicMock()
        concl_result.scalars.return_value = [conclusion]

        extraction_result = MagicMock()
        extraction_result.scalar_one_or_none.return_value = SimpleNamespace(status="completed")

        cand_result = MagicMock()
        cand_result.scalars.return_value = [
            SimpleNamespace(
                id=uuid4(),
                ordinal=1,
                statement="候选",
                scope="scope",
                confidence_level="high",
                limitations=None,
                status="pending",
            )
        ]

        saved_result = MagicMock()
        saved_result.scalars.return_value = []

        session.execute = AsyncMock(
            side_effect=[rev_result, concl_result, extraction_result, cand_result, saved_result]
        )

        monkeypatch.setattr(
            TimelineQueryService,
            "_load_plan_ref",
            AsyncMock(return_value=None),
        )

        detail = await service.get_turn_detail(wid, turn.id)
        assert len(detail.selected_conclusions) == 1
        assert detail.selected_conclusions[0].statement == "结论陈述"
        assert detail.result is not None
        assert detail.result["summary"] == "摘要"
        assert detail.extraction_status == "completed"
        assert len(detail.candidates) == 1

    async def test_success_with_saved_conclusions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)

        wid = uuid4()
        turn = _turn(workspace_id=wid)

        concl_id = uuid4()
        saved_concl = SimpleNamespace(
            id=concl_id,
            workspace_id=wid,
            source_type="ai_original",
            evidence_status="data_supported",
            status="active",
        )

        saved_rev = SimpleNamespace(
            conclusion_id=concl_id,
            revision_number=3,
            statement="保存的结论",
        )

        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn",
            AsyncMock(return_value=turn),
        )
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "list_turn_context",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn_result",
            AsyncMock(return_value=None),
        )

        # session.execute calls: extraction, candidates, saved_conclusions, saved_revisions
        extraction_result = MagicMock()
        extraction_result.scalar_one_or_none.return_value = None

        cand_result = MagicMock()
        cand_result.scalars.return_value = []

        saved_result = MagicMock()
        saved_result.scalars.return_value = [saved_concl]

        rev_result = MagicMock()
        rev_result.scalars.return_value = [saved_rev]

        session.execute = AsyncMock(
            side_effect=[extraction_result, cand_result, saved_result, rev_result]
        )

        monkeypatch.setattr(
            TimelineQueryService,
            "_load_plan_ref",
            AsyncMock(return_value=None),
        )

        detail = await service.get_turn_detail(wid, turn.id)
        assert len(detail.saved_conclusions) == 1
        assert detail.saved_conclusions[0].revision_number == 3
        assert detail.saved_conclusions[0].statement == "保存的结论"

    async def test_success_revision_missing_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When revision is None for a context row, it should be skipped."""
        session = _make_session()
        service = _make_service(monkeypatch, session)

        wid = uuid4()
        turn = _turn(workspace_id=wid)

        ctx_row = SimpleNamespace(
            turn_id=turn.id,
            conclusion_revision_id=uuid4(),
            position=0,
        )

        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn",
            AsyncMock(return_value=turn),
        )
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "list_turn_context",
            AsyncMock(return_value=[ctx_row]),
        )
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn_result",
            AsyncMock(return_value=None),
        )

        # revisions returns empty → revision is None → skip
        rev_result = MagicMock()
        rev_result.scalars.return_value = []

        extraction_result = MagicMock()
        extraction_result.scalar_one_or_none.return_value = None

        cand_result = MagicMock()
        cand_result.scalars.return_value = []

        saved_result = MagicMock()
        saved_result.scalars.return_value = []

        session.execute = AsyncMock(
            side_effect=[rev_result, extraction_result, cand_result, saved_result]
        )

        monkeypatch.setattr(
            TimelineQueryService,
            "_load_plan_ref",
            AsyncMock(return_value=None),
        )

        detail = await service.get_turn_detail(wid, turn.id)
        assert detail.selected_conclusions == []


class TestLoadPlanRef:
    """_load_plan_ref static method tests."""

    async def test_no_plan_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        plan_result = MagicMock()
        plan_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=plan_result)

        result = await TimelineQueryService._load_plan_ref(session, uuid4())
        assert result is None

    async def test_plan_found_returns_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        plan_id = uuid4()
        turn_id = uuid4()
        plan = SimpleNamespace(
            id=plan_id,
            turn_id=turn_id,
            version_number=2,
            status="approved",
        )
        plan_result = MagicMock()
        plan_result.scalar_one_or_none.return_value = plan
        session.execute = AsyncMock(return_value=plan_result)

        result = await TimelineQueryService._load_plan_ref(session, turn_id)
        assert result is not None
        assert result.plan_id == plan_id
        assert result.version_number == 2
        assert result.status == "approved"


# ============================================================
# get_turn_detail_api tests
# ============================================================


class TestGetTurnDetailApi:
    """get_turn_detail_api — dict-returning API variant."""

    async def test_turn_not_found_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)

        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn",
            AsyncMock(return_value=None),
        )

        with pytest.raises(AppError) as exc_info:
            await service.get_turn_detail_api(uuid4(), uuid4())
        assert exc_info.value.code == "not_found"

    async def test_turn_wrong_workspace_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)

        wid = uuid4()
        turn = _turn(workspace_id=uuid4())

        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn",
            AsyncMock(return_value=turn),
        )

        with pytest.raises(AppError) as exc_info:
            await service.get_turn_detail_api(wid, turn.id)
        assert exc_info.value.code == "not_found"

    async def test_success_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)

        wid = uuid4()
        turn = _turn(workspace_id=wid)

        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn",
            AsyncMock(return_value=turn),
        )

        # Mock all session.execute calls:
        # 1. ctx_result (ResearchTurnContext)
        ctx_result = MagicMock()
        ctx_result.scalars.return_value = []

        # 2. cand_result (ResearchConclusionCandidate)
        cand_result = MagicMock()
        cand_result.scalars.return_value = []

        # 3. concl_result (ResearchConclusion)
        concl_result = MagicMock()
        concl_result.scalars.return_value = []

        # 4. result_row (ResearchTurnResult)
        result_result = MagicMock()
        result_result.scalar_one_or_none.return_value = None

        # 5. plan_row (ResearchAnalysisPlanVersion)
        plan_result = MagicMock()
        plan_result.scalar_one_or_none.return_value = None

        session.execute = AsyncMock(
            side_effect=[ctx_result, cand_result, concl_result, result_result, plan_result]
        )

        # Mock FactDataLoader
        fact_loader = MagicMock()
        fact_loader.load_fact_samples = AsyncMock(return_value=[])
        monkeypatch.setattr(
            "packages.research.timeline.fact_data_loader.FactDataLoader",
            MagicMock(return_value=fact_loader),
        )

        out = await service.get_turn_detail_api(wid, turn.id)
        assert out["turn"]["turn_id"] == str(turn.id)
        assert out["selected_conclusions"] == []
        assert out["candidates"] == []
        assert out["saved_conclusions"] == []
        assert out["result"] is None
        assert out["plan"] is None
        assert out["fact_samples"] == []
        assert out["access_restricted"] is False

    async def test_success_with_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)

        wid = uuid4()
        turn = _turn(workspace_id=wid)

        monkeypatch.setattr(tq_mod, "require_owned_workspace", AsyncMock())
        monkeypatch.setattr(
            tq_mod.TimelineRepository,
            "get_turn",
            AsyncMock(return_value=turn),
        )

        # Context with revision
        rev_id = uuid4()
        concl_id = uuid4()
        ctx_row = SimpleNamespace(
            turn_id=turn.id,
            conclusion_revision_id=rev_id,
            position=0,
        )
        revision = SimpleNamespace(
            id=rev_id,
            conclusion_id=concl_id,
            statement="陈述",
        )
        conclusion = SimpleNamespace(
            id=concl_id,
            source_type="ai_original",
            evidence_status="data_supported",
        )

        ctx_result = MagicMock()
        ctx_result.scalars.return_value = [ctx_row]

        # session.get is called for revision and conclusion
        session.get = AsyncMock(side_effect=[revision, conclusion])

        # Candidates
        cand = SimpleNamespace(
            id=uuid4(),
            ordinal=1,
            statement="候选1",
            scope="scope",
            confidence_level="high",
            limitations=None,
            status="pending",
        )
        cand_result = MagicMock()
        cand_result.scalars.return_value = [cand]

        # Saved conclusions (active, workspace-scoped)
        saved_concl = SimpleNamespace(
            id=uuid4(),
            workspace_id=wid,
            source_type="ai_original",
            evidence_status="data_supported",
            status="active",
            current_revision_id=uuid4(),
        )
        concl_result = MagicMock()
        concl_result.scalars.return_value = [saved_concl]

        # Saved revision
        saved_rev = SimpleNamespace(revision_number=2, statement="保存陈述")

        # Turn result
        tr = SimpleNamespace(
            summary="结果摘要",
            structured_output={"k": "v"},
            method_summary="方法",
        )
        result_result = MagicMock()
        result_result.scalar_one_or_none.return_value = tr

        # Plan
        plan_entity = SimpleNamespace(
            id=uuid4(),
            version_number=1,
            status="draft",
            dag_structure={"nodes": []},
            coverage_declaration={"coverage": 0.8},
        )
        plan_result = MagicMock()
        plan_result.scalar_one_or_none.return_value = plan_entity

        # session.get calls: revision, conclusion (for selected), saved_rev (for saved conclusions)
        session.get = AsyncMock(side_effect=[revision, conclusion, saved_rev])

        session.execute = AsyncMock(
            side_effect=[ctx_result, cand_result, concl_result, result_result, plan_result]
        )

        # Mock FactDataLoader
        fact_loader = MagicMock()
        fact_loader.load_fact_samples = AsyncMock(return_value=[{"fact_id": "1"}])
        monkeypatch.setattr(
            "packages.research.timeline.fact_data_loader.FactDataLoader",
            MagicMock(return_value=fact_loader),
        )

        out = await service.get_turn_detail_api(wid, turn.id)
        assert len(out["selected_conclusions"]) == 1
        assert out["selected_conclusions"][0]["statement"] == "陈述"
        assert len(out["candidates"]) == 1
        assert out["candidates"][0]["statement"] == "候选1"
        assert len(out["saved_conclusions"]) == 1
        assert out["saved_conclusions"][0]["revision_number"] == 2
        assert out["result"]["summary"] == "结果摘要"
        assert out["plan"]["status"] == "draft"
        assert len(out["fact_samples"]) == 1
