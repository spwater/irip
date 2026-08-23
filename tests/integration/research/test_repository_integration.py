"""研究域子仓库集成测试。

覆盖 11 个低覆盖率的子仓库模块，使用真实测试数据库验证 CRUD、
查询、分页、错误路径等全部公开方法：

- packages/research/repository/_cursor.py      — 游标编解码
- packages/research/repository/workspace.py     — WorkspaceRepository
- packages/research/repository/view.py          — ViewRepository
- packages/research/repository/search.py        — SearchRepository
- packages/research/repository/result.py        — ResultRepository
- packages/research/repository/dataset.py       — DatasetRepository
- packages/research/repository/insight.py       — InsightRepository
- packages/research/repository/knowledge.py     — KnowledgeReferenceRepository
- packages/research/repository/evidence.py      — EvidenceRefRepository + SnapshotRepository
- packages/research/repository/favorite.py      — FavoriteRepository
- packages/research/repository/lineage.py       — LineageEdgeRepository

通过 ``async_session_factory`` + ``test_user`` fixture 连接测试库，
每个测试自行创建并清理工作空间（CASCADE 级联删除子表）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.ids import new_id
from packages.research.entities import (
    ResearchEvidenceSnapshot,
    ResearchWorkspace,
)
from packages.research.execution.entities_trusted import (
    ResearchAnalysisPlanVersion,
    ResearchAnalysisRun,
)
from packages.research.repository._cursor import _decode_cursor, _encode_cursor
from packages.research.repository.dataset import DatasetRepository
from packages.research.repository.evidence import EvidenceRefRepository, SnapshotRepository
from packages.research.repository.favorite import FavoriteRepository
from packages.research.repository.insight import InsightRepository
from packages.research.repository.knowledge import KnowledgeReferenceRepository
from packages.research.repository.lineage import LineageEdgeRepository
from packages.research.repository.result import ResultRepository
from packages.research.repository.search import SearchRepository
from packages.research.repository.view import ViewRepository
from packages.research.repository.workspace import WorkspaceRepository
from packages.research.timeline.entities import ResearchTurn

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_CONTENT_HASH = "a" * 64


@dataclass(frozen=True)
class _Seed:
    """Minimal workspace → snapshot → plan → run chain IDs."""

    workspace_id: UUID
    snapshot_id: UUID
    plan_version_id: UUID
    run_id: UUID
    owner_id: UUID
    dept_id: UUID


async def _seed_chain(
    factory: async_sessionmaker[AsyncSession],
    owner_id: UUID,
    dept_id: UUID,
    *,
    ws_name: str = "repo-integration-test",
) -> _Seed:
    """Create workspace → snapshot → turn → plan → run, return ID bundle.

    FK dependencies are flushed in layered order within a single transaction.
    The turn is required because ``research_analysis_plan_version.turn_id``
    has a DB-level NOT NULL constraint.
    """
    async with factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=owner_id,
                department_id=dept_id,
                name=ws_name,
            )
            session.add(ws)
            await session.flush()

            snap = ResearchEvidenceSnapshot(
                id=new_id(),
                workspace_id=ws.id,
                snapshot_number=1,
                content_hash=_CONTENT_HASH,
                permission_envelope={},
                field_manifest={},
                source_refs=[],
                created_by=owner_id,
            )
            session.add(snap)
            await session.flush()

            turn = ResearchTurn(
                id=new_id(),
                workspace_id=ws.id,
                turn_number=1,
                kind="analysis",
                status="succeeded",
                question_text_snapshot="seed question",
                question_origin="manual",
                evidence_snapshot_id=snap.id,
                idempotency_key=f"seed-{ws.id}",
            )
            session.add(turn)
            await session.flush()

            plan = ResearchAnalysisPlanVersion(
                id=new_id(),
                workspace_id=ws.id,
                version_number=1,
                dag_structure={"steps": []},
                status="confirmed",
                created_by=owner_id,
                turn_id=turn.id,
            )
            session.add(plan)
            await session.flush()

            run = ResearchAnalysisRun(
                id=new_id(),
                workspace_id=ws.id,
                plan_version_id=plan.id,
                snapshot_id=snap.id,
                run_number=1,
                status="succeeded",
                image_digest="test-digest",
                created_by=owner_id,
                attempt_number=1,
                turn_id=turn.id,
            )
            session.add(run)
            await session.flush()

            return _Seed(
                workspace_id=ws.id,
                snapshot_id=snap.id,
                plan_version_id=plan.id,
                run_id=run.id,
                owner_id=owner_id,
                dept_id=dept_id,
            )


async def _cleanup_workspace(
    factory: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
) -> None:
    """Delete workspace; CASCADE removes all child rows."""
    async with factory() as session:
        async with session.begin():
            await session.execute(
                sa.text("DELETE FROM research_workspace WHERE id = :id"),
                {"id": workspace_id},
            )


# ---------------------------------------------------------------------------
# _cursor encode / decode
# ---------------------------------------------------------------------------


class TestCursorEncodeDecode:
    """Cursor pagination helper tests (pure, no DB)."""

    @pytest.mark.integration
    def test_roundtrip(self) -> None:
        """Encode then decode returns the same timestamp and UUID."""
        ts = datetime(2026, 1, 15, 12, 30, 45, tzinfo=UTC)
        entity_id = uuid4()
        cursor = _encode_cursor(ts, entity_id)
        decoded_ts, decoded_id = _decode_cursor(cursor)
        assert decoded_ts == ts
        assert decoded_id == entity_id

    @pytest.mark.integration
    def test_decode_invalid_base64(self) -> None:
        """Non-ASCII input triggers UnicodeEncodeError → ValueError."""
        # urlsafe_b64decode first calls .encode("ascii"), so a non-ASCII
        # string triggers UnicodeEncodeError which the function wraps as
        # "无效的游标编码".
        with pytest.raises(ValueError, match="无效的游标编码"):
            _decode_cursor("这不是有效的base64")

    @pytest.mark.integration
    def test_decode_invalid_json(self) -> None:
        """Valid base64 but invalid JSON raises ValueError."""
        import base64

        bad = base64.urlsafe_b64encode(b"not json").decode("ascii")
        with pytest.raises(ValueError, match="无效的游标 JSON"):
            _decode_cursor(bad)

    @pytest.mark.integration
    def test_decode_missing_fields(self) -> None:
        """JSON missing required keys raises ValueError."""
        import base64
        import json

        payload = json.dumps({"v": "2026-01-01T00:00:00"}).encode()
        cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(ValueError, match="游标缺少必要字段"):
            _decode_cursor(cursor)

    @pytest.mark.integration
    def test_decode_invalid_timestamp(self) -> None:
        """v field not a valid ISO datetime raises ValueError."""
        import base64
        import json

        payload = json.dumps({"v": "not-a-date", "id": str(uuid4())}).encode()
        cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(ValueError, match="游标 v 字段不是合法 ISO 时间"):
            _decode_cursor(cursor)

    @pytest.mark.integration
    def test_decode_invalid_uuid(self) -> None:
        """id field not a valid UUID raises ValueError."""
        import base64
        import json

        payload = json.dumps({"v": "2026-01-01T00:00:00", "id": "not-a-uuid"}).encode()
        cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(ValueError, match="游标 id 字段不是合法 UUID"):
            _decode_cursor(cursor)


# ---------------------------------------------------------------------------
# WorkspaceRepository
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_workspace_insert_and_get(async_session_factory, test_user) -> None:
    """Insert workspace then get it back by id + owner."""
    owner = test_user.user_id
    dept = test_user.department_id
    try:
        async with async_session_factory() as session:
            async with session.begin():
                ws = await WorkspaceRepository.insert_workspace(
                    session,
                    owner_user_id=owner,
                    department_id=dept,
                    name="ws-get-test",
                )
                ws_id = ws.id

        async with async_session_factory() as session:
            found = await WorkspaceRepository.get_workspace(session, ws_id, owner)
            assert found is not None
            assert found.name == "ws-get-test"
            assert found.status == "draft"
            assert found.next_turn_number == 1

        # Wrong owner returns None
        async with async_session_factory() as session:
            assert await WorkspaceRepository.get_workspace(session, ws_id, uuid4()) is None

        # Non-existent id returns None
        async with async_session_factory() as session:
            assert await WorkspaceRepository.get_workspace(session, uuid4(), owner) is None
    finally:
        await _cleanup_workspace(async_session_factory, ws_id)


@pytest.mark.integration
async def test_workspace_list_with_pagination(async_session_factory, test_user) -> None:
    """list_workspaces returns keyset-paginated results with next cursor."""
    owner = test_user.user_id
    dept = test_user.department_id
    ws_ids: list[UUID] = []
    try:
        async with async_session_factory() as session:
            async with session.begin():
                for i in range(5):
                    ws = await WorkspaceRepository.insert_workspace(
                        session,
                        owner_user_id=owner,
                        department_id=dept,
                        name=f"ws-page-{i}",
                    )
                    ws_ids.append(ws.id)

        # Page size 2 — first page
        async with async_session_factory() as session:
            page1, cursor1 = await WorkspaceRepository.list_workspaces(
                session,
                owner,
                page_size=2,
            )
            assert len(page1) == 2
            assert cursor1 is not None

            # Second page
            page2, cursor2 = await WorkspaceRepository.list_workspaces(
                session,
                owner,
                cursor=cursor1,
                page_size=2,
            )
            assert len(page2) == 2
            assert cursor2 is not None

            # Third page (1 remaining)
            page3, cursor3 = await WorkspaceRepository.list_workspaces(
                session,
                owner,
                cursor=cursor2,
                page_size=2,
            )
            assert len(page3) == 1
            assert cursor3 is None

        # All 5 ids are covered across pages
        all_ids = {w.id for w in page1 + page2 + page3}
        assert all_ids == set(ws_ids)
    finally:
        for wid in ws_ids:
            await _cleanup_workspace(async_session_factory, wid)


@pytest.mark.integration
async def test_workspace_list_with_status_filter(async_session_factory, test_user) -> None:
    """list_workspaces filters by status when provided."""
    owner = test_user.user_id
    dept = test_user.department_id
    ws_ids: list[UUID] = []
    try:
        async with async_session_factory() as session:
            async with session.begin():
                ws1 = await WorkspaceRepository.insert_workspace(
                    session,
                    owner_user_id=owner,
                    department_id=dept,
                    name="draft-ws",
                )
                ws2 = await WorkspaceRepository.insert_workspace(
                    session,
                    owner_user_id=owner,
                    department_id=dept,
                    name="archived-ws",
                    status="archived",
                )
                ws_ids.extend([ws1.id, ws2.id])

        async with async_session_factory() as session:
            # No filter: both
            all_ws, _ = await WorkspaceRepository.list_workspaces(session, owner)
            assert len(all_ws) == 2

            # Filter draft: only ws1
            draft_ws, _ = await WorkspaceRepository.list_workspaces(
                session,
                owner,
                status="draft",
            )
            assert len(draft_ws) == 1
            assert draft_ws[0].name == "draft-ws"

            # Filter archived: only ws2
            archived_ws, _ = await WorkspaceRepository.list_workspaces(
                session,
                owner,
                status="archived",
            )
            assert len(archived_ws) == 1
            assert archived_ws[0].name == "archived-ws"
    finally:
        for wid in ws_ids:
            await _cleanup_workspace(async_session_factory, wid)


@pytest.mark.integration
async def test_workspace_update_status(async_session_factory, test_user) -> None:
    """update_workspace_status changes status in DB."""
    owner = test_user.user_id
    dept = test_user.department_id
    try:
        async with async_session_factory() as session:
            async with session.begin():
                ws = await WorkspaceRepository.insert_workspace(
                    session,
                    owner_user_id=owner,
                    department_id=dept,
                    name="ws-status",
                )
                ws_id = ws.id

        async with async_session_factory() as session:
            async with session.begin():
                await WorkspaceRepository.update_workspace_status(session, ws_id, "archived")

        async with async_session_factory() as session:
            found = await WorkspaceRepository.get_workspace(session, ws_id, owner)
            assert found is not None
            assert found.status == "archived"
    finally:
        await _cleanup_workspace(async_session_factory, ws_id)


@pytest.mark.integration
async def test_workspace_update_name(async_session_factory, test_user) -> None:
    """update_workspace_name changes name in DB."""
    owner = test_user.user_id
    dept = test_user.department_id
    try:
        async with async_session_factory() as session:
            async with session.begin():
                ws = await WorkspaceRepository.insert_workspace(
                    session,
                    owner_user_id=owner,
                    department_id=dept,
                    name="old-name",
                )
                ws_id = ws.id

        async with async_session_factory() as session:
            async with session.begin():
                await WorkspaceRepository.update_workspace_name(session, ws_id, "new-name")

        async with async_session_factory() as session:
            found = await WorkspaceRepository.get_workspace(session, ws_id, owner)
            assert found is not None
            assert found.name == "new-name"
    finally:
        await _cleanup_workspace(async_session_factory, ws_id)


@pytest.mark.integration
async def test_workspace_update_latest_snapshot(async_session_factory, test_user) -> None:
    """update_workspace_latest_snapshot sets the snapshot pointer."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                await WorkspaceRepository.update_workspace_latest_snapshot(
                    session,
                    seed.workspace_id,
                    seed.snapshot_id,
                )

        async with async_session_factory() as session:
            found = await WorkspaceRepository.get_workspace(
                session,
                seed.workspace_id,
                seed.owner_id,
            )
            assert found is not None
            assert found.latest_snapshot_id == seed.snapshot_id
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_workspace_allocate_turn_number(async_session_factory, test_user) -> None:
    """allocate_turn_number returns sequential numbers and increments counter."""
    owner = test_user.user_id
    dept = test_user.department_id
    try:
        async with async_session_factory() as session:
            async with session.begin():
                ws = await WorkspaceRepository.insert_workspace(
                    session,
                    owner_user_id=owner,
                    department_id=dept,
                    name="ws-turn",
                )
                ws_id = ws.id

        # First allocation returns 1 (default next_turn_number)
        async with async_session_factory() as session:
            async with session.begin():
                n1 = await WorkspaceRepository.allocate_turn_number(session, ws_id)
                n2 = await WorkspaceRepository.allocate_turn_number(session, ws_id)
        assert n1 == 1
        assert n2 == 2

        # Counter persisted
        async with async_session_factory() as session:
            found = await WorkspaceRepository.get_workspace(session, ws_id, owner)
            assert found is not None
            assert found.next_turn_number == 3
    finally:
        await _cleanup_workspace(async_session_factory, ws_id)


