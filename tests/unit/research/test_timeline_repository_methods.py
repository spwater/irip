"""Unit tests for TimelineRepository, CandidateRepository, ConclusionRepository,
and ConclusionBarRepository — all with mock sessions (no real DB).

These repository methods are all @staticmethod async that accept an AsyncSession
and use session.execute / session.add / session.flush. We mock the session to
exercise the logic without a database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.research.timeline.conclusion_bar_repository import ConclusionBarRepository
from packages.research.timeline.conclusion_repository import (
    CandidateRepository,
    ConclusionRepository,
    decode_conclusion_cursor,
    encode_conclusion_cursor,
)
from packages.research.timeline.repository import TimelineRepository

# ============================================================
# Helpers
# ============================================================


def _mock_session() -> MagicMock:
    """Create a mock session for repository testing."""
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    return session


def _result_with_scalars(rows: list, scalar: object | None = None) -> MagicMock:
    """Create a mock execute() return value with .scalars() returning rows.

    Supports both `result.scalars().all()` and `list(result.scalars())` patterns.
    """
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = rows
    # Also support iteration: list(result.scalars())
    scalars_mock.__iter__ = MagicMock(return_value=iter(rows))
    result.scalars.return_value = scalars_mock
    if scalar is not None:
        result.scalar_one_or_none.return_value = scalar
    return result


def _result_with_scalar(scalar: object | None) -> MagicMock:
    """Create a mock execute() return value with scalar_one_or_none."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    return result


# ============================================================
# Conclusion cursor encode/decode (pure functions)
# ============================================================


class TestConclusionCursorEncodeDecode:
    """Test encode/decode conclusion cursor (pure functions)."""

    def test_roundtrip(self) -> None:
        ts = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        cid = uuid4()
        cursor = encode_conclusion_cursor(ts, cid)
        decoded_ts, decoded_id = decode_conclusion_cursor(cursor)
        assert decoded_ts == ts
        assert decoded_id == cid

    def test_malformed_cursor_raises(self) -> None:
        with pytest.raises(AppError, match="Invalid conclusion cursor"):
            decode_conclusion_cursor("bad-cursor")


# ============================================================
# CandidateRepository tests
# ============================================================


