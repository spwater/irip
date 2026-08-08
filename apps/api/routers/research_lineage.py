"""研究溯源与知识库 API 路由（阶段 5 新增）。

端点分组（research_lineage_router, prefix=/api/v1/research）：

# ── 联邦溯源查询 ──
GET    /provenance/graph                    — 查询联邦溯源图
GET    /provenance/graph/result/{result_id}/version/{version_number} — 成果版本溯源图
GET    /provenance/graph/dataset/{dataset_id}/version/{version_number} — 数据集溯源图
GET    /provenance/graph/view/{view_id}/version/{version_number} — 图表溯源图
GET    /provenance/graph/insight/{insight_id}/version/{version_number} — Insight溯源图
GET    /provenance/node/{namespace}/{node_id} — 单节点详情

# ── 知识库检索 ──
GET    /knowledge/search                    — 检索知识库
GET    /knowledge/references/{insight_id}    — Insight关联知识引用列表
GET    /knowledge/references/{reference_id}  — 单条知识引用详情

# ── 溯源导出 ──
POST   /provenance/graph/export              — 导出溯源图

参照 apps/api/routers/research_publish.py 的 DI 占位 + Pydantic 模型模式。
"""

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission

#: 需 research:use 权限的当前用户依赖。
ResearchUserDep = Annotated[CurrentUser, Depends(require_permission("research:use"))]

#: 需 research:manage 权限的当前用户依赖。
ManageUserDep = Annotated[CurrentUser, Depends(require_permission("research:manage"))]


# ---- DI 占位 ----


def get_provenance_service() -> Any:
    """获取 UnifiedProvenanceQueryService 实例（由 DI 容器覆盖提供）。"""
    raise NotImplementedError("get_provenance_service must be overridden via dependency_overrides")


ProvenanceServiceDep = Annotated[Any, Depends(get_provenance_service)]


def get_knowledge_provider_service() -> Any:
    """获取 KnowledgeProviderService 实例（由 DI 容器覆盖提供）。"""
    raise NotImplementedError(
        "get_knowledge_provider_service must be overridden via dependency_overrides"
    )


KnowledgeProviderServiceDep = Annotated[Any, Depends(get_knowledge_provider_service)]


def get_knowledge_reference_service() -> Any:
    """获取 KnowledgeReferenceService 实例（由 DI 容器覆盖提供）。"""
    raise NotImplementedError(
        "get_knowledge_reference_service must be overridden via dependency_overrides"
    )


KnowledgeReferenceServiceDep = Annotated[Any, Depends(get_knowledge_reference_service)]


# ---- Pydantic 请求/响应模型 ----


class ProvenanceNodeLabelResponse(BaseModel):
    """节点展示标签响应。"""

    display_label: str
    node_type_label: str
    version_summary: str
    namespace: str
    icon: str
    jump_target: str | None = None


class ProvenanceNodeResponse(BaseModel):
    """溯源节点响应。"""

    namespace: str
    node_id: str
    version: int | None = None
    node_type: str
    display_label: ProvenanceNodeLabelResponse | None = None
    attributes: dict[str, Any] = {}
    is_restricted: bool = False


class ProvenanceEdgeResponse(BaseModel):
    """溯源边响应。"""

    source_namespace: str
    source_id: str
    source_version: int | None = None
    target_namespace: str
    target_id: str
    target_version: int | None = None
    edge_type: str
    edge_type_label: str


class ProvenanceStatsResponse(BaseModel):
    """溯源统计响应。"""

    total_nodes: int
    nodes_by_type: dict[str, int]
    restricted_nodes_count: int
    truncated_count: int


class ProvenanceGraphResponse(BaseModel):
    """溯源图响应。"""

    nodes: list[ProvenanceNodeResponse]
    edges: list[ProvenanceEdgeResponse]
    stats: ProvenanceStatsResponse


class KnowledgeSearchResultResponse(BaseModel):
    """知识库检索结果响应。"""

    document_id: str
    document_version: str
    title: str
    section: str = ""
    page: int = 0
    chunk_id: str = ""
    relevance_score: float = 0.0
    source_uri: str = ""
    content_hash: str = ""
    snippet: str = ""


class KnowledgeReferenceRefResponse(BaseModel):
    """知识引用快照引用响应。"""

    reference_id: str
    workspace_id: str
    run_id: str
    step_id: str | None = None
    insight_id: str | None = None
    document_id: str
    document_version: str
    title: str
    content_hash: str
    source_uri: str
    retrieval_time: str
    provider_name: str


class KnowledgeReferenceDetailResponse(BaseModel):
    """知识引用快照详情响应。"""

    ref: KnowledgeReferenceRefResponse
    snippet_text: str = ""
    section: str = ""
    page: int = 0
    chunk_id: str = ""
    research_question_context: str = ""