@pytest.mark.integration
async def test_workspace_delete(async_session_factory, test_user) -> None:
    """delete_workspace physically removes the row."""
    owner = test_user.user_id
    dept = test_user.department_id
    async with async_session_factory() as session:
        async with session.begin():
            ws = await WorkspaceRepository.insert_workspace(
                session,
                owner_user_id=owner,
                department_id=dept,
                name="ws-delete",
            )
            ws_id = ws.id

    async with async_session_factory() as session:
        async with session.begin():
            await WorkspaceRepository.delete_workspace(session, ws_id)

    async with async_session_factory() as session:
        assert await WorkspaceRepository.get_workspace(session, ws_id, owner) is None


# ---------------------------------------------------------------------------
# EvidenceRefRepository + SnapshotRepository
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_evidence_ref_crud(async_session_factory, test_user) -> None:
    """Insert, list, get, update status, count active for evidence refs."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        source_id = new_id()
        # Insert two refs
        async with async_session_factory() as session:
            async with session.begin():
                ref1 = await EvidenceRefRepository.insert_evidence_ref(
                    session,
                    workspace_id=seed.workspace_id,
                    source_namespace="core:fact",
                    source_id=source_id,
                    source_name="Fact A",
                    added_by=seed.owner_id,
                )
                ref2 = await EvidenceRefRepository.insert_evidence_ref(
                    session,
                    workspace_id=seed.workspace_id,
                    source_namespace="core:fact",
                    source_id=new_id(),
                    source_version="v2",
                    source_name="Fact B",
                    added_by=seed.owner_id,
                )
                ref_ids = [ref1.id, ref2.id]

        # List all (2 active)
        async with async_session_factory() as session:
            refs = await EvidenceRefRepository.list_evidence_refs(session, seed.workspace_id)
            assert len(refs) == 2

            # Filter by status=active
            active_refs = await EvidenceRefRepository.list_evidence_refs(
                session,
                seed.workspace_id,
                status="active",
            )
            assert len(active_refs) == 2

            # Count active
            count = await EvidenceRefRepository.count_active_evidence_refs(
                session, seed.workspace_id
            )
            assert count == 2

            # Get one ref
            fetched = await EvidenceRefRepository.get_evidence_ref(
                session, ref_ids[0], seed.workspace_id
            )
            assert fetched is not None
            assert fetched.source_name == "Fact A"

            # Get with wrong workspace returns None
            assert (
                await EvidenceRefRepository.get_evidence_ref(session, ref_ids[0], uuid4()) is None
            )

        # Soft-delete ref1
        async with async_session_factory() as session:
            async with session.begin():
                await EvidenceRefRepository.update_evidence_ref_status(
                    session, ref_ids[0], "removed"
                )

        async with async_session_factory() as session:
            # Count active now 1
            count = await EvidenceRefRepository.count_active_evidence_refs(
                session, seed.workspace_id
            )
            assert count == 1

            # List active only
            active_refs = await EvidenceRefRepository.list_evidence_refs(
                session,
                seed.workspace_id,
                status="active",
            )
            assert len(active_refs) == 1
            assert active_refs[0].id == ref_ids[1]

            # List removed
            removed_refs = await EvidenceRefRepository.list_evidence_refs(
                session,
                seed.workspace_id,
                status="removed",
            )
            assert len(removed_refs) == 1
            assert removed_refs[0].id == ref_ids[0]
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_snapshot_crud(async_session_factory, test_user) -> None:
    """Insert, list, get latest for evidence snapshots."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        # Insert a second snapshot (the seed already created #1)
        async with async_session_factory() as session:
            async with session.begin():
                snap2 = await SnapshotRepository.insert_snapshot(
                    session,
                    workspace_id=seed.workspace_id,
                    snapshot_number=2,
                    content_hash="b" * 64,
                    permission_envelope={"scope": "dept"},
                    field_manifest={"fact_id": ["col1"]},
                    source_refs=[{"namespace": "core:fact", "id": str(new_id())}],
                    created_by=seed.owner_id,
                )
                snap2_id = snap2.id

        async with async_session_factory() as session:
            # List all snapshots (descending by number)
            snapshots = await SnapshotRepository.list_snapshots(session, seed.workspace_id)
            assert len(snapshots) == 2
            assert snapshots[0].snapshot_number == 2
            assert snapshots[1].snapshot_number == 1

            # Latest is #2
            latest = await SnapshotRepository.get_latest_snapshot(session, seed.workspace_id)
            assert latest is not None
            assert latest.id == snap2_id
            assert latest.snapshot_number == 2

        # Empty workspace returns no latest
        empty_ws_id = new_id()
        async with async_session_factory() as session:
            async with session.begin():
                ws = ResearchWorkspace(
                    id=empty_ws_id,
                    owner_user_id=seed.owner_id,
                    department_id=seed.dept_id,
                    name="empty-ws",
                )
                session.add(ws)

        async with async_session_factory() as session:
            latest_none = await SnapshotRepository.get_latest_snapshot(session, empty_ws_id)
            assert latest_none is None

            snapshots_none = await SnapshotRepository.list_snapshots(session, empty_ws_id)
            assert snapshots_none == []

        async with async_session_factory() as session:
            async with session.begin():
                await session.execute(
                    sa.text("DELETE FROM research_workspace WHERE id = :id"),
                    {"id": empty_ws_id},
                )
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


