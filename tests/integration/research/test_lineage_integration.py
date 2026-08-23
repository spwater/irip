"""溯源边记录集成测试：LineageEdgeService。

覆盖 ``packages.research.lineage.lineage`` 的全部方法：
- record_publication_edges: workspace/dataset/view/insight → result 边
- record_edge: 单条边记录
- list_edges_by_source / list_edges_by_target: 按源/目标查询

DB 依赖：通过 ``async_session_factory`` fixture 连接测试库。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest
import sqlalchemy as sa

from packages.common.ids import new_id
from packages.research.dtos import ProductRefCollection
from packages.research.entities import ResearchWorkspace
from packages.research.lineage.lineage import LineageEdgeService

# ============================================================
# 共享 seed / cleanup
# ============================================================


@dataclass(frozen=True)
class _IDs:
    """测试所需 UUID 集合。"""

    workspace_id: UUID
    result_id: UUID
    dataset_id: UUID
    view_id: UUID
    insight_id: UUID


async def _seed_workspace(factory, user) -> UUID:
    """插入工作空间，返回 ID。"""
    async with factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=user.user_id,
                department_id=user.department_id,
                name="lineage-test-ws",
            )
            session.add(ws)
            await session.flush()
            return ws.id


async def _cleanup(factory, workspace_id: UUID) -> None:
    """清理溯源边 + 工作空间。"""
    async with factory() as session:
        async with session.begin():
            await session.execute(
                sa.text("DELETE FROM research_lineage_edge WHERE workspace_id = :wid"),
                {"wid": str(workspace_id)},
            )
            await session.execute(
                sa.text("DELETE FROM research_workspace WHERE id = :wid"),
                {"wid": str(workspace_id)},
            )


# ============================================================
# record_publication_edges
# ============================================================


@pytest.mark.integration
async def test_record_publication_edges_creates_all_edge_types(
    async_session_factory, test_user
) -> None:
    """record_publication_edges 创建 workspace/dataset/view/insight 四类边。"""
    ws_id = await _seed_workspace(async_session_factory, test_user)
    result_id = new_id()
    dataset_id = new_id()
    view_id = new_id()
    insight_id = new_id()
    try:
        svc = LineageEdgeService(async_session_factory)
        product_refs = ProductRefCollection(
            dataset_version_refs=[{"dataset_id": str(dataset_id), "version_number": 1}],
            view_version_refs=[{"view_id": str(view_id), "version_number": 1}],
            insight_version_refs=[{"insight_id": str(insight_id), "version_number": 1}],
        )
        async with async_session_factory() as session:
            async with session.begin():
                await svc.record_publication_edges(
                    session,
                    result_id,
                    version_number=1,
                    workspace_id=ws_id,
                    product_refs=product_refs,
                )

        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT edge_type, source_namespace, target_namespace "
                        "FROM research_lineage_edge WHERE workspace_id = :wid"
                    ),
                    {"wid": str(ws_id)},
                )
            ).fetchall()
        edge_types = {r[0] for r in rows}
        assert "workspace_to_result" in edge_types
        assert "dataset_to_result" in edge_types
        assert "view_to_result" in edge_types
        assert "insight_to_result" in edge_types
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_record_publication_edges_skips_missing_ids(async_session_factory, test_user) -> None:
    """dataset_id/view_id/insight_id 为空时跳过对应边，仅创建 workspace 边。"""
    ws_id = await _seed_workspace(async_session_factory, test_user)
    result_id = new_id()
    try:
        svc = LineageEdgeService(async_session_factory)
        product_refs = ProductRefCollection(
            dataset_version_refs=[{"version_number": 1}],  # 无 dataset_id
            view_version_refs=[{"view_id": "", "version_number": 1}],
            insight_version_refs=[],
        )
        async with async_session_factory() as session:
            async with session.begin():
                await svc.record_publication_edges(
                    session,
                    result_id,
                    version_number=1,
                    workspace_id=ws_id,
                    product_refs=product_refs,
                )

        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT edge_type FROM research_lineage_edge WHERE workspace_id = :wid"
                    ),
                    {"wid": str(ws_id)},
                )
            ).fetchall()
        edge_types = [r[0] for r in rows]
        assert edge_types == ["workspace_to_result"]
    finally:
        await _cleanup(async_session_factory, ws_id)


# ============================================================
# record_edge
# ============================================================


@pytest.mark.integration
async def test_record_edge_single(async_session_factory, test_user) -> None:
    """record_edge 记录单条边并携带版本号。"""
    ws_id = await _seed_workspace(async_session_factory, test_user)
    source_id = new_id()
    target_id = new_id()
    try:
        svc = LineageEdgeService(async_session_factory)
        async with async_session_factory() as session:
            async with session.begin():
                await svc.record_edge(
                    session,
                    source_namespace="research:dataset_version",
                    source_id=source_id,
                    target_namespace="research:result_version",
                    target_id=target_id,
                    edge_type="dataset_to_result",
                    workspace_id=ws_id,
                    source_version=2,
                    target_version=3,
                )

        async with async_session_factory() as session:
            row = (
                await session.execute(
                    sa.text(
                        "SELECT source_version, target_version, edge_type "
                        "FROM research_lineage_edge WHERE source_id = :sid AND target_id = :tid"
                    ),
                    {"sid": str(source_id), "tid": str(target_id)},
                )
            ).fetchone()
        assert row is not None
        assert row[0] == 2
        assert row[1] == 3
        assert row[2] == "dataset_to_result"
    finally:
        await _cleanup(async_session_factory, ws_id)


# ============================================================
# list_edges_by_source / list_edges_by_target
# ============================================================


@pytest.mark.integration
async def test_list_edges_by_source(async_session_factory, test_user) -> None:
    """按源节点查询溯源边。"""
    ws_id = await _seed_workspace(async_session_factory, test_user)
    source_id = new_id()
    target_a = new_id()
    target_b = new_id()
    try:
        svc = LineageEdgeService(async_session_factory)
        async with async_session_factory() as session:
            async with session.begin():
                await svc.record_edge(
                    session,
                    source_namespace="research:workspace",
                    source_id=source_id,
                    target_namespace="research:result_version",
                    target_id=target_a,
                    edge_type="workspace_to_result",
                    workspace_id=ws_id,
                )
                await svc.record_edge(
                    session,
                    source_namespace="research:workspace",
                    source_id=source_id,
                    target_namespace="research:result_version",
                    target_id=target_b,
                    edge_type="workspace_to_result",
                    workspace_id=ws_id,
                )

        edges = await svc.list_edges_by_source("research:workspace", source_id)
        assert len(edges) == 2
        target_ids = {e.target_id for e in edges}
        assert target_a in target_ids
        assert target_b in target_ids
        assert all(e.source_namespace == "research:workspace" for e in edges)
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_list_edges_by_target(async_session_factory, test_user) -> None:
    """按目标节点查询溯源边。"""
    ws_id = await _seed_workspace(async_session_factory, test_user)
    src_a = new_id()
    src_b = new_id()
    target_id = new_id()
    try:
        svc = LineageEdgeService(async_session_factory)
        async with async_session_factory() as session:
            async with session.begin():
                await svc.record_edge(
                    session,
                    source_namespace="research:dataset_version",
                    source_id=src_a,
                    target_namespace="research:result_version",
                    target_id=target_id,
                    edge_type="dataset_to_result",
                    workspace_id=ws_id,
                )
                await svc.record_edge(
                    session,
                    source_namespace="research:view_version",
                    source_id=src_b,
                    target_namespace="research:result_version",
                    target_id=target_id,
                    edge_type="view_to_result",
                    workspace_id=ws_id,
                )

        edges = await svc.list_edges_by_target("research:result_version", target_id)
        assert len(edges) == 2
        edge_types = {e.edge_type for e in edges}
        assert "dataset_to_result" in edge_types
        assert "view_to_result" in edge_types
    finally:
        await _cleanup(async_session_factory, ws_id)


@pytest.mark.integration
async def test_list_edges_empty(async_session_factory, test_user) -> None:
    """无匹配边时返回空列表。"""
    svc = LineageEdgeService(async_session_factory)
    edges = await svc.list_edges_by_source("research:workspace", new_id())
    assert edges == []
    edges = await svc.list_edges_by_target("research:result_version", new_id())
    assert edges == []
