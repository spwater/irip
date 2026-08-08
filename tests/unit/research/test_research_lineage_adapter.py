"""单元测试：ResearchLineageAdapterImpl 只读研究溯源适配器。

覆盖：
- query_node 各命名空间路由 + 节点不存在返回 None + 未知命名空间返回 None；
- query_incoming_edges 委托 ResearchRepository.list_edges_by_target + 边转换；
- check_permission 各命名空间分支 + 无 user_id + 异常吞掉返回 False；
- _edge_to_provenance_edge 边类型标签映射（已知/未知）。

使用 Mock session_factory（async context manager），不依赖真实数据库。
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from packages.research.adapters.research_lineage import (
    _EDGE_TYPE_LABELS,
    ResearchLineageAdapterImpl,
    _edge_to_provenance_edge,
)
from packages.research.dtos import ProvenanceEdge
from packages.research.entities import ResearchLineageEdge

# ============================================================
# Helpers
# ============================================================


class _FakeSessionContext:
    """模拟 ``self._factory()`` 返回的 async context manager。"""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _make_factory(session: Any) -> Any:
    """构造返回固定 session 的 session_factory mock。"""

    def factory() -> _FakeSessionContext:
        return _FakeSessionContext(session)

    return factory


def _make_execute_result(scalar: Any = None) -> MagicMock:
    """构造 session.execute 返回的 Result mock。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    result.first.return_value = scalar
    return result


def _make_snapshot(
    snapshot_id: UUID | None = None,
    snapshot_number: int = 3,
    content_hash: str = "a" * 64,
    workspace_id: UUID | None = None,
) -> Any:
    snap = MagicMock()
    snap.id = snapshot_id or uuid4()
    snap.snapshot_number = snapshot_number
    snap.content_hash = content_hash
    snap.workspace_id = workspace_id or uuid4()
    return snap


def _make_run(run_id: UUID | None = None, status: str = "succeeded") -> Any:
    run = MagicMock()
    run.id = run_id or uuid4()
    run.status = status
    run.workspace_id = uuid4()
    return run


def _make_step(step_id: UUID | None = None) -> Any:
    step = MagicMock()
    step.id = step_id or uuid4()
    step.step_key = "step_key_1"
    step.status = "succeeded"
    step.run_id = uuid4()
    return step


def _make_dataset(dataset_id: UUID | None = None) -> Any:
    ds = MagicMock()
    ds.id = dataset_id or uuid4()
    ds.name = "衍生数据集A"
    ds.status = "active"
    ds.current_version = 2
    ds.workspace_id = uuid4()
    return ds


def _make_view(view_id: UUID | None = None) -> Any:
    v = MagicMock()
    v.id = view_id or uuid4()
    v.name = "散点图"
    v.status = "active"
    v.current_version = 1
    v.caption = "图注"
    return v


def _make_view_version() -> Any:
    v = MagicMock()
    v.version_number = 4
    v.image_format = "png"
    return v


def _make_insight(insight_id: UUID | None = None) -> Any:
    ins = MagicMock()
    ins.id = insight_id or uuid4()
    ins.name = "关键结论"
    ins.status = "accepted"
    ins.current_version = 2
    ins.workspace_id = uuid4()
    return ins


def _make_workspace() -> Any:
    ws = MagicMock()
    ws.id = uuid4()
    ws.name = "工作空间"
    ws.status = "draft"
    return ws


def _make_knowledge_reference() -> Any:
    ref = MagicMock()
    ref.id = uuid4()
    ref.title = "参考文献"
    ref.document_id = "doc-001"
    ref.document_version = "1.0"
    ref.provider_name = "知网"
    ref.source_uri = "https://example.com/doc"
    ref.workspace_id = uuid4()
    return ref


def _make_lineage_edge(edge_type: str = "run_to_step") -> ResearchLineageEdge:
    return MagicMock(
        spec=ResearchLineageEdge,
        source_namespace="research:analysis_run",
        source_id=uuid4(),
        source_version=None,
        target_namespace="research:analysis_step",
        target_id=uuid4(),
        target_version=None,
        edge_type=edge_type,
    )


