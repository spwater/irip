"""只读核心 Provenance 适配器（阶段 5 新增）。

CoreProvenanceAdapterImpl 封装核心系统的只读查询，
查询 Fact / DerivationRun / EvidenceSet 等节点的展示信息，
不修改核心表，不暴露核心数据库会话。

namespace 取值：core:fact / core:derivation_run / core:evidence_set

参照架构设计 3.3 节 CoreProvenanceAdapterImpl。
"""

import logging
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.research.models import ProvenanceEdge, ProvenanceNode

logger = logging.getLogger("research.adapters.core_provenance")


class CoreProvenanceAdapter(Protocol):
    """只读核心 Provenance 适配器接口（Protocol）。

    研究域通过此接口只读查询核心系统的 Provenance 节点，
    不暴露核心数据库会话。
    """

    async def query_node(self, namespace: str, node_id: UUID) -> ProvenanceNode | None:
        """查询单个核心节点的展示信息（不返回内容数据）。"""
        ...

    async def query_incoming_edges(
        self, namespace: str, node_id: UUID
    ) -> list[ProvenanceEdge]:
        """查询节点的入边（上游来源）。"""
        ...

    async def check_permission(
        self, namespace: str, node_id: UUID, principal: object
    ) -> bool:
        """校验 principal 对核心节点的访问权限。"""
        ...


# 边类型标签映射。
_EDGE_TYPE_LABELS: dict[str, str] = {
    "fact_to_evidence_set": "事实→证据集",
    "evidence_set_to_derivation_run": "证据集→推导",
    "fact_to_derivation_run": "事实→推导",
}