# ---------------------------------------------------------------------------
# DatasetRepository
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_dataset_insert_get_list(async_session_factory, test_user) -> None:
    """Insert dataset, get by id, list by workspace."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                ds = await DatasetRepository.insert_dataset(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="test-dataset",
                    summary="A test dataset",
                    tags=["tag1", "tag2"],
                    source_run_id=seed.run_id,
                )
                ds_id = ds.id

        async with async_session_factory() as session:
            found = await DatasetRepository.get_dataset(session, ds_id)
            assert found is not None
            assert found.name == "test-dataset"
            assert found.summary == "A test dataset"
            assert found.tags == ["tag1", "tag2"]
            assert found.status == "confirmed"
            assert found.current_version == 0

            # With workspace filter
            found_ws = await DatasetRepository.get_dataset(session, ds_id, seed.workspace_id)
            assert found_ws is not None

            # Wrong workspace returns None
            assert await DatasetRepository.get_dataset(session, ds_id, uuid4()) is None

            # List by workspace
            datasets = await DatasetRepository.list_datasets(session, seed.workspace_id)
            assert len(datasets) == 1
            assert datasets[0].id == ds_id
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_dataset_update_metadata_and_version(async_session_factory, test_user) -> None:
    """Update dataset name/summary/tags and current_version."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                ds = await DatasetRepository.insert_dataset(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="ds-update",
                    source_run_id=seed.run_id,
                )
                ds_id = ds.id

        async with async_session_factory() as session:
            async with session.begin():
                await DatasetRepository.update_dataset_metadata(
                    session,
                    ds_id,
                    name="updated-name",
                    summary="updated summary",
                    tags=["new"],
                )
                await DatasetRepository.update_dataset_current_version(session, ds_id, 3)

        async with async_session_factory() as session:
            found = await DatasetRepository.get_dataset(session, ds_id)
            assert found is not None
            assert found.name == "updated-name"
            assert found.summary == "updated summary"
            assert found.tags == ["new"]
            assert found.current_version == 3
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_dataset_search(async_session_factory, test_user) -> None:
    """search_derived_datasets filters by owner, query, and workspace."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                await DatasetRepository.insert_dataset(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="alpha-dataset",
                    source_run_id=seed.run_id,
                )
                await DatasetRepository.insert_dataset(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="beta-dataset",
                    status="draft",
                    source_run_id=seed.run_id,
                )

        async with async_session_factory() as session:
            # No query: only confirmed datasets (alpha)
            all_ds = await DatasetRepository.search_derived_datasets(session, seed.owner_id)
            assert len(all_ds) == 1
            assert all_ds[0].name == "alpha-dataset"

            # Query "alpha"
            queried = await DatasetRepository.search_derived_datasets(
                session,
                seed.owner_id,
                query="alpha",
            )
            assert len(queried) == 1
            assert queried[0].name == "alpha-dataset"

            # Query "beta" — no results (beta is draft, filtered out)
            queried_beta = await DatasetRepository.search_derived_datasets(
                session,
                seed.owner_id,
                query="beta",
            )
            assert len(queried_beta) == 0

            # Filter by workspace
            ws_filtered = await DatasetRepository.search_derived_datasets(
                session,
                seed.owner_id,
                workspace_id=seed.workspace_id,
            )
            assert len(ws_filtered) == 1

            # Wrong owner returns empty
            wrong_owner = await DatasetRepository.search_derived_datasets(session, uuid4())
            assert len(wrong_owner) == 0
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_dataset_version_crud(async_session_factory, test_user) -> None:
    """Insert, get, list, get latest for dataset versions."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                ds = await DatasetRepository.insert_dataset(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="ds-versioned",
                    source_run_id=seed.run_id,
                )
                ds_id = ds.id

                v1 = await DatasetRepository.insert_dataset_version(
                    session,
                    dataset_id=ds_id,
                    version_number=1,
                    metadata_content={"description": "v1"},
                    points_content=[{"name": "p1", "value": 42, "unit": "kg"}],
                    series_content=[{"name": "s1", "columns": ["x"], "rows": [[1]]}],
                    field_manifest=[{"name": "x", "type": "number"}],
                    source_run_id=seed.run_id,
                    content_hash="c1" * 32,
                    created_by=seed.owner_id,
                )
                v1_id = v1.id

                v2 = await DatasetRepository.insert_dataset_version(
                    session,
                    dataset_id=ds_id,
                    version_number=2,
                    metadata_content={"description": "v2"},
                    points_content=[],
                    series_content=[],
                    field_manifest=[],
                    source_run_id=seed.run_id,
                    content_hash="c2" * 32,
                    created_by=seed.owner_id,
                )

        async with async_session_factory() as session:
            # Get specific version
            found_v1 = await DatasetRepository.get_dataset_version(session, ds_id, 1)
            assert found_v1 is not None
            assert found_v1.id == v1_id
            assert found_v1.metadata_content == {"description": "v1"}

            # Non-existent version
            assert await DatasetRepository.get_dataset_version(session, ds_id, 99) is None

            # List versions (descending)
            versions = await DatasetRepository.list_dataset_versions(session, ds_id)
            assert len(versions) == 2
            assert versions[0].version_number == 2
            assert versions[1].version_number == 1

            # Latest
            latest = await DatasetRepository.get_latest_dataset_version(session, ds_id)
            assert latest is not None
            assert latest.id == v2.id

            # No versions for unknown dataset
            assert await DatasetRepository.get_latest_dataset_version(session, uuid4()) is None
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


