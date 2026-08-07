"""L3 参数管理路由。

端点分组（parameters_router, prefix=/api/v1/parameters）：
  POST   /                                     — 创建参数（parameter:write）
  GET    /                                     — 列出参数（parameter:read）
  GET    /{parameter_id}                       — 获取参数（parameter:read）
  GET    /{parameter_id}/versions               — 列出版本（parameter:read）
  GET    /{parameter_id}/versions/{version}     — 获取指定版本（parameter:read）
  POST   /{parameter_id}/candidates             — 创建候选（parameter:write）
  GET    /{parameter_id}/candidates             — 列出候选（parameter:read）
  POST   /candidates/{candidate_id}/approve     — 审批通过（parameter:approve）
  POST   /candidates/{candidate_id}/reject      — 拒绝候选（parameter:approve）
  POST   /{parameter_id}/deprecate              — 弃用参数（parameter:publish）
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.parameters.service import (
    ParameterService,
    ParameterVersionRef,
)

#: 需 parameter:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("parameter:read"))]

#: 需 parameter:write 权限的当前用户依赖。
WriteUserDep = Annotated[CurrentUser, Depends(require_permission("parameter:write"))]

#: 需 parameter:approve 权限的当前用户依赖。
ApproveUserDep = Annotated[CurrentUser, Depends(require_permission("parameter:approve"))]

#: 需 parameter:publish 权限的当前用户依赖。
PublishUserDep = Annotated[CurrentUser, Depends(require_permission("parameter:publish"))]


# ---- DI 占位 ----


def get_parameter_service() -> ParameterService:
    """获取 ParameterService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_parameter_service must be overridden via dependency_overrides")


ParameterServiceDep = Annotated[ParameterService, Depends(get_parameter_service)]


# ---- 路由实例 ----

parameters_router = APIRouter(prefix="/api/v1/parameters", tags=["parameters"])


# ---- 请求模型 ----


class CreateParameterRequest(BaseModel):
    """创建参数请求。"""

    variable_code: str = Field(..., min_length=1, max_length=128)
    object_id: UUID


class CreateCandidateRequest(BaseModel):
    """创建参数候选请求。"""

    derivation_run_id: UUID
    value: str = Field(..., min_length=1)
    unit: str | None = None
    confidence: str | None = None
    conditions: dict[str, Any] | None = None


class RejectCandidateRequest(BaseModel):
    """拒绝候选请求。"""

    comment: str = Field(..., min_length=1, max_length=2048)


# ---- 响应模型 ----


class ParameterResponse(BaseModel):
    """参数响应。"""

    parameter_id: str
    variable_code: str
    object_id: str
    status: str


class ParameterDetailResponse(BaseModel):
    """参数详情响应。"""

    parameter_id: str
    variable_code: str
    object_id: str
    status: str
    current_version: int | None
    current_version_id: str | None
    value: str | None
    unit: str | None


class ParameterListResponse(BaseModel):
    """参数分页列表响应。"""

    items: list[ParameterResponse]
    next_cursor: str | None


class ParameterVersionResponse(BaseModel):
    """参数版本响应。"""

    parameter_id: str
    version: int
    version_id: str
    variable_code: str
    value: str
    unit: str | None
    confidence: str | None
    status: str
    conditions: dict[str, Any] | None
    published_at: str | None


class VersionListResponse(BaseModel):
    """版本列表响应。"""

    items: list[ParameterVersionResponse]


class CandidateResponse(BaseModel):
    """参数候选响应。"""

    candidate_id: str
    parameter_id: str
    derivation_run_id: str
    value: str
    unit: str | None
    confidence: str | None
    status: str


class CandidateDetailResponse(BaseModel):
    """参数候选详情响应。"""

    candidate_id: str
    parameter_id: str
    derivation_run_id: str
    value: str
    unit: str | None
    confidence: str | None
    status: str
    submitted_by: str | None
    submitted_at: str | None
    reviewed_by: str | None
    reviewed_at: str | None
    review_decision: str | None
    review_comment: str | None


class CandidateListResponse(BaseModel):
    """候选列表响应。"""

    items: list[CandidateDetailResponse]