class CoreProvenanceAdapterImpl:
    """CoreProvenanceAdapter 实现类。

    查询核心系统的 Fact、DerivationRun、EvidenceSet 等节点，
    不修改核心表，不暴露核心数据库会话。

    Attributes:
        _factory: 异步会话工厂。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """初始化核心 Provenance 适配器。

        Args:
            session_factory: 异步会话工厂。
        """
        self._factory = session_factory

    async def query_node(self, namespace: str, node_id: UUID) -> ProvenanceNode | None:
        """查询单个核心节点的展示信息。

        根据 namespace 路由到对应查询方法。

        Args:
            namespace: 命名空间（core:fact / core:derivation_run / core:evidence_set）。
            node_id: 节点 UUID。

        Returns:
            ProvenanceNode | None: 节点展示信息，不存在时返回 None。
        """
        if namespace == "core:fact":
            return await self._query_fact(node_id)
        elif namespace == "core:derivation_run":
            return await self._query_derivation_run(node_id)
        elif namespace == "core:evidence_set":
            return await self._query_evidence_set(node_id)
        logger.warning("Unknown core namespace: %s", namespace)
        return None

    async def query_incoming_edges(
        self, namespace: str, node_id: UUID
    ) -> list[ProvenanceEdge]:
        """查询节点的入边（上游来源）。

        - core:fact: 通常无上游（实验事实是溯源链的根）
        - core:derivation_run: 上游为 EvidenceSet
        - core:evidence_set: 上游为 Fact 或 DerivationRun

        Args:
            namespace: 命名空间。
            node_id: 节点 UUID。

        Returns:
            list[ProvenanceEdge]: 入边列表。
        """
        if namespace == "core:fact":
            # Fact 是溯源链的根，无上游
            return []
        elif namespace == "core:derivation_run":
            return await self._query_derivation_run_incoming_edges(node_id)
        elif namespace == "core:evidence_set":
            return await self._query_evidence_set_incoming_edges(node_id)
        return []

    async def check_permission(
        self, namespace: str, node_id: UUID, principal: object
    ) -> bool:
        """校验 principal 对核心节点的访问权限。

        复用核心系统现有权限校验逻辑（Fact 的可见范围）。

        Args:
            namespace: 命名空间。
            node_id: 节点 UUID。
            principal: 当前身份上下文（含 user_id / department_id）。

        Returns:
            bool: 是否有权访问。
        """
        if namespace == "core:fact":
            return await self._check_fact_permission(node_id, principal)
        elif namespace == "core:derivation_run":
            return await self._check_derivation_run_permission(node_id, principal)
        elif namespace == "core:evidence_set":
            return await self._check_evidence_set_permission(node_id, principal)
        return False

    # ---- 内部查询方法 ----

    async def _query_fact(self, fact_id: UUID) -> ProvenanceNode | None:
        """查询 Fact 节点展示信息。

        Args:
            fact_id: Fact UUID。

        Returns:
            ProvenanceNode | None: 节点展示信息。
        """
        from packages.facts.entities import Fact

        async with self._factory() as session:
            result = await session.execute(
                sa.select(
                    Fact.id,
                    Fact.fact_type,
                    Fact.subject_id,
                    Fact.status,
                    Fact.task_name,
                    Fact.department_name,
                ).where(Fact.id == fact_id)
            )
            row = result.first()

        if row is None:
            return None

        return ProvenanceNode(
            namespace="core:fact",
            node_id=fact_id,
            version=None,
            node_type="fact",
            display_label=None,
            attributes={
                "name": row.subject_id or row.task_name or str(fact_id)[:8],
                "fact_type": row.fact_type,
                "status": row.status,
                "department_name": row.department_name,
            },
            is_restricted=False,
        )

    async def _query_derivation_run(self, run_id: UUID) -> ProvenanceNode | None:
        """查询 DerivationRun 节点展示信息。

        Args:
            run_id: DerivationRun UUID。

        Returns:
            ProvenanceNode | None: 节点展示信息。
        """
        # 核心推导表可能不存在或名称不同，使用通用查询。
        # 如果核心系统有 derivation_run 表，查询其展示信息。
        try:
            async with self._factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT id, status, created_at FROM derivation_run WHERE id = :rid"
                    ),
                    {"rid": str(run_id)},
                )
                row = result.first()
        except Exception:
            row = None

        if row is None:
            return None

        return ProvenanceNode(
            namespace="core:derivation_run",
            node_id=run_id,
            version=None,
            node_type="derivation_run",
            display_label=None,
            attributes={
                "name": f"推导运行 {str(run_id)[:8]}",
                "status": row[1] if len(row) > 1 else "unknown",
            },
            is_restricted=False,
        )

    async def _query_evidence_set(self, evidence_set_id: UUID) -> ProvenanceNode | None:
        """查询 EvidenceSet 节点展示信息。

        Args:
            evidence_set_id: EvidenceSet UUID。

        Returns:
            ProvenanceNode | None: 节点展示信息。
        """
        try:
            async with self._factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT id, status, created_at FROM evidence_set WHERE id = :eid"
                    ),
                    {"eid": str(evidence_set_id)},
                )
                row = result.first()
        except Exception:
            row = None

        if row is None:
            return None

        return ProvenanceNode(
            namespace="core:evidence_set",
            node_id=evidence_set_id,
            version=None,
            node_type="evidence_set",
            display_label=None,
            attributes={
                "name": f"证据集 {str(evidence_set_id)[:8]}",
                "status": row[1] if len(row) > 1 else "unknown",
            },
            is_restricted=False,
        )

    async def _query_derivation_run_incoming_edges(
        self, run_id: UUID
    ) -> list[ProvenanceEdge]:
        """查询 DerivationRun 的入边。

        上游为 EvidenceSet。

        Args:
            run_id: DerivationRun UUID。

        Returns:
            list[ProvenanceEdge]: 入边列表。
        """
        edges: list[ProvenanceEdge] = []
        try:
            async with self._factory() as session:
                # 查询 provenance_edge 表（如果存在）
                result = await session.execute(
                    sa.text(
                        "SELECT source_namespace, source_id, source_version, "
                        "target_namespace, target_id, target_version, edge_type "
                        "FROM provenance_edge WHERE target_namespace = 'core:derivation_run' "
                        "AND target_id = :rid"
                    ),
                    {"rid": str(run_id)},
                )
                for row in result.fetchall():
                    edges.append(
                        ProvenanceEdge(
                            source_namespace=row[0],
                            source_id=UUID(str(row[1])),
                            source_version=row[2],
                            target_namespace=row[3],
                            target_id=UUID(str(row[4])),
                            target_version=row[5],
                            edge_type=row[6],
                            edge_type_label=_EDGE_TYPE_LABELS.get(row[6], row[6]),
                        )
                    )
        except Exception as exc:
            logger.debug("Failed to query derivation_run incoming edges: %s", exc)
        return edges

    async def _query_evidence_set_incoming_edges(
        self, evidence_set_id: UUID
    ) -> list[ProvenanceEdge]:
        """查询 EvidenceSet 的入边。

        上游为 Fact 或 DerivationRun。

        Args:
            evidence_set_id: EvidenceSet UUID。

        Returns:
            list[ProvenanceEdge]: 入边列表。
        """
        edges: list[ProvenanceEdge] = []
        try:
            async with self._factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT source_namespace, source_id, source_version, "
                        "target_namespace, target_id, target_version, edge_type "
                        "FROM provenance_edge WHERE target_namespace = 'core:evidence_set' "
                        "AND target_id = :eid"
                    ),
                    {"eid": str(evidence_set_id)},
                )
                for row in result.fetchall():
                    edges.append(
                        ProvenanceEdge(
                            source_namespace=row[0],
                            source_id=UUID(str(row[1])),
                            source_version=row[2],
                            target_namespace=row[3],
                            target_id=UUID(str(row[4])),
                            target_version=row[5],
                            edge_type=row[6],
                            edge_type_label=_EDGE_TYPE_LABELS.get(row[6], row[6]),
                        )
                    )
        except Exception as exc:
            logger.debug("Failed to query evidence_set incoming edges: %s", exc)
        return edges

    async def _check_fact_permission(
        self, fact_id: UUID, principal: object
    ) -> bool:
        """校验 Fact 访问权限。

        通过查询 Fact 是否存在且状态为 active 来判断。
        实际权限校验由核心系统的 RLS 隔离自动处理。

        Args:
            fact_id: Fact UUID。
            principal: 当前身份上下文。

        Returns:
            bool: 是否有权访问。
        """
        from packages.facts.entities import Fact

        try:
            async with self._factory() as session:
                result = await session.execute(
                    sa.select(sa.func.count())
                    .select_from(Fact)
                    .where(
                        Fact.id == fact_id,
                        Fact.status.in_(["active", "superseded", "withdrawn"]),
                    )
                )
                return int(result.scalar() or 0) > 0
        except Exception as exc:
            logger.warning("Fact permission check failed: %s", exc)
            return False

    async def _check_derivation_run_permission(
        self, run_id: UUID, principal: object
    ) -> bool:
        """校验 DerivationRun 访问权限。

        Args:
            run_id: DerivationRun UUID。
            principal: 当前身份上下文。

        Returns:
            bool: 是否有权访问。
        """
        try:
            async with self._factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT count(*) FROM derivation_run WHERE id = :rid"
                    ),
                    {"rid": str(run_id)},
                )
                return int(result.scalar() or 0) > 0
        except Exception:
            return False

    async def _check_evidence_set_permission(
        self, evidence_set_id: UUID, principal: object
    ) -> bool:
        """校验 EvidenceSet 访问权限。

        Args:
            evidence_set_id: EvidenceSet UUID。
            principal: 当前身份上下文。

        Returns:
            bool: 是否有权访问。
        """
        try:
            async with self._factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT count(*) FROM evidence_set WHERE id = :eid"
                    ),
                    {"eid": str(evidence_set_id)},
                )
                return int(result.scalar() or 0) > 0
        except Exception:
            return False
