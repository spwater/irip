"""只读研究 Lineage 适配器（阶段 5 新增）。

ResearchLineageAdapterImpl 封装研究域的只读查询，
查询 EvidenceSnapshot / AnalysisRun / Step / DerivedDataset / View /
Insight / ResultVersion / Workspace / KnowledgeReference 节点 +
research_lineage_edge 入边。

跨边界边（source_namespace 为 core:*）由 query_incoming_edges 返回，
统一服务根据 source_namespace 路由到 CoreProvenanceAdapter 继续追溯。

参照架构设计 3.3 节 ResearchLineageAdapterImpl。
"""

import logging
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.research.entities import (
    ResearchDerivedDataset,
    ResearchEvidenceSnapshot,
    ResearchInsight,
    ResearchKnowledgeReference,
    ResearchLineageEdge,
    ResearchView,
    ResearchViewVersion,
    ResearchWorkspace,
)
from packages.research.execution.entities_trusted import (
    ResearchAnalysisRun,
    ResearchAnalysisStep,
)
from packages.research.models import ProvenanceEdge, ProvenanceNode
from packages.research.repository import ResearchRepository

logger = logging.getLogger("research.adapters.research_lineage")


class ResearchLineageAdapter(Protocol):
    """只读研究 Lineage 适配器接口（Protocol）。

    查询研究域节点和溯源边（research_lineage_edge 表）。
    跨边界边（source_namespace 为 core:*）由本方法返回，
    统一服务根据 source_namespace 路由到 CoreProvenanceAdapter 继续追溯。
    """

    async def query_node(self, namespace: str, node_id: UUID) -> ProvenanceNode | None:
        """查询单个研究域节点的展示信息。"""
        ...

    async def query_incoming_edges(self, namespace: str, node_id: UUID) -> list[ProvenanceEdge]:
        """查询节点的入边（上游来源）。"""
        ...

    async def check_permission(self, namespace: str, node_id: UUID, principal: object) -> bool:
        """校验 principal 对研究域节点的访问权限。"""
        ...


# 边类型标签映射。
_EDGE_TYPE_LABELS: dict[str, str] = {
    "workspace_to_result": "空间→成果",
    "dataset_to_result": "数据集→成果",
    "view_to_result": "图表→成果",
    "insight_to_result": "Insight→成果",
    "fact_to_snapshot": "事实→快照",
    "published_derived_to_snapshot": "已发布数据→快照",
    "snapshot_to_run": "快照→运行",
    "run_to_step": "运行→步骤",
    "run_to_dataset": "运行→数据集",
    "run_to_view": "运行→图表",
    "run_to_insight": "运行→Insight",
    "knowledge_ref_to_insight": "知识引用→Insight",
}


def _edge_to_provenance_edge(e: ResearchLineageEdge) -> ProvenanceEdge:
    """将 ORM 实体转换为 ProvenanceEdge dataclass。

    Args:
        e: 溯源边 ORM 实体。

    Returns:
        ProvenanceEdge: 溯源边 dataclass。
    """
    return ProvenanceEdge(
        source_namespace=e.source_namespace,
        source_id=e.source_id,
        source_version=e.source_version,
        target_namespace=e.target_namespace,
        target_id=e.target_id,
        target_version=e.target_version,
        edge_type=e.edge_type,
        edge_type_label=_EDGE_TYPE_LABELS.get(e.edge_type, e.edge_type),
    )