# ---------------------------------------------------------------------------
# ViewRepository
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_view_insert_get_list(async_session_factory, test_user) -> None:
    """Insert view, get by id, list by workspace."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                view = await ViewRepository.insert_view(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="test-view",
                    caption="A caption",
                    display_order=1,
                    source_run_id=seed.run_id,
                )
                view_id = view.id

        async with async_session_factory() as session:
            found = await ViewRepository.get_view(session, view_id)
            assert found is not None
            assert found.name == "test-view"
            assert found.caption == "A caption"
            assert found.display_order == 1
            assert found.status == "confirmed"
            assert found.current_version == 0

            # With workspace filter
            found_ws = await ViewRepository.get_view(session, view_id, seed.workspace_id)
            assert found_ws is not None

            # Wrong workspace returns None
            assert await ViewRepository.get_view(session, view_id, uuid4()) is None

            # List views
            views = await ViewRepository.list_views(session, seed.workspace_id)
            assert len(views) == 1
            assert views[0].id == view_id
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_view_update_metadata_and_version(async_session_factory, test_user) -> None:
    """Update view name/caption/display_order and current_version."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                view = await ViewRepository.insert_view(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="v-update",
                    source_run_id=seed.run_id,
                )
                view_id = view.id

        async with async_session_factory() as session:
            async with session.begin():
                await ViewRepository.update_view_metadata(
                    session,
                    view_id,
                    name="updated",
                    caption="new caption",
                    display_order=5,
                )
                await ViewRepository.update_view_current_version(session, view_id, 2)

        async with async_session_factory() as session:
            found = await ViewRepository.get_view(session, view_id)
            assert found is not None
            assert found.name == "updated"
            assert found.caption == "new caption"
            assert found.display_order == 5
            assert found.current_version == 2
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_view_version_crud(async_session_factory, test_user) -> None:
    """Insert, get, list view versions."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                view = await ViewRepository.insert_view(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="v-ver",
                    source_run_id=seed.run_id,
                )
                view_id = view.id

                vv1 = await ViewRepository.insert_view_version(
                    session,
                    view_id=view_id,
                    version_number=1,
                    image_storage_path="charts/v1.png",
                    image_format="png",
                    image_width=800,
                    image_height=600,
                    image_content_hash="h1" * 32,
                    source_run_id=seed.run_id,
                    created_by=seed.owner_id,
                )
                vv1_id = vv1.id

                await ViewRepository.insert_view_version(
                    session,
                    view_id=view_id,
                    version_number=2,
                    image_storage_path="charts/v2.pdf",
                    image_format="pdf",
                    image_content_hash="h2" * 32,
                    source_run_id=seed.run_id,
                    chart_description="Updated chart",
                    created_by=seed.owner_id,
                )

        async with async_session_factory() as session:
            # Get specific version
            found_v1 = await ViewRepository.get_view_version(session, view_id, 1)
            assert found_v1 is not None
            assert found_v1.id == vv1_id
            assert found_v1.image_format == "png"
            assert found_v1.image_width == 800

            # Non-existent version
            assert await ViewRepository.get_view_version(session, view_id, 99) is None

            # List versions (descending)
            versions = await ViewRepository.list_view_versions(session, view_id)
            assert len(versions) == 2
            assert versions[0].version_number == 2
            assert versions[0].chart_description == "Updated chart"
            assert versions[1].version_number == 1
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


# ---------------------------------------------------------------------------
# InsightRepository
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_insight_insert_get_list(async_session_factory, test_user) -> None:
    """Insert insight, get by id, list by workspace."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                insight = await InsightRepository.insert_insight(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="test-insight",
                    source_run_id=seed.run_id,
                )
                insight_id = insight.id

        async with async_session_factory() as session:
            found = await InsightRepository.get_insight(session, insight_id)
            assert found is not None
            assert found.name == "test-insight"
            assert found.status == "confirmed"
            assert found.current_version == 0

            # With workspace filter
            found_ws = await InsightRepository.get_insight(session, insight_id, seed.workspace_id)
            assert found_ws is not None

            # Wrong workspace returns None
            assert await InsightRepository.get_insight(session, insight_id, uuid4()) is None

            # List
            insights = await InsightRepository.list_insights(session, seed.workspace_id)
            assert len(insights) == 1
            assert insights[0].id == insight_id
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_insight_update_metadata_and_version(async_session_factory, test_user) -> None:
    """Update insight name and current_version."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                insight = await InsightRepository.insert_insight(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="ins-update",
                    source_run_id=seed.run_id,
                )
                insight_id = insight.id

        async with async_session_factory() as session:
            async with session.begin():
                await InsightRepository.update_insight_metadata(
                    session,
                    insight_id,
                    name="updated-insight",
                )
                await InsightRepository.update_insight_current_version(session, insight_id, 2)

        async with async_session_factory() as session:
            found = await InsightRepository.get_insight(session, insight_id)
            assert found is not None
            assert found.name == "updated-insight"
            assert found.current_version == 2
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_insight_version_crud(async_session_factory, test_user) -> None:
    """Insert, get, list, get latest for insight versions."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                insight = await InsightRepository.insert_insight(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="ins-ver",
                    source_run_id=seed.run_id,
                )
                insight_id = insight.id

                iv1 = await InsightRepository.insert_insight_version(
                    session,
                    insight_id=insight_id,
                    version_number=1,
                    conclusion="Result A is significant",
                    scope="All experiments",
                    evidence_refs=[{"snapshot_id": str(seed.snapshot_id)}],
                    method_refs=[],
                    confidence_level="high",
                    limitations="Small sample size",
                    evidence_source_label="experimental_data",
                    ai_original_text="AI suggested this",
                    created_by=seed.owner_id,
                )
                iv1_id = iv1.id

                await InsightRepository.insert_insight_version(
                    session,
                    insight_id=insight_id,
                    version_number=2,
                    conclusion="Result A confirmed",
                    scope="All experiments",
                    evidence_refs=[],
                    method_refs=[],
                    confidence_level="very high",
                    limitations="None",
                    evidence_source_label="experimental_data",
                    is_modified=True,
                    modification_note="Corrected scope",
                    source_run_id=seed.run_id,
                    created_by=seed.owner_id,
                )

        async with async_session_factory() as session:
            # Get specific version
            found_v1 = await InsightRepository.get_insight_version(session, insight_id, 1)
            assert found_v1 is not None
            assert found_v1.id == iv1_id
            assert found_v1.conclusion == "Result A is significant"
            assert found_v1.ai_original_text == "AI suggested this"
            assert found_v1.is_modified is False

            # Non-existent
            assert await InsightRepository.get_insight_version(session, insight_id, 99) is None

            # List (descending)
            versions = await InsightRepository.list_insight_versions(session, insight_id)
            assert len(versions) == 2
            assert versions[0].version_number == 2
            assert versions[0].is_modified is True
            assert versions[0].modification_note == "Corrected scope"

            # Latest
            latest = await InsightRepository.get_latest_insight_version(session, insight_id)
            assert latest is not None
            assert latest.version_number == 2

            # No versions for unknown insight
            assert await InsightRepository.get_latest_insight_version(session, uuid4()) is None
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_insight_candidate_crud(async_session_factory, test_user) -> None:
    """Insert, get, list, update status for insight candidates."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                cand = await InsightRepository.insert_insight_candidate(
                    session,
                    workspace_id=seed.workspace_id,
                    run_id=seed.run_id,
                    conclusion="Candidate conclusion",
                    scope="Test scope",
                    evidence_refs=[],
                    method_refs=[],
                    confidence_level="medium",
                    limitations="Limited data",
                    evidence_source_label="model_inference",
                    ai_raw_text="AI raw output",
                )
                cand_id = cand.id

                await InsightRepository.insert_insight_candidate(
                    session,
                    workspace_id=seed.workspace_id,
                    run_id=seed.run_id,
                    conclusion="Second candidate",
                    scope="Wide scope",
                    evidence_refs=[],
                    method_refs=[],
                    confidence_level="low",
                    limitations="Very limited",
                    evidence_source_label="model_inference",
                    ai_raw_text="Another output",
                )

        async with async_session_factory() as session:
            # Get candidate
            found = await InsightRepository.get_insight_candidate(session, cand_id)
            assert found is not None
            assert found.conclusion == "Candidate conclusion"
            assert found.status == "pending"

            # Non-existent
            assert await InsightRepository.get_insight_candidate(session, uuid4()) is None

            # List all (2)
            all_cands = await InsightRepository.list_insight_candidates(session, seed.run_id)
            assert len(all_cands) == 2

        # Update status to accepted
        accepted_insight_id = new_id()
        async with async_session_factory() as session:
            async with session.begin():
                await InsightRepository.update_insight_candidate_status(
                    session,
                    cand_id,
                    "accepted",
                    accepted_insight_id=accepted_insight_id,
                    reviewed_by=seed.owner_id,
                )

        async with async_session_factory() as session:
            # Filter by status=accepted
            accepted = await InsightRepository.list_insight_candidates(
                session,
                seed.run_id,
                status="accepted",
            )
            assert len(accepted) == 1
            assert accepted[0].id == cand_id
            assert accepted[0].accepted_insight_id == accepted_insight_id
            assert accepted[0].reviewed_by == seed.owner_id
            assert accepted[0].reviewed_at is not None

            # Filter by status=pending
            pending = await InsightRepository.list_insight_candidates(
                session,
                seed.run_id,
                status="pending",
            )
            assert len(pending) == 1
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


# ---------------------------------------------------------------------------
# ResultRepository
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_result_insert_get_list(async_session_factory, test_user) -> None:
    """Insert result, get by id, get by owner, list by workspace."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                result = await ResultRepository.insert_result(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="test-result",
                )
                result_id = result.id

        async with async_session_factory() as session:
            found = await ResultRepository.get_result(session, result_id)
            assert found is not None
            assert found.name == "test-result"
            assert found.status == "published"
            assert found.current_version == 0
            assert found.current_acl_type == "private"

            # Get by owner
            found_owner = await ResultRepository.get_result_by_owner(
                session, result_id, seed.owner_id
            )
            assert found_owner is not None

            # Wrong owner returns None
            assert await ResultRepository.get_result_by_owner(session, result_id, uuid4()) is None

            # List by workspace
            results = await ResultRepository.list_results_by_workspace(session, seed.workspace_id)
            assert len(results) == 1
            assert results[0].id == result_id
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_result_update_version_acl_metadata_status(async_session_factory, test_user) -> None:
    """Update current_version, ACL, metadata name, and status."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                result = await ResultRepository.insert_result(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="r-update",
                )
                result_id = result.id

        async with async_session_factory() as session:
            async with session.begin():
                await ResultRepository.update_result_current_version(session, result_id, 2)
                await ResultRepository.update_result_acl(
                    session,
                    result_id,
                    "explicit",
                    [str(seed.owner_id)],
                )
                await ResultRepository.update_result_metadata(session, result_id, "renamed")
                await ResultRepository.update_result_status(session, result_id, "archived")

        async with async_session_factory() as session:
            found = await ResultRepository.get_result(session, result_id)
            assert found is not None
            assert found.current_version == 2
            assert found.current_acl_type == "explicit"
            assert found.current_explicit_user_ids == [str(seed.owner_id)]
            assert found.name == "renamed"
            assert found.status == "archived"
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_result_list_published_and_count(async_session_factory, test_user) -> None:
    """list_published_results and count_published_results_by_workspace."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                await ResultRepository.insert_result(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="published-1",
                    status="published",
                )
                await ResultRepository.insert_result(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="published-2",
                    status="published",
                )
                await ResultRepository.insert_result(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="archived-1",
                    status="archived",
                )

        async with async_session_factory() as session:
            # List published (cross-user)
            published = await ResultRepository.list_published_results(session)
            published_names = {r.name for r in published}
            assert "published-1" in published_names
            assert "published-2" in published_names
            assert "archived-1" not in published_names

            # Count published by workspace
            count = await ResultRepository.count_published_results_by_workspace(
                session,
                seed.workspace_id,
            )
            assert count == 2

            # Count for empty workspace
            assert (
                await ResultRepository.count_published_results_by_workspace(
                    session,
                    uuid4(),
                )
                == 0
            )
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_result_version_crud(async_session_factory, test_user) -> None:
    """Insert, get, list, get latest, update status for result versions."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                result = await ResultRepository.insert_result(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="rv-test",
                )
                result_id = result.id

                rv1 = await ResultRepository.insert_result_version(
                    session,
                    result_id=result_id,
                    version_number=1,
                    title="Version One",
                    summary="First version",
                    tags=["v1"],
                    release_notes="Initial release",
                    dataset_version_refs=[],
                    view_version_refs=[],
                    insight_version_refs=[],
                    evidence_snapshot_ids=[str(seed.snapshot_id)],
                    analysis_run_ids=[str(seed.run_id)],
                    source_run_statuses={str(seed.run_id): "succeeded"},
                    publisher=seed.owner_id,
                    content_hash="r" * 64,
                    published_permission_envelope={},
                )
                rv1_id = rv1.id

                await ResultRepository.insert_result_version(
                    session,
                    result_id=result_id,
                    version_number=2,
                    title="Version Two",
                    summary="Second version",
                    tags=["v2", "latest"],
                    release_notes="Updated",
                    dataset_version_refs=[],
                    view_version_refs=[],
                    insight_version_refs=[],
                    evidence_snapshot_ids=[],
                    analysis_run_ids=[],
                    source_run_statuses={},
                    publisher=seed.owner_id,
                    content_hash="r" * 64,
                    published_permission_envelope={"scope": "all"},
                )

        async with async_session_factory() as session:
            # Get specific version
            found_v1 = await ResultRepository.get_result_version(session, result_id, 1)
            assert found_v1 is not None
            assert found_v1.id == rv1_id
            assert found_v1.title == "Version One"
            assert found_v1.status == "active"

            # Non-existent
            assert await ResultRepository.get_result_version(session, result_id, 99) is None

            # List (descending)
            versions = await ResultRepository.list_result_versions(session, result_id)
            assert len(versions) == 2
            assert versions[0].version_number == 2
            assert versions[1].version_number == 1

            # Latest
            latest = await ResultRepository.get_latest_result_version(session, result_id)
            assert latest is not None
            assert latest.version_number == 2

            # No versions for unknown result
            assert await ResultRepository.get_latest_result_version(session, uuid4()) is None

        # Update version status to superseded
        async with async_session_factory() as session:
            async with session.begin():
                await ResultRepository.update_result_version_status(session, rv1_id, "superseded")

        async with async_session_factory() as session:
            found_v1_updated = await ResultRepository.get_result_version(session, result_id, 1)
            assert found_v1_updated is not None
            assert found_v1_updated.status == "superseded"
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_result_version_search(async_session_factory, test_user) -> None:
    """search_result_versions filters by query and result_ids."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                result = await ResultRepository.insert_result(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="search-test",
                )
                result_id = result.id

                await ResultRepository.insert_result_version(
                    session,
                    result_id=result_id,
                    version_number=1,
                    title="Alpha Report",
                    summary="Summary about revenue",
                    tags=["finance"],
                    release_notes=None,
                    dataset_version_refs=[],
                    view_version_refs=[],
                    insight_version_refs=[],
                    evidence_snapshot_ids=[],
                    analysis_run_ids=[],
                    source_run_statuses={},
                    publisher=seed.owner_id,
                    content_hash="s" * 64,
                    published_permission_envelope={},
                )
                await ResultRepository.insert_result_version(
                    session,
                    result_id=result_id,
                    version_number=2,
                    title="Beta Report",
                    summary="Summary about operations",
                    tags=["ops"],
                    release_notes=None,
                    dataset_version_refs=[],
                    view_version_refs=[],
                    insight_version_refs=[],
                    evidence_snapshot_ids=[],
                    analysis_run_ids=[],
                    source_run_statuses={},
                    publisher=seed.owner_id,
                    content_hash="t" * 64,
                    published_permission_envelope={},
                )

        async with async_session_factory() as session:
            # No query: all active versions
            all_v = await ResultRepository.search_result_versions(session, None)
            assert len(all_v) == 2

            # Query "alpha" matches title
            alpha = await ResultRepository.search_result_versions(session, "Alpha")
            assert len(alpha) == 1
            assert alpha[0].title == "Alpha Report"

            # Query "finance" matches tags
            finance = await ResultRepository.search_result_versions(session, "finance")
            assert len(finance) == 1
            assert finance[0].tags == ["finance"]

            # Filter by result_ids
            filtered = await ResultRepository.search_result_versions(
                session,
                None,
                result_ids=[result_id],
            )
            assert len(filtered) == 2

            # Empty result_ids filter
            empty = await ResultRepository.search_result_versions(
                session,
                None,
                result_ids=[uuid4()],
            )
            assert len(empty) == 0
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_result_acl_revision_crud(async_session_factory, test_user) -> None:
    """Insert, get latest, list ACL revisions."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                result = await ResultRepository.insert_result(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="acl-test",
                )
                result_id = result.id

                rev1 = await ResultRepository.insert_acl_revision(
                    session,
                    result_id=result_id,
                    revision_number=1,
                    acl_type="private",
                    explicit_user_ids=[],
                    changed_by=seed.owner_id,
                    change_reason="Initial",
                )
                rev1_id = rev1.id

                await ResultRepository.insert_acl_revision(
                    session,
                    result_id=result_id,
                    revision_number=2,
                    acl_type="explicit",
                    explicit_user_ids=[str(seed.owner_id)],
                    previous_acl_type="private",
                    previous_explicit_user_ids=[],
                    changed_by=seed.owner_id,
                    change_reason="Added explicit user",
                )

                await ResultRepository.insert_acl_revision(
                    session,
                    result_id=result_id,
                    revision_number=3,
                    acl_type="all",
                    explicit_user_ids=[],
                    previous_acl_type="explicit",
                    previous_explicit_user_ids=[str(seed.owner_id)],
                    changed_by=seed.owner_id,
                    is_declassify=True,
                    declassify_reason="Approved for public",
                )

        async with async_session_factory() as session:
            # Get latest revision
            latest = await ResultRepository.get_latest_acl_revision(session, result_id)
            assert latest is not None
            assert latest.revision_number == 3
            assert latest.acl_type == "all"
            assert latest.is_declassify is True
            assert latest.declassify_reason == "Approved for public"

            # No revision for unknown result
            assert await ResultRepository.get_latest_acl_revision(session, uuid4()) is None

            # List all (ascending)
            revisions = await ResultRepository.list_acl_revisions(session, result_id)
            assert len(revisions) == 3
            assert revisions[0].id == rev1_id
            assert revisions[0].revision_number == 1
            assert revisions[2].revision_number == 3
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


# ---------------------------------------------------------------------------
# FavoriteRepository
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_favorite_insert_check_delete_list(async_session_factory, test_user) -> None:
    """Full lifecycle: insert, check, list, list IDs, delete."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                result1 = await ResultRepository.insert_result(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="fav-result-1",
                )
                result2 = await ResultRepository.insert_result(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="fav-result-2",
                )
                r1_id, r2_id = result1.id, result2.id

                await FavoriteRepository.insert_favorite(
                    session,
                    result_id=r1_id,
                    user_id=seed.owner_id,
                )
                await FavoriteRepository.insert_favorite(
                    session,
                    result_id=r2_id,
                    user_id=seed.owner_id,
                )

        async with async_session_factory() as session:
            # Check r1 is favorited
            assert await FavoriteRepository.check_favorite(session, r1_id, seed.owner_id) is True
            # Check non-favorited result
            assert await FavoriteRepository.check_favorite(session, uuid4(), seed.owner_id) is False

            # List favorites
            favs = await FavoriteRepository.list_favorites(session, seed.owner_id)
            assert len(favs) == 2

            # List favorite result IDs
            fav_rids = await FavoriteRepository.list_favorite_result_ids(session, seed.owner_id)
            assert set(fav_rids) == {r1_id, r2_id}

        # Delete one favorite
        async with async_session_factory() as session:
            async with session.begin():
                await FavoriteRepository.delete_favorite(session, r1_id, seed.owner_id)

        async with async_session_factory() as session:
            assert await FavoriteRepository.check_favorite(session, r1_id, seed.owner_id) is False
            favs_after = await FavoriteRepository.list_favorites(session, seed.owner_id)
            assert len(favs_after) == 1
            assert favs_after[0].result_id == r2_id

            # Delete again is idempotent (no error)
            await FavoriteRepository.delete_favorite(session, r1_id, seed.owner_id)
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_favorite_list_empty(async_session_factory, test_user) -> None:
    """list_favorites and list_favorite_result_ids return empty for new user."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            favs = await FavoriteRepository.list_favorites(session, seed.owner_id)
            assert favs == []

            rids = await FavoriteRepository.list_favorite_result_ids(session, seed.owner_id)
            assert rids == []
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


# ---------------------------------------------------------------------------
# KnowledgeReferenceRepository
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_knowledge_reference_insert_get(async_session_factory, test_user) -> None:
    """Insert and get a knowledge reference."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                ref = await KnowledgeReferenceRepository.insert_knowledge_reference(
                    session,
                    workspace_id=seed.workspace_id,
                    run_id=seed.run_id,
                    document_id="doc-001",
                    document_version="v3",
                    title="Research Handbook",
                    section="§2.3",
                    page=42,
                    chunk_id="chunk-7",
                    snippet_text="Key finding: reaction rate doubles at 80°C.",
                    content_hash="k" * 64,
                    source_uri="kb://handbook/section-2.3",
                    provider_name="internal-kb",
                )
                ref_id = ref.id

        async with async_session_factory() as session:
            found = await KnowledgeReferenceRepository.get_knowledge_reference(session, ref_id)
            assert found is not None
            assert found.document_id == "doc-001"
            assert found.title == "Research Handbook"
            assert found.section == "§2.3"
            assert found.page == 42
            assert found.provider_name == "internal-kb"

            # Non-existent
            assert (
                await KnowledgeReferenceRepository.get_knowledge_reference(session, uuid4()) is None
            )
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_knowledge_reference_list_by_run_and_step(async_session_factory, test_user) -> None:
    """list_knowledge_references_by_run with optional step_id filter."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        step_id = new_id()
        async with async_session_factory() as session:
            async with session.begin():
                # Need to create an analysis step for the FK
                from packages.research.execution.entities_trusted import ResearchAnalysisStep

                step = ResearchAnalysisStep(
                    id=step_id,
                    run_id=seed.run_id,
                    step_key="extract",
                    step_index=0,
                    method="knowledge",
                    status="succeeded",
                )
                session.add(step)
                await session.flush()

                await KnowledgeReferenceRepository.insert_knowledge_reference(
                    session,
                    workspace_id=seed.workspace_id,
                    run_id=seed.run_id,
                    step_id=step_id,
                    document_id="doc-step",
                    document_version="v1",
                    title="Step Doc",
                    content_hash="a" * 64,
                    source_uri="kb://step-doc",
                    provider_name="internal-kb",
                )
                await KnowledgeReferenceRepository.insert_knowledge_reference(
                    session,
                    workspace_id=seed.workspace_id,
                    run_id=seed.run_id,
                    document_id="doc-run",
                    document_version="v1",
                    title="Run Doc",
                    content_hash="b" * 64,
                    source_uri="kb://run-doc",
                    provider_name="internal-kb",
                )

        async with async_session_factory() as session:
            # List by run (all 2)
            refs = await KnowledgeReferenceRepository.list_knowledge_references_by_run(
                session,
                seed.run_id,
            )
            assert len(refs) == 2

            # List by run + step (only 1)
            step_refs = await KnowledgeReferenceRepository.list_knowledge_references_by_run(
                session,
                seed.run_id,
                step_id=step_id,
            )
            assert len(step_refs) == 1
            assert step_refs[0].document_id == "doc-step"

            # Empty for unknown run
            empty = await KnowledgeReferenceRepository.list_knowledge_references_by_run(
                session,
                uuid4(),
            )
            assert empty == []
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_knowledge_reference_list_by_insight(async_session_factory, test_user) -> None:
    """list_knowledge_references_by_insight returns refs linked to an insight."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                insight = await InsightRepository.insert_insight(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="insight-kb",
                    source_run_id=seed.run_id,
                )
                insight_id = insight.id

                await KnowledgeReferenceRepository.insert_knowledge_reference(
                    session,
                    workspace_id=seed.workspace_id,
                    run_id=seed.run_id,
                    insight_id=insight_id,
                    document_id="doc-insight",
                    document_version="v1",
                    title="Insight Evidence",
                    content_hash="i" * 64,
                    source_uri="kb://insight-evidence",
                    provider_name="internal-kb",
                )
                await KnowledgeReferenceRepository.insert_knowledge_reference(
                    session,
                    workspace_id=seed.workspace_id,
                    run_id=seed.run_id,
                    document_id="doc-no-insight",
                    document_version="v1",
                    title="No Insight",
                    content_hash="j" * 64,
                    source_uri="kb://no-insight",
                    provider_name="internal-kb",
                )

        async with async_session_factory() as session:
            refs = await KnowledgeReferenceRepository.list_knowledge_references_by_insight(
                session,
                insight_id,
            )
            assert len(refs) == 1
            assert refs[0].document_id == "doc-insight"

            # Empty for unknown insight
            empty = await KnowledgeReferenceRepository.list_knowledge_references_by_insight(
                session,
                uuid4(),
            )
            assert empty == []
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


