"""溯源边写入服务（阶段 5 新增）。

LineageWriterService 通过事件驱动 Hook 在关键事件中创建溯源边（仅追加）。
Hook 为可选调用，失败时记录告警日志不阻断主流程。

事件 Hook：
- on_snapshot_frozen: 证据快照冻结时创建 fact→snapshot 跨边界边
- on_run_started: Analysis Run 启动时创建 snapshot→run 边
- on_step_completed: Analysis Step 完成时创建 run→step 边
- on_product_confirmed: 产物确认时创建 run→product 边
- on_knowledge_referenced: 知识引用保存时创建 knowledge_ref→insight 边

参照架构设计 3.3 节 LineageWriterService。
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.research.dtos import LineageEdgeRef
from packages.research.repository import ResearchRepository

logger = logging.getLogger("research.lineage_writer")


class LineageWriterService:
    """溯源边写入服务。

    通过事件驱动 Hook 在关键事件中创建溯源边（仅追加）。
    Hook 为可选调用，失败时记录告警日志不阻断主流程。

    Attributes:
        _factory: 异步会话工厂。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """初始化溯源边写入服务。

        Args:
            session_factory: 异步会话工厂。
        """
        self._factory = session_factory

    async def on_snapshot_frozen(
        self,
        snapshot_id: UUID,
        source_refs: list[dict[str, Any]],
    ) -> None:
        """证据快照冻结时创建溯源边。

        对每个 source_ref 创建边：
        {source_namespace}:{source_id} → research:evidence_snapshot:{snapshot_id}

        edge_type:
        - fact_to_snapshot（source_namespace 为 core:fact）
        - published_derived_to_snapshot（source_namespace 为 research:published_derived）

        Args:
            snapshot_id: 快照 UUID。
            source_refs: 源引用列表（[{namespace, id, version}]）。
        """
        try:
            async with self._factory() as session:
                async with session.begin():
                    for ref in source_refs:
                        ns = ref.get("namespace", "")
                        ref_id_str = ref.get("id", "")
                        if not ns or not ref_id_str:
                            continue
                        try:
                            source_id = UUID(ref_id_str)
                        except (ValueError, TypeError):
                            continue

                        if ns == "core:fact":
                            edge_type = "fact_to_snapshot"
                        elif ns == "research:published_derived":
                            edge_type = "published_derived_to_snapshot"
                        elif ns == "research:derived":
                            edge_type = "published_derived_to_snapshot"
                        else:
                            edge_type = "fact_to_snapshot"

                        await ResearchRepository.insert_lineage_edge(
                            session,
                            source_namespace=ns,
                            source_id=source_id,
                            target_namespace="research:evidence_snapshot",
                            target_id=snapshot_id,
                            edge_type=edge_type,
                        )
        except Exception as exc:
            logger.warning("on_snapshot_frozen hook failed: %s", exc)

    async def on_run_started(
        self,
        run_id: UUID,
        snapshot_ids: list[UUID],
    ) -> None:
        """Analysis Run 启动时创建溯源边。

        对每个 snapshot_id 创建边：
        research:evidence_snapshot:{snapshot_id} → research:analysis_run:{run_id}
        edge_type: snapshot_to_run

        Args:
            run_id: Run UUID。
            snapshot_ids: 快照 ID 列表。
        """
        try:
            async with self._factory() as session:
                async with session.begin():
                    for snapshot_id in snapshot_ids:
                        await ResearchRepository.insert_lineage_edge(
                            session,
                            source_namespace="research:evidence_snapshot",
                            source_id=snapshot_id,
                            target_namespace="research:analysis_run",
                            target_id=run_id,
                            edge_type="snapshot_to_run",
                        )
        except Exception as exc:
            logger.warning("on_run_started hook failed: %s", exc)

    async def on_step_completed(
        self,
        run_id: UUID,
        step_id: UUID,
    ) -> None:
        """Analysis Step 完成时创建溯源边。

        research:analysis_run:{run_id} → research:analysis_step:{step_id}
        edge_type: run_to_step

        Args:
            run_id: Run UUID。
            step_id: 步骤 UUID。
        """
        try:
            async with self._factory() as session:
                async with session.begin():
                    await ResearchRepository.insert_lineage_edge(
                        session,
                        source_namespace="research:analysis_run",
                        source_id=run_id,
                        target_namespace="research:analysis_step",
                        target_id=step_id,
                        edge_type="run_to_step",
                    )
        except Exception as exc:
            logger.warning("on_step_completed hook failed: %s", exc)

    async def on_product_confirmed(
        self,
        run_id: UUID,
        product_namespace: str,
        product_id: UUID,
        product_type: str,
    ) -> None:
        """产物确认时创建溯源边。

        research:analysis_run:{run_id} → {product_namespace}:{product_id}
        edge_type: run_to_dataset / run_to_view / run_to_insight

        Args:
            run_id: Run UUID。
            product_namespace: 产物命名空间。
            product_id: 产物 UUID。
            product_type: 产物类型（dataset / view / insight）。
        """
        type_to_edge: dict[str, str] = {
            "dataset": "run_to_dataset",
            "view": "run_to_view",
            "insight": "run_to_insight",
        }
        edge_type = type_to_edge.get(product_type, f"run_to_{product_type}")

        try:
            async with self._factory() as session:
                async with session.begin():
                    await ResearchRepository.insert_lineage_edge(
                        session,
                        source_namespace="research:analysis_run",
                        source_id=run_id,
                        target_namespace=product_namespace,
                        target_id=product_id,
                        edge_type=edge_type,
                    )
        except Exception as exc:
            logger.warning("on_product_confirmed hook failed: %s", exc)

    async def on_knowledge_referenced(
        self,
        reference_id: UUID,
        insight_id: UUID | None,
    ) -> None:
        """知识引用保存时创建溯源边。

        research:knowledge_reference:{reference_id} → research:insight:{insight_id}
        edge_type: knowledge_ref_to_insight

        Args:
            reference_id: 知识引用快照 UUID。
            insight_id: Insight UUID（可空，为空时跳过边创建）。
        """
        if insight_id is None:
            return
        try:
            async with self._factory() as session:
                async with session.begin():
                    await ResearchRepository.insert_lineage_edge(
                        session,
                        source_namespace="research:knowledge_reference",
                        source_id=reference_id,
                        target_namespace="research:insight",
                        target_id=insight_id,
                        edge_type="knowledge_ref_to_insight",
                    )
        except Exception as exc:
            logger.warning("on_knowledge_referenced hook failed: %s", exc)

    async def record_edge(
        self,
        source_namespace: str,
        source_id: UUID,
        target_namespace: str,
        target_id: UUID,
        edge_type: str,
        source_version: int | None = None,
        target_version: int | None = None,
    ) -> None:
        """记录单条溯源边（仅追加）。

        Args:
            source_namespace: 源命名空间。
            source_id: 源对象 UUID。
            target_namespace: 目标命名空间。
            target_id: 目标对象 UUID。
            edge_type: 边类型。
            source_version: 源版本号（可选）。
            target_version: 目标版本号（可选）。
        """
        async with self._factory() as session:
            async with session.begin():
                await ResearchRepository.insert_lineage_edge(
                    session,
                    source_namespace=source_namespace,
                    source_id=source_id,
                    target_namespace=target_namespace,
                    target_id=target_id,
                    edge_type=edge_type,
                    source_version=source_version,
                    target_version=target_version,
                )

    async def list_edges_by_source(
        self,
        source_namespace: str,
        source_id: UUID,
    ) -> list[LineageEdgeRef]:
        """按源节点查询溯源边。

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
        """按目标节点查询溯源边。

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