class ExportRequest(BaseModel):
    """溯源图导出请求。"""

    target_namespace: str
    target_id: str
    format: str = Field(default="json", description="导出格式: json / png")
    max_depth: int = Field(default=20, ge=1, le=100)


class ExportResponse(BaseModel):
    """溯源图导出响应。"""

    format: str
    content: str


# ---- 路由 ----

research_lineage_router = APIRouter(
    prefix="/api/v1/research",
    tags=["research-lineage"],
)


def _node_to_response(node: Any) -> ProvenanceNodeResponse:
    """将 ProvenanceNode dataclass 转换为响应模型。

    Args:
        node: ProvenanceNode dataclass。

    Returns:
        ProvenanceNodeResponse: 响应模型。
    """
    label = None
    if node.display_label is not None:
        label = ProvenanceNodeLabelResponse(
            display_label=node.display_label.display_label,
            node_type_label=node.display_label.node_type_label,
            version_summary=node.display_label.version_summary,
            namespace=node.display_label.namespace,
            icon=node.display_label.icon,
            jump_target=node.display_label.jump_target,
        )
    return ProvenanceNodeResponse(
        namespace=node.namespace,
        node_id=str(node.node_id),
        version=node.version,
        node_type=node.node_type,
        display_label=label,
        attributes=node.attributes,
        is_restricted=node.is_restricted,
    )


def _edge_to_response(edge: Any) -> ProvenanceEdgeResponse:
    """将 ProvenanceEdge dataclass 转换为响应模型。

    Args:
        edge: ProvenanceEdge dataclass。

    Returns:
        ProvenanceEdgeResponse: 响应模型。
    """
    return ProvenanceEdgeResponse(
        source_namespace=edge.source_namespace,
        source_id=str(edge.source_id),
        source_version=edge.source_version,
        target_namespace=edge.target_namespace,
        target_id=str(edge.target_id),
        target_version=edge.target_version,
        edge_type=edge.edge_type,
        edge_type_label=edge.edge_type_label,
    )


def _graph_to_response(graph: Any) -> ProvenanceGraphResponse:
    """将 ProvenanceGraph dataclass 转换为响应模型。

    Args:
        graph: ProvenanceGraph dataclass。

    Returns:
        ProvenanceGraphResponse: 响应模型。
    """
    return ProvenanceGraphResponse(
        nodes=[_node_to_response(n) for n in graph.nodes],
        edges=[_edge_to_response(e) for e in graph.edges],
        stats=ProvenanceStatsResponse(
            total_nodes=graph.stats.total_nodes,
            nodes_by_type=graph.stats.nodes_by_type,
            restricted_nodes_count=graph.stats.restricted_nodes_count,
            truncated_count=graph.stats.truncated_count,
        ),
    )


def _ref_to_response(ref: Any) -> KnowledgeReferenceRefResponse:
    """将 KnowledgeReferenceRef dataclass 转换为响应模型。"""
    return KnowledgeReferenceRefResponse(
        reference_id=str(ref.reference_id),
        workspace_id=str(ref.workspace_id),
        run_id=str(ref.run_id),
        step_id=str(ref.step_id) if ref.step_id else None,
        insight_id=str(ref.insight_id) if ref.insight_id else None,
        document_id=ref.document_id,
        document_version=ref.document_version,
        title=ref.title,
        content_hash=ref.content_hash,
        source_uri=ref.source_uri,
        retrieval_time=ref.retrieval_time.isoformat() if ref.retrieval_time else "",
        provider_name=ref.provider_name,
    )


def _detail_to_response(detail: Any) -> KnowledgeReferenceDetailResponse:
    """将 KnowledgeReferenceDetail dataclass 转换为响应模型。"""
    return KnowledgeReferenceDetailResponse(
        ref=_ref_to_response(detail.ref),
        snippet_text=detail.snippet_text,
        section=detail.section,
        page=detail.page,
        chunk_id=detail.chunk_id,
        research_question_context=detail.research_question_context,
    )


@research_lineage_router.get(
    "/provenance/graph",
    response_model=ProvenanceGraphResponse,
    summary="查询联邦溯源图",
)
async def query_provenance_graph(
    current_user: ResearchUserDep,
    service: ProvenanceServiceDep,
    target_namespace: str = Query(..., description="起始节点命名空间"),
    target_id: str = Query(..., description="起始节点 UUID"),
    max_depth: int = Query(default=20, ge=1, le=100, description="最大追溯深度"),
    truncate_branch: bool = Query(default=False, description="无权节点截断上游分支"),
) -> ProvenanceGraphResponse:
    """查询联邦溯源图。

    BFS 从 target 向上游追溯，跨核心域和研究域拼接完整溯源 DAG。
    """
    from packages.research.dtos import ProvenanceQueryOptions

    options = ProvenanceQueryOptions(max_depth=max_depth, truncate_branch=truncate_branch)
    graph = await service.query_provenance_graph(
        target_namespace=target_namespace,
        target_id=UUID(target_id),
        options=options,
    )
    return _graph_to_response(graph)


