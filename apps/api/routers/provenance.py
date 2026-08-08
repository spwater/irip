"""溯源与推导管理路由（IRIP Task 17）。

端点分组（provenance_router, prefix=/api/v1/provenance）：
  POST   /evidence-sets                      — 创建证据集（provenance:write）
  POST   /evidence-sets/{id}/freeze           — 冻结证据集（provenance:write）
  GET    /evidence-sets/{id}                  — 获取证据集（provenance:read）
  GET    /evidence-sets/{id}/members          — 列出成员（provenance:read）
  POST   /recipes                             — 创建配方（provenance:write）
  POST   /recipes/{id}/publish               — 发布配方版本（provenance:publish）
  GET    /recipes                             — 列出配方（provenance:read）
  GET    /recipes/{id}                        — 获取配方（provenance:read）
  POST   /derivation-runs                     — 创建推导运行（provenance:write）
  POST   /derivation-runs/{id}/replay         — 回放推导运行（provenance:write）
  GET    /derivation-runs/{id}                 — 获取推导运行（provenance:read）
  GET    /derivation-runs                     — 列出推导运行（provenance:read）
  GET    /derivation-runs/{id}/graph          — 获取溯源图（provenance:read）
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.provenance.derivations import DerivationRunRef, DerivationService
from packages.provenance.evidence import EvidenceMember, EvidenceService, EvidenceSetRef
from packages.provenance.graph import ProvenanceGraph, ProvenanceGraphService
from packages.provenance.recipes import RecipeService, RecipeVersion

#: 需 provenance:write 权限的当前用户依赖。
WriteUserDep = Annotated[CurrentUser, Depends(require_permission("provenance:write"))]

#: 需 provenance:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("provenance:read"))]

#: 需 provenance:publish 权限的当前用户依赖。
PublishUserDep = Annotated[CurrentUser, Depends(require_permission("provenance:publish"))]


# ---- DI 占位 ----


def get_evidence_service() -> EvidenceService:
    """获取 EvidenceService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_evidence_service must be overridden via dependency_overrides")


def get_recipe_service() -> RecipeService:
    """获取 RecipeService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_recipe_service must be overridden via dependency_overrides")


def get_derivation_service() -> DerivationService:
    """获取 DerivationService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_derivation_service must be overridden via dependency_overrides")


