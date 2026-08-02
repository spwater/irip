"""溯源图服务。

ProvenanceGraphService 提供溯源图的构建、遍历与边管理。

溯源图将推导运行连接回原始事实，支持从推导结果向上追溯到
原始数据，也支持向下遍历到参数版本。

核心功能：
1. get_graph: 从推导运行出发，构建完整溯源图（节点 + 边）。
2. get_paths_to_raw: 从参数版本追溯到原始事实的路径。
3. add_edge: 添加溯源边。

节点类型：fact, intermediate_artifact,
derivation_run, parameter_version。
边类型：selected_from, transformed_by, produced, published_as。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import session_scope
from packages.common.dept_visibility import compute_visible_dept_ids
from packages.common.ids import new_id
from packages.facts.entities import Fact
from packages.provenance.entities import (
    DerivationRun,
    ProvenanceEdge,
)


@dataclass(frozen=True)
class ProvenanceNode:
    """溯源图节点（不可变值对象）。

    Attributes:
        id: 节点 UUID。
        node_type: 节点类型。
        label: 节点标签（用于展示）。
        version: 版本标识。
        status: 状态。
    """

    id: UUID
    node_type: Literal[
        "fact",
        "intermediate_artifact",
        "derivation_run",
        "parameter_version",
    ]
    label: str
    version: str
    status: str


@dataclass(frozen=True)
class ProvenanceEdgeRef:
    """溯源图边（不可变值对象）。

    Attributes:
        source_id: 源节点 UUID。
        source_type: 源节点类型。
        target_id: 目标节点 UUID。
        target_type: 目标节点类型。
        edge_type: 边类型。
    """

    source_id: UUID
    source_type: str
    target_id: UUID
    target_type: str
    edge_type: Literal["selected_from", "transformed_by", "produced", "published_as"]


@dataclass(frozen=True)
class ProvenanceGraph:
    """完整溯源图（不可变值对象）。

    Attributes:
        nodes: 节点元组。
        edges: 边元组。
    """

    nodes: tuple[ProvenanceNode, ...]
    edges: tuple[ProvenanceEdgeRef, ...]


class ProvenanceGraphService:
    """溯源图业务编排服务。

    依赖注入 session_factory（事务管理）、department_id（当前部门）。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
    ) -> None:
        """初始化溯源图服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
        """
        self._factory = session_factory
        self._dept_id = department_id

    async def get_graph(self, derivation_run_id: UUID) -> ProvenanceGraph:
        """获取推导运行的完整溯源图。

        从 derivation_run 出发，沿 provenance_edge 向上遍历到 facts，
        向下遍历到 parameter_versions。

        Args:
            derivation_run_id: 推导运行 UUID。

        Returns:
            ProvenanceGraph: 完整溯源图。

        Raises:
            AppError: code="not_found"，当推导运行不存在时。
        """
        from packages.common.errors import AppError

        nodes: dict[UUID, ProvenanceNode] = {}
        edges: list[ProvenanceEdgeRef] = []
        visited_edges: set[tuple[UUID, UUID, str]] = set()

        async with self._factory() as session:
            visible_ids = await compute_visible_dept_ids(session, self._dept_id)
            # 加载推导运行节点
            run = await session.scalar(
                sa.select(DerivationRun).where(
                    DerivationRun.id == derivation_run_id,
                    DerivationRun.department_id.in_(visible_ids),
                )
            )
            if run is None:
                raise AppError(
                    code="not_found",
                    message=f"推导运行不存在: {derivation_run_id}",
                    retryable=False,
                    fields={"run_id": str(derivation_run_id)},
                )

            # 添加推导运行节点
            run_node = ProvenanceNode(
                id=run.id,
                node_type="derivation_run",
                label="DerivationRun",
                version=str(run.id),
                status=run.status,
            )
            nodes[run.id] = run_node

            # 加载所有相关的溯源边
            edge_result = await session.execute(
                sa.select(ProvenanceEdge).where(
                    ProvenanceEdge.derivation_run_id == derivation_run_id,
                    ProvenanceEdge.department_id.in_(visible_ids),
                )
            )
            provenance_edges = edge_result.scalars().all()

            # 收集所有 fact IDs
            fact_ids: list[UUID] = []
            for pe in provenance_edges:
                edge_key = (pe.source_id, pe.target_id, pe.edge_type)
                if edge_key not in visited_edges:
                    visited_edges.add(edge_key)
                    edges.append(
                        ProvenanceEdgeRef(
                            source_id=pe.source_id,
                            source_type=pe.source_type,
                            target_id=pe.target_id,
                            target_type=pe.target_type,
                            edge_type=pe.edge_type,  # type: ignore[arg-type]
                        )
                    )

                # 收集 fact 目标
                if pe.target_type == "fact":
                    fact_ids.append(pe.target_id)

            # 加载 fact 节点
            if fact_ids:
                f_result = await session.execute(
                    sa.select(Fact).where(Fact.id.in_(fact_ids))
                )
                facts = f_result.scalars().all()

                for f in facts:
                    f_node = ProvenanceNode(
                        id=f.id,
                        node_type="fact",
                        label=f.subject_id,
                        version="",
                        status=f.status,
                    )
                    nodes[f.id] = f_node

            # 加载 parameter_version 节点（如果有的话）
            for pe in provenance_edges:
                if pe.target_type == "parameter_version":
                    if pe.target_id not in nodes:
                        param_node = ProvenanceNode(
                            id=pe.target_id,
                            node_type="parameter_version",
                            label="ParameterVersion",
                            version="",
                            status="published",
                        )
                        nodes[pe.target_id] = param_node

        return ProvenanceGraph(
            nodes=tuple(nodes.values()),
            edges=tuple(edges),
        )

    async def get_paths_to_raw(self, parameter_version_id: UUID) -> list[list[ProvenanceNode]]:
        """从参数版本追溯到原始事实的路径。

        沿溯源边从参数版本向上遍历，直到到达事实节点。

        Args:
            parameter_version_id: 参数版本 UUID。

        Returns:
            list[list[ProvenanceNode]]: 路径列表，每条路径从参数版本到事实。
        """
        async with self._factory() as session:
            visible_ids = await compute_visible_dept_ids(session, self._dept_id)
            # BFS: 从 parameter_version 出发，沿边向上遍历
            edges_result = await session.execute(
                sa.select(ProvenanceEdge).where(
                    ProvenanceEdge.target_id == parameter_version_id,
                    ProvenanceEdge.target_type == "parameter_version",
                    ProvenanceEdge.department_id.in_(visible_ids),
                )
            )
            start_edges = edges_result.scalars().all()

            if not start_edges:
                return []

            paths: list[list[ProvenanceNode]] = []
            queue: deque[tuple[UUID, str, list[ProvenanceNode]]] = deque()

            # 初始化：从 parameter_version 节点开始
            param_node = ProvenanceNode(
                id=parameter_version_id,
                node_type="parameter_version",
                label="ParameterVersion",
                version="",
                status="published",
            )
            for edge in start_edges:
                queue.append((edge.source_id, edge.source_type, [param_node]))

            visited: set[UUID] = {parameter_version_id}

            while queue:
                current_id, current_type, path = queue.popleft()

                if current_id in visited and current_type != "fact":
                    continue
                visited.add(current_id)

                # 加载当前节点信息
                current_node: ProvenanceNode | None = None
                if current_type == "fact":
                    f = await session.scalar(
                        sa.select(Fact).where(Fact.id == current_id)
                    )
                    if f is not None:
                        current_node = ProvenanceNode(
                            id=f.id,
                            node_type="fact",
                            label=f.subject_id,
                            version="",
                            status=f.status,
                        )
                        paths.append(path + [current_node])
                        continue
                elif current_type == "derivation_run":
                    run = await session.scalar(
                        sa.select(DerivationRun).where(DerivationRun.id == current_id)
                    )
                    if run is not None:
                        current_node = ProvenanceNode(
                            id=run.id,
                            node_type="derivation_run",
                            label="DerivationRun",
                            version=str(run.id),
                            status=run.status,
                        )

                if current_node is None:
                    continue

                # 继续向上查找边
                up_edges_result = await session.execute(
                    sa.select(ProvenanceEdge).where(
                        ProvenanceEdge.target_id == current_id,
                        ProvenanceEdge.department_id.in_(visible_ids),
                    )
                )
                up_edges = up_edges_result.scalars().all()
                for ue in up_edges:
                    if ue.source_id not in visited:
                        queue.append((ue.source_id, ue.source_type, path + [current_node]))

            return paths

    async def add_edge(
        self,
        derivation_run_id: UUID,
        source_type: str,
        source_id: UUID,
        target_type: str,
        target_id: UUID,
        edge_type: str,
        metadata: dict | None = None,
    ) -> None:
        """添加溯源边。

        Args:
            derivation_run_id: 推导运行 UUID。
            source_type: 源节点类型。
            source_id: 源节点 UUID。
            target_type: 目标节点类型。
            target_id: 目标节点 UUID。
            edge_type: 边类型。
            metadata: 元数据（可选）。
        """
        async with session_scope(self._factory) as session:
            edge = ProvenanceEdge(
                id=new_id(),
                department_id=self._dept_id,
                derivation_run_id=derivation_run_id,
                source_type=source_type,
                source_id=source_id,
                target_type=target_type,
                target_id=target_id,
                edge_type=edge_type,
                metadata_=metadata,
            )
            session.add(edge)
            await session.flush()
