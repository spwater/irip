"""事实管理路由（IRIP Task 15）。

端点分组（facts_router, prefix=/api/v1/facts）：
  POST   /api/v1/facts                        — 创建事实（fact:write）
  GET    /api/v1/facts                        — 列表过滤（fact:read）
  GET    /api/v1/facts/search?q=              — 全文搜索（fact:read）
  GET    /api/v1/facts/{id}                   — 获取最新修订（fact:read）
  GET    /api/v1/facts/{id}/revisions         — 列出所有修订（fact:read）
  GET    /api/v1/facts/{id}/revisions/{r}    — 获取特定修订（fact:read）
  GET    /api/v1/facts/{id}/observations      — 获取观察值（fact:read）
  POST   /api/v1/facts/{id}/revise           — 创建新修订（fact:write）
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.facts.observations import (
    FactRevisionRef,
    NormalizedObservation,
    RawObservation,
)
from packages.facts.service import CreateFactCommand, FactService

#: 需 fact:write 权限的当前用户依赖。
WriteUserDep = Annotated[
    CurrentUser, Depends(require_permission("fact:write"))
]

#: 需 fact:read 权限的当前用户依赖。
ReadUserDep = Annotated[
    CurrentUser, Depends(require_permission("fact:read"))
]


# ---- DI 占位 ----


def get_fact_service() -> FactService:
    """获取 FactService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_fact_service must be overridden via dependency_overrides"
    )


FactServiceDep = Annotated[FactService, Depends(get_fact_service)]


# ---- 路由实例 ----

facts_router = APIRouter(prefix="/api/v1/facts", tags=["facts"])


# ---- 请求模型 ----


class RawObservationItem(BaseModel):
    """原始观察值请求项。"""

    source_path: str = Field(..., min_length=1, max_length=500)
    source_value: str = Field(..., min_length=1)
    source_unit: str | None = Field(None, max_length=64)
    source_name: str | None = Field(None, max_length=256)
    artifact_id: UUID | None = None
    id: UUID | None = Field(None, description="预生成 ID，用于标准化观察值引用")


class NormalizedObservationItem(BaseModel):
    """标准化观察值请求项。"""

    variable_version_id: UUID
    raw_observation_id: UUID | None = Field(
        None, description="原始观察值 ID（必须非空）"
    )
    value: str = Field(..., min_length=1)
    unit: str | None = Field(None, max_length=64)


class CreateFactRequest(BaseModel):
    """创建事实请求。"""

    fact_type: Literal[
        "experiment_run", "simulation_run", "document_record", "model_execution"
    ]
    template_version_id: UUID
    object_id: UUID
    subject_id: str = Field(..., min_length=1, max_length=256)
    started_at: datetime
    ended_at: datetime | None = None
    method_version_id: UUID | None = None
    raw: list[RawObservationItem] = Field(default_factory=list)
    normalized: list[NormalizedObservationItem] = Field(default_factory=list)
    artifacts: list[UUID] = Field(default_factory=list)
    idempotency_key: str | None = Field(None, max_length=256)


class ReviseFactRequest(BaseModel):
    """修订事实请求。"""

    reason: str = Field(..., min_length=1, max_length=2000)
    subject_id: str | None = Field(None, max_length=256)
    method_version_id: UUID | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    raw: list[RawObservationItem] | None = None
    normalized: list[NormalizedObservationItem] | None = None
    artifacts: list[UUID] | None = None


# ---- 响应模型 ----


class FactRevisionResponse(BaseModel):
    """事实修订响应。"""

    fact_id: str
    revision: int
    revision_id: str
    fact_type: str
    subject_id: str
    status: str


class FactListResponse(BaseModel):
    """事实分页列表响应。"""

    items: list[FactRevisionResponse]
    next_cursor: str | None


class RawObservationResponse(BaseModel):
    """原始观察值响应。"""

    id: str
    fact_revision_id: str
    source_path: str
    source_value: str
    source_unit: str | None
    source_name: str | None
    artifact_id: str | None


class NormalizedObservationResponse(BaseModel):
    """标准化观察值响应。"""

    id: str
    fact_revision_id: str
    variable_version_id: str
    raw_observation_id: str
    value: str
    unit: str | None


class ObservationsResponse(BaseModel):
    """观察值响应。"""

    raw: list[RawObservationResponse]
    normalized: list[NormalizedObservationResponse]


# ---- 辅助函数 ----


def _ref_to_response(ref: FactRevisionRef) -> FactRevisionResponse:
    """将 FactRevisionRef 转为响应模型。"""
    return FactRevisionResponse(
        fact_id=str(ref.fact_id),
        revision=ref.revision,
        revision_id=str(ref.revision_id),
        fact_type=ref.fact_type,
        subject_id=ref.subject_id,
        status=ref.status,
    )