class TestCandidateRepository:
    """CandidateRepository — mock session tests."""

    async def test_insert_candidates(self) -> None:
        session = _mock_session()
        candidates = [
            {"statement": "结论1", "scope": "scope1", "confidence_level": "high"},
            {"statement": "结论2", "scope": None, "confidence_level": "low"},
        ]
        result = await CandidateRepository.insert_candidates(
            session,
            extraction_id=uuid4(),
            turn_id=uuid4(),
            candidates=candidates,
        )
        assert len(result) == 2
        assert session.add.call_count == 2
        session.flush.assert_awaited_once()

    async def test_insert_candidates_empty(self) -> None:
        session = _mock_session()
        result = await CandidateRepository.insert_candidates(
            session,
            extraction_id=uuid4(),
            turn_id=uuid4(),
            candidates=[],
        )
        assert result == []
        session.flush.assert_awaited_once()

    async def test_list_candidates_by_turn(self) -> None:
        session = _mock_session()
        rows = [SimpleNamespace(id=uuid4(), ordinal=0), SimpleNamespace(id=uuid4(), ordinal=1)]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result = await CandidateRepository.list_candidates_by_turn(session, uuid4())
        assert len(result) == 2

    async def test_list_candidates_empty(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalars([]))
        result = await CandidateRepository.list_candidates_by_turn(session, uuid4())
        assert result == []

    async def test_get_candidate_found(self) -> None:
        session = _mock_session()
        cand = SimpleNamespace(id=uuid4(), statement="test")
        session.execute = AsyncMock(return_value=_result_with_scalar(cand))
        result = await CandidateRepository.get_candidate(session, uuid4())
        assert result is not None

    async def test_get_candidate_not_found(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        result = await CandidateRepository.get_candidate(session, uuid4())
        assert result is None

    async def test_update_candidate_status(self) -> None:
        session = _mock_session()
        await CandidateRepository.update_candidate_status(session, uuid4(), "saved")
        session.execute.assert_awaited_once()

    async def test_update_candidate_status_with_saved_conclusion(self) -> None:
        session = _mock_session()
        await CandidateRepository.update_candidate_status(
            session, uuid4(), "saved", saved_conclusion_id=uuid4()
        )
        session.execute.assert_awaited_once()


# ============================================================
# ConclusionRepository tests
# ============================================================


class TestConclusionRepository:
    """ConclusionRepository — mock session tests."""

    async def test_insert_conclusion(self) -> None:
        session = _mock_session()
        result = await ConclusionRepository.insert_conclusion(
            session,
            workspace_id=uuid4(),
            source_turn_id=uuid4(),
            source_run_id=uuid4(),
            source_candidate_id=None,
            source_type="ai_original",
            evidence_status="data_supported",
            created_by=uuid4(),
        )
        assert result.source_type == "ai_original"
        assert result.status == "active"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_insert_revision(self) -> None:
        session = _mock_session()
        result = await ConclusionRepository.insert_revision(
            session,
            conclusion_id=uuid4(),
            revision_number=1,
            statement="测试结论",
            scope="scope1",
            evidence_refs=[],
            limitations=None,
            editor=uuid4(),
        )
        assert result.statement == "测试结论"
        assert result.revision_number == 1
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_insert_revision_with_no_evidence_refs(self) -> None:
        session = _mock_session()
        result = await ConclusionRepository.insert_revision(
            session,
            conclusion_id=uuid4(),
            revision_number=1,
            statement="测试",
            scope=None,
            evidence_refs=None,
            limitations="limit",
            editor=uuid4(),
        )
        assert result.evidence_refs == []

    async def test_set_current_revision(self) -> None:
        session = _mock_session()
        await ConclusionRepository.set_current_revision(session, uuid4(), uuid4())
        session.execute.assert_awaited_once()

    async def test_get_conclusion_found(self) -> None:
        session = _mock_session()
        concl = SimpleNamespace(id=uuid4())
        session.execute = AsyncMock(return_value=_result_with_scalar(concl))
        result = await ConclusionRepository.get_conclusion(session, uuid4())
        assert result is not None

    async def test_get_conclusion_not_found(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        result = await ConclusionRepository.get_conclusion(session, uuid4())
        assert result is None

    async def test_get_revision_found(self) -> None:
        session = _mock_session()
        rev = SimpleNamespace(id=uuid4())
        session.execute = AsyncMock(return_value=_result_with_scalar(rev))
        result = await ConclusionRepository.get_revision(session, uuid4())
        assert result is not None

    async def test_get_revision_not_found(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        result = await ConclusionRepository.get_revision(session, uuid4())
        assert result is None

    async def test_get_latest_revision_found(self) -> None:
        session = _mock_session()
        rev = SimpleNamespace(id=uuid4(), revision_number=3)
        session.execute = AsyncMock(return_value=_result_with_scalar(rev))
        result = await ConclusionRepository.get_latest_revision(session, uuid4())
        assert result is not None

    async def test_get_latest_revision_not_found(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        result = await ConclusionRepository.get_latest_revision(session, uuid4())
        assert result is None

    async def test_list_revisions(self) -> None:
        session = _mock_session()
        rows = [SimpleNamespace(id=uuid4(), revision_number=1)]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result = await ConclusionRepository.list_revisions(session, uuid4())
        assert len(result) == 1

    async def test_list_revisions_empty(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalars([]))
        result = await ConclusionRepository.list_revisions(session, uuid4())
        assert result == []

    async def test_update_conclusion_lock_success(self) -> None:
        session = _mock_session()
        row = SimpleNamespace(id=uuid4(), lock_version=1)
        session.execute = AsyncMock(return_value=_result_with_scalar(row))
        result = await ConclusionRepository.update_conclusion_lock(session, uuid4(), 0)
        assert result.lock_version == 1

    async def test_update_conclusion_lock_mismatch_raises(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        with pytest.raises(AppError) as exc_info:
            await ConclusionRepository.update_conclusion_lock(session, uuid4(), 0)
        assert exc_info.value.code == "state_conflict"

    async def test_archive_conclusion_success(self) -> None:
        session = _mock_session()
        row = SimpleNamespace(id=uuid4())
        session.execute = AsyncMock(return_value=_result_with_scalar(row))
        await ConclusionRepository.archive_conclusion(session, uuid4(), 0)
        session.execute.assert_awaited_once()

    async def test_archive_conclusion_mismatch_raises(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        with pytest.raises(AppError) as exc_info:
            await ConclusionRepository.archive_conclusion(session, uuid4(), 0)
        assert exc_info.value.code == "state_conflict"

    async def test_list_conclusions_empty(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalars([]))
        rows, cursor = await ConclusionRepository.list_conclusions(session, uuid4())
        assert rows == []
        assert cursor is None

    async def test_list_conclusions_with_rows_no_next(self) -> None:
        session = _mock_session()
        rows = [SimpleNamespace(id=uuid4(), updated_at=datetime(2026, 1, 1, tzinfo=UTC))]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result, cursor = await ConclusionRepository.list_conclusions(session, uuid4(), page_size=20)
        assert len(result) == 1
        assert cursor is None

    async def test_list_conclusions_with_next_cursor(self) -> None:
        session = _mock_session()
        rows = [
            SimpleNamespace(id=uuid4(), updated_at=datetime(2026, 1, 1, tzinfo=UTC)),
            SimpleNamespace(id=uuid4(), updated_at=datetime(2026, 1, 2, tzinfo=UTC)),
        ]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result, cursor = await ConclusionRepository.list_conclusions(session, uuid4(), page_size=1)
        assert len(result) == 1
        assert cursor is not None

    async def test_list_conclusions_with_cursor(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalars([]))
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        cid = uuid4()
        cursor = encode_conclusion_cursor(ts, cid)
        rows, _ = await ConclusionRepository.list_conclusions(session, uuid4(), cursor=cursor)
        assert rows == []

    async def test_list_conclusions_invalid_page_size(self) -> None:
        session = _mock_session()
        with pytest.raises(AppError) as exc_info:
            await ConclusionRepository.list_conclusions(session, uuid4(), page_size=0)
        assert exc_info.value.code == "validation_failed"

        with pytest.raises(AppError) as exc_info:
            await ConclusionRepository.list_conclusions(session, uuid4(), page_size=51)
        assert exc_info.value.code == "validation_failed"

    async def test_count_conclusions_by_workspace(self) -> None:
        session = _mock_session()
        result_mock = MagicMock()
        result_mock.scalar_one.return_value = 5
        session.execute = AsyncMock(return_value=result_mock)
        count = await ConclusionRepository.count_conclusions_by_workspace(session, uuid4())
        assert count == 5


# ============================================================
# ConclusionBarRepository tests
# ============================================================


class TestConclusionBarRepository:
    """ConclusionBarRepository — mock session tests."""

    async def test_insert_item(self) -> None:
        session = _mock_session()
        result = await ConclusionBarRepository.insert_item(
            session,
            workspace_id=uuid4(),
            turn_id=uuid4(),
            block_type="table",
            title="测试",
            content_snapshot={"columns": [], "rows": []},
            source_info={},
            created_by=uuid4(),
        )
        assert result.block_type == "table"
        assert result.title == "测试"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_list_items(self) -> None:
        session = _mock_session()
        rows = [SimpleNamespace(id=uuid4(), block_type="table")]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result = await ConclusionBarRepository.list_items(session, uuid4())
        assert len(result) == 1

    async def test_list_items_empty(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalars([]))
        result = await ConclusionBarRepository.list_items(session, uuid4())
        assert result == []

    async def test_get_item_found(self) -> None:
        session = _mock_session()
        item = SimpleNamespace(id=uuid4())
        session.execute = AsyncMock(return_value=_result_with_scalar(item))
        result = await ConclusionBarRepository.get_item(session, uuid4())
        assert result is not None

    async def test_get_item_not_found(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        result = await ConclusionBarRepository.get_item(session, uuid4())
        assert result is None

    async def test_delete_item_success(self) -> None:
        session = _mock_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = uuid4()
        session.execute = AsyncMock(return_value=result_mock)
        result = await ConclusionBarRepository.delete_item(session, uuid4())
        assert result is True

    async def test_delete_item_not_found(self) -> None:
        session = _mock_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        result = await ConclusionBarRepository.delete_item(session, uuid4())
        assert result is False

    async def test_get_items_by_ids_empty(self) -> None:
        session = _mock_session()
        result = await ConclusionBarRepository.get_items_by_ids(session, [])
        assert result == []

    async def test_get_items_by_ids_found(self) -> None:
        session = _mock_session()
        id1 = uuid4()
        id2 = uuid4()
        rows = [
            SimpleNamespace(id=id1, block_type="table"),
            SimpleNamespace(id=id2, block_type="text"),
        ]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result = await ConclusionBarRepository.get_items_by_ids(session, [id1, id2])
        assert len(result) == 2

    async def test_get_items_by_ids_partial_found(self) -> None:
        """Test that only found items are returned, in input order."""
        session = _mock_session()
        id1 = uuid4()
        id2 = uuid4()
        # Only id1 found
        rows = [SimpleNamespace(id=id1, block_type="table")]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result = await ConclusionBarRepository.get_items_by_ids(session, [id1, id2])
        assert len(result) == 1
        assert result[0].id == id1


# ============================================================
# TimelineRepository tests
# ============================================================


class TestTimelineRepositoryTurnCRUD:
    """TimelineRepository — Turn CRUD tests."""

    async def test_insert_turn(self) -> None:
        session = _mock_session()
        result = await TimelineRepository.insert_turn(
            session,
            workspace_id=uuid4(),
            turn_number=1,
            kind="initial_ai",
            status="queued",
            question_text="问题",
            question_origin="manual",
            evidence_snapshot_id=uuid4(),
            recommendation_item_id=None,
            idempotency_key="key",
        )
        assert result.turn_number == 1
        assert result.kind == "initial_ai"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_get_turn_found(self) -> None:
        session = _mock_session()
        turn = SimpleNamespace(id=uuid4())
        session.execute = AsyncMock(return_value=_result_with_scalar(turn))
        result = await TimelineRepository.get_turn(session, uuid4())
        assert result is not None

    async def test_get_turn_not_found(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        result = await TimelineRepository.get_turn(session, uuid4())
        assert result is None

    async def test_get_turn_by_idempotency_found(self) -> None:
        session = _mock_session()
        turn = SimpleNamespace(id=uuid4())
        session.execute = AsyncMock(return_value=_result_with_scalar(turn))
        result = await TimelineRepository.get_turn_by_idempotency(session, uuid4(), "key")
        assert result is not None

    async def test_get_turn_by_idempotency_not_found(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        result = await TimelineRepository.get_turn_by_idempotency(session, uuid4(), "key")
        assert result is None

    async def test_update_turn_status_success(self) -> None:
        session = _mock_session()
        row = SimpleNamespace(id=uuid4(), status="running")
        session.execute = AsyncMock(return_value=_result_with_scalar(row))
        result = await TimelineRepository.update_turn_status(session, uuid4(), "queued", "running")
        assert result.status == "running"

    async def test_update_turn_status_mismatch_raises(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        with pytest.raises(AppError) as exc_info:
            await TimelineRepository.update_turn_status(session, uuid4(), "queued", "running")
        assert exc_info.value.code == "state_conflict"

    async def test_lock_turn_inputs_success(self) -> None:
        session = _mock_session()
        row = SimpleNamespace(id=uuid4(), status="planning")
        session.execute = AsyncMock(return_value=_result_with_scalar(row))
        result = await TimelineRepository.lock_turn_inputs(session, uuid4(), "v1", "v1")
        assert result.status == "planning"

    async def test_lock_turn_inputs_mismatch_raises(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        with pytest.raises(AppError) as exc_info:
            await TimelineRepository.lock_turn_inputs(session, uuid4(), "v1", "v1")
        assert exc_info.value.code == "state_conflict"


class TestTimelineRepositoryTurnContext:
    """TimelineRepository — Turn context tests."""

    async def test_insert_turn_context(self) -> None:
        session = _mock_session()
        revision_ids = [(uuid4(), 0), (uuid4(), 1)]
        await TimelineRepository.insert_turn_context(
            session, turn_id=uuid4(), conclusion_revision_ids=revision_ids
        )
        assert session.add.call_count == 2
        session.flush.assert_awaited_once()

    async def test_insert_turn_context_empty(self) -> None:
        session = _mock_session()
        await TimelineRepository.insert_turn_context(
            session, turn_id=uuid4(), conclusion_revision_ids=[]
        )
        session.flush.assert_awaited_once()

    async def test_list_turn_context(self) -> None:
        session = _mock_session()
        rows = [SimpleNamespace(turn_id=uuid4(), position=0)]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result = await TimelineRepository.list_turn_context(session, uuid4())
        assert len(result) == 1

    async def test_list_turn_context_empty(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalars([]))
        result = await TimelineRepository.list_turn_context(session, uuid4())
        assert result == []


class TestTimelineRepositoryListTurns:
    """TimelineRepository — list_turns pagination tests."""

    async def test_list_turns_empty(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalars([]))
        rows, cursor = await TimelineRepository.list_turns(session, uuid4())
        assert rows == []
        assert cursor is None

    async def test_list_turns_with_rows_no_next(self) -> None:
        session = _mock_session()
        rows = [SimpleNamespace(id=uuid4(), turn_number=1)]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result, cursor = await TimelineRepository.list_turns(session, uuid4())
        assert len(result) == 1
        assert cursor is None

    async def test_list_turns_with_next_cursor(self) -> None:
        session = _mock_session()
        rows = [
            SimpleNamespace(id=uuid4(), turn_number=2),
            SimpleNamespace(id=uuid4(), turn_number=1),
        ]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result, cursor = await TimelineRepository.list_turns(session, uuid4(), page_size=1)
        assert len(result) == 1
        assert cursor is not None

    async def test_list_turns_with_cursor(self) -> None:
        from packages.research.timeline.repository import encode_cursor

        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalars([]))
        cursor = encode_cursor(5, uuid4())
        rows, _ = await TimelineRepository.list_turns(session, uuid4(), cursor=cursor)
        assert rows == []

    async def test_list_turns_invalid_page_size(self) -> None:
        session = _mock_session()
        with pytest.raises(AppError):
            await TimelineRepository.list_turns(session, uuid4(), page_size=0)
        with pytest.raises(AppError):
            await TimelineRepository.list_turns(session, uuid4(), page_size=51)

    async def test_get_active_run_status_running(self) -> None:
        session = _mock_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ("running",)
        session.execute = AsyncMock(return_value=result_mock)
        status = await TimelineRepository.get_active_run_status(session, uuid4())
        assert status == "running"

    async def test_get_active_run_status_none(self) -> None:
        session = _mock_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        status = await TimelineRepository.get_active_run_status(session, uuid4())
        assert status is None


class TestTimelineRepositoryBatch:
    """TimelineRepository — recommendation batch tests."""

    async def test_insert_batch(self) -> None:
        session = _mock_session()
        result = await TimelineRepository.insert_batch(
            session,
            workspace_id=uuid4(),
            snapshot_id=uuid4(),
            mode="initial",
            prompt_template_version="v1",
            output_schema_version="v1",
            idempotency_key="key",
        )
        assert result.mode == "initial"
        assert result.status == "queued"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_get_batch_found(self) -> None:
        session = _mock_session()
        batch = SimpleNamespace(id=uuid4())
        session.execute = AsyncMock(return_value=_result_with_scalar(batch))
        result = await TimelineRepository.get_batch(session, uuid4())
        assert result is not None

    async def test_get_batch_not_found(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        result = await TimelineRepository.get_batch(session, uuid4())
        assert result is None

    async def test_get_batch_by_idempotency_found(self) -> None:
        session = _mock_session()
        batch = SimpleNamespace(id=uuid4())
        session.execute = AsyncMock(return_value=_result_with_scalar(batch))
        result = await TimelineRepository.get_batch_by_idempotency(session, uuid4(), "key")
        assert result is not None

    async def test_get_batch_by_idempotency_not_found(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        result = await TimelineRepository.get_batch_by_idempotency(session, uuid4(), "key")
        assert result is None

    async def test_update_batch_status_success(self) -> None:
        session = _mock_session()
        row = SimpleNamespace(id=uuid4(), status="succeeded")
        session.execute = AsyncMock(return_value=_result_with_scalar(row))
        result = await TimelineRepository.update_batch_status(
            session, uuid4(), "running", "succeeded"
        )
        assert result.status == "succeeded"

    async def test_update_batch_status_mismatch_raises(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        with pytest.raises(AppError) as exc_info:
            await TimelineRepository.update_batch_status(session, uuid4(), "running", "succeeded")
        assert exc_info.value.code == "state_conflict"

    async def test_update_batch_status_with_extra_values(self) -> None:
        session = _mock_session()
        row = SimpleNamespace(id=uuid4(), status="failed")
        session.execute = AsyncMock(return_value=_result_with_scalar(row))
        await TimelineRepository.update_batch_status(
            session, uuid4(), "running", "failed", error_code="RuntimeError"
        )
        session.execute.assert_awaited_once()

    async def test_insert_recommendation_items(self) -> None:
        session = _mock_session()
        items = [
            {"question": "问题1", "rationale": "理由1", "evidence_hints": ["hint1"]},
            {"question": "问题2", "rationale": "理由2", "evidence_hints": []},
        ]
        result = await TimelineRepository.insert_recommendation_items(
            session, batch_id=uuid4(), items=items
        )
        assert len(result) == 2
        assert session.add.call_count == 2
        session.flush.assert_awaited_once()

    async def test_insert_recommendation_items_empty(self) -> None:
        session = _mock_session()
        result = await TimelineRepository.insert_recommendation_items(
            session, batch_id=uuid4(), items=[]
        )
        assert result == []
        session.flush.assert_awaited_once()

    async def test_list_recommendation_items(self) -> None:
        session = _mock_session()
        rows = [SimpleNamespace(id=uuid4(), question="q1")]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result = await TimelineRepository.list_recommendation_items(session, uuid4())
        assert len(result) == 1

    async def test_list_recommendation_items_empty(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalars([]))
        result = await TimelineRepository.list_recommendation_items(session, uuid4())
        assert result == []


class TestTimelineRepositoryTurnResult:
    """TimelineRepository — turn result tests."""

    async def test_insert_turn_result(self) -> None:
        session = _mock_session()
        result = await TimelineRepository.insert_turn_result(
            session,
            turn_id=uuid4(),
            run_id=uuid4(),
            result_kind="analysis",
            summary="摘要",
            structured_output={"k": "v"},
            method_summary="方法",
            evidence_refs=["ref1"],
            limitations="limit",
        )
        assert result.result_kind == "analysis"
        assert result.summary == "摘要"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_insert_turn_result_with_defaults(self) -> None:
        session = _mock_session()
        result = await TimelineRepository.insert_turn_result(
            session,
            turn_id=uuid4(),
            run_id=uuid4(),
            result_kind="analysis",
        )
        assert result.summary is None
        assert result.evidence_refs == []

    async def test_get_turn_result_found(self) -> None:
        session = _mock_session()
        result_row = SimpleNamespace(id=uuid4(), summary="摘要")
        session.execute = AsyncMock(return_value=_result_with_scalar(result_row))
        result = await TimelineRepository.get_turn_result(session, uuid4())
        assert result is not None

    async def test_get_turn_result_not_found(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        result = await TimelineRepository.get_turn_result(session, uuid4())
        assert result is None


class TestTimelineRepositoryExtraction:
    """TimelineRepository — extraction job tests."""

    async def test_insert_extraction_job(self) -> None:
        session = _mock_session()
        result = await TimelineRepository.insert_extraction_job(
            session, workspace_id=uuid4(), turn_id=uuid4(), run_id=uuid4()
        )
        assert result.status == "queued"
        assert result.attempt == 1
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_get_extraction_job_found(self) -> None:
        session = _mock_session()
        job = SimpleNamespace(id=uuid4(), status="running")
        session.execute = AsyncMock(return_value=_result_with_scalar(job))
        result = await TimelineRepository.get_extraction_job(session, uuid4())
        assert result is not None

    async def test_get_extraction_job_not_found(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        result = await TimelineRepository.get_extraction_job(session, uuid4())
        assert result is None

    async def test_get_extraction_by_run_found(self) -> None:
        session = _mock_session()
        job = SimpleNamespace(id=uuid4())
        session.execute = AsyncMock(return_value=_result_with_scalar(job))
        result = await TimelineRepository.get_extraction_by_run(session, uuid4())
        assert result is not None

    async def test_get_extraction_by_run_not_found(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        result = await TimelineRepository.get_extraction_by_run(session, uuid4())
        assert result is None

    async def test_update_extraction_status_success(self) -> None:
        session = _mock_session()
        row = SimpleNamespace(id=uuid4(), status="completed")
        session.execute = AsyncMock(return_value=_result_with_scalar(row))
        result = await TimelineRepository.update_extraction_status(
            session, uuid4(), "running", "completed"
        )
        assert result.status == "completed"

    async def test_update_extraction_status_mismatch_raises(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalar(None))
        with pytest.raises(AppError) as exc_info:
            await TimelineRepository.update_extraction_status(
                session, uuid4(), "running", "completed"
            )
        assert exc_info.value.code == "state_conflict"

    async def test_update_extraction_status_with_extra_values(self) -> None:
        session = _mock_session()
        row = SimpleNamespace(id=uuid4(), status="failed")
        session.execute = AsyncMock(return_value=_result_with_scalar(row))
        await TimelineRepository.update_extraction_status(
            session, uuid4(), "running", "failed", error_code="TimeoutError"
        )
        session.execute.assert_awaited_once()

    async def test_update_heartbeat(self) -> None:
        session = _mock_session()
        await TimelineRepository.update_heartbeat(session, uuid4())
        session.execute.assert_awaited_once()

    async def test_list_stale_running_extractions(self) -> None:
        session = _mock_session()
        rows = [SimpleNamespace(id=uuid4(), status="running")]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result = await TimelineRepository.list_stale_running_extractions(session)
        assert len(result) == 1

    async def test_list_stale_running_extractions_empty(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalars([]))
        result = await TimelineRepository.list_stale_running_extractions(session)
        assert result == []

    async def test_list_queued_extractions_without_delivery(self) -> None:
        session = _mock_session()
        rows = [SimpleNamespace(id=uuid4(), status="queued")]
        session.execute = AsyncMock(return_value=_result_with_scalars(rows))
        result = await TimelineRepository.list_queued_extractions_without_delivery(session)
        assert len(result) == 1

    async def test_list_queued_extractions_without_delivery_empty(self) -> None:
        session = _mock_session()
        session.execute = AsyncMock(return_value=_result_with_scalars([]))
        result = await TimelineRepository.list_queued_extractions_without_delivery(session)
        assert result == []