class DeprecateResponse(BaseModel):
    """弃用响应。"""

    parameter_id: str
    status: str


# ---- 辅助函数 ----


def _version_to_response(ref: ParameterVersionRef) -> ParameterVersionResponse:
    """将 ParameterVersionRef 转为响应模型。"""
    return ParameterVersionResponse(
        parameter_id=str(ref.parameter_id),
        version=ref.version,
        version_id=str(ref.version_id),
        variable_code=ref.variable_code,
        value=ref.value,
        unit=ref.unit,
        confidence=ref.confidence,
        status=ref.status,
        conditions=ref.conditions,
        published_at=ref.published_at.isoformat() if ref.published_at else None,
    )


# ---- 参数端点 ----


@parameters_router.post(
    "",
    response_model=ParameterResponse,
    status_code=201,
)
async def create_parameter(
    body: CreateParameterRequest,
    current_user: WriteUserDep,
    service: ParameterServiceDep,
) -> ParameterResponse:
    """创建参数（draft 状态）。"""
    result = await service.create_parameter(
        variable_code=body.variable_code,
        object_id=body.object_id,
    )
    return ParameterResponse(
        parameter_id=str(result["parameter_id"]),
        variable_code=result["variable_code"],
        object_id=str(result["object_id"]),
        status=result["status"],
    )