def _raw_to_response(r: RawObservation) -> RawObservationResponse:
    """将 RawObservation 转为响应模型。"""
    return RawObservationResponse(
        id=str(r.id),
        fact_revision_id=str(r.fact_revision_id),
        source_path=r.source_path,
        source_value=r.source_value,
        source_unit=r.source_unit,
        source_name=r.source_name,
        artifact_id=str(r.artifact_id) if r.artifact_id else None,
    )


def _normalized_to_response(
    n: NormalizedObservation,
) -> NormalizedObservationResponse:
    """将 NormalizedObservation 转为响应模型。"""
    return NormalizedObservationResponse(
        id=str(n.id),
        fact_revision_id=str(n.fact_revision_id),
        variable_version_id=str(n.variable_version_id),
        raw_observation_id=str(n.raw_observation_id),
        value=n.value,
        unit=n.unit,
    )


# ---- 端点 ----


@facts_router.post(
    "", response_model=FactRevisionResponse, status_code=201
)
async def create_fact(
    body: CreateFactRequest,
    current_user: WriteUserDep,
    service: FactServiceDep,
) -> FactRevisionResponse:
    """创建事实（revision 1）。

    创建一个新事实，包含原始与标准化观察值和工件链接。
    支持幂等键去重：相同 idempotency_key 不会创建重复事实。
    """
    from packages.facts.observations import (
        NormalizedObservationInput,
        RawObservationInput,
    )

    command = CreateFactCommand(
        fact_type=body.fact_type,
        template_version_id=body.template_version_id,
        organization_id=service._org_id,
        object_id=body.object_id,
        subject_id=body.subject_id,
        started_at=body.started_at,
        ended_at=body.ended_at,
        method_version_id=body.method_version_id,
        raw=tuple(
            RawObservationInput(
                source_path=r.source_path,
                source_value=r.source_value,
                source_unit=r.source_unit,
                source_name=r.source_name,
                artifact_id=r.artifact_id,
                id=r.id,
            )
            for r in body.raw
        ),
        normalized=tuple(
            NormalizedObservationInput(
                variable_version_id=n.variable_version_id,
                raw_observation_id=n.raw_observation_id,
                value=n.value,
                unit=n.unit,
            )
            for n in body.normalized
        ),
        artifacts=tuple(body.artifacts),
        idempotency_key=body.idempotency_key,
        created_by=current_user.user_id,
    )
    ref = await service.create(command)
    return _ref_to_response(ref)