def get_provenance_graph_service() -> ProvenanceGraphService:
    """获取 ProvenanceGraphService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_provenance_graph_service must be overridden via dependency_overrides"
    )


EvidenceServiceDep = Annotated[EvidenceService, Depends(get_evidence_service)]
RecipeServiceDep = Annotated[RecipeService, Depends(get_recipe_service)]
DerivationServiceDep = Annotated[DerivationService, Depends(get_derivation_service)]
GraphServiceDep = Annotated[ProvenanceGraphService, Depends(get_provenance_graph_service)]


# ---- 路由实例 ----

provenance_router = APIRouter(prefix="/api/v1/provenance", tags=["provenance"])


# ---- 请求模型 ----


class CreateEvidenceSetRequest(BaseModel):
    """创建证据集请求。"""

    name: str = Field(..., min_length=1, max_length=256)


class FreezeEvidenceSetRequest(BaseModel):
    """冻结证据集请求。"""

    fact_filter: dict[str, Any] | None = Field(
        None, description="过滤条件，如 {'quality': 'passed'}"
    )


class CreateRecipeRequest(BaseModel):
    """创建配方请求。"""

    code: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=256)


class PublishRecipeVersionRequest(BaseModel):
    """发布配方版本请求。"""

    component_name: str = Field(..., min_length=1, max_length=128)
    component_version: str = Field(..., min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict[str, Any])
    random_seed: int = Field(default=42, ge=0)
    output_definitions: list[str] = Field(default_factory=list)


class CreateDerivationRunRequest(BaseModel):
    """创建推导运行请求。"""

    evidence_set_version_id: UUID
    recipe_version_id: UUID


# ---- 响应模型 ----


class EvidenceSetResponse(BaseModel):
    """证据集响应。"""

    set_id: str
    name: str
    status: str
    version: int
    version_id: str | None
    member_count: int


class EvidenceSetCreatedResponse(BaseModel):
    """创建证据集响应。"""

    set_id: str
    name: str
    status: str


class EvidenceSetVersionResponse(BaseModel):
    """证据集版本响应。"""

    set_id: str
    version: int
    version_id: str
    member_count: int
    status: str


class EvidenceMemberResponse(BaseModel):
    """证据集成员响应。"""

    fact_id: str
    observation_id: str | None
    decision: str
    reason: str


class ListMembersResponse(BaseModel):
    """成员列表响应。"""

    members: list[EvidenceMemberResponse]


class RecipeResponse(BaseModel):
    """配方响应。"""

    recipe_id: str
    code: str
    display_name: str
    status: str
    version: int = 0


class RecipeCreatedResponse(BaseModel):
    """创建配方响应。"""

    recipe_id: str
    code: str
    display_name: str
    status: str


class RecipeVersionResponse(BaseModel):
    """配方版本响应。"""

    id: str
    recipe_id: str
    version: int
    component_name: str
    component_version: str
    parameters: dict[str, Any]
    random_seed: int
    output_definitions: list[str]
    status: str


class RecipeListResponse(BaseModel):
    """配方分页列表响应。"""

    items: list[RecipeResponse]
    next_cursor: str | None


class ParameterCandidateOutputResponse(BaseModel):
    """推导输出候选响应。"""

    variable_code: str
    value: str
    unit: str | None
    confidence: float
    exclusion_reasons: list[str]


class DerivationRunResponse(BaseModel):
    """推导运行响应。"""

    id: str
    status: str
    output_digest: str
    outputs: list[ParameterCandidateOutputResponse]


class DerivationRunListResponse(BaseModel):
    """推导运行分页列表响应。"""

    items: list[DerivationRunResponse]
    next_cursor: str | None


class ProvenanceNodeResponse(BaseModel):
    """溯源图节点响应。"""

    id: str
    node_type: str
    label: str
    version: str
    status: str


class ProvenanceEdgeResponse(BaseModel):
    """溯源图边响应。"""

    source_id: str
    source_type: str
    target_id: str
    target_type: str
    edge_type: str


class ProvenanceGraphResponse(BaseModel):
    """溯源图响应。"""

    nodes: list[ProvenanceNodeResponse]
    edges: list[ProvenanceEdgeResponse]


# ---- 辅助函数 ----


def _member_to_response(m: EvidenceMember) -> EvidenceMemberResponse:
    """将 EvidenceMember 转为响应模型。"""
    return EvidenceMemberResponse(
        fact_id=str(m.fact_id),
        observation_id=str(m.observation_id) if m.observation_id else None,
        decision=m.decision,
        reason=m.reason,
    )


def _output_to_response(out: Any) -> ParameterCandidateOutputResponse:
    """将 ParameterCandidateOutput 转为响应模型。"""
    return ParameterCandidateOutputResponse(
        variable_code=out.variable_code,
        value=str(out.value),
        unit=out.unit,
        confidence=out.confidence,
        exclusion_reasons=list(out.exclusion_reasons),
    )


def _run_to_response(ref: DerivationRunRef) -> DerivationRunResponse:
    """将 DerivationRunRef 转为响应模型。"""
    return DerivationRunResponse(
        id=str(ref.id),
        status=ref.status,
        output_digest=ref.output_digest,
        outputs=[_output_to_response(o) for o in ref.outputs],
    )


# ---- 证据集端点 ----


@provenance_router.post(
    "/evidence-sets",
    response_model=EvidenceSetCreatedResponse,
    status_code=201,
)
async def create_evidence_set(
    body: CreateEvidenceSetRequest,
    current_user: WriteUserDep,
    service: EvidenceServiceDep,
) -> EvidenceSetCreatedResponse:
    """创建空的证据集（draft 状态）。"""
    result = await service.create_set(body.name)
    return EvidenceSetCreatedResponse(
        set_id=str(result["set_id"]),
        name=result["name"],
        status=result["status"],
    )


@provenance_router.post(
    "/evidence-sets/{set_id}/freeze",
    response_model=EvidenceSetVersionResponse,
)
async def freeze_evidence_set(
    set_id: UUID,
    body: FreezeEvidenceSetRequest,
    current_user: WriteUserDep,
    service: EvidenceServiceDep,
) -> EvidenceSetVersionResponse:
    """冻结证据集：创建不可变版本。"""
    ref: EvidenceSetRef = await service.freeze(set_id, fact_filter=body.fact_filter)
    return EvidenceSetVersionResponse(
        set_id=str(ref.set_id),
        version=ref.version,
        version_id=str(ref.version_id),
        member_count=ref.member_count,
        status=ref.status,
    )


@provenance_router.get("/evidence-sets/{set_id}", response_model=EvidenceSetResponse)
async def get_evidence_set(
    set_id: UUID,
    current_user: ReadUserDep,
    service: EvidenceServiceDep,
) -> EvidenceSetResponse:
    """获取证据集详情。"""
    result = await service.get_set(set_id)
    return EvidenceSetResponse(
        set_id=str(result["set_id"]),
        name=result["name"],
        status=result["status"],
        version=result["version"],
        version_id=str(result["version_id"]) if result.get("version_id") else None,
        member_count=result["member_count"],
    )


@provenance_router.get(
    "/evidence-sets/{set_id}/members",
    response_model=ListMembersResponse,
)
async def list_evidence_members(
    set_id: UUID,
    current_user: ReadUserDep,
    service: EvidenceServiceDep,
    version: int | None = Query(None, description="版本号，None 表示最新"),
) -> ListMembersResponse:
    """列出证据集版本的成员。"""
    members = await service.list_members(set_id, version=version)
    return ListMembersResponse(members=[_member_to_response(m) for m in members])


# ---- 配方端点 ----


@provenance_router.post("/recipes", response_model=RecipeCreatedResponse, status_code=201)
async def create_recipe(
    body: CreateRecipeRequest,
    current_user: WriteUserDep,
    service: RecipeServiceDep,
) -> RecipeCreatedResponse:
    """创建推导配方（draft 状态）。"""
    result = await service.create_recipe(body.code, body.display_name)
    return RecipeCreatedResponse(
        recipe_id=str(result["recipe_id"]),
        code=result["code"],
        display_name=result["display_name"],
        status=result["status"],
    )


@provenance_router.post(
    "/recipes/{recipe_id}/publish",
    response_model=RecipeVersionResponse,
)
async def publish_recipe_version(
    recipe_id: UUID,
    body: PublishRecipeVersionRequest,
    current_user: PublishUserDep,
    service: RecipeServiceDep,
) -> RecipeVersionResponse:
    """发布配方版本（不可变）。"""
    rv: RecipeVersion = await service.publish_version(
        recipe_id=recipe_id,
        component_name=body.component_name,
        component_version=body.component_version,
        parameters=body.parameters,
        random_seed=body.random_seed,
        output_definitions=tuple(body.output_definitions),
    )
    return RecipeVersionResponse(
        id=str(rv.id),
        recipe_id=str(rv.recipe_id),
        version=rv.version,
        component_name=rv.component_name,
        component_version=rv.component_version,
        parameters=rv.parameters,
        random_seed=rv.random_seed,
        output_definitions=list(rv.output_definitions),
        status=rv.status,
    )


@provenance_router.get("/recipes", response_model=RecipeListResponse)
async def list_recipes(
    current_user: ReadUserDep,
    service: RecipeServiceDep,
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> RecipeListResponse:
    """分页列出配方。"""
    items, next_cursor = await service.list_recipes(cursor=cursor, page_size=page_size)
    return RecipeListResponse(
        items=[
            RecipeResponse(
                recipe_id=str(item["recipe_id"]),
                code=item["code"],
                display_name=item["display_name"],
                status=item["status"],
                version=item.get("version", 0),
            )
            for item in items
        ],
        next_cursor=next_cursor,
    )


@provenance_router.get("/recipes/{recipe_id}", response_model=RecipeResponse)
async def get_recipe(
    recipe_id: UUID,
    current_user: ReadUserDep,
    service: RecipeServiceDep,
) -> RecipeResponse:
    """获取配方详情。"""
    result = await service.get_recipe(recipe_id)
    return RecipeResponse(
        recipe_id=str(result["recipe_id"]),
        code=result["code"],
        display_name=result["display_name"],
        status=result["status"],
        version=result.get("version", 0),
    )


# ---- 推导运行端点 ----


@provenance_router.post(
    "/derivation-runs",
    response_model=DerivationRunResponse,
    status_code=201,
)
async def create_derivation_run(
    body: CreateDerivationRunRequest,
    current_user: WriteUserDep,
    service: DerivationServiceDep,
) -> DerivationRunResponse:
    """创建推导运行（执行配方并产出参数候选）。"""
    ref = await service.create_run(
        evidence_set_version_id=body.evidence_set_version_id,
        recipe_version_id=body.recipe_version_id,
    )
    return _run_to_response(ref)


@provenance_router.post(
    "/derivation-runs/{run_id}/replay",
    response_model=DerivationRunResponse,
)
async def replay_derivation_run(
    run_id: UUID,
    current_user: WriteUserDep,
    service: DerivationServiceDep,
) -> DerivationRunResponse:
    """回放推导运行（相同输入和配方，产生相同输出摘要）。"""
    ref = await service.replay(run_id)
    return _run_to_response(ref)


@provenance_router.get("/derivation-runs/{run_id}", response_model=DerivationRunResponse)
async def get_derivation_run(
    run_id: UUID,
    current_user: ReadUserDep,
    service: DerivationServiceDep,
) -> DerivationRunResponse:
    """获取推导运行详情。"""
    ref = await service.get_run(run_id)
    return _run_to_response(ref)


@provenance_router.get("/derivation-runs", response_model=DerivationRunListResponse)
async def list_derivation_runs(
    current_user: ReadUserDep,
    service: DerivationServiceDep,
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> DerivationRunListResponse:
    """分页列出推导运行。"""
    refs, next_cursor = await service.list_runs(cursor=cursor, page_size=page_size)
    return DerivationRunListResponse(
        items=[_run_to_response(r) for r in refs],
        next_cursor=next_cursor,
    )


@provenance_router.get(
    "/derivation-runs/{run_id}/graph",
    response_model=ProvenanceGraphResponse,
)
async def get_provenance_graph(
    run_id: UUID,
    current_user: ReadUserDep,
    service: GraphServiceDep,
) -> ProvenanceGraphResponse:
    """获取推导运行的完整溯源图。"""
    graph: ProvenanceGraph = await service.get_graph(run_id)
    return ProvenanceGraphResponse(
        nodes=[
            ProvenanceNodeResponse(
                id=str(n.id),
                node_type=n.node_type,
                label=n.label,
                version=n.version,
                status=n.status,
            )
            for n in graph.nodes
        ],
        edges=[
            ProvenanceEdgeResponse(
                source_id=str(e.source_id),
                source_type=e.source_type,
                target_id=str(e.target_id),
                target_type=e.target_type,
                edge_type=e.edge_type,
            )
            for e in graph.edges
        ],
    )