# ============================================================
# _edge_to_provenance_edge
# ============================================================


class TestEdgeToProvenanceEdge:
    """_edge_to_provenance_edge 转换函数测试。"""

    def test_known_edge_type_uses_label(self) -> None:
        """已知边类型使用中文标签。"""
        e = _make_lineage_edge(edge_type="run_to_step")
        edge = _edge_to_provenance_edge(e)
        assert edge.edge_type == "run_to_step"
        assert edge.edge_type_label == _EDGE_TYPE_LABELS["run_to_step"]

    def test_unknown_edge_type_falls_back_to_raw(self) -> None:
        """未知边类型回退为原始字符串。"""
        e = _make_lineage_edge(edge_type="custom_edge")
        edge = _edge_to_provenance_edge(e)
        assert edge.edge_type == "custom_edge"
        assert edge.edge_type_label == "custom_edge"

    def test_all_fields_mapped(self) -> None:
        """所有字段正确映射。"""
        src_id = uuid4()
        tgt_id = uuid4()
        e = MagicMock(
            spec=ResearchLineageEdge,
            source_namespace="core:fact",
            source_id=src_id,
            source_version=3,
            target_namespace="research:evidence_snapshot",
            target_id=tgt_id,
            target_version=1,
            edge_type="fact_to_snapshot",
        )
        edge: ProvenanceEdge = _edge_to_provenance_edge(e)
        assert edge.source_namespace == "core:fact"
        assert edge.source_id == src_id
        assert edge.source_version == 3
        assert edge.target_namespace == "research:evidence_snapshot"
        assert edge.target_id == tgt_id
        assert edge.target_version == 1
        assert edge.edge_type_label == _EDGE_TYPE_LABELS["fact_to_snapshot"]


# ============================================================
# query_node
# ============================================================