# ---------------------------------------------------------------------------
# LineageEdgeRepository
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_lineage_edge_insert_list_by_source_and_target(
    async_session_factory,
    test_user,
) -> None:
    """Insert lineage edge, query by source and by target."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        source_id = new_id()
        target_id = new_id()
        target2_id = new_id()

        async with async_session_factory() as session:
            async with session.begin():
                edge1 = await LineageEdgeRepository.insert_lineage_edge(
                    session,
                    source_namespace="research:workspace",
                    source_id=source_id,
                    target_namespace="research:result_version",
                    target_id=target_id,
                    edge_type="workspace_to_result",
                    workspace_id=seed.workspace_id,
                )
                edge1_id = edge1.id

                await LineageEdgeRepository.insert_lineage_edge(
                    session,
                    source_namespace="research:workspace",
                    source_id=source_id,
                    target_namespace="research:result_version",
                    target_id=target2_id,
                    edge_type="workspace_to_result",
                    workspace_id=seed.workspace_id,
                    source_version=1,
                    target_version=2,
                )

        async with async_session_factory() as session:
            # List by source (2 edges)
            by_source = await LineageEdgeRepository.list_edges_by_source(
                session,
                "research:workspace",
                source_id,
            )
            assert len(by_source) == 2
            assert edge1_id in {e.id for e in by_source}

            # List by target (1 edge)
            by_target = await LineageEdgeRepository.list_edges_by_target(
                session,
                "research:result_version",
                target_id,
            )
            assert len(by_target) == 1
            assert by_target[0].id == edge1_id
            assert by_target[0].edge_type == "workspace_to_result"

            # List by target2
            by_target2 = await LineageEdgeRepository.list_edges_by_target(
                session,
                "research:result_version",
                target2_id,
            )
            assert len(by_target2) == 1
            assert by_target2[0].source_version == 1
            assert by_target2[0].target_version == 2

            # Empty results
            assert (
                await LineageEdgeRepository.list_edges_by_source(
                    session,
                    "nonexistent",
                    source_id,
                )
                == []
            )
            assert (
                await LineageEdgeRepository.list_edges_by_target(
                    session,
                    "nonexistent",
                    target_id,
                )
                == []
            )
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)


# ---------------------------------------------------------------------------
# SearchRepository
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_search_published_datasets(async_session_factory, test_user) -> None:
    """search_published_datasets finds published result versions with datasets."""
    seed = await _seed_chain(async_session_factory, test_user.user_id, test_user.department_id)
    try:
        async with async_session_factory() as session:
            async with session.begin():
                result = await ResultRepository.insert_result(
                    session,
                    workspace_id=seed.workspace_id,
                    owner_user_id=seed.owner_id,
                    name="search-result",
                    status="published",
                )
                result_id = result.id

                # Active version with dataset refs
                await ResultRepository.insert_result_version(
                    session,
                    result_id=result_id,
                    version_number=1,
                    title="Published Alpha",
                    summary="Dataset about alpha metrics",
                    tags=["alpha"],
                    release_notes=None,
                    dataset_version_refs=[{"dataset_id": str(new_id()), "version_number": 1}],
                    view_version_refs=[],
                    insight_version_refs=[],
                    evidence_snapshot_ids=[],
                    analysis_run_ids=[],
                    source_run_statuses={},
                    publisher=seed.owner_id,
                    content_hash="p" * 64,
                    published_permission_envelope={},
                )

                # Superseded version (should not appear)
                await ResultRepository.insert_result_version(
                    session,
                    result_id=result_id,
                    version_number=2,
                    title="Superseded Beta",
                    summary="Should not be found",
                    tags=["beta"],
                    release_notes=None,
                    dataset_version_refs=[],
                    view_version_refs=[],
                    insight_version_refs=[],
                    evidence_snapshot_ids=[],
                    analysis_run_ids=[],
                    source_run_statuses={},
                    publisher=seed.owner_id,
                    content_hash="q" * 64,
                    published_permission_envelope={},
                    status="superseded",
                )

        async with async_session_factory() as session:
            # No filters: only active + published
            results = await SearchRepository.search_published_datasets(session)
            assert len(results) == 1
            rv, rr = results[0]
            assert rv.title == "Published Alpha"
            assert rr.id == result_id
            assert rr.status == "published"

            # Query by title
            by_title = await SearchRepository.search_published_datasets(session, query="Alpha")
            assert len(by_title) == 1
            assert by_title[0][0].title == "Published Alpha"

            # Query by summary
            by_summary = await SearchRepository.search_published_datasets(
                session,
                query="alpha metrics",
            )
            assert len(by_summary) == 1

            # Query that doesn't match
            no_match = await SearchRepository.search_published_datasets(
                session, query="nonexistent"
            )
            assert no_match == []

            # Filter by result_id
            by_result = await SearchRepository.search_published_datasets(
                session,
                result_id=result_id,
            )
            assert len(by_result) == 1

            # Filter by non-existent result_id
            by_wrong_result = await SearchRepository.search_published_datasets(
                session,
                result_id=uuid4(),
            )
            assert by_wrong_result == []
    finally:
        await _cleanup_workspace(async_session_factory, seed.workspace_id)