class ResearchLineageAdapterImpl:
    """ResearchLineageAdapter 实现类。

    查询研究域节点和溯源边（research_lineage_edge 表）。
    跨边界边（source_namespace 为 core:*）由 query_incoming_edges 返回，
    统一服务根据 source_namespace 路由到 CoreProvenanceAdapter 继续追溯。

    Attributes:
        _factory: 异步会话工厂。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """初始化研究 Lineage 适配器。

        Args:
            session_factory: 异步会话工厂。
        """
        self._factory = session_factory

    async def query_node(self, namespace: str, node_id: UUID) -> ProvenanceNode | None:
        """查询单个研究域节点的展示信息。

        按 namespace 路由到对应查询方法。

        Args:
            namespace: 命名空间。
            node_id: 节点 UUID。

        Returns:
            ProvenanceNode | None: 节点展示信息，不存在时返回 None。
        """
        routing = {
            "research:evidence_snapshot": self._query_evidence_snapshot,
            "research:analysis_run": self._query_analysis_run,
            "research:analysis_step": self._query_analysis_step,
            "research:derived_dataset": self._query_derived_dataset,
            "research:derived_dataset_version": self._query_derived_dataset_version,
            "research:dataset_version": self._query_dataset_version,
            "research:view": self._query_view,
            "research:view_version": self._query_view_version,
            "research:insight": self._query_insight,
            "research:insight_version": self._query_insight_version,
            "research:result_version": self._query_result_version,
            "research:workspace": self._query_workspace,
            "research:knowledge_reference": self._query_knowledge_reference,
        }
        handler = routing.get(namespace)
        if handler is None:
            logger.warning("Unknown research namespace: %s", namespace)
            return None
        return await handler(node_id)

    async def query_incoming_edges(self, namespace: str, node_id: UUID) -> list[ProvenanceEdge]:
        """查询节点的入边（上游来源）。

        从 research_lineage_edge 表查询 target_namespace + target_id 匹配的边。
        返回入边列表（含跨边界边，source_namespace 可能为 core:*）。

        Args:
            namespace: 命名空间。
            node_id: 节点 UUID。

        Returns:
            list[ProvenanceEdge]: 入边列表。
        """
        async with self._factory() as session:
            edges = await ResearchRepository.list_edges_by_target(session, namespace, node_id)
        return [_edge_to_provenance_edge(e) for e in edges]

    async def check_permission(self, namespace: str, node_id: UUID, principal: object) -> bool:
        """校验 principal 对研究域节点的访问权限。

        复用阶段 1-4 权限校验逻辑。

        Args:
            namespace: 命名空间。
            node_id: 节点 UUID。
            principal: 当前身份上下文（含 user_id）。

        Returns:
            bool: 是否有权访问。
        """
        if namespace == "research:evidence_snapshot":
            return await self._check_evidence_snapshot_permission(node_id, principal)
        elif namespace == "research:analysis_run":
            return await self._check_analysis_run_permission(node_id, principal)
        elif namespace in (
            "research:derived_dataset",
            "research:derived_dataset_version",
            "research:dataset_version",
        ):
            return await self._check_product_permission(namespace, node_id, principal)
        elif namespace in ("research:view", "research:view_version"):
            return await self._check_product_permission(namespace, node_id, principal)
        elif namespace in ("research:insight", "research:insight_version"):
            return await self._check_product_permission(namespace, node_id, principal)
        elif namespace == "research:result_version":
            return await self._check_result_version_permission(node_id, principal)
        elif namespace == "research:workspace":
            return await self._check_workspace_permission(node_id, principal)
        elif namespace == "research:knowledge_reference":
            return await self._check_knowledge_reference_permission(node_id, principal)
        elif namespace == "research:analysis_step":
            return await self._check_analysis_run_permission(node_id, principal)
        return True

    # ---- 节点查询方法 ----

    async def _query_evidence_snapshot(self, snapshot_id: UUID) -> ProvenanceNode | None:
        """查询证据快照节点。"""
        async with self._factory() as session:
            result = await session.execute(
                sa.select(ResearchEvidenceSnapshot).where(
                    ResearchEvidenceSnapshot.id == snapshot_id
                )
            )
            snap = result.scalar_one_or_none()

        if snap is None:
            return None

        return ProvenanceNode(
            namespace="research:evidence_snapshot",
            node_id=snapshot_id,
            version=snap.snapshot_number,
            node_type="evidence_snapshot",
            display_label=None,
            attributes={
                "name": f"证据快照 #{snap.snapshot_number}",
                "snapshot_number": snap.snapshot_number,
                "content_hash": snap.content_hash[:16],
                "workspace_id": str(snap.workspace_id),
            },
            is_restricted=False,
        )

    async def _query_analysis_run(self, run_id: UUID) -> ProvenanceNode | None:
        """查询分析运行节点。"""
        async with self._factory() as session:
            result = await session.execute(
                sa.select(ResearchAnalysisRun).where(ResearchAnalysisRun.id == run_id)
            )
            run = result.scalar_one_or_none()

        if run is None:
            return None

        return ProvenanceNode(
            namespace="research:analysis_run",
            node_id=run_id,
            version=None,
            node_type="analysis_run",
            display_label=None,
            attributes={
                "name": f"分析运行 {str(run_id)[:8]}",
                "status": run.status,
                "workspace_id": str(run.workspace_id),
            },
            is_restricted=False,
        )

    async def _query_analysis_step(self, step_id: UUID) -> ProvenanceNode | None:
        """查询分析步骤节点。"""
        async with self._factory() as session:
            result = await session.execute(
                sa.select(ResearchAnalysisStep).where(ResearchAnalysisStep.id == step_id)
            )
            step = result.scalar_one_or_none()

        if step is None:
            return None

        return ProvenanceNode(
            namespace="research:analysis_step",
            node_id=step_id,
            version=None,
            node_type="analysis_step",
            display_label=None,
            attributes={
                "name": step.step_key,
                "step_key": step.step_key,
                "status": step.status,
                "run_id": str(step.run_id),
            },
            is_restricted=False,
        )

    async def _query_derived_dataset(self, dataset_id: UUID) -> ProvenanceNode | None:
        """查询衍生数据集节点。"""
        async with self._factory() as session:
            result = await session.execute(
                sa.select(ResearchDerivedDataset).where(ResearchDerivedDataset.id == dataset_id)
            )
            ds = result.scalar_one_or_none()

        if ds is None:
            return None

        return ProvenanceNode(
            namespace="research:derived_dataset",
            node_id=dataset_id,
            version=ds.current_version,
            node_type="derived_dataset",
            display_label=None,
            attributes={
                "name": ds.name,
                "status": ds.status,
                "version_number": ds.current_version,
                "workspace_id": str(ds.workspace_id),
            },
            is_restricted=False,
        )

    async def _query_derived_dataset_version(self, dataset_id: UUID) -> ProvenanceNode | None:
        """查询衍生数据集版本节点（取最新版本）。"""
        async with self._factory() as session:
            version = await ResearchRepository.get_latest_dataset_version(session, dataset_id)
        if version is None:
            return None

        return ProvenanceNode(
            namespace="research:derived_dataset_version",
            node_id=dataset_id,
            version=version.version_number,
            node_type="derived_dataset_version",
            display_label=None,
            attributes={
                "name": f"数据集版本 v{version.version_number}",
                "version_number": version.version_number,
                "content_hash": version.content_hash[:16],
                "dataset_id": str(dataset_id),
            },
            is_restricted=False,
        )

    async def _query_dataset_version(self, dataset_id: UUID) -> ProvenanceNode | None:
        """查询数据集版本节点（research:dataset_version 命名空间）。

        返回的节点保持 research:dataset_version 命名空间和原始 dataset_id，
        不重定向到 research:derived_dataset_version（避免边引用节点不匹配）。
        """
        async with self._factory() as session:
            ds_result = await session.execute(
                sa.select(ResearchDerivedDataset).where(ResearchDerivedDataset.id == dataset_id)
            )
            ds = ds_result.scalar_one_or_none()
            if ds is None:
                return None
            version = await ResearchRepository.get_latest_dataset_version(session, dataset_id)

        return ProvenanceNode(
            namespace="research:dataset_version",
            node_id=dataset_id,
            version=version.version_number if version else ds.current_version,
            node_type="dataset_version",
            display_label=None,
            attributes={
                "name": ds.name,
                "version_number": version.version_number if version else ds.current_version,
                "dataset_id": str(dataset_id),
            },
            is_restricted=False,
        )

    async def _query_view(self, view_id: UUID) -> ProvenanceNode | None:
        """查询研究视图节点。"""
        async with self._factory() as session:
            result = await session.execute(
                sa.select(ResearchView).where(ResearchView.id == view_id)
            )
            v = result.scalar_one_or_none()

        if v is None:
            return None

        return ProvenanceNode(
            namespace="research:view",
            node_id=view_id,
            version=v.current_version,
            node_type="view",
            display_label=None,
            attributes={
                "name": v.name,
                "status": v.status,
                "version_number": v.current_version,
                "caption": v.caption or "",
            },
            is_restricted=False,
        )

    async def _query_view_version(self, view_id: UUID) -> ProvenanceNode | None:
        """查询视图版本节点（取最新版本）。"""
        async with self._factory() as session:
            result = await session.execute(
                sa.select(ResearchViewVersion)
                .where(ResearchViewVersion.view_id == view_id)
                .order_by(ResearchViewVersion.version_number.desc())
                .limit(1)
            )
            v = result.scalar_one_or_none()

        if v is None:
            return None

        return ProvenanceNode(
            namespace="research:view_version",
            node_id=view_id,
            version=v.version_number,
            node_type="view_version",
            display_label=None,
            attributes={
                "name": f"图表版本 v{v.version_number}",
                "version_number": v.version_number,
                "image_format": v.image_format,
            },
            is_restricted=False,
        )

    async def _query_insight(self, insight_id: UUID) -> ProvenanceNode | None:
        """查询 Insight 节点。"""
        async with self._factory() as session:
            result = await session.execute(
                sa.select(ResearchInsight).where(ResearchInsight.id == insight_id)
            )
            ins = result.scalar_one_or_none()

        if ins is None:
            return None

        return ProvenanceNode(
            namespace="research:insight",
            node_id=insight_id,
            version=ins.current_version,
            node_type="insight",
            display_label=None,
            attributes={
                "name": ins.name,
                "status": ins.status,
                "version_number": ins.current_version,
                "workspace_id": str(ins.workspace_id),
            },
            is_restricted=False,
        )

    async def _query_insight_version(self, insight_id: UUID) -> ProvenanceNode | None:
        """查询 Insight 版本节点（取最新版本）。"""
        async with self._factory() as session:
            version = await ResearchRepository.get_latest_insight_version(session, insight_id)
        if version is None:
            return None

        return ProvenanceNode(
            namespace="research:insight_version",
            node_id=insight_id,
            version=version.version_number,
            node_type="insight_version",
            display_label=None,
            attributes={
                "name": f"Insight 版本 v{version.version_number}",
                "version_number": version.version_number,
                "conclusion": version.conclusion[:100] if version.conclusion else "",
                "evidence_source_label": version.evidence_source_label,
            },
            is_restricted=False,
        )

    async def _query_result_version(self, result_id: UUID) -> ProvenanceNode | None:
        """查询成果版本节点（取最新版本）。"""
        async with self._factory() as session:
            version = await ResearchRepository.get_latest_result_version(session, result_id)
        if version is None:
            return None

        return ProvenanceNode(
            namespace="research:result_version",
            node_id=result_id,
            version=version.version_number,
            node_type="result_version",
            display_label=None,
            attributes={
                "name": version.title,
                "version_number": version.version_number,
                "title": version.title,
                "status": version.status,
            },
            is_restricted=False,
        )

    async def _query_workspace(self, workspace_id: UUID) -> ProvenanceNode | None:
        """查询工作空间节点。"""
        async with self._factory() as session:
            result = await session.execute(
                sa.select(ResearchWorkspace).where(ResearchWorkspace.id == workspace_id)
            )
            ws = result.scalar_one_or_none()

        if ws is None:
            return None

        return ProvenanceNode(
            namespace="research:workspace",
            node_id=workspace_id,
            version=None,
            node_type="workspace",
            display_label=None,
            attributes={
                "name": ws.name,
                "status": ws.status,
            },
            is_restricted=False,
        )

    async def _query_knowledge_reference(self, reference_id: UUID) -> ProvenanceNode | None:
        """查询知识引用快照节点。"""
        async with self._factory() as session:
            result = await session.execute(
                sa.select(ResearchKnowledgeReference).where(
                    ResearchKnowledgeReference.id == reference_id
                )
            )
            ref = result.scalar_one_or_none()

        if ref is None:
            return None

        return ProvenanceNode(
            namespace="research:knowledge_reference",
            node_id=reference_id,
            version=None,
            node_type="knowledge_reference",
            display_label=None,
            attributes={
                "name": ref.title,
                "title": ref.title,
                "document_id": ref.document_id,
                "document_version": ref.document_version,
                "provider_name": ref.provider_name,
                "source_uri": ref.source_uri,
            },
            is_restricted=False,
        )

    # ---- 权限校验方法 ----

    async def _check_evidence_snapshot_permission(
        self, snapshot_id: UUID, principal: object
    ) -> bool:
        """校验证据快照权限。"""
        try:
            user_id = getattr(principal, "user_id", None)
            if user_id is None:
                return False
            async with self._factory() as session:
                result = await session.execute(
                    sa.select(ResearchEvidenceSnapshot.workspace_id).where(
                        ResearchEvidenceSnapshot.id == snapshot_id
                    )
                )
                row = result.first()
                if row is None:
                    return False
                workspace_id = row[0]
                ws = await ResearchRepository.get_workspace(session, workspace_id, user_id)
                return ws is not None
        except Exception as exc:
            logger.warning("Snapshot permission check failed: %s", exc)
            return False

    async def _check_analysis_run_permission(self, run_id: UUID, principal: object) -> bool:
        """校验分析运行权限（通过 Workspace 归属）。"""
        try:
            user_id = getattr(principal, "user_id", None)
            if user_id is None:
                return False
            async with self._factory() as session:
                result = await session.execute(
                    sa.select(ResearchAnalysisRun.workspace_id).where(
                        ResearchAnalysisRun.id == run_id
                    )
                )
                row = result.first()
                if row is None:
                    # For analysis_step, check by step's run_id
                    result2 = await session.execute(
                        sa.select(ResearchAnalysisStep.run_id).where(
                            ResearchAnalysisStep.id == run_id
                        )
                    )
                    step_row = result2.first()
                    if step_row is None:
                        return False
                    run_row = await session.execute(
                        sa.select(ResearchAnalysisRun.workspace_id).where(
                            ResearchAnalysisRun.id == step_row[0]
                        )
                    )
                    run_result = run_row.first()
                    if run_result is None:
                        return False
                    workspace_id = run_result[0]
                else:
                    workspace_id = row[0]
                ws = await ResearchRepository.get_workspace(session, workspace_id, user_id)
                return ws is not None
        except Exception as exc:
            logger.warning("Analysis run permission check failed: %s", exc)
            return False

    async def _check_product_permission(
        self, namespace: str, node_id: UUID, principal: object
    ) -> bool:
        """校验产物权限（通过 Workspace 归属或成果包 ACL）。"""
        try:
            user_id = getattr(principal, "user_id", None)
            if user_id is None:
                return False
            async with self._factory() as session:
                workspace_id = None
                if "dataset" in namespace:
                    result = await session.execute(
                        sa.select(ResearchDerivedDataset.workspace_id).where(
                            ResearchDerivedDataset.id == node_id
                        )
                    )
                    row = result.first()
                    if row is not None:
                        workspace_id = row[0]
                elif "view" in namespace:
                    result = await session.execute(
                        sa.select(ResearchView.workspace_id).where(ResearchView.id == node_id)
                    )
                    row = result.first()
                    if row is not None:
                        workspace_id = row[0]
                elif "insight" in namespace:
                    result = await session.execute(
                        sa.select(ResearchInsight.workspace_id).where(ResearchInsight.id == node_id)
                    )
                    row = result.first()
                    if row is not None:
                        workspace_id = row[0]

                if workspace_id is not None:
                    ws = await ResearchRepository.get_workspace(session, workspace_id, user_id)
                    return ws is not None
                return False
        except Exception as exc:
            logger.warning("Product permission check failed: %s", exc)
            return False

    async def _check_result_version_permission(self, result_id: UUID, principal: object) -> bool:
        """校验成果版本权限（成果包已发布即可见）。"""
        try:
            async with self._factory() as session:
                result = await ResearchRepository.get_result(session, result_id)
                if result is None:
                    return False
                return result.status == "published"
        except Exception as exc:
            logger.warning("Result version permission check failed: %s", exc)
            return False

    async def _check_workspace_permission(self, workspace_id: UUID, principal: object) -> bool:
        """校验工作空间权限（归属校验）。"""
        try:
            user_id = getattr(principal, "user_id", None)
            if user_id is None:
                return False
            async with self._factory() as session:
                ws = await ResearchRepository.get_workspace(session, workspace_id, user_id)
                return ws is not None
        except Exception as exc:
            logger.warning("Workspace permission check failed: %s", exc)
            return False

    async def _check_knowledge_reference_permission(
        self, reference_id: UUID, principal: object
    ) -> bool:
        """校验知识引用权限（通过关联 Insight 或 Workspace 归属）。"""
        try:
            user_id = getattr(principal, "user_id", None)
            if user_id is None:
                return False
            async with self._factory() as session:
                ref = await ResearchRepository.get_knowledge_reference(session, reference_id)
                if ref is None:
                    return False
                # 通过 workspace_id 校验归属
                ws = await ResearchRepository.get_workspace(session, ref.workspace_id, user_id)
                return ws is not None
        except Exception as exc:
            logger.warning("Knowledge reference permission check failed: %s", exc)
            return False