class TestQueryNode:
    """query_node 路由与节点查询测试。"""

    async def test_unknown_namespace_returns_none(self) -> None:
        """未知命名空间返回 None。"""
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(MagicMock()))
        result = await adapter.query_node("research:unknown", uuid4())
        assert result is None

    async def test_evidence_snapshot_node(self) -> None:
        """evidence_snapshot 命名空间返回正确节点。"""
        snap_id = uuid4()
        snap = _make_snapshot(snapshot_id=snap_id, snapshot_number=5)
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=snap))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("research:evidence_snapshot", snap_id)

        assert node is not None
        assert node.namespace == "research:evidence_snapshot"
        assert node.node_id == snap_id
        assert node.node_type == "evidence_snapshot"
        assert node.version == 5
        assert node.attributes["snapshot_number"] == 5

    async def test_evidence_snapshot_not_found(self) -> None:
        """evidence_snapshot 不存在返回 None。"""
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=None))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        assert await adapter.query_node("research:evidence_snapshot", uuid4()) is None

    async def test_analysis_run_node(self) -> None:
        """analysis_run 命名空间返回正确节点。"""
        run_id = uuid4()
        run = _make_run(run_id=run_id, status="running")
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=run))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("research:analysis_run", run_id)

        assert node is not None
        assert node.namespace == "research:analysis_run"
        assert node.node_type == "analysis_run"
        assert node.attributes["status"] == "running"

    async def test_analysis_step_node(self) -> None:
        """analysis_step 命名空间返回正确节点。"""
        step_id = uuid4()
        step = _make_step(step_id=step_id)
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=step))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("research:analysis_step", step_id)

        assert node is not None
        assert node.node_type == "analysis_step"
        assert node.attributes["step_key"] == "step_key_1"

    async def test_derived_dataset_node(self) -> None:
        """derived_dataset 命名空间返回正确节点。"""
        ds_id = uuid4()
        ds = _make_dataset(dataset_id=ds_id)
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=ds))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("research:derived_dataset", ds_id)

        assert node is not None
        assert node.node_type == "derived_dataset"
        assert node.version == 2
        assert node.attributes["name"] == "衍生数据集A"

    async def test_view_node(self) -> None:
        """view 命名空间返回正确节点。"""
        view_id = uuid4()
        v = _make_view(view_id=view_id)
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=v))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("research:view", view_id)

        assert node is not None
        assert node.node_type == "view"
        assert node.attributes["caption"] == "图注"

    async def test_view_version_node(self) -> None:
        """view_version 命名空间返回最新版本节点。"""
        view_id = uuid4()
        vv = _make_view_version()
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=vv))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("research:view_version", view_id)

        assert node is not None
        assert node.node_type == "view_version"
        assert node.version == 4
        assert node.attributes["image_format"] == "png"

    async def test_insight_node(self) -> None:
        """insight 命名空间返回正确节点。"""
        ins_id = uuid4()
        ins = _make_insight(insight_id=ins_id)
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=ins))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("research:insight", ins_id)

        assert node is not None
        assert node.node_type == "insight"
        assert node.attributes["status"] == "accepted"

    async def test_workspace_node(self) -> None:
        """workspace 命名空间返回正确节点。"""
        ws_id = uuid4()
        ws = _make_workspace()
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=ws))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("research:workspace", ws_id)

        assert node is not None
        assert node.node_type == "workspace"
        assert node.attributes["name"] == "工作空间"

    async def test_knowledge_reference_node(self) -> None:
        """knowledge_reference 命名空间返回正确节点。"""
        ref_id = uuid4()
        ref = _make_knowledge_reference()
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=ref))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("research:knowledge_reference", ref_id)

        assert node is not None
        assert node.node_type == "knowledge_reference"
        assert node.attributes["title"] == "参考文献"
        assert node.attributes["provider_name"] == "知网"

    async def test_derived_dataset_version_node(self) -> None:
        """derived_dataset_version 命名空间委托 get_latest_dataset_version。"""
        ds_id = uuid4()
        version = MagicMock()
        version.version_number = 7
        version.content_hash = "b" * 64
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=MagicMock()))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_latest_dataset_version",
            new_callable=AsyncMock,
            return_value=version,
        ):
            node = await adapter.query_node("research:derived_dataset_version", ds_id)

        assert node is not None
        assert node.node_type == "derived_dataset_version"
        assert node.version == 7

    async def test_derived_dataset_version_not_found(self) -> None:
        """derived_dataset_version 无版本时返回 None。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_latest_dataset_version",
            new_callable=AsyncMock,
            return_value=None,
        ):
            assert await adapter.query_node("research:derived_dataset_version", uuid4()) is None

    async def test_dataset_version_node(self) -> None:
        """dataset_version 命名空间返回 dataset_version 节点（不重定向）。"""
        ds_id = uuid4()
        ds = _make_dataset(dataset_id=ds_id)
        version = MagicMock()
        version.version_number = 9
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=ds))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_latest_dataset_version",
            new_callable=AsyncMock,
            return_value=version,
        ):
            node = await adapter.query_node("research:dataset_version", ds_id)

        assert node is not None
        assert node.namespace == "research:dataset_version"
        assert node.node_type == "dataset_version"
        assert node.version == 9

    async def test_dataset_version_node_no_version_falls_back_to_current(self) -> None:
        """dataset_version 无版本记录时回退到 dataset.current_version。"""
        ds_id = uuid4()
        ds = _make_dataset(dataset_id=ds_id)
        ds.current_version = 1
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=ds))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_latest_dataset_version",
            new_callable=AsyncMock,
            return_value=None,
        ):
            node = await adapter.query_node("research:dataset_version", ds_id)

        assert node is not None
        assert node.version == 1

    async def test_dataset_version_node_not_found(self) -> None:
        """dataset_version 数据集不存在时返回 None。"""
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=None))
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        assert await adapter.query_node("research:dataset_version", uuid4()) is None

    async def test_insight_version_node(self) -> None:
        """insight_version 命名空间委托 get_latest_insight_version。"""
        ins_id = uuid4()
        version = MagicMock()
        version.version_number = 2
        version.conclusion = "结论内容"
        version.evidence_source_label = "experimental_data"
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_latest_insight_version",
            new_callable=AsyncMock,
            return_value=version,
        ):
            node = await adapter.query_node("research:insight_version", ins_id)

        assert node is not None
        assert node.node_type == "insight_version"
        assert node.version == 2

    async def test_result_version_node(self) -> None:
        """result_version 命名空间委托 get_latest_result_version。"""
        result_id = uuid4()
        version = MagicMock()
        version.version_number = 1
        version.title = "成果标题"
        version.status = "active"
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_latest_result_version",
            new_callable=AsyncMock,
            return_value=version,
        ):
            node = await adapter.query_node("research:result_version", result_id)

        assert node is not None
        assert node.node_type == "result_version"
        assert node.attributes["title"] == "成果标题"


# ============================================================
# query_incoming_edges
# ============================================================


class TestQueryIncomingEdges:
    """query_incoming_edges 入边查询测试。"""

    async def test_returns_converted_edges(self) -> None:
        """返回转换后的 ProvenanceEdge 列表。"""
        session = MagicMock()
        edge = _make_lineage_edge(edge_type="run_to_step")
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.list_edges_by_target",
            new_callable=AsyncMock,
            return_value=[edge],
        ):
            edges = await adapter.query_incoming_edges("research:analysis_step", uuid4())

        assert len(edges) == 1
        assert isinstance(edges[0], ProvenanceEdge)
        assert edges[0].edge_type == "run_to_step"

    async def test_empty_edges(self) -> None:
        """无入边时返回空列表。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.list_edges_by_target",
            new_callable=AsyncMock,
            return_value=[],
        ):
            edges = await adapter.query_incoming_edges("research:insight", uuid4())

        assert edges == []


