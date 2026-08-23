"""EvidenceSnapshotService 集成测试。

覆盖 ``packages.research.snapshots`` 的核心逻辑：
- freeze_snapshot: 无证据校验 / 冻结成功 / 快照编号递增 / lineage hook
- list_snapshots: 列表与 not_found
- _compute_content_hash: 确定性哈希 / derived 数据纳入
- _build_permission_envelope / _build_field_manifest

DB 依赖：通过 ``async_session_factory`` fixture 连接测试库。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
import sqlalchemy as sa

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.research.dtos import FactSummary
from packages.research.entities import (
    ResearchEvidenceSnapshot,
    ResearchWorkspace,
    WorkspaceEvidenceRef,
)
from packages.research.snapshots import EvidenceSnapshotService

# ============================================================
# Helpers
# ============================================================


class FakeFactProvider:
    """假 CoreFactProvider，支持 get_fact_summary / get_fact_fields / get_fact_data。"""

    def __init__(
        self,
        summary: FactSummary | None = None,
        fields: list[str] | None = None,
        data: dict | None = None,
    ) -> None:
        self._summary = summary or FactSummary(
            fact_id=new_id(),
            fact_type="core:fact",
            subject_id="测试数据",
            status="confirmed",
            department_name="研发部",
        )
        self._fields = fields or ["温度", "压力"]
        self._data = data or {
            "metadata": {"温度": 100, "批次": "A001"},
            "points": [{"name": "压力", "value": 5.0}],
        }

    async def get_fact_summary(self, fact_id):
        return self._summary

    async def get_fact_fields(self, fact_id):
        return self._fields

    async def get_fact_data(self, fact_id):
        return self._data


async def _seed_workspace_with_evidence(factory, user, fact_id: UUID) -> UUID:
    """插入工作空间 + 一条 active 证据引用，返回 workspace_id。"""
    async with factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=user.user_id,
                department_id=user.department_id,
                name="snapshot-test-ws",
            )
            session.add(ws)
            await session.flush()

            session.add(
                WorkspaceEvidenceRef(
                    id=new_id(),
                    workspace_id=ws.id,
                    source_namespace="core:fact",
                    source_id=fact_id,
                    source_version=None,
                    source_name="测试数据",
                    status="active",
                    added_by=user.user_id,
                )
            )
            await session.flush()
            return ws.id


async def _cleanup(factory, workspace_id: UUID) -> None:
    """清理审计 + 工作空间。"""
    async with factory() as session:
        async with session.begin():
            await session.execute(sa.text("ALTER TABLE audit_event DISABLE TRIGGER ALL"))
            await session.execute(
                sa.text(
                    "DELETE FROM audit_event WHERE department_id = "
                    "(SELECT department_id FROM research_workspace WHERE id = :wid)"
                ),
                {"wid": str(workspace_id)},
            )
            await session.execute(sa.text("ALTER TABLE audit_event ENABLE TRIGGER ALL"))
            await session.execute(
                sa.text("DELETE FROM research_workspace WHERE id = :wid"),
                {"wid": str(workspace_id)},
            )


def _make_snapshot_service(
    factory, user, fact_provider=None, lineage_writer=None
) -> EvidenceSnapshotService:
    """构造 EvidenceSnapshotService。"""
    return EvidenceSnapshotService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        fact_provider=fact_provider or FakeFactProvider(),
        lineage_writer=lineage_writer,
    )


@pytest.fixture
async def ws_with_evidence(async_session_factory, test_user):
    """提供含证据的工作空间并在测试后清理。"""
    fact_id = new_id()
    ws_id = await _seed_workspace_with_evidence(async_session_factory, test_user, fact_id)
    try:
        yield ws_id, fact_id, async_session_factory, test_user
    finally:
        await _cleanup(async_session_factory, ws_id)


# ============================================================
# freeze_snapshot
# ============================================================


@pytest.mark.integration
async def test_freeze_snapshot_no_evidence(async_session_factory, test_user) -> None:
    """无活跃证据时 freeze_snapshot 抛出 validation_failed。"""
    async with async_session_factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=test_user.user_id,
                department_id=test_user.department_id,
                name="empty-snapshot-ws",
            )
            session.add(ws)
            await session.flush()
            ws_id = ws.id
    try:
        svc = _make_snapshot_service(async_session_factory, test_user)
        with pytest.raises(AppError) as exc_info:
            await svc.freeze_snapshot(ws_id)
        assert exc_info.value.code == "validation_failed"
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_freeze_snapshot_success(ws_with_evidence) -> None:
    """冻结快照成功：编号 1，content_hash 64 字符。"""
    ws_id, _fact_id, factory, user = ws_with_evidence
    svc = _make_snapshot_service(factory, user)
    ref = await svc.freeze_snapshot(ws_id)
    assert ref.snapshot_number == 1
    assert len(ref.content_hash) == 64
    assert ref.snapshot_id is not None

    async with factory() as session:
        snap = await session.scalar(
            sa.select(ResearchEvidenceSnapshot).where(
                ResearchEvidenceSnapshot.id == ref.snapshot_id
            )
        )
        assert snap is not None
        assert snap.snapshot_number == 1


@pytest.mark.integration
async def test_freeze_snapshot_increments_number(ws_with_evidence) -> None:
    """多次冻结快照编号递增。"""
    ws_id, _fact_id, factory, user = ws_with_evidence
    svc = _make_snapshot_service(factory, user)
    ref1 = await svc.freeze_snapshot(ws_id)
    ref2 = await svc.freeze_snapshot(ws_id)
    assert ref1.snapshot_number == 1
    assert ref2.snapshot_number == 2


@pytest.mark.integration
async def test_freeze_snapshot_invokes_lineage_hook(ws_with_evidence) -> None:
    """lineage_writer 存在时调用 on_snapshot_frozen hook。"""
    ws_id, _fact_id, factory, user = ws_with_evidence
    hook = AsyncMock()
    writer = MagicMock()
    writer.on_snapshot_frozen = hook
    svc = _make_snapshot_service(factory, user, lineage_writer=writer)
    await svc.freeze_snapshot(ws_id)
    assert hook.await_count == 1


@pytest.mark.integration
async def test_freeze_snapshot_hook_failure_does_not_block(ws_with_evidence) -> None:
    """lineage hook 抛异常时不阻断主流程。"""
    ws_id, _fact_id, factory, user = ws_with_evidence
    hook = AsyncMock(side_effect=RuntimeError("hook boom"))
    writer = MagicMock()
    writer.on_snapshot_frozen = hook
    svc = _make_snapshot_service(factory, user, lineage_writer=writer)
    ref = await svc.freeze_snapshot(ws_id)
    assert ref.snapshot_id is not None  # 主流程仍成功


@pytest.mark.integration
async def test_freeze_snapshot_workspace_not_found(async_session_factory, test_user) -> None:
    """工作空间不存在时 freeze_snapshot 抛出 not_found。"""
    svc = _make_snapshot_service(async_session_factory, test_user)
    with pytest.raises(AppError) as exc_info:
        await svc.freeze_snapshot(new_id())
    assert exc_info.value.code == "not_found"


@pytest.mark.integration
async def test_freeze_snapshot_requires_actor(async_session_factory, test_user) -> None:
    """actor_id 为 None 时 freeze_snapshot 抛出 forbidden。"""
    fact_id = new_id()
    ws_id = await _seed_workspace_with_evidence(async_session_factory, test_user, fact_id)
    try:
        svc = EvidenceSnapshotService(
            session_factory=async_session_factory,
            department_id=test_user.department_id,
            actor_id=None,
            fact_provider=FakeFactProvider(),
        )
        with pytest.raises(AppError) as exc_info:
            await svc.freeze_snapshot(ws_id)
        assert exc_info.value.code == "forbidden"
    finally:
        await _cleanup(async_session_factory, ws_id)


# ============================================================
# list_snapshots
# ============================================================


@pytest.mark.integration
async def test_list_snapshots(ws_with_evidence) -> None:
    """list_snapshots 返回已冻结的快照列表。"""
    ws_id, _fact_id, factory, user = ws_with_evidence
    svc = _make_snapshot_service(factory, user)
    await svc.freeze_snapshot(ws_id)
    await svc.freeze_snapshot(ws_id)
    snaps = await svc.list_snapshots(ws_id)
    assert len(snaps) == 2
    assert snaps[0].snapshot_number == 2
    assert snaps[1].snapshot_number == 1


@pytest.mark.integration
async def test_list_snapshots_not_found(async_session_factory, test_user) -> None:
    """工作空间不存在时 list_snapshots 抛出 not_found。"""
    svc = _make_snapshot_service(async_session_factory, test_user)
    with pytest.raises(AppError) as exc_info:
        await svc.list_snapshots(new_id())
    assert exc_info.value.code == "not_found"


# ============================================================
# _compute_content_hash / _build_* (纯逻辑)
# ============================================================


@pytest.mark.integration
async def test_compute_content_hash_deterministic() -> None:
    """相同输入产生相同哈希。"""
    svc = _make_snapshot_service(MagicMock(), MagicMock())  # 不使用 factory
    ref = MagicMock()
    ref.source_namespace = "core:fact"
    ref.source_id = new_id()
    fact_fields_map = {ref.source_id: ["温度", "压力"]}
    fact_data_map = {
        ref.source_id: {"metadata": {"温度": 1}, "points": [{"name": "压力", "value": 2}]}
    }
    h1 = svc._compute_content_hash([ref], fact_fields_map, fact_data_map)
    h2 = svc._compute_content_hash([ref], fact_fields_map, fact_data_map)
    assert h1 == h2
    assert len(h1) == 64


@pytest.mark.integration
async def test_compute_content_hash_changes_with_data() -> None:
    """数据变化时哈希变化。"""
    svc = _make_snapshot_service(MagicMock(), MagicMock())
    ref = MagicMock()
    ref.source_namespace = "core:fact"
    ref.source_id = new_id()
    fact_fields_map = {ref.source_id: ["温度"]}
    h1 = svc._compute_content_hash(
        [ref], fact_fields_map, {ref.source_id: {"metadata": {"温度": 1}}}
    )
    h2 = svc._compute_content_hash(
        [ref], fact_fields_map, {ref.source_id: {"metadata": {"温度": 2}}}
    )
    assert h1 != h2


@pytest.mark.integration
async def test_build_permission_envelope() -> None:
    """_build_permission_envelope 记录每个 source 的权限快照。"""
    svc = _make_snapshot_service(MagicMock(), MagicMock())
    fact_id = new_id()
    ref = MagicMock()
    ref.source_namespace = "core:fact"
    ref.source_id = fact_id
    summaries = {
        fact_id: FactSummary(
            fact_id=fact_id,
            fact_type="core:fact",
            subject_id="x",
            status="confirmed",
            department_name="研发部",
        )
    }
    envelope = svc._build_permission_envelope([ref], summaries)
    key = str(fact_id)
    assert key in envelope
    assert envelope[key]["status"] == "confirmed"
    assert envelope[key]["department_name"] == "研发部"


@pytest.mark.integration
async def test_build_field_manifest() -> None:
    """_build_field_manifest 按 fact_id 映射字段名列表。"""
    svc = _make_snapshot_service(MagicMock(), MagicMock())
    fact_id = new_id()
    ref = MagicMock()
    ref.source_namespace = "core:fact"
    ref.source_id = fact_id
    manifest = svc._build_field_manifest([ref], {fact_id: ["温度", "压力"]})
    assert manifest[str(fact_id)] == ["温度", "压力"]


@pytest.mark.integration
async def test_extract_field_value_from_metadata() -> None:
    """_extract_field_value 从 metadata 提取字段值。"""
    svc = _make_snapshot_service(MagicMock(), MagicMock())
    data = {"metadata": {"温度": 100}, "points": [{"name": "压力", "value": 5}]}
    assert svc._extract_field_value(data, "温度") == 100
    assert svc._extract_field_value(data, "压力") == 5
    assert svc._extract_field_value(data, "不存在") is None
