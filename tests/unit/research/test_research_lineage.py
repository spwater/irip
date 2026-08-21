"""阶段 5：统一溯源与知识接口 — 核心测试。

覆盖：
- UnifiedProvenanceQueryService: 联邦溯源图拼接 / BFS / 循环保护 / 深度限制 / 受限节点
- KnowledgeReferenceService: 知识引用快照 / 截断
- LineageWriterService: 事件驱动 Hook / 失败不阻断
- NodeDisplayLabelGenerator: 节点展示标签

使用 Mock 适配器模拟数据库查询，不依赖真实数据库。

参照架构设计 arch-research-lineage.md 3.3 节。
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from packages.research.knowledge_reference import (
    SNIPPET_INLINE_THRESHOLD,
    SNIPPET_MAX_SIZE,
    TRUNCATION_SUFFIX,
    KnowledgeReferenceService,
)
from packages.research.labels import NodeDisplayLabelGenerator
from packages.research.lineage_writer import LineageWriterService
from packages.research.provenance import (
    DEFAULT_MAX_DEPTH,
    UnifiedProvenanceQueryService,
)

from packages.research.dtos import (
    ProvenanceEdge,
    ProvenanceNode,
    ProvenanceQueryOptions,
)

# ============================================================
# Helpers
# ============================================================


class MockAdapter:
    """模拟溯源适配器（CoreProvenanceAdapter / ResearchLineageAdapter）。"""

    def __init__(
        self,
        nodes: dict[tuple[str, UUID], ProvenanceNode] | None = None,
        edges: dict[tuple[str, UUID], list[ProvenanceEdge]] | None = None,
        permissions: dict[tuple[str, UUID], bool] | None = None,
    ):
        self._nodes = nodes or {}
        self._edges = edges or {}
        self._permissions = permissions or {}

    async def query_node(self, namespace: str, node_id: UUID) -> ProvenanceNode | None:
        return self._nodes.get((namespace, node_id))

    async def query_incoming_edges(self, namespace: str, node_id: UUID) -> list[ProvenanceEdge]:
        return list(self._edges.get((namespace, node_id), []))

    async def check_permission(self, namespace: str, node_id: UUID, principal: object) -> bool:
        return self._permissions.get((namespace, node_id), True)


def _make_node(
    namespace: str,
    node_id: UUID,
    node_type: str = "test",
    attributes: dict | None = None,
) -> ProvenanceNode:
    """构造 ProvenanceNode。"""
    return ProvenanceNode(
        namespace=namespace,
        node_id=node_id,
        version=None,
        node_type=node_type,
        display_label=None,
        attributes=attributes or {"name": "test"},
        is_restricted=False,
    )


def _make_edge(
    source_ns: str,
    source_id: UUID,
    target_ns: str,
    target_id: UUID,
    edge_type: str = "test_edge",
) -> ProvenanceEdge:
    """构造 ProvenanceEdge。"""
    return ProvenanceEdge(
        source_namespace=source_ns,
        source_id=source_id,
        source_version=None,
        target_namespace=target_ns,
        target_id=target_id,
        target_version=None,
        edge_type=edge_type,
        edge_type_label=edge_type,
    )


def _make_provenance_service(
    core_adapter: MockAdapter,
    research_adapter: MockAdapter,
    actor_id: UUID | None = None,
) -> UnifiedProvenanceQueryService:
    """创建带 Mock 适配器的 UnifiedProvenanceQueryService。"""
    return UnifiedProvenanceQueryService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=actor_id or uuid4(),
        core_adapter=core_adapter,
        research_adapter=research_adapter,
    )


class FakeS3:
    """模拟 S3 / MinIO 客户端。"""

    def __init__(self):
        self._store: dict[str, bytes] = {}

    def put_object(self, path: str, content: bytes) -> None:
        self._store[path] = content

    def get_object(self, path: str) -> bytes:
        return self._store.get(path, b"")


# ============================================================
# 1. UnifiedProvenanceQueryService — 联邦溯源图拼接
# ============================================================


class TestUnifiedProvenanceQueryService:
    """联邦溯源图查询服务测试。"""

    @pytest.mark.asyncio
    async def test_provenance_single_node(self):
        """单节点查询：仅返回目标节点，无边。"""
        target_id = uuid4()
        target_ns = "research:insight"
        target_node = _make_node(target_ns, target_id, "insight")

        research_adapter = MockAdapter(
            nodes={(target_ns, target_id): target_node},
            edges={},
        )
        svc = _make_provenance_service(MockAdapter(), research_adapter)

        graph = await svc.query_provenance_graph(target_ns, target_id)

        assert len(graph.nodes) == 1
        assert graph.nodes[0].node_id == target_id
        assert len(graph.edges) == 0
        assert graph.stats.total_nodes == 1

    @pytest.mark.asyncio
    async def test_provenance_bfs_traversal(self):
        """BFS 上游追溯：A → B → C 三层链。"""
        c_id = uuid4()
        b_id = uuid4()
        a_id = uuid4()

        # 节点
        nodes = {
            ("research:insight", c_id): _make_node("research:insight", c_id, "insight"),
            ("research:analysis_run", b_id): _make_node(
                "research:analysis_run", b_id, "analysis_run"
            ),
            ("research:evidence_snapshot", a_id): _make_node(
                "research:evidence_snapshot", a_id, "evidence_snapshot"
            ),
        }

        # 边：C 的上游是 B，B 的上游是 A
        edges = {
            ("research:insight", c_id): [
                _make_edge(
                    "research:analysis_run", b_id, "research:insight", c_id, "run_to_insight"
                ),
            ],
            ("research:analysis_run", b_id): [
                _make_edge(
                    "research:evidence_snapshot",
                    a_id,
                    "research:analysis_run",
                    b_id,
                    "snapshot_to_run",
                ),
            ],
            ("research:evidence_snapshot", a_id): [],
        }

        research_adapter = MockAdapter(nodes=nodes, edges=edges)
        svc = _make_provenance_service(MockAdapter(), research_adapter)

        graph = await svc.query_provenance_graph("research:insight", c_id)

        assert len(graph.nodes) == 3
        assert len(graph.edges) == 2
        assert graph.stats.total_nodes == 3

    @pytest.mark.asyncio
    async def test_provenance_cycle_protection(self):
        """循环保护：A → B → A 不产生无限循环。"""
        a_id = uuid4()
        b_id = uuid4()

        nodes = {
            ("research:insight", a_id): _make_node("research:insight", a_id, "insight"),
            ("research:analysis_run", b_id): _make_node(
                "research:analysis_run", b_id, "analysis_run"
            ),
        }

        # A ← B, B ← A（循环）
        edges = {
            ("research:insight", a_id): [
                _make_edge("research:analysis_run", b_id, "research:insight", a_id),
            ],
            ("research:analysis_run", b_id): [
                _make_edge("research:insight", a_id, "research:analysis_run", b_id),
            ],
        }

        research_adapter = MockAdapter(nodes=nodes, edges=edges)
        svc = _make_provenance_service(MockAdapter(), research_adapter)

        graph = await svc.query_provenance_graph("research:insight", a_id)

        # 循环保护：只有 2 个节点，不应无限扩展
        assert len(graph.nodes) == 2
        assert graph.stats.total_nodes == 2

    @pytest.mark.asyncio
    async def test_provenance_depth_limit(self):
        """深度限制：超过 max_depth 的分支被截断。"""
        # 构造 5 层链：A → B → C → D → E
        node_ids = [uuid4() for _ in range(5)]
        namespaces = [
            "research:evidence_snapshot",
            "research:analysis_run",
            "research:derived_dataset",
            "research:view",
            "research:insight",
        ]

        nodes = {
            (namespaces[i], node_ids[i]): _make_node(namespaces[i], node_ids[i]) for i in range(5)
        }

        edges = {}
        for i in range(4):
            edges[(namespaces[i + 1], node_ids[i + 1])] = [
                _make_edge(namespaces[i], node_ids[i], namespaces[i + 1], node_ids[i + 1]),
            ]
        edges[(namespaces[0], node_ids[0])] = []

        research_adapter = MockAdapter(nodes=nodes, edges=edges)
        svc = _make_provenance_service(MockAdapter(), research_adapter)

        # 从 E 开始溯源，max_depth=2 应只到达 3 层
        graph = await svc.query_provenance_graph(
            namespaces[4],
            node_ids[4],
            options=ProvenanceQueryOptions(max_depth=2),
        )

        # E(depth=0) + D(depth=1) + C(depth=2) = 3 个节点
        # B 的入边在 depth=3 被截断
        assert len(graph.nodes) == 3
        assert graph.stats.truncated_count >= 1

    @pytest.mark.asyncio
    async def test_provenance_default_max_depth(self):
        """默认最大深度为 20。"""
        assert DEFAULT_MAX_DEPTH == 20

    @pytest.mark.asyncio
    async def test_provenance_restricted_node(self):
        """受限节点：无权节点替换为 RestrictedNode。"""
        target_id = uuid4()
        upstream_id = uuid4()

        nodes = {
            ("research:insight", target_id): _make_node("research:insight", target_id, "insight"),
            ("research:analysis_run", upstream_id): _make_node(
                "research:analysis_run", upstream_id, "analysis_run"
            ),
        }
        edges = {
            ("research:insight", target_id): [
                _make_edge("research:analysis_run", upstream_id, "research:insight", target_id),
            ],
            ("research:analysis_run", upstream_id): [],
        }

        # upstream 无权限
        research_adapter = MockAdapter(
            nodes=nodes,
            edges=edges,
            permissions={("research:analysis_run", upstream_id): False},
        )
        svc = _make_provenance_service(MockAdapter(), research_adapter)

        graph = await svc.query_provenance_graph("research:insight", target_id)

        # 应有 2 个节点（1 个正常 + 1 个受限）
        assert len(graph.nodes) == 2
        restricted_nodes = [n for n in graph.nodes if n.is_restricted]
        assert len(restricted_nodes) == 1
        assert restricted_nodes[0].node_type == "restricted"
        assert restricted_nodes[0].display_label.display_label == "受限来源"
        assert graph.stats.restricted_nodes_count == 1

    @pytest.mark.asyncio
    async def test_provenance_truncate_branch(self):
        """truncate_branch=True 时递归移除受限节点的上游。"""
        target_id = uuid4()
        mid_id = uuid4()
        top_id = uuid4()

        nodes = {
            ("research:insight", target_id): _make_node("research:insight", target_id, "insight"),
            ("research:analysis_run", mid_id): _make_node(
                "research:analysis_run", mid_id, "analysis_run"
            ),
            ("research:evidence_snapshot", top_id): _make_node(
                "research:evidence_snapshot", top_id, "evidence_snapshot"
            ),
        }
        edges = {
            ("research:insight", target_id): [
                _make_edge("research:analysis_run", mid_id, "research:insight", target_id),
            ],
            ("research:analysis_run", mid_id): [
                _make_edge("research:evidence_snapshot", top_id, "research:analysis_run", mid_id),
            ],
            ("research:evidence_snapshot", top_id): [],
        }

        # mid 无权限，truncate_branch 应移除 mid 和 top
        research_adapter = MockAdapter(
            nodes=nodes,
            edges=edges,
            permissions={("research:analysis_run", mid_id): False},
        )
        svc = _make_provenance_service(MockAdapter(), research_adapter)

        graph = await svc.query_provenance_graph(
            "research:insight",
            target_id,
            options=ProvenanceQueryOptions(truncate_branch=True),
        )

        # 只有 target_id 节点保留，mid 和 top 都被移除
        non_restricted = [n for n in graph.nodes if not n.is_restricted]
        assert len(non_restricted) == 1
        assert non_restricted[0].node_id == target_id
        assert len(graph.edges) == 0

    @pytest.mark.asyncio
    async def test_provenance_cross_boundary_edge(self):
        """跨边界边：research 节点的上游有 core 节点时，应路由到 CoreAdapter。"""
        snapshot_id = uuid4()
        fact_id = uuid4()

        research_nodes = {
            ("research:evidence_snapshot", snapshot_id): _make_node(
                "research:evidence_snapshot", snapshot_id, "evidence_snapshot"
            ),
        }
        research_edges = {
            ("research:evidence_snapshot", snapshot_id): [
                _make_edge(
                    "core:fact",
                    fact_id,
                    "research:evidence_snapshot",
                    snapshot_id,
                    "fact_to_snapshot",
                ),
            ],
        }

        core_nodes = {
            ("core:fact", fact_id): _make_node("core:fact", fact_id, "fact"),
        }
        core_edges = {
            ("core:fact", fact_id): [],
        }

        core_adapter = MockAdapter(nodes=core_nodes, edges=core_edges)
        research_adapter = MockAdapter(nodes=research_nodes, edges=research_edges)
        svc = _make_provenance_service(core_adapter, research_adapter)

        graph = await svc.query_provenance_graph("research:evidence_snapshot", snapshot_id)

        # 应包含 2 个节点：snapshot + fact
        assert len(graph.nodes) == 2
        namespaces = {n.namespace for n in graph.nodes}
        assert "research:evidence_snapshot" in namespaces
        assert "core:fact" in namespaces

    @pytest.mark.asyncio
    async def test_provenance_node_not_found(self):
        """节点不存在时返回空图。"""
        research_adapter = MockAdapter(nodes={}, edges={})
        svc = _make_provenance_service(MockAdapter(), research_adapter)

        graph = await svc.query_provenance_graph("research:insight", uuid4())

        assert len(graph.nodes) == 0
        assert len(graph.edges) == 0
        assert graph.stats.total_nodes == 0

    @pytest.mark.asyncio
    async def test_route_adapter_core(self):
        """命名空间路由：core:* → core_adapter。"""
        core_adapter = MockAdapter()
        research_adapter = MockAdapter()
        svc = _make_provenance_service(core_adapter, research_adapter)

        assert svc._route_adapter("core:fact") is core_adapter
        assert svc._route_adapter("core:derivation_run") is core_adapter

    @pytest.mark.asyncio
    async def test_route_adapter_research(self):
        """命名空间路由：research:* → research_adapter。"""
        core_adapter = MockAdapter()
        research_adapter = MockAdapter()
        svc = _make_provenance_service(core_adapter, research_adapter)

        assert svc._route_adapter("research:insight") is research_adapter
        assert svc._route_adapter("research:evidence_snapshot") is research_adapter

    @pytest.mark.asyncio
    async def test_compute_stats(self):
        """统计信息计算。"""
        nodes = [
            _make_node("research:insight", uuid4(), "insight"),
            _make_node("research:analysis_run", uuid4(), "analysis_run"),
            ProvenanceNode(
                namespace="restricted",
                node_id=UUID(int=0),
                version=None,
                node_type="restricted",
                display_label=NodeDisplayLabelGenerator.restricted_label(),
                attributes={},
                is_restricted=True,
            ),
        ]
        edges = [
            _make_edge("research:analysis_run", uuid4(), "research:insight", uuid4()),
        ]
        svc = _make_provenance_service(MockAdapter(), MockAdapter())
        stats = svc._compute_stats(nodes, edges, truncated_count=3)

        assert stats.total_nodes == 3
        assert stats.restricted_nodes_count == 1
        assert stats.truncated_count == 3
        assert stats.nodes_by_type.get("insight") == 1
        assert stats.nodes_by_type.get("analysis_run") == 1
        assert stats.nodes_by_type.get("restricted") == 1


# ============================================================
# 2. KnowledgeReferenceService — 知识引用快照
# ============================================================


class TestKnowledgeReferenceService:
    """知识引用快照管理服务测试。"""

    def _make_service(
        self,
        s3: FakeS3 | None = None,
    ) -> KnowledgeReferenceService:
        return KnowledgeReferenceService(
            session_factory=MagicMock(),
            department_id=uuid4(),
            actor_id=uuid4(),
            lineage_writer=AsyncMock(),
            s3=s3 or FakeS3(),
        )

    def test_truncate_snippet_within_limit(self):
        """短文本（<64KB）不截断。"""
        svc = self._make_service()
        text = "A" * 1000
        result = svc._truncate_snippet(text)
        assert result == text

    def test_truncate_snippet_exceeds_limit(self):
        """超过 64KB 的文本被截断并标注。"""
        svc = self._make_service()
        text = "X" * (SNIPPET_MAX_SIZE + 1000)
        result = svc._truncate_snippet(text)

        encoded = result.encode("utf-8")
        assert len(encoded) <= SNIPPET_MAX_SIZE
        assert TRUNCATION_SUFFIX in result

    def test_truncate_snippet_exact_limit(self):
        """正好 64KB 的文本不截断。"""
        svc = self._make_service()
        text = "Y" * SNIPPET_MAX_SIZE
        result = svc._truncate_snippet(text)
        assert result == text
        assert TRUNCATION_SUFFIX not in result

    def test_truncate_snippet_empty(self):
        """空文本不截断。"""
        svc = self._make_service()
        assert svc._truncate_snippet("") == ""

    def test_inline_threshold_is_4kb(self):
        """短文本阈值应为 4KB。"""
        assert SNIPPET_INLINE_THRESHOLD == 4 * 1024

    def test_max_size_is_64kb(self):
        """单条快照限制应为 64KB。"""
        assert SNIPPET_MAX_SIZE == 64 * 1024

    def test_store_snippet_path_format(self):
        """MinIO 存储路径格式正确。"""
        s3 = FakeS3()
        svc = self._make_service(s3)
        ref_id = uuid4()
        ws_id = uuid4()
        run_id = uuid4()
        text = "Long text" * 1000

        path = svc._store_snippet(ref_id, text, ws_id, run_id)

        assert path is not None
        assert str(ws_id) in path
        assert str(run_id) in path
        assert str(ref_id) in path
        assert path.endswith(".json")

    def test_store_and_retrieve_snippet(self):
        """存储和读取长文本快照。"""
        s3 = FakeS3()
        svc = self._make_service(s3)
        ref_id = uuid4()
        ws_id = uuid4()
        run_id = uuid4()
        text = "Knowledge snippet content" * 500

        path = svc._store_snippet(ref_id, text, ws_id, run_id)
        assert path is not None

        retrieved = svc._retrieve_snippet(path)
        assert retrieved == text

    def test_retrieve_snippet_not_found(self):
        """读取不存在的路径返回空字符串。"""
        svc = self._make_service()
        result = svc._retrieve_snippet("nonexistent/path.json")
        assert result == ""


# ============================================================
# 3. LineageWriterService — 事件驱动 Hook
# ============================================================


class _AsyncSessionContext:
    """模拟 async session context manager。"""

    def __init__(self, session: AsyncMock):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        pass


class _AsyncSessionFactory:
    """模拟 async_sessionmaker，返回 async context manager。"""

    def __init__(self):
        self.session = AsyncMock()
        self.session.begin = MagicMock(return_value=_NullAsyncContext())

    def __call__(self):
        return _AsyncSessionContext(self.session)


class _NullAsyncContext:
    """空 async context manager for session.begin()。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class TestLineageWriterService:
    """溯源边写入服务测试。"""

    def _make_service(self) -> LineageWriterService:
        return LineageWriterService(session_factory=_AsyncSessionFactory())

    @pytest.mark.asyncio
    async def test_on_snapshot_frozen_hook(self):
        """快照冻结 Hook：创建 fact→snapshot 边。"""
        svc = self._make_service()

        ws_id = uuid4()
        with patch.object(
            svc, "_resolve_workspace_id", new_callable=AsyncMock, return_value=ws_id
        ):
            with patch(
                "packages.research.lineage_writer.ResearchRepository.insert_lineage_edge",
                new_callable=AsyncMock,
            ) as mock_insert:
                await svc.on_snapshot_frozen(
                    uuid4(),
                    [
                        {"namespace": "core:fact", "id": str(uuid4())},
                        {"namespace": "core:fact", "id": str(uuid4())},
                    ],
                )
                # 应调用 2 次（2 个 source_refs）
                assert mock_insert.call_count == 2
                # 验证 edge_type
                first_call = mock_insert.call_args_list[0]
                assert first_call.kwargs["edge_type"] == "fact_to_snapshot"
                assert first_call.kwargs["target_namespace"] == "research:evidence_snapshot"

    @pytest.mark.asyncio
    async def test_on_snapshot_frozen_published_derived(self):
        """快照冻结 Hook：published_derived 来源使用正确的 edge_type。"""
        svc = self._make_service()

        ws_id = uuid4()
        with patch.object(
            svc, "_resolve_workspace_id", new_callable=AsyncMock, return_value=ws_id
        ):
            with patch(
                "packages.research.lineage_writer.ResearchRepository.insert_lineage_edge",
                new_callable=AsyncMock,
            ) as mock_insert:
                await svc.on_snapshot_frozen(
                    uuid4(),
                    [{"namespace": "research:published_derived", "id": str(uuid4())}],
                )
                assert mock_insert.call_count == 1
                assert mock_insert.call_args.kwargs["edge_type"] == "published_derived_to_snapshot"

    @pytest.mark.asyncio
    async def test_on_snapshot_frozen_invalid_ref(self):
        """快照冻结 Hook：无效的 source_ref 被跳过。"""
        svc = self._make_service()

        with patch(
            "packages.research.lineage_writer.ResearchRepository.insert_lineage_edge",
            new_callable=AsyncMock,
        ) as mock_insert:
            await svc.on_snapshot_frozen(
                uuid4(),
                [
                    {"namespace": "", "id": str(uuid4())},  # 空 namespace
                    {"namespace": "core:fact", "id": ""},  # 空 id
                    {"namespace": "core:fact", "id": "not-a-uuid"},  # 非法 UUID
                ],
            )
            assert mock_insert.call_count == 0

    @pytest.mark.asyncio
    async def test_on_run_started_hook(self):
        """Run 启动 Hook：创建 snapshot→run 边。"""
        svc = self._make_service()

        ws_id = uuid4()
        with patch.object(
            svc, "_resolve_workspace_id", new_callable=AsyncMock, return_value=ws_id
        ):
            with patch(
                "packages.research.lineage_writer.ResearchRepository.insert_lineage_edge",
                new_callable=AsyncMock,
            ) as mock_insert:
                await svc.on_run_started(uuid4(), [uuid4(), uuid4(), uuid4()])
                assert mock_insert.call_count == 3
                for call in mock_insert.call_args_list:
                    assert call.kwargs["edge_type"] == "snapshot_to_run"
                    assert call.kwargs["target_namespace"] == "research:analysis_run"
                    assert call.kwargs["source_namespace"] == "research:evidence_snapshot"

    @pytest.mark.asyncio
    async def test_on_step_completed_hook(self):
        """步骤完成 Hook：创建 run→step 边。"""
        svc = self._make_service()

        ws_id = uuid4()
        with patch.object(
            svc, "_resolve_workspace_id", new_callable=AsyncMock, return_value=ws_id
        ):
            with patch(
                "packages.research.lineage_writer.ResearchRepository.insert_lineage_edge",
                new_callable=AsyncMock,
            ) as mock_insert:
                await svc.on_step_completed(uuid4(), uuid4())
                assert mock_insert.call_count == 1
                call = mock_insert.call_args
                assert call.kwargs["edge_type"] == "run_to_step"
                assert call.kwargs["source_namespace"] == "research:analysis_run"
                assert call.kwargs["target_namespace"] == "research:analysis_step"

    @pytest.mark.asyncio
    async def test_on_product_confirmed_hook(self):
        """产物确认 Hook：创建 run→product 边。"""
        svc = self._make_service()

        ws_id = uuid4()
        with patch.object(
            svc, "_resolve_workspace_id", new_callable=AsyncMock, return_value=ws_id
        ):
            with patch(
                "packages.research.lineage_writer.ResearchRepository.insert_lineage_edge",
                new_callable=AsyncMock,
            ) as mock_insert:
                await svc.on_product_confirmed(
                    uuid4(),
                    "research:derived_dataset",
                    uuid4(),
                    "dataset",
                )
                assert mock_insert.call_count == 1
                assert mock_insert.call_args.kwargs["edge_type"] == "run_to_dataset"

    @pytest.mark.asyncio
    async def test_on_knowledge_referenced_hook(self):
        """知识引用 Hook：创建 knowledge_ref→insight 边。"""
        svc = self._make_service()

        ws_id = uuid4()
        with patch.object(
            svc, "_resolve_workspace_id", new_callable=AsyncMock, return_value=ws_id
        ):
            with patch(
                "packages.research.lineage_writer.ResearchRepository.insert_lineage_edge",
                new_callable=AsyncMock,
            ) as mock_insert:
                await svc.on_knowledge_referenced(uuid4(), uuid4())
                assert mock_insert.call_count == 1
                assert mock_insert.call_args.kwargs["edge_type"] == "knowledge_ref_to_insight"

    @pytest.mark.asyncio
    async def test_on_knowledge_referenced_no_insight(self):
        """知识引用 Hook：insight_id 为 None 时跳过。"""
        svc = self._make_service()

        with patch(
            "packages.research.lineage_writer.ResearchRepository.insert_lineage_edge",
            new_callable=AsyncMock,
        ) as mock_insert:
            await svc.on_knowledge_referenced(uuid4(), None)
            assert mock_insert.call_count == 0

    @pytest.mark.asyncio
    async def test_hook_failure_does_not_block(self):
        """Hook 失败不阻断主流程（异常被捕获并记录告警）。"""
        svc = self._make_service()

        with patch(
            "packages.research.lineage_writer.ResearchRepository.insert_lineage_edge",
            new_callable=AsyncMock,
            side_effect=Exception("DB connection failed"),
        ):
            # 不应抛出异常
            await svc.on_snapshot_frozen(uuid4(), [{"namespace": "core:fact", "id": str(uuid4())}])
            await svc.on_run_started(uuid4(), [uuid4()])
            await svc.on_step_completed(uuid4(), uuid4())
            await svc.on_product_confirmed(uuid4(), "research:derived_dataset", uuid4(), "dataset")
            await svc.on_knowledge_referenced(uuid4(), uuid4())

        # 所有 Hook 均未抛出异常，测试通过


