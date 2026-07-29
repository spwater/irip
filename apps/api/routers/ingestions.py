"""数据导入映射路由：预览 / 映射评分 / 映射配置生命周期。

端点（IRIP Task 13）：
  POST /api/v1/ingestions/preview                  — 预览数据源（ingestion:write）
  POST /api/v1/ingestions/mapping/rank             — 评分映射候选（ingestion:write）
  POST /api/v1/ingestions/mapping-profiles         — 创建草稿配置（ingestion:write）
  GET  /api/v1/ingestions/mapping-profiles         — 列表（ingestion:read）
  GET  /api/v1/ingestions/mapping-profiles/{id}    — 详情（ingestion:read）
  POST /api/v1/ingestions/mapping-profiles/{id}/submit  — 提交审核（ingestion:write）
  POST /api/v1/ingestions/mapping-profiles/{id}/publish — 发布（ingestion:publish）
  POST /api/v1/ingestions/mapping-profiles/{id}/reject  — 拒绝（ingestion:publish）

安全约定：
- 创建/提交/预览/评分需 require_permission("ingestion:write")；
- 列表/详情需 require_permission("ingestion:read")；
- 发布/拒绝需 require_permission("ingestion:publish")；
- 密钥凭据绝不返回（仅 secret_id 引用出现在 source_config 中）。
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.connectors.contracts import (
    ConnectorSource,
    MappingRule,
    PreviewTable,
)
from packages.connectors.mapping import (
    IngestionService,
    MappingProfileService,
    MappingService,
)

#: 路由实例。
ingestions_router = APIRouter(prefix="/api/v1/ingestions", tags=["ingestions"])

#: 需 ingestion:write 权限的当前用户依赖。
WriteUserDep = Annotated[CurrentUser, Depends(require_permission("ingestion:write"))]

#: 需 ingestion:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("ingestion:read"))]

#: 需 ingestion:publish 权限的当前用户依赖。
PublishUserDep = Annotated[CurrentUser, Depends(require_permission("ingestion:publish"))]


def get_mapping_service() -> MappingService:
    """获取 MappingService 实例（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入按请求构造的实例
    （需当前用户上下文查询 organization_id）。
    """
    raise NotImplementedError("get_mapping_service must be overridden via dependency_overrides")


def get_mapping_profile_service() -> MappingProfileService:
    """获取 MappingProfileService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_mapping_profile_service must be overridden via dependency_overrides"
    )