@facts_router.get("", response_model=FactListResponse)
async def list_facts(
    current_user: ReadUserDep,
    service: FactServiceDep,
    fact_type: str | None = Query(None, description="按事实类型过滤"),
    object_id: UUID | None = Query(None, description="按工业对象过滤"),
    status: str | None = Query(None, description="按状态过滤"),
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> FactListResponse:
    """分页列出事实（支持按 fact_type, object_id, status 过滤）。"""
    filters: dict = {}
    if fact_type is not None:
        filters["fact_type"] = fact_type
    if object_id is not None:
        filters["object_id"] = object_id
    if status is not None:
        filters["status"] = status

    refs, next_cursor = await service.list_facts(
        filters=filters if filters else None,
        cursor=cursor,
        page_size=page_size,
    )
    return FactListResponse(
        items=[_ref_to_response(r) for r in refs],
        next_cursor=next_cursor,
    )


@facts_router.get("/search", response_model=FactListResponse)
async def search_facts(
    current_user: ReadUserDep,
    service: FactServiceDep,
    q: str = Query(..., min_length=1, description="搜索查询"),
    fact_type: str | None = Query(None, description="按事实类型过滤"),
    object_id: UUID | None = Query(None, description="按工业对象过滤"),
    status: str | None = Query(None, description="按状态过滤"),
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> FactListResponse:
    """全文搜索事实（基于 subject_id 和 fact_type 的 tsvector）。"""
    filters: dict = {}
    if fact_type is not None:
        filters["fact_type"] = fact_type
    if object_id is not None:
        filters["object_id"] = object_id
    if status is not None:
        filters["status"] = status

    refs, next_cursor = await service.search(
        query=q,
        filters=filters if filters else None,
        cursor=cursor,
        page_size=page_size,
    )
    return FactListResponse(
        items=[_ref_to_response(r) for r in refs],
        next_cursor=next_cursor,
    )


@facts_router.get("/{fact_id}", response_model=FactRevisionResponse)
async def get_fact(
    fact_id: UUID,
    current_user: ReadUserDep,
    service: FactServiceDep,
) -> FactRevisionResponse:
    """获取事实的最新修订。"""
    ref = await service.get(fact_id)
    return _ref_to_response(ref)


@facts_router.get(
    "/{fact_id}/revisions", response_model=FactListResponse
)
async def list_revisions(
    fact_id: UUID,
    current_user: ReadUserDep,
    service: FactServiceDep,
) -> FactListResponse:
    """列出事实的所有修订历史。"""
    refs = await service.list_revisions(fact_id)
    return FactListResponse(
        items=[_ref_to_response(r) for r in refs],
        next_cursor=None,
    )


@facts_router.get(
    "/{fact_id}/revisions/{revision}",
    response_model=FactRevisionResponse,
)
async def get_revision(
    fact_id: UUID,
    revision: int,
    current_user: ReadUserDep,
    service: FactServiceDep,
) -> FactRevisionResponse:
    """获取事实的特定修订。"""
    ref = await service.get(fact_id, revision=revision)
    return _ref_to_response(ref)


@facts_router.get(
    "/{fact_id}/observations", response_model=ObservationsResponse
)
async def get_observations(
    fact_id: UUID,
    current_user: ReadUserDep,
    service: FactServiceDep,
    revision: int | None = Query(None, description="修订号，None 表示最新"),
) -> ObservationsResponse:
    """获取事实的观察值（原始 + 标准化）。"""
    raws, norms = await service.get_observations(fact_id, revision=revision)
    return ObservationsResponse(
        raw=[_raw_to_response(r) for r in raws],
        normalized=[_normalized_to_response(n) for n in norms],
    )


@facts_router.get("/{fact_id}/data")
async def get_fact_data(
    fact_id: UUID,
    current_user: ReadUserDep,
    service: FactServiceDep,
) -> dict:
    """获取事实关联的提取数据（从 artifact 下载 JSON）。

    返回 {"metadata": {...}, "data": [...]} 格式的干净数据。
    """
    import json as json_mod
    import sqlalchemy as sa
    from packages.facts.entities import FactArtifact, FactRevision
    from packages.common.artifacts import ArtifactService
    from apps.api.main import _build_s3_repo

    # 获取最新修订
    fact = await service.get(fact_id)
    revision_id = fact.revision_id

    async with service._factory() as session:
        # 查 fact_artifact + artifact，找 JSON 类型的（提取数据）
        from packages.common.artifacts import Artifact
        result = await session.execute(
            sa.select(FactArtifact, Artifact)
            .where(
                FactArtifact.fact_revision_id == revision_id,
                FactArtifact.artifact_id == Artifact.id,
                Artifact.media_type == "application/json",
            )
            .limit(1)
        )
        row = result.first()
        if row is None:
            return {"metadata": {}, "data": []}

        fa = row[0]
        # 下载 artifact 内容
        s3_repo = _build_s3_repo()
        artifact_svc = ArtifactService(
            s3_repo=s3_repo,
            session_factory=service._factory,
            organization_id=service._org_id,
            uploaded_by=current_user.user_id,
        )
        data_bytes = await artifact_svc.get_bytes(fa.artifact_id)
        return json_mod.loads(data_bytes.decode("utf-8"))


@facts_router.post(
    "/{fact_id}/revise", response_model=FactRevisionResponse
)
async def revise_fact(
    fact_id: UUID,
    body: ReviseFactRequest,
    current_user: WriteUserDep,
    service: FactServiceDep,
) -> FactRevisionResponse:
    """创建事实的新修订（旧修订不可变）。"""
    from packages.facts.observations import (
        NormalizedObservationInput,
        RawObservationInput,
    )

    changes: dict = {"reason": body.reason}
    if body.subject_id is not None:
        changes["subject_id"] = body.subject_id
    if body.method_version_id is not None:
        changes["method_version_id"] = body.method_version_id
    if body.started_at is not None:
        changes["started_at"] = body.started_at
    if body.ended_at is not None:
        changes["ended_at"] = body.ended_at
    if body.raw is not None:
        changes["raw"] = tuple(
            RawObservationInput(
                source_path=r.source_path,
                source_value=r.source_value,
                source_unit=r.source_unit,
                source_name=r.source_name,
                artifact_id=r.artifact_id,
                id=r.id,
            )
            for r in body.raw
        )
    if body.normalized is not None:
        changes["normalized"] = tuple(
            NormalizedObservationInput(
                variable_version_id=n.variable_version_id,
                raw_observation_id=n.raw_observation_id,
                value=n.value,
                unit=n.unit,
            )
            for n in body.normalized
        )
    if body.artifacts is not None:
        changes["artifacts"] = tuple(body.artifacts)

    ref = await service.revise(fact_id, reason=body.reason, changes=changes)
    return _ref_to_response(ref)