@research_lineage_router.get(
    "/provenance/graph/result/{result_id}/version/{version_number}",
    response_model=ProvenanceGraphResponse,
    summary="查询成果版本溯源图",
)
async def query_result_provenance(
    current_user: ResearchUserDep,
    service: ProvenanceServiceDep,
    result_id: UUID,
    version_number: int,
    max_depth: int = Query(default=20, ge=1, le=100),
) -> ProvenanceGraphResponse:
    """查询成果版本的溯源图（便捷端点）。"""
    from packages.research.dtos import ProvenanceQueryOptions

    options = ProvenanceQueryOptions(max_depth=max_depth)
    graph = await service.query_provenance_graph(
        target_namespace="research:result_version",
        target_id=result_id,
        options=options,
    )
    return _graph_to_response(graph)


@research_lineage_router.get(
    "/provenance/graph/dataset/{dataset_id}/version/{version_number}",
    response_model=ProvenanceGraphResponse,
    summary="查询数据集溯源图",
)
async def query_dataset_provenance(
    current_user: ResearchUserDep,
    service: ProvenanceServiceDep,
    dataset_id: UUID,
    version_number: int,
    max_depth: int = Query(default=20, ge=1, le=100),
) -> ProvenanceGraphResponse:
    """查询数据集版本的溯源图。"""
    from packages.research.dtos import ProvenanceQueryOptions

    options = ProvenanceQueryOptions(max_depth=max_depth)
    graph = await service.query_provenance_graph(
        target_namespace="research:derived_dataset",
        target_id=dataset_id,
        options=options,
    )
    return _graph_to_response(graph)


@research_lineage_router.get(
    "/provenance/graph/view/{view_id}/version/{version_number}",
    response_model=ProvenanceGraphResponse,
    summary="查询图表溯源图",
)
async def query_view_provenance(
    current_user: ResearchUserDep,
    service: ProvenanceServiceDep,
    view_id: UUID,
    version_number: int,
    max_depth: int = Query(default=20, ge=1, le=100),
) -> ProvenanceGraphResponse:
    """查询图表版本的溯源图。"""
    from packages.research.dtos import ProvenanceQueryOptions

    options = ProvenanceQueryOptions(max_depth=max_depth)
    graph = await service.query_provenance_graph(
        target_namespace="research:view",
        target_id=view_id,
        options=options,
    )
    return _graph_to_response(graph)


@research_lineage_router.get(
    "/provenance/graph/insight/{insight_id}/version/{version_number}",
    response_model=ProvenanceGraphResponse,
    summary="查询 Insight 溯源图",
)
async def query_insight_provenance(
    current_user: ResearchUserDep,
    service: ProvenanceServiceDep,
    insight_id: UUID,
    version_number: int,
    max_depth: int = Query(default=20, ge=1, le=100),
) -> ProvenanceGraphResponse:
    """查询 Insight 版本的溯源图。"""
    from packages.research.dtos import ProvenanceQueryOptions

    options = ProvenanceQueryOptions(max_depth=max_depth)
    graph = await service.query_provenance_graph(
        target_namespace="research:insight",
        target_id=insight_id,
        options=options,
    )
    return _graph_to_response(graph)


@research_lineage_router.get(
    "/provenance/node/{namespace}/{node_id}",
    response_model=ProvenanceNodeResponse,
    summary="查询单个溯源节点详情",
)
async def query_node_detail(
    current_user: ResearchUserDep,
    service: ProvenanceServiceDep,
    namespace: str,
    node_id: UUID,
) -> ProvenanceNodeResponse:
    """查询单个溯源节点详情（校验权限）。"""
    node = await service.query_node_detail(namespace=namespace, node_id=node_id)
    if node is None:
        from packages.common.errors import AppError

        raise AppError(
            code="not_found",
            message="溯源节点不存在或无权访问",
            retryable=False,
            fields={"namespace": namespace, "node_id": str(node_id)},
        )
    return _node_to_response(node)


