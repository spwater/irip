"""联邦式统一溯源查询服务（阶段 5 新增）。

UnifiedProvenanceQueryService 协调 CoreProvenanceAdapter 和 ResearchLineageAdapter，
跨边界拼接为完整溯源图。

查询流程（PRD 6.5 节）：
1. 确定起始节点：根据 target_namespace 路由到对应 Adapter
2. BFS 从 target 向上游追溯（循环保护 + 深度限制）
3. 权限裁剪（图拼接后统一执行）
4. 生成展示标签
5. 统计信息
6. 返回 ProvenanceGraph

参照架构设计 3.3 节 UnifiedProvenanceQueryService。
"""

import logging
from collections import deque
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.audit.events import AuditEventData
from packages.audit.repository import AuditRecorder
from packages.common.database import ScopedSessionMixin
from packages.research.labels import NodeDisplayLabelGenerator
from packages.research.models import (
    ProvenanceEdge,
    ProvenanceGraph,
    ProvenanceGraphStats,
    ProvenanceNode,
    ProvenanceQueryOptions,
    RestrictedNode,
)

logger = logging.getLogger("research.provenance")

#: 默认最大追溯深度。
DEFAULT_MAX_DEPTH: int = 20


class UnifiedProvenanceQueryService(ScopedSessionMixin):
    """联邦式统一溯源查询服务。

    协调 CoreProvenanceAdapter 和 ResearchLineageAdapter，
    跨边界拼接为完整溯源图。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _core_adapter: 核心 Provenance 适配器。
        _research_adapter: 研究 Lineage 适配器。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        core_adapter: object,
        research_adapter: object,
    ) -> None:
        """初始化统一溯源查询服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            core_adapter: CoreProvenanceAdapter 实例。
            research_adapter: ResearchLineageAdapter 实例。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._core_adapter = core_adapter
        self._research_adapter = research_adapter
        self._rls_dept_id: UUID | None = None

    async def query_provenance_graph(
        self,
        target_namespace: str,
        target_id: UUID,
        options: ProvenanceQueryOptions | None = None,
    ) -> ProvenanceGraph:
        """查询联邦溯源图。

        1. 初始化 options（max_depth 默认 20, truncate_branch 默认 False）
        2. BFS 遍历：从 target 向上游追溯（循环保护 + 深度限制）
        3. 权限裁剪：无权节点替换为 RestrictedNode
        4. 生成展示标签（NodeDisplayLabelGenerator）
        5. 统计信息
        6. 审计
        7. 返回 ProvenanceGraph

        Args:
            target_namespace: 起始节点命名空间。
            target_id: 起始节点 UUID。
            options: 查询选项（可空，使用默认值）。

        Returns:
            ProvenanceGraph: 溯源图。
        """
        opts = options or ProvenanceQueryOptions()
        max_depth = opts.max_depth if opts.max_depth > 0 else DEFAULT_MAX_DEPTH

        # 1. BFS 遍历
        nodes, edges, truncated_count = await self._bfs_traverse(
            target_namespace, target_id, max_depth
        )

        # 2. 权限裁剪
        nodes, edges = await self._prune_permissions(nodes, edges, opts.truncate_branch)

        # 3. 生成展示标签
        labeled_nodes = self._generate_display_labels(nodes)

        # 4. 统计信息
        stats = self._compute_stats(labeled_nodes, edges, truncated_count)

        # 5. 审计
        actor_id = self._actor_id or UUID(int=0)
        try:
            async with self._scoped_session() as session:
                await AuditRecorder.record(
                    session,
                    AuditEventData(
                        department_id=self._dept_id,
                        action="research.provenance.query",
                        actor_user_id=actor_id,
                        resource_type="research_provenance_graph",
                        resource_id=target_id,
                        payload={
                            "target_namespace": target_namespace,
                            "max_depth": max_depth,
                            "node_count": stats.total_nodes,
                            "restricted_count": stats.restricted_nodes_count,
                            "truncated_count": stats.truncated_count,
                        },
                    ),
                )
        except Exception as exc:
            logger.warning("Provenance audit record failed: %s", exc)

        return ProvenanceGraph(nodes=labeled_nodes, edges=edges, stats=stats)

    async def query_node_detail(self, namespace: str, node_id: UUID) -> ProvenanceNode | None:
        """查询单个溯源节点详情（校验权限）。

        Args:
            namespace: 命名空间。
            node_id: 节点 UUID。

        Returns:
            ProvenanceNode | None: 节点详情，不存在或无权限时返回 None。
        """
        adapter = self._route_adapter(namespace)
        node = await adapter.query_node(namespace, node_id)  # type: ignore[attr-defined]
        if node is None:
            return None

        # 权限校验
        principal = _SimplePrincipal(self._actor_id, self._dept_id)
        has_perm = await adapter.check_permission(namespace, node_id, principal)  # type: ignore[attr-defined]
        if not has_perm:
            return None

        # 生成展示标签
        node_data = dict(node.attributes)
        node_data["node_id"] = node_id
        node_data["version"] = node.version
        label = NodeDisplayLabelGenerator.generate(namespace, node_data)

        return ProvenanceNode(
            namespace=node.namespace,
            node_id=node.node_id,
            version=node.version,
            node_type=node.node_type,
            display_label=label,
            attributes=node.attributes,
            is_restricted=False,
        )

    def _route_adapter(self, namespace: str) -> object:
        """根据命名空间路由到对应 Adapter。

        Args:
            namespace: 命名空间。

        Returns:
            对应的 Adapter 实例（core:* → CoreAdapter, research:* → ResearchAdapter）。
        """
        if namespace.startswith("core:"):
            return self._core_adapter
        return self._research_adapter

    async def _bfs_traverse(
        self,
        target_namespace: str,
        target_id: UUID,
        max_depth: int,
    ) -> tuple[list[ProvenanceNode], list[ProvenanceEdge], int]:
        """BFS 遍历，返回 (nodes, edges, truncated_count)。

        队列初始化 → 已访问集合（循环保护）→ 根据 namespace 路由到对应 Adapter →
        Adapter.query_node + query_incoming_edges → 入边 source 节点入队 →
        跨边界边由 ResearchLineageAdapter 返回（source_namespace 为 core:* 时
        自动路由到 CoreProvenanceAdapter 继续追溯）。

        Args:
            target_namespace: 起始节点命名空间。
            target_id: 起始节点 UUID。
            max_depth: 最大追溯深度。

        Returns:
            tuple: (节点列表, 边列表, 被截断的分支数)。
        """
        nodes: list[ProvenanceNode] = []
        edges: list[ProvenanceEdge] = []
        node_keys: set[tuple[str, UUID]] = set()
        truncated_count: int = 0

        # 队列: (namespace, node_id, depth)
        queue: deque[tuple[str, UUID, int]] = deque()
        queue.append((target_namespace, target_id, 0))

        while queue:
            ns, nid, depth = queue.popleft()

            # 循环保护：已访问节点跳过
            key = (ns, nid)
            if key in node_keys:
                continue
            node_keys.add(key)

            # 路由到对应 Adapter
            adapter = self._route_adapter(ns)

            # 查询节点
            node = await adapter.query_node(ns, nid)  # type: ignore[attr-defined]
            if node is None:
                continue
            nodes.append(node)

            # 查询入边
            incoming = await adapter.query_incoming_edges(ns, nid)  # type: ignore[attr-defined]
            edges.extend(incoming)

            # 对每条入边的 source 节点入队
            for edge in incoming:
                source_ns = edge.source_namespace
                source_id = edge.source_id
                source_key = (source_ns, source_id)

                if source_key in node_keys:
                    continue

                if depth + 1 > max_depth:
                    truncated_count += 1
                    continue

                queue.append((source_ns, source_id, depth + 1))

        return nodes, edges, truncated_count

    async def _prune_permissions(
        self,
        nodes: list[ProvenanceNode],
        edges: list[ProvenanceEdge],
        truncate_branch: bool,
    ) -> tuple[list[ProvenanceNode], list[ProvenanceEdge]]:
        """权限裁剪，返回裁剪后的 (nodes, edges)。

        对每个节点校验 adapter.check_permission，
        无权节点替换为 RestrictedNode（不含名称/ID/属性/内容）。
        涉及被替换节点的边 target 端更新为受限临时 ID。
        truncate_branch=True 时递归移除被截断节点的全部上游分支。

        Args:
            nodes: 节点列表。
            edges: 边列表。
            truncate_branch: 是否截断无权节点的整个上游分支。

        Returns:
            tuple: (裁剪后的节点列表, 裁剪后的边列表)。
        """
        principal = _SimplePrincipal(self._actor_id, self._dept_id)

        # 检查每个节点的权限
        restricted_keys: set[tuple[str, UUID]] = set()
        restricted_id_map: dict[tuple[str, UUID], str] = {}

        for i, node in enumerate(nodes):
            adapter = self._route_adapter(node.namespace)
            try:
                has_perm = await adapter.check_permission(node.namespace, node.node_id, principal)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning(
                    "Permission check failed for %s:%s: %s",
                    node.namespace,
                    node.node_id,
                    exc,
                )
                has_perm = False

            if not has_perm:
                key = (node.namespace, node.node_id)
                restricted_keys.add(key)
                temp_id = f"restricted_{i}"
                restricted_id_map[key] = temp_id

        if not restricted_keys:
            return nodes, edges

        # 构建被截断节点的集合（truncate_branch 模式下递归移除上游）
        if truncate_branch:
            # 构建邻接表：target → sources
            target_to_sources: dict[tuple[str, UUID], list[tuple[str, UUID]]] = {}
            for edge in edges:
                target_key = (edge.target_namespace, edge.target_id)
                source_key = (edge.source_namespace, edge.source_id)
                target_to_sources.setdefault(target_key, []).append(source_key)

            # BFS 递归标记被截断节点的全部上游
            to_remove: set[tuple[str, UUID]] = set(restricted_keys)
            remove_queue: deque[tuple[str, UUID]] = deque(restricted_keys)
            while remove_queue:
                current = remove_queue.popleft()
                for src in target_to_sources.get(current, []):
                    if src not in to_remove:
                        to_remove.add(src)
                        remove_queue.append(src)
        else:
            to_remove = restricted_keys

        # 替换节点
        new_nodes: list[ProvenanceNode] = []
        for i, node in enumerate(nodes):
            key = (node.namespace, node.node_id)
            if key in to_remove:
                temp_id = restricted_id_map.get(key, f"restricted_{i}")
                self._create_restricted_node(i)
                # 用受限节点替换（保持列表位置），每个受限节点用唯一 UUID 避免图渲染冲突
                import uuid as _uuid

                restricted_uuid = _uuid.uuid5(_uuid.NAMESPACE_OID, temp_id)
                new_nodes.append(
                    ProvenanceNode(
                        namespace="restricted",
                        node_id=restricted_uuid,
                        version=None,
                        node_type="restricted",
                        display_label=NodeDisplayLabelGenerator.restricted_label(),
                        attributes={"temp_id": temp_id},
                        is_restricted=True,
                    )
                )
            else:
                new_nodes.append(node)

        # 过滤边：移除涉及被截断节点的边
        new_edges: list[ProvenanceEdge] = []
        for edge in edges:
            source_key = (edge.source_namespace, edge.source_id)
            target_key = (edge.target_namespace, edge.target_id)
            if source_key in to_remove or target_key in to_remove:
                continue
            new_edges.append(edge)

        return new_nodes, new_edges

    def _create_restricted_node(self, index: int) -> RestrictedNode:
        """生成受限占位节点（临时 ID: restricted_{index}）。

        每次查询重新生成，不可枚举。

        Args:
            index: 索引值。

        Returns:
            RestrictedNode: 受限占位节点。
        """
        return RestrictedNode(
            node_type="restricted",
            display_label="受限来源",
            attributes={},
            temp_id=f"restricted_{index}",
        )

    def _generate_display_labels(self, nodes: list[ProvenanceNode]) -> list[ProvenanceNode]:
        """为每个可见节点生成展示标签。

        Args:
            nodes: 节点列表。

        Returns:
            list[ProvenanceNode]: 带展示标签的节点列表。
        """
        labeled: list[ProvenanceNode] = []
        for node in nodes:
            if node.is_restricted:
                labeled.append(node)
                continue

            node_data = dict(node.attributes)
            node_data["node_id"] = node.node_id
            node_data["version"] = node.version
            label = NodeDisplayLabelGenerator.generate(node.namespace, node_data)

            labeled.append(
                ProvenanceNode(
                    namespace=node.namespace,
                    node_id=node.node_id,
                    version=node.version,
                    node_type=node.node_type,
                    display_label=label,
                    attributes=node.attributes,
                    is_restricted=False,
                )
            )
        return labeled

    def _compute_stats(
        self,
        nodes: list[ProvenanceNode],
        edges: list[ProvenanceEdge],
        truncated_count: int,
    ) -> ProvenanceGraphStats:
        """计算统计信息。

        Args:
            nodes: 节点列表。
            edges: 边列表。
            truncated_count: 被截断的分支数。

        Returns:
            ProvenanceGraphStats: 统计信息。
        """
        nodes_by_type: dict[str, int] = {}
        restricted_count: int = 0

        for node in nodes:
            node_type = node.node_type
            nodes_by_type[node_type] = nodes_by_type.get(node_type, 0) + 1
            if node.is_restricted:
                restricted_count += 1

        return ProvenanceGraphStats(
            total_nodes=len(nodes),
            nodes_by_type=nodes_by_type,
            restricted_nodes_count=restricted_count,
            truncated_count=truncated_count,
        )


class _SimplePrincipal:
    """简易身份上下文（用于权限校验）。

    Attributes:
        user_id: 用户 ID。
        department_id: 部门 ID。
    """

    def __init__(self, user_id: UUID | None, department_id: UUID) -> None:
        """初始化身份上下文。

        Args:
            user_id: 用户 ID。
            department_id: 部门 ID。
        """
        self.user_id = user_id
        self.department_id = department_id