# ============================================================
# check_permission
# ============================================================


class TestCheckPermission:
    """check_permission 权限校验测试。"""

    def _make_principal(self, user_id: UUID | None = None) -> Any:
        principal = MagicMock()
        principal.user_id = user_id or uuid4()
        return principal

    async def test_unknown_namespace_returns_true(self) -> None:
        """未知命名空间默认返回 True（无限制）。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))
        ok = await adapter.check_permission("research:unknown", uuid4(), self._make_principal())
        assert ok is True

    async def test_evidence_snapshot_no_user_returns_false(self) -> None:
        """evidence_snapshot 权限校验：principal 无 user_id 返回 False。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))
        principal = MagicMock()
        principal.user_id = None
        ok = await adapter.check_permission("research:evidence_snapshot", uuid4(), principal)
        assert ok is False

    async def test_evidence_snapshot_permission_granted(self) -> None:
        """evidence_snapshot 权限校验：workspace 存在返回 True。"""
        session = MagicMock()
        result = MagicMock()
        result.first.return_value = (uuid4(),)
        session.execute = AsyncMock(return_value=result)
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_workspace",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            ok = await adapter.check_permission(
                "research:evidence_snapshot", uuid4(), self._make_principal()
            )
        assert ok is True

    async def test_evidence_snapshot_not_found_returns_false(self) -> None:
        """evidence_snapshot 快照不存在返回 False。"""
        session = MagicMock()
        result = MagicMock()
        result.first.return_value = None
        session.execute = AsyncMock(return_value=result)
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        ok = await adapter.check_permission(
            "research:evidence_snapshot", uuid4(), self._make_principal()
        )
        assert ok is False

    async def test_analysis_run_permission_granted(self) -> None:
        """analysis_run 权限校验通过（workspace 存在）。"""
        session = MagicMock()
        result = MagicMock()
        result.first.return_value = (uuid4(),)
        session.execute = AsyncMock(return_value=result)
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_workspace",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            ok = await adapter.check_permission(
                "research:analysis_run", uuid4(), self._make_principal()
            )
        assert ok is True

    async def test_analysis_step_permission_via_step(self) -> None:
        """analysis_step 权限校验：通过 step.run_id 找到 run 后校验。"""
        session = MagicMock()
        run_ws_result = MagicMock()
        run_ws_result.first.return_value = None  # run not found directly
        step_result = MagicMock()
        step_result.first.return_value = (uuid4(),)  # step found
        run_result2 = MagicMock()
        run_result2.first.return_value = (uuid4(),)  # run via step found
        session.execute = AsyncMock(side_effect=[run_ws_result, step_result, run_result2])
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_workspace",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            ok = await adapter.check_permission(
                "research:analysis_step", uuid4(), self._make_principal()
            )
        assert ok is True

    async def test_analysis_run_no_user_returns_false(self) -> None:
        """analysis_run 无 user_id 返回 False。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))
        principal = MagicMock()
        principal.user_id = None
        assert await adapter.check_permission("research:analysis_run", uuid4(), principal) is False

    async def test_product_permission_dataset_granted(self) -> None:
        """derived_dataset 权限校验通过。"""
        session = MagicMock()
        result = MagicMock()
        result.first.return_value = (uuid4(),)
        session.execute = AsyncMock(return_value=result)
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_workspace",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            ok = await adapter.check_permission(
                "research:derived_dataset", uuid4(), self._make_principal()
            )
        assert ok is True

    async def test_product_permission_view_no_workspace_returns_false(self) -> None:
        """view 无 workspace_id 返回 False。"""
        session = MagicMock()
        result = MagicMock()
        result.first.return_value = None
        session.execute = AsyncMock(return_value=result)
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        ok = await adapter.check_permission("research:view", uuid4(), self._make_principal())
        assert ok is False

    async def test_result_version_permission_published(self) -> None:
        """result_version 已发布返回 True。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_result",
            new_callable=AsyncMock,
            return_value=MagicMock(status="published"),
        ):
            ok = await adapter.check_permission(
                "research:result_version", uuid4(), self._make_principal()
            )
        assert ok is True

    async def test_result_version_permission_unpublished(self) -> None:
        """result_version 未发布返回 False。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_result",
            new_callable=AsyncMock,
            return_value=MagicMock(status="draft"),
        ):
            ok = await adapter.check_permission(
                "research:result_version", uuid4(), self._make_principal()
            )
        assert ok is False

    async def test_result_version_not_found_returns_false(self) -> None:
        """result_version 成果不存在返回 False。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_result",
            new_callable=AsyncMock,
            return_value=None,
        ):
            ok = await adapter.check_permission(
                "research:result_version", uuid4(), self._make_principal()
            )
        assert ok is False

    async def test_workspace_permission_granted(self) -> None:
        """workspace 权限校验通过。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_workspace",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            ok = await adapter.check_permission(
                "research:workspace", uuid4(), self._make_principal()
            )
        assert ok is True

    async def test_workspace_no_user_returns_false(self) -> None:
        """workspace 无 user_id 返回 False。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))
        principal = MagicMock()
        principal.user_id = None
        assert await adapter.check_permission("research:workspace", uuid4(), principal) is False

    async def test_knowledge_reference_permission_granted(self) -> None:
        """knowledge_reference 权限校验通过。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        ref = MagicMock()
        ref.workspace_id = uuid4()
        with (
            patch(
                "packages.research.adapters.research_lineage.ResearchRepository.get_knowledge_reference",
                new_callable=AsyncMock,
                return_value=ref,
            ),
            patch(
                "packages.research.adapters.research_lineage.ResearchRepository.get_workspace",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
        ):
            ok = await adapter.check_permission(
                "research:knowledge_reference", uuid4(), self._make_principal()
            )
        assert ok is True

    async def test_knowledge_reference_not_found_returns_false(self) -> None:
        """knowledge_reference 不存在返回 False。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_knowledge_reference",
            new_callable=AsyncMock,
            return_value=None,
        ):
            ok = await adapter.check_permission(
                "research:knowledge_reference", uuid4(), self._make_principal()
            )
        assert ok is False

    async def test_permission_exception_returns_false(self) -> None:
        """权限校验异常时返回 False（不抛出）。"""
        session = MagicMock()
        adapter = ResearchLineageAdapterImpl(session_factory=_make_factory(session))

        with patch(
            "packages.research.adapters.research_lineage.ResearchRepository.get_workspace",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB error"),
        ):
            ok = await adapter.check_permission(
                "research:workspace", uuid4(), self._make_principal()
            )
        assert ok is False