def get_ingestion_service() -> IngestionService:
    """获取 IngestionService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_ingestion_service must be overridden via dependency_overrides")


#: 服务依赖类型别名。
MappingServiceDep = Annotated[MappingService, Depends(get_mapping_service)]
MappingProfileServiceDep = Annotated[MappingProfileService, Depends(get_mapping_profile_service)]
IngestionServiceDep = Annotated[IngestionService, Depends(get_ingestion_service)]


# ---- 请求模型 ----


class SourceFileConfig(BaseModel):
    """文件数据源配置。"""

    path: str = Field(..., min_length=1)
    format: Literal["csv", "xlsx", "json"]


class SourcePostgresConfig(BaseModel):
    """PostgreSQL 数据源配置（仅 secret_id 引用）。"""

    secret_id: str = Field(..., description="密钥 UUID 引用")
    query: str = Field(..., min_length=1)


class SourceRestConfig(BaseModel):
    """REST 数据源配置（仅 secret_id 引用）。"""

    secret_id: str = Field(..., description="密钥 UUID 引用")
    path: str = Field(..., min_length=1)
    method: Literal["GET", "POST"] = "GET"


class SourceSpec(BaseModel):
    """数据源描述。"""

    kind: Literal["file", "postgres", "rest"]
    file: SourceFileConfig | None = None
    postgres: SourcePostgresConfig | None = None
    rest: SourceRestConfig | None = None


class PreviewRequest(BaseModel):
    """预览请求。"""

    source: SourceSpec
    limit: int = Field(100, ge=1, le=10000)


class RankRequest(BaseModel):
    """映射评分请求。"""

    source_name: str = Field(..., min_length=1)
    source_unit: str | None = Field(None)
    data_type: Literal["number", "text", "boolean", "datetime"]


class RuleSpec(BaseModel):
    """映射规则请求。"""

    source_path: str = Field(..., min_length=1)
    target_variable_version_id: str = Field(..., description="目标变量版本 UUID")
    source_unit: str | None = Field(None)
    missing_policy: Literal["reject", "null", "default"] = "reject"
    default_value: str | None = Field(None)


class CreateProfileRequest(BaseModel):
    """创建映射配置请求。"""

    name: str = Field(..., min_length=1, max_length=200)
    source: SourceSpec
    rules: list[RuleSpec] = Field(..., min_length=1)


class UpdateRulesRequest(BaseModel):
    """更新规则请求。"""

    rules: list[RuleSpec] = Field(..., min_length=1)


# ---- 响应模型 ----


class PreviewRowResponse(BaseModel):
    """预览行响应（值序列化为字符串）。"""

    values: list[str | None]


class PreviewResponse(BaseModel):
    """预览响应。"""

    columns: list[str]
    rows: list[PreviewRowResponse]
    row_count: int


class CandidateReasons(BaseModel):
    """映射候选响应。"""

    variable_code: str
    variable_version_id: str
    score: float
    reasons: list[str]


class RankResponse(BaseModel):
    """映射评分响应。"""

    candidates: list[CandidateReasons]


class ProfileVersionResponse(BaseModel):
    """配置版本响应。"""

    id: str
    profile_id: str
    version: int
    rules: list[RuleSpec]
    status: str
    published_at: datetime | None
    lock_version: int
    created_at: datetime


class ProfileDetailResponse(BaseModel):
    """配置详情响应。"""

    id: str
    organization_id: str
    name: str
    source_kind: str
    source_config: dict
    status: str
    lock_version: int
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    version: ProfileVersionResponse


class ProfileListResponse(BaseModel):
    """配置列表响应。"""

    items: list[ProfileDetailResponse]
    next_cursor: str | None


# ---- 辅助函数 ----


def _source_to_contract(spec: SourceSpec) -> ConnectorSource:
    """将请求 SourceSpec 转为 ConnectorSource。"""
    if spec.kind == "file":
        config: dict = spec.file.model_dump() if spec.file else {}
    elif spec.kind == "postgres":
        config = spec.postgres.model_dump() if spec.postgres else {}
    elif spec.kind == "rest":
        config = spec.rest.model_dump() if spec.rest else {}
    else:
        config = {}
    return ConnectorSource(kind=spec.kind, config=config)


def _rules_to_contract(rules: list[RuleSpec]) -> list[MappingRule]:
    """将请求规则转为 MappingRule 列表。"""
    return [
        MappingRule(
            source_path=r.source_path,
            target_variable_version_id=UUID(r.target_variable_version_id),
            source_unit=r.source_unit,
            missing_policy=r.missing_policy,
            default_value=r.default_value,
        )
        for r in rules
    ]


def _rules_from_detail(rules: list) -> list[RuleSpec]:
    """从详情字典的规则列表转为 RuleSpec 响应。"""
    result: list[RuleSpec] = []
    for r in rules:
        if isinstance(r, MappingRule):
            result.append(
                RuleSpec(
                    source_path=r.source_path,
                    target_variable_version_id=str(r.target_variable_version_id),
                    source_unit=r.source_unit,
                    missing_policy=r.missing_policy,
                    default_value=r.default_value,
                )
            )
    return result


def _profile_to_response(detail: dict) -> ProfileDetailResponse:
    """将配置详情字典转为响应模型。"""
    version = detail["version"]
    return ProfileDetailResponse(
        id=detail["id"],
        organization_id=detail["organization_id"],
        name=detail["name"],
        source_kind=detail["source_kind"],
        source_config=detail["source_config"],
        status=detail["status"],
        lock_version=detail["lock_version"],
        created_at=detail["created_at"],
        updated_at=detail["updated_at"],
        created_by=detail["created_by"],
        version=ProfileVersionResponse(
            id=version["id"],
            profile_id=version["profile_id"],
            version=version["version"],
            rules=_rules_from_detail(version["rules"]),
            status=version["status"],
            published_at=version["published_at"],
            lock_version=version["lock_version"],
            created_at=version["created_at"],
        ),
    )


def _preview_to_response(table: PreviewTable) -> PreviewResponse:
    """将 PreviewTable 转为响应模型。"""
    return PreviewResponse(
        columns=list(table.columns),
        rows=[
            PreviewRowResponse(values=[None if v is None else str(v) for v in row])
            for row in table.rows
        ],
        row_count=table.row_count,
    )


# ---- 端点：预览 ----


@ingestions_router.post("/preview", response_model=PreviewResponse)
async def preview_source(
    body: PreviewRequest,
    current_user: WriteUserDep,
    service: IngestionServiceDep,
) -> PreviewResponse:
    """预览数据源（按 kind 构造连接器并读取前 limit 行）。

    Args:
        body: 预览请求（含 source 与 limit）。
        current_user: 当前认证用户（需 ingestion:write 权限）。
        service: 预览服务。

    Returns:
        PreviewResponse: 预览结果（列名 + 行 + 总行数）。
    """
    source = _source_to_contract(body.source)
    table = await service.preview(source, limit=body.limit)
    return _preview_to_response(table)


# ---- 端点：映射评分 ----


@ingestions_router.post("/mapping/rank", response_model=RankResponse)
async def rank_mapping(
    body: RankRequest,
    current_user: WriteUserDep,
    service: MappingServiceDep,
) -> RankResponse:
    """对源字段评分，返回按分数降序的已发布变量候选。

    Args:
        body: 评分请求（source_name / source_unit / data_type）。
        current_user: 当前认证用户（需 ingestion:write 权限）。
        service: 映射评分服务。

    Returns:
        RankResponse: 候选列表（按分数降序）。
    """
    candidates = await service.rank(
        source_name=body.source_name,
        source_unit=body.source_unit,
        data_type=body.data_type,
    )
    return RankResponse(
        candidates=[
            CandidateReasons(
                variable_code=c.variable_code,
                variable_version_id=str(c.variable_version_id),
                score=round(c.score, 4),
                reasons=list(c.reasons),
            )
            for c in candidates
        ]
    )


# ---- 端点：映射配置 CRUD / 生命周期 ----


@ingestions_router.post("/mapping-profiles", response_model=ProfileDetailResponse, status_code=201)
async def create_profile(
    body: CreateProfileRequest,
    current_user: WriteUserDep,
    service: MappingProfileServiceDep,
) -> ProfileDetailResponse:
    """创建草稿映射配置。

    Args:
        body: 创建请求（name / source / rules）。
        current_user: 当前认证用户（需 ingestion:write 权限）。
        service: 映射配置服务。

    Returns:
        ProfileDetailResponse: 新创建的配置详情（201 Created）。
    """
    detail = await service.create_profile(
        name=body.name,
        source=body.source.model_dump(exclude_none=True),
        rules=_rules_to_contract(body.rules),
    )
    return _profile_to_response(detail)


@ingestions_router.get("/mapping-profiles", response_model=ProfileListResponse)
async def list_profiles(
    current_user: ReadUserDep,
    service: MappingProfileServiceDep,
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> ProfileListResponse:
    """分页查询映射配置列表。

    Args:
        current_user: 当前认证用户（需 ingestion:read 权限）。
        service: 映射配置服务。
        cursor: 分页游标。
        page_size: 每页数量。

    Returns:
        ProfileListResponse: 分页列表。
    """
    items, next_cursor = await service.list_profiles(cursor=cursor, page_size=page_size)
    return ProfileListResponse(
        items=[_profile_to_response(item) for item in items],
        next_cursor=next_cursor,
    )


@ingestions_router.get("/mapping-profiles/{profile_id}", response_model=ProfileDetailResponse)
async def get_profile(
    profile_id: UUID,
    current_user: ReadUserDep,
    service: MappingProfileServiceDep,
) -> ProfileDetailResponse:
    """查询单个映射配置详情。

    Args:
        profile_id: 配置 UUID。
        current_user: 当前认证用户（需 ingestion:read 权限）。
        service: 映射配置服务。

    Returns:
        ProfileDetailResponse: 配置详情（含规则）。
    """
    detail = await service.get_profile(profile_id)
    return _profile_to_response(detail)


@ingestions_router.post(
    "/mapping-profiles/{profile_id}/submit", response_model=ProfileDetailResponse
)
async def submit_profile(
    profile_id: UUID,
    current_user: WriteUserDep,
    service: MappingProfileServiceDep,
) -> ProfileDetailResponse:
    """提交审核（DRAFT → IN_REVIEW）。

    Args:
        profile_id: 配置 UUID。
        current_user: 当前认证用户（需 ingestion:write 权限）。
        service: 映射配置服务。

    Returns:
        ProfileDetailResponse: 更新后的配置详情。
    """
    detail = await service.submit_profile(profile_id)
    return _profile_to_response(detail)


@ingestions_router.post(
    "/mapping-profiles/{profile_id}/publish", response_model=ProfileDetailResponse
)
async def publish_profile(
    profile_id: UUID,
    current_user: PublishUserDep,
    service: MappingProfileServiceDep,
) -> ProfileDetailResponse:
    """发布配置（IN_REVIEW → PUBLISHED，规则此后不可变）。

    Args:
        profile_id: 配置 UUID。
        current_user: 当前认证用户（需 ingestion:publish 权限）。
        service: 映射配置服务。

    Returns:
        ProfileDetailResponse: 已发布的配置详情。
    """
    detail = await service.publish_profile(profile_id)
    return _profile_to_response(detail)


@ingestions_router.post(
    "/mapping-profiles/{profile_id}/reject", response_model=ProfileDetailResponse
)
async def reject_profile(
    profile_id: UUID,
    current_user: PublishUserDep,
    service: MappingProfileServiceDep,
) -> ProfileDetailResponse:
    """拒绝配置（IN_REVIEW → REJECTED）。

    Args:
        profile_id: 配置 UUID。
        current_user: 当前认证用户（需 ingestion:publish 权限）。
        service: 映射配置服务。

    Returns:
        ProfileDetailResponse: 已拒绝的配置详情。
    """
    detail = await service.reject_profile(profile_id)
    return _profile_to_response(detail)
