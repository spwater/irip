"""单元测试：CoreProvenanceAdapterImpl 只读核心溯源适配器。

覆盖：
- query_node 各命名空间路由（core:fact / core:derivation_run / core:evidence_set）
  + 未知命名空间 + 节点不存在；
- query_incoming_edges 各命名空间分支（fact 空列表 / derivation_run / evidence_set
  + 异常吞掉返回空）；
- check_permission 各命名空间分支 + 异常返回 False。

使用 Mock session_factory，不依赖真实数据库。
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from packages.research.adapters.core_provenance import (
    _EDGE_TYPE_LABELS,
    CoreProvenanceAdapterImpl,
)

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
    def factory() -> _FakeSessionContext:
        return _FakeSessionContext(session)

    return factory


def _make_result(
    first_row: Any = None,
    scalar: Any = None,
    fetchall_rows: list[Any] | None = None,
) -> MagicMock:
    """构造 session.execute 返回的 Result mock。"""
    result = MagicMock()
    result.first.return_value = first_row
    result.scalar.return_value = scalar
    result.fetchall.return_value = fetchall_rows or []
    return result


def _make_fact_row(
    fact_id: UUID | None = None,
    subject_id: str = "subject-001",
    task_name: str | None = None,
) -> Any:
    """构造 Fact 行（属性访问形式）。"""
    row = MagicMock()
    row.id = fact_id or uuid4()
    row.fact_type = "experiment_run"
    row.subject_id = subject_id
    row.task_name = task_name
    row.status = "active"
    row.department_name = "研发一部"
    return row


# ============================================================
# query_node
# ============================================================


class TestQueryNode:
    """query_node 路由与节点查询测试。"""

    async def test_unknown_namespace_returns_none(self) -> None:
        """未知命名空间返回 None。"""
        session = MagicMock()
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))
        assert await adapter.query_node("core:unknown", uuid4()) is None

    async def test_fact_node(self) -> None:
        """core:fact 命名空间返回正确节点。"""
        fact_id = uuid4()
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_result(first_row=_make_fact_row(fact_id)))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("core:fact", fact_id)

        assert node is not None
        assert node.namespace == "core:fact"
        assert node.node_type == "fact"
        assert node.attributes["fact_type"] == "experiment_run"
        assert node.attributes["status"] == "active"

    async def test_fact_not_found(self) -> None:
        """core:fact 不存在返回 None。"""
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_result(first_row=None))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))
        assert await adapter.query_node("core:fact", uuid4()) is None

    async def test_derivation_run_node(self) -> None:
        """core:derivation_run 命名空间返回正确节点。"""
        run_id = uuid4()
        session = MagicMock()
        row = (run_id, "succeeded", "2026-01-01T00:00:00")
        session.execute = AsyncMock(return_value=_make_result(first_row=row))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("core:derivation_run", run_id)

        assert node is not None
        assert node.namespace == "core:derivation_run"
        assert node.node_type == "derivation_run"
        assert node.attributes["status"] == "succeeded"

    async def test_derivation_run_not_found(self) -> None:
        """core:derivation_run 不存在返回 None（查询异常也返回 None）。"""
        session = MagicMock()
        session.execute = AsyncMock(side_effect=Exception("table missing"))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))
        assert await adapter.query_node("core:derivation_run", uuid4()) is None

    async def test_evidence_set_node(self) -> None:
        """core:evidence_set 命名空间返回正确节点。"""
        es_id = uuid4()
        session = MagicMock()
        row = (es_id, "active", "2026-01-01T00:00:00")
        session.execute = AsyncMock(return_value=_make_result(first_row=row))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("core:evidence_set", es_id)

        assert node is not None
        assert node.namespace == "core:evidence_set"
        assert node.node_type == "evidence_set"

    async def test_evidence_set_not_found(self) -> None:
        """core:evidence_set 不存在返回 None。"""
        session = MagicMock()
        session.execute = AsyncMock(side_effect=Exception("no table"))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))
        assert await adapter.query_node("core:evidence_set", uuid4()) is None

    async def test_fact_node_uses_subject_id_when_no_task_name(self) -> None:
        """Fact 行 subject_id 优先用于 name。"""
        fact_id = uuid4()
        session = MagicMock()
        row = _make_fact_row(fact_id=fact_id, subject_id="SUBJ-42", task_name=None)
        session.execute = AsyncMock(return_value=_make_result(first_row=row))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        node = await adapter.query_node("core:fact", fact_id)
        assert node is not None
        assert node.attributes["name"] == "SUBJ-42"


# ============================================================
# query_incoming_edges
# ============================================================


class TestQueryIncomingEdges:
    """query_incoming_edges 入边查询测试。"""

    async def test_fact_returns_empty(self) -> None:
        """core:fact 是溯源链根，无上游。"""
        session = MagicMock()
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))
        assert await adapter.query_incoming_edges("core:fact", uuid4()) == []

    async def test_unknown_namespace_returns_empty(self) -> None:
        """未知命名空间返回空列表。"""
        session = MagicMock()
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))
        assert await adapter.query_incoming_edges("core:unknown", uuid4()) == []

    async def test_derivation_run_incoming_edges(self) -> None:
        """derivation_run 入边查询返回转换后的边。"""
        session = MagicMock()
        src_id = uuid4()
        tgt_id = uuid4()
        row = (
            "core:evidence_set",
            str(src_id),
            None,
            "core:derivation_run",
            str(tgt_id),
            None,
            "evidence_set_to_derivation_run",
        )
        session.execute = AsyncMock(return_value=_make_result(fetchall_rows=[row]))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        edges = await adapter.query_incoming_edges("core:derivation_run", tgt_id)

        assert len(edges) == 1
        assert edges[0].source_namespace == "core:evidence_set"
        assert edges[0].edge_type == "evidence_set_to_derivation_run"
        assert edges[0].edge_type_label == _EDGE_TYPE_LABELS["evidence_set_to_derivation_run"]

    async def test_derivation_run_incoming_edges_error_returns_empty(self) -> None:
        """derivation_run 入边查询异常时返回空列表。"""
        session = MagicMock()
        session.execute = AsyncMock(side_effect=Exception("no table"))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        edges = await adapter.query_incoming_edges("core:derivation_run", uuid4())
        assert edges == []

    async def test_evidence_set_incoming_edges(self) -> None:
        """evidence_set 入边查询返回转换后的边。"""
        session = MagicMock()
        src_id = uuid4()
        tgt_id = uuid4()
        row = (
            "core:fact",
            str(src_id),
            None,
            "core:evidence_set",
            str(tgt_id),
            None,
            "fact_to_evidence_set",
        )
        session.execute = AsyncMock(return_value=_make_result(fetchall_rows=[row]))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        edges = await adapter.query_incoming_edges("core:evidence_set", tgt_id)

        assert len(edges) == 1
        assert edges[0].edge_type == "fact_to_evidence_set"

    async def test_evidence_set_incoming_edges_error_returns_empty(self) -> None:
        """evidence_set 入边查询异常时返回空列表。"""
        session = MagicMock()
        session.execute = AsyncMock(side_effect=Exception("no table"))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        assert await adapter.query_incoming_edges("core:evidence_set", uuid4()) == []


# ============================================================
# check_permission
# ============================================================


class TestCheckPermission:
    """check_permission 权限校验测试。"""

    async def test_unknown_namespace_returns_false(self) -> None:
        """未知命名空间返回 False。"""
        session = MagicMock()
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))
        assert await adapter.check_permission("core:unknown", uuid4(), MagicMock()) is False

    async def test_fact_permission_granted(self) -> None:
        """core:fact 存在且状态合法返回 True。"""
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=1))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        ok = await adapter.check_permission("core:fact", uuid4(), MagicMock())
        assert ok is True

    async def test_fact_permission_not_found(self) -> None:
        """core:fact 不存在返回 False。"""
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=0))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        ok = await adapter.check_permission("core:fact", uuid4(), MagicMock())
        assert ok is False

    async def test_fact_permission_exception_returns_false(self) -> None:
        """core:fact 权限校验异常返回 False。"""
        session = MagicMock()
        session.execute = AsyncMock(side_effect=Exception("DB down"))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        ok = await adapter.check_permission("core:fact", uuid4(), MagicMock())
        assert ok is False

    async def test_derivation_run_permission_granted(self) -> None:
        """core:derivation_run 存在返回 True。"""
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=1))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        ok = await adapter.check_permission("core:derivation_run", uuid4(), MagicMock())
        assert ok is True

    async def test_derivation_run_permission_not_found(self) -> None:
        """core:derivation_run 不存在返回 False。"""
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=0))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        ok = await adapter.check_permission("core:derivation_run", uuid4(), MagicMock())
        assert ok is False

    async def test_derivation_run_permission_exception_returns_false(self) -> None:
        """core:derivation_run 权限校验异常返回 False。"""
        session = MagicMock()
        session.execute = AsyncMock(side_effect=Exception("no table"))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        ok = await adapter.check_permission("core:derivation_run", uuid4(), MagicMock())
        assert ok is False

    async def test_evidence_set_permission_granted(self) -> None:
        """core:evidence_set 存在返回 True。"""
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=1))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        ok = await adapter.check_permission("core:evidence_set", uuid4(), MagicMock())
        assert ok is True

    async def test_evidence_set_permission_not_found(self) -> None:
        """core:evidence_set 不存在返回 False。"""
        session = MagicMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=0))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        ok = await adapter.check_permission("core:evidence_set", uuid4(), MagicMock())
        assert ok is False

    async def test_evidence_set_permission_exception_returns_false(self) -> None:
        """core:evidence_set 权限校验异常返回 False。"""
        session = MagicMock()
        session.execute = AsyncMock(side_effect=Exception("no table"))
        adapter = CoreProvenanceAdapterImpl(session_factory=_make_factory(session))

        ok = await adapter.check_permission("core:evidence_set", uuid4(), MagicMock())
        assert ok is False