# ============================================================
# 4. NodeDisplayLabelGenerator — 节点展示标签
# ============================================================


class TestNodeDisplayLabelGenerator:
    """节点展示标签生成器测试。"""

    def test_generate_fact_label(self):
        """core:fact 命名空间生成正确的标签。"""
        label = NodeDisplayLabelGenerator.generate(
            "core:fact",
            {"name": "实验事实#1", "node_id": str(uuid4())},
        )
        assert label.display_label == "实验事实#1"
        assert label.node_type_label == "实验事实"
        assert label.icon == "🔬"
        assert label.namespace == "core:fact"
        assert label.jump_target is not None

    def test_generate_insight_label(self):
        """research:insight 命名空间生成正确的标签。"""
        label = NodeDisplayLabelGenerator.generate(
            "research:insight",
            {"name": "Key Finding", "node_id": str(uuid4())},
        )
        assert label.display_label == "Key Finding"
        assert label.node_type_label == "Insight"
        assert label.icon == "💡"

    def test_generate_restricted_label(self):
        """restricted 命名空间生成受限标签。"""
        label = NodeDisplayLabelGenerator.generate("restricted", {})
        assert label.display_label == "受限来源"
        assert label.node_type_label == "受限来源"
        assert label.icon == "🔒"
        assert label.jump_target is None

    def test_generate_long_name_truncated(self):
        """超长名称截断到 60 字符。"""
        long_name = "A" * 100
        label = NodeDisplayLabelGenerator.generate(
            "core:fact", {"name": long_name, "node_id": str(uuid4())}
        )
        assert len(label.display_label) <= 60
        assert "..." in label.display_label

    def test_generate_with_title_fallback(self):
        """name 为空时使用 title。"""
        label = NodeDisplayLabelGenerator.generate(
            "research:result_version",
            {"title": "Result Title", "node_id": str(uuid4())},
        )
        assert label.display_label == "Result Title"

    def test_generate_with_subject_id_fallback(self):
        """name 和 title 为空时使用 subject_id。"""
        label = NodeDisplayLabelGenerator.generate(
            "core:fact",
            {"subject_id": "SUBJ-001", "node_id": str(uuid4())},
        )
        assert label.display_label == "SUBJ-001"

    def test_generate_version_summary(self):
        """version 字段生成版本摘要。"""
        label = NodeDisplayLabelGenerator.generate(
            "research:derived_dataset",
            {"name": "Dataset", "version": 3, "node_id": str(uuid4())},
        )
        assert label.version_summary == "v3"

    def test_generate_version_number_summary(self):
        """version_number 字段生成版本摘要。"""
        label = NodeDisplayLabelGenerator.generate(
            "research:derived_dataset",
            {"name": "Dataset", "version_number": 5, "node_id": str(uuid4())},
        )
        assert label.version_summary == "v5"

    def test_generate_snapshot_number_summary(self):
        """snapshot_number 字段生成快照摘要。"""
        label = NodeDisplayLabelGenerator.generate(
            "research:evidence_snapshot",
            {"name": "Snapshot", "snapshot_number": 2, "node_id": str(uuid4())},
        )
        assert label.version_summary == "快照 #2"

    def test_get_type_label_fact(self):
        """get_type_label: core:fact → 实验事实。"""
        assert NodeDisplayLabelGenerator.get_type_label("core:fact") == "实验事实"

    def test_get_type_label_restricted(self):
        """get_type_label: restricted → 受限来源。"""
        assert NodeDisplayLabelGenerator.get_type_label("restricted") == "受限来源"

    def test_get_type_label_unknown(self):
        """get_type_label: 未知命名空间 → 未知。"""
        assert NodeDisplayLabelGenerator.get_type_label("unknown:ns") == "未知"

    def test_get_icon_fact(self):
        """get_icon: core:fact → 🔬。"""
        assert NodeDisplayLabelGenerator.get_icon("core:fact") == "🔬"

    def test_get_icon_restricted(self):
        """get_icon: restricted → 🔒。"""
        assert NodeDisplayLabelGenerator.get_icon("restricted") == "🔒"

    def test_get_jump_target_fact(self):
        """get_jump_target: core:fact 返回 URL。"""
        nid = uuid4()
        target = NodeDisplayLabelGenerator.get_jump_target("core:fact", nid)
        assert target is not None
        assert str(nid) in target

    def test_get_jump_target_restricted(self):
        """get_jump_target: restricted 返回 None。"""
        assert NodeDisplayLabelGenerator.get_jump_target("restricted", uuid4()) is None

    def test_restricted_label(self):
        """restricted_label 返回固定受限标签。"""
        label = NodeDisplayLabelGenerator.restricted_label()
        assert label.display_label == "受限来源"
        assert label.node_type_label == "受限来源"
        assert label.icon == "🔒"
        assert label.jump_target is None
        assert label.namespace == "restricted"