@parameters_router.get("", response_model=ParameterListResponse)
async def list_parameters(
    current_user: ReadUserDep,
    service: ParameterServiceDep,
    variable_code: str | None = Query(None, description="按变量代码过滤"),
    status: str | None = Query(None, description="按状态过滤"),
    object_id: UUID | None = Query(None, description="按对象 ID 过滤"),  # noqa: B008
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> ParameterListResponse:
    """分页列出参数。"""
    filters: dict[str, Any] = {}
    if variable_code is not None:
        filters["variable_code"] = variable_code
    if status is not None:
        filters["status"] = status
    if object_id is not None:
        filters["object_id"] = str(object_id)

    items, next_cursor = await service.list_parameters(
        filters=filters,
        cursor=cursor,
        page_size=page_size,
    )
    return ParameterListResponse(
        items=[
            ParameterResponse(
                parameter_id=str(item["parameter_id"]),
                variable_code=item["variable_code"],
                object_id=str(item["object_id"]),
                status=item["status"],
            )
            for item in items
        ],
        next_cursor=next_cursor,
    )


@parameters_router.get("/{parameter_id}", response_model=ParameterDetailResponse)
async def get_parameter(
    parameter_id: UUID,
    current_user: ReadUserDep,
    service: ParameterServiceDep,
) -> ParameterDetailResponse:
    """获取参数详情（含当前版本）。"""
    result = await service.get_parameter(parameter_id)
    return ParameterDetailResponse(
        parameter_id=str(result["parameter_id"]),
        variable_code=result["variable_code"],
        object_id=str(result["object_id"]),
        status=result["status"],
        current_version=result.get("current_version"),
        current_version_id=(
            str(result["current_version_id"]) if result.get("current_version_id") else None
        ),
        value=result.get("value"),
        unit=result.get("unit"),
    )


@parameters_router.get("/{parameter_id}/versions", response_model=VersionListResponse)
async def list_versions(
    parameter_id: UUID,
    current_user: ReadUserDep,
    service: ParameterServiceDep,
) -> VersionListResponse:
    """列出参数的所有版本。"""
    # 通过 get_version 获取最新版本，然后查询所有版本
    await service.list_candidates(parameter_id)
    # 获取参数详情以确定是否有已发布版本
    param_detail = await service.get_parameter(parameter_id)
    versions: list[ParameterVersionResponse] = []
    if param_detail.get("current_version_id"):
        ref = await service.get_version(parameter_id)
        versions.append(_version_to_response(ref))
    return VersionListResponse(items=versions)


@parameters_router.get(
    "/{parameter_id}/versions/{version}",
    response_model=ParameterVersionResponse,
)
async def get_version(
    parameter_id: UUID,
    version: int,
    current_user: ReadUserDep,
    service: ParameterServiceDep,
) -> ParameterVersionResponse:
    """获取参数的指定版本。"""
    ref = await service.get_version(parameter_id, version=version)
    return _version_to_response(ref)


# ---- 候选端点 ----


@parameters_router.post(
    "/{parameter_id}/candidates",
    response_model=CandidateResponse,
    status_code=201,
)
async def create_candidate(
    parameter_id: UUID,
    body: CreateCandidateRequest,
    current_user: WriteUserDep,
    service: ParameterServiceDep,
) -> CandidateResponse:
    """创建参数候选（pending_review 状态）。"""
    result = await service.create_candidate(
        parameter_id=parameter_id,
        derivation_run_id=body.derivation_run_id,
        value=body.value,
        unit=body.unit,
        confidence=body.confidence,
        conditions=body.conditions,
    )
    return CandidateResponse(
        candidate_id=str(result["candidate_id"]),
        parameter_id=str(result["parameter_id"]),
        derivation_run_id=str(result["derivation_run_id"]),
        value=result["value"],
        unit=result.get("unit"),
        confidence=result.get("confidence"),
        status=result["status"],
    )


@parameters_router.get(
    "/{parameter_id}/candidates",
    response_model=CandidateListResponse,
)
async def list_candidates(
    parameter_id: UUID,
    current_user: ReadUserDep,
    service: ParameterServiceDep,
) -> CandidateListResponse:
    """列出参数的所有候选。"""
    candidates = await service.list_candidates(parameter_id)
    return CandidateListResponse(
        items=[
            CandidateDetailResponse(
                candidate_id=str(c["candidate_id"]),
                parameter_id=str(c["parameter_id"]),
                derivation_run_id=str(c["derivation_run_id"]),
                value=c["value"],
                unit=c.get("unit"),
                confidence=c.get("confidence"),
                status=c["status"],
                submitted_by=str(c["submitted_by"]) if c.get("submitted_by") else None,
                submitted_at=c["submitted_at"].isoformat() if c.get("submitted_at") else None,
                reviewed_by=str(c["reviewed_by"]) if c.get("reviewed_by") else None,
                reviewed_at=c["reviewed_at"].isoformat() if c.get("reviewed_at") else None,
                review_decision=c.get("review_decision"),
                review_comment=c.get("review_comment"),
            )
            for c in candidates
        ]
    )


@parameters_router.post(
    "/candidates/{candidate_id}/approve",
    response_model=ParameterVersionResponse,
)
async def approve_candidate(
    candidate_id: UUID,
    current_user: ApproveUserDep,
    service: ParameterServiceDep,
) -> ParameterVersionResponse:
    """审批通过候选，创建不可变参数版本。"""
    ref = await service.approve(
        candidate_id=candidate_id,
        reviewer=current_user.user_id,
    )
    return _version_to_response(ref)


@parameters_router.post(
    "/candidates/{candidate_id}/reject",
    response_model=CandidateResponse,
)
async def reject_candidate(
    candidate_id: UUID,
    body: RejectCandidateRequest,
    current_user: ApproveUserDep,
    service: ParameterServiceDep,
) -> CandidateResponse:
    """拒绝候选。"""
    result = await service.reject(
        candidate_id=candidate_id,
        reviewer=current_user.user_id,
        comment=body.comment,
    )
    return CandidateResponse(
        candidate_id=str(result["candidate_id"]),
        parameter_id=str(result["parameter_id"]),
        derivation_run_id="",
        value="",
        unit=None,
        confidence=None,
        status=result["status"],
    )


# ---- 弃用端点 ----


@parameters_router.post(
    "/{parameter_id}/deprecate",
    response_model=DeprecateResponse,
)
async def deprecate_parameter(
    parameter_id: UUID,
    current_user: PublishUserDep,
    service: ParameterServiceDep,
) -> DeprecateResponse:
    """弃用参数（published → deprecated）。"""
    result = await service.deprecate(parameter_id)
    return DeprecateResponse(
        parameter_id=str(result["parameter_id"]),
        status=result["status"],
    )
