"""溯源边记录服务。

LineageEdgeService 封装溯源边记录逻辑，为阶段 5 ResearchLineageAdapter
提供数据源。溯源边仅追加（append-only），创建后不允许 UPDATE/DELETE。

参照架构设计 3.3 节。
"""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.research.dtos import LineageEdgeRef, ProductRefCollection
from packages.research.repository import ResearchRepository

logger = logging.getLogger("research.lineage")


class LineageEdgeService:
    """研究侧溯源边记录服务。

    为阶段 5 ResearchLineageAdapter 提供数据源。
    溯源边仅追加（append-only），创建后不允许 UPDATE/DELETE。

    Attributes:
        _factory: 异步会话工厂。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """初始化溯源边服务。

        Args:
            session_factory: 异步会话工厂。
        """
        self._factory = session_factory

    async def record_publication_edges(
        self,
        session: AsyncSession,
        result_id: UUID,
        version_number: int,
        workspace_id: UUID,
        product_refs: ProductRefCollection,
    ) -> None:
        """发布时创建溯源边记录。

        创建以下细粒度边：
        - workspace → result_version (edge_type: workspace_to_result)
        - dataset_version → result_version (edge_type: dataset_to_result)
        - view_version → result_version (edge_type: view_to_result)
        - insight_version → result_version (edge_type: insight_to_result)

        使用传入的 session（在 PublicationService 的事务内执行）。

        Args:
            session: 异步会话（复用 PublicationService 的事务会话）。
            result_id: 成果包 ID。
            version_number: 版本号。
            workspace_id: 来源工作空间 ID。
            product_refs: 产物引用集合。
        """
        # workspace → result_version
        await ResearchRepository.insert_lineage_edge(
            session,
            source_namespace="research:workspace",
            source_id=workspace_id,
            target_namespace="research:result_version",
            target_id=result_id,
            edge_type="workspace_to_result",
            workspace_id=workspace_id,
            target_version=version_number,
        )

        # dataset_version → result_version
        for ref in product_refs.dataset_version_refs:
            dataset_id = UUID(str(ref.get("dataset_id", ""))) if ref.get("dataset_id") else None
            dataset_version = ref.get("version_number")
            if dataset_id is not None:
                await ResearchRepository.insert_lineage_edge(
                    session,
                    source_namespace="research:dataset_version",
                    source_id=dataset_id,
                    target_namespace="research:result_version",
                    target_id=result_id,
                    edge_type="dataset_to_result",
                    workspace_id=workspace_id,
                    source_version=dataset_version,
                    target_version=version_number,
                )

        # view_version → result_version
        for ref in product_refs.view_version_refs:
            view_id = UUID(str(ref.get("view_id", ""))) if ref.get("view_id") else None
            view_version = ref.get("version_number")
            if view_id is not None:
                await ResearchRepository.insert_lineage_edge(
                    session,
                    source_namespace="research:view_version",
                    source_id=view_id,
                    target_namespace="research:result_version",
                    target_id=result_id,
                    edge_type="view_to_result",
                    workspace_id=workspace_id,
                    source_version=view_version,
                    target_version=version_number,
                )

        # insight_version → result_version
        for ref in product_refs.insight_version_refs:
            insight_id = UUID(str(ref.get("insight_id", ""))) if ref.get("insight_id") else None
            insight_version = ref.get("version_number")
            if insight_id is not None:
                await ResearchRepository.insert_lineage_edge(
                    session,
                    source_namespace="research:insight_version",
                    source_id=insight_id,
                    target_namespace="research:result_version",
                    target_id=result_id,
                    edge_type="insight_to_result",
                    workspace_id=workspace_id,
                    source_version=insight_version,
                    target_version=version_number,
                )

    async def record_edge(
        self,
        session: AsyncSession,
        source_namespace: str,
        source_id: UUID,
        target_namespace: str,
        target_id: UUID,
        edge_type: str,
        workspace_id: UUID,
        source_version: int | None = None,
        target_version: int | None = None,
    ) -> None:
        """记录单条溯源边。

        使用传入的 session（在调用方的事务内执行）。

        Args:
            session: 异步会话。
            source_namespace: 源命名空间。
            source_id: 源对象 UUID。
            target_namespace: 目标命名空间。
            target_id: 目标对象 UUID。
            edge_type: 边类型。
            workspace_id: 所属工作空间 ID（NOT NULL，用于 RLS 所有权隔离）。
            source_version: 源版本号（可选）。
            target_version: 目标版本号（可选）。
        """
        await ResearchRepository.insert_lineage_edge(
            session,
            source_namespace=source_namespace,
            source_id=source_id,
            target_namespace=target_namespace,
            target_id=target_id,
            edge_type=edge_type,
            workspace_id=workspace_id,
            source_version=source_version,
            target_version=target_version,
        )

    async def list_edges_by_source(
        self,
        source_namespace: str,
        source_id: UUID,
    ) -> list[LineageEdgeRef]:
        """按源节点查询溯源边（阶段 5 使用）。

        Args:
            source_namespace: 源命名空间。
            source_id: 源对象 UUID。

        Returns:
            list[LineageEdgeRef]: 溯源边引用列表。
        """
        async with self._factory() as session:
            edges = await ResearchRepository.list_edges_by_source(
                session, source_namespace, source_id
            )
            return [
                LineageEdgeRef(
                    source_namespace=e.source_namespace,
                    source_id=e.source_id,
                    source_version=e.source_version,
                    target_namespace=e.target_namespace,
                    target_id=e.target_id,
                    target_version=e.target_version,
                    edge_type=e.edge_type,
                )
                for e in edges
            ]

    async def list_edges_by_target(
        self,
        target_namespace: str,
        target_id: UUID,
    ) -> list[LineageEdgeRef]:
        """按目标节点查询溯源边（阶段 5 使用）。

        Args:
            target_namespace: 目标命名空间。
            target_id: 目标对象 UUID。

        Returns:
            list[LineageEdgeRef]: 溯源边引用列表。
        """
        async with self._factory() as session:
            edges = await ResearchRepository.list_edges_by_target(
                session, target_namespace, target_id
            )
            return [
                LineageEdgeRef(
                    source_namespace=e.source_namespace,
                    source_id=e.source_id,
                    source_version=e.source_version,
                    target_namespace=e.target_namespace,
                    target_id=e.target_id,
                    target_version=e.target_version,
                    edge_type=e.edge_type,
                )
                for e in edges
            ]