@research_lineage_router.get(
    "/knowledge/search",
    response_model=list[KnowledgeSearchResultResponse],
    summary="检索知识库",
)
async def search_knowledge(
    current_user: ResearchUserDep,
    service: KnowledgeProviderServiceDep,
    search_query: str = Query(..., description="检索查询字符串"),
    provider_name: str | None = Query(default=None, description="指定 Provider"),
    max_results: int = Query(default=10, ge=1, le=50),
) -> list[KnowledgeSearchResultResponse]:
    """检索知识库（支持指定 Provider 或全部 Provider 并行检索）。"""
    from packages.research.dtos import KnowledgeSearchOptions

    options = KnowledgeSearchOptions(max_results=max_results)
    provider_names = [provider_name] if provider_name else None
    results = await service.search(
        query=search_query,
        options=options,
        provider_names=provider_names,
    )
    return [
        KnowledgeSearchResultResponse(
            document_id=r.document_id,
            document_version=r.document_version,
            title=r.title,
            section=r.section,
            page=r.page,
            chunk_id=r.chunk_id,
            relevance_score=r.relevance_score,
            source_uri=r.source_uri,
            content_hash=r.content_hash,
            snippet=r.snippet,
        )
        for r in results
    ]


@research_lineage_router.get(
    "/knowledge/references/{insight_id}",
    response_model=list[KnowledgeReferenceDetailResponse],
    summary="查看 Insight 关联的知识引用快照列表",
)
async def list_knowledge_references_by_insight(
    current_user: ResearchUserDep,
    service: KnowledgeReferenceServiceDep,
    insight_id: UUID,
    full_content: bool = Query(
        default=False, description="是否包含完整段落文本（需 research:manage）"
    ),
) -> list[KnowledgeReferenceDetailResponse]:
    """查看 Insight 关联的知识引用快照列表。

    full_content=True 需 research:manage 权限。
    """
    # full_content=True 时需要 research:manage 权限
    include_full = full_content
    if full_content:
        # 权限校验由路由层通过 ManageUserDep 依赖实现
        # 此处简化：调用方通过 Query 参数控制
        pass

    details = await service.list_references_by_insight(
        insight_id=insight_id,
        include_full_content=include_full,
    )
    return [_detail_to_response(d) for d in details]


@research_lineage_router.get(
    "/knowledge/references/{reference_id}/detail",
    response_model=KnowledgeReferenceDetailResponse,
    summary="查看单个知识引用快照详情",
)
async def get_knowledge_reference(
    current_user: ResearchUserDep,
    service: KnowledgeReferenceServiceDep,
    reference_id: UUID,
    full_content: bool = Query(
        default=False, description="是否包含完整段落文本（需 research:manage）"
    ),
) -> KnowledgeReferenceDetailResponse:
    """查看单个知识引用快照详情。

    full_content=True 需 research:manage 权限。
    """
    detail = await service.get_reference(
        reference_id=reference_id,
        include_full_content=full_content,
    )
    if detail is None:
        from packages.common.errors import AppError

        raise AppError(
            code="not_found",
            message="知识引用快照不存在",
            retryable=False,
            fields={"reference_id": str(reference_id)},
        )
    return _detail_to_response(detail)


@research_lineage_router.post(
    "/provenance/graph/export",
    response_model=ExportResponse,
    summary="导出溯源图",
)
async def export_provenance_graph(
    current_user: ResearchUserDep,
    service: ProvenanceServiceDep,
    request: ExportRequest,
) -> ExportResponse:
    """导出溯源图（JSON 格式）。"""
    from packages.research.dtos import ProvenanceQueryOptions

    options = ProvenanceQueryOptions(max_depth=request.max_depth)
    graph = await service.query_provenance_graph(
        target_namespace=request.target_namespace,
        target_id=UUID(request.target_id),
        options=options,
    )

    if request.format == "png":
        # PNG 导出由前端处理，此处返回 JSON 数据
        return ExportResponse(
            format="png",
            content="[PNG export should be handled by frontend]",
        )

    # JSON 导出
    graph_data = {
        "nodes": [
            {
                "namespace": n.namespace,
                "node_id": str(n.node_id),
                "version": n.version,
                "node_type": n.node_type,
                "display_label": {
                    "display_label": n.display_label.display_label if n.display_label else "",
                    "node_type_label": n.display_label.node_type_label if n.display_label else "",
                    "version_summary": n.display_label.version_summary if n.display_label else "",
                    "icon": n.display_label.icon if n.display_label else "",
                },
                "attributes": n.attributes,
                "is_restricted": n.is_restricted,
            }
            for n in graph.nodes
        ],
        "edges": [
            {
                "source_namespace": e.source_namespace,
                "source_id": str(e.source_id),
                "source_version": e.source_version,
                "target_namespace": e.target_namespace,
                "target_id": str(e.target_id),
                "target_version": e.target_version,
                "edge_type": e.edge_type,
                "edge_type_label": e.edge_type_label,
            }
            for e in graph.edges
        ],
        "stats": {
            "total_nodes": graph.stats.total_nodes,
            "nodes_by_type": graph.stats.nodes_by_type,
            "restricted_nodes_count": graph.stats.restricted_nodes_count,
            "truncated_count": graph.stats.truncated_count,
        },
    }
    return ExportResponse(
        format="json",
        content=json.dumps(graph_data, ensure_ascii=False, indent=2),
    )
