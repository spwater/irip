"""溯源边子仓库。

封装 ResearchLineageEdge（仅追加、不可变）的数据库操作：
插入、按源节点查询、按目标节点查询。

所有方法均为 @staticmethod async，接受 AsyncSession 参数，不自行管理事务。
事务由 Service 层通过 ScopedSessionMixin 管理。
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.ids import new_id
from packages.research.entities import ResearchLineageEdge


class LineageEdgeRepository:
    """溯源边数据访问层。

    所有方法接受 AsyncSession 参数，不自行管理事务。
    事务由 Service 层通过 ScopedSessionMixin 管理。
    """

    @staticmethod
    async def insert_lineage_edge(
        session: AsyncSession,
        *,
        source_namespace: str,
        source_id: UUID,
        target_namespace: str,
        target_id: UUID,
        edge_type: str,
        source_version: int | None = None,
        target_version: int | None = None,
    ) -> ResearchLineageEdge:
        """插入溯源边（仅追加，不可变）。

        Args:
            session: 异步会话。
            source_namespace: 源命名空间。
            source_id: 源对象 UUID。
            target_namespace: 目标命名空间。
            target_id: 目标对象 UUID。
            edge_type: 边类型。
            source_version: 源版本号（可选）。
            target_version: 目标版本号（可选）。

        Returns:
            ResearchLineageEdge: 溯源边 ORM 实体。
        """
        edge = ResearchLineageEdge(
            id=new_id(),
            source_namespace=source_namespace,
            source_id=source_id,
            source_version=source_version,
            target_namespace=target_namespace,
            target_id=target_id,
            target_version=target_version,
            edge_type=edge_type,
        )
        session.add(edge)
        await session.flush()
        return edge

    @staticmethod
    async def list_edges_by_source(
        session: AsyncSession,
        source_namespace: str,
        source_id: UUID,
    ) -> list[ResearchLineageEdge]:
        """按源节点查询溯源边。

        Args:
            session: 异步会话。
            source_namespace: 源命名空间。
            source_id: 源对象 UUID。

        Returns:
            list[ResearchLineageEdge]: 溯源边列表。
        """
        res = await session.execute(
            sa.select(ResearchLineageEdge).where(
                ResearchLineageEdge.source_namespace == source_namespace,
                ResearchLineageEdge.source_id == source_id,
            )
        )
        return list(res.scalars().all())

    @staticmethod
    async def list_edges_by_target(
        session: AsyncSession,
        target_namespace: str,
        target_id: UUID,
    ) -> list[ResearchLineageEdge]:
        """按目标节点查询溯源边。

        Args:
            session: 异步会话。
            target_namespace: 目标命名空间。
            target_id: 目标对象 UUID。

        Returns:
            list[ResearchLineageEdge]: 溯源边列表。
        """
        res = await session.execute(
            sa.select(ResearchLineageEdge).where(
                ResearchLineageEdge.target_namespace == target_namespace,
                ResearchLineageEdge.target_id == target_id,
            )
        )
        return list(res.scalars().all())
