"""事实模板、方法与标准包管理路由（IRIP Task 12）。

端点分三组：

模板（templates_router, prefix=/api/v1/templates）：
  POST   /api/v1/templates                  — 创建模板（standard:write）
  GET    /api/v1/templates                  — 列表（standard:read）
  GET    /api/v1/templates/{id}             — 详情（standard:read）
  POST   /api/v1/templates/{id}/observations — 添加观测（standard:write）
  POST   /api/v1/templates/{id}/submit       — 提交审核（standard:write）
  POST   /api/v1/templates/{id}/publish      — 发布（standard:publish）
  POST   /api/v1/templates/{id}/reject       — 拒绝（standard:publish）
  POST   /api/v1/templates/{id}/deprecate    — 弃用（standard:publish）

方法（methods_router, prefix=/api/v1/methods）：
  POST   /api/v1/methods                     — 创建方法（standard:write）
  GET    /api/v1/methods                     — 列表（standard:read）
  GET    /api/v1/methods/{id}                — 详情（standard:read）
  POST   /api/v1/methods/{id}/submit         — 提交（standard:write）
  POST   /api/v1/methods/{id}/publish        — 发布（standard:publish）

标准包（packages_router, prefix=/api/v1/packages）：
  POST   /api/v1/packages                    — 创建包（standard:write）
  GET    /api/v1/packages                    — 列表（standard:read）
  GET    /api/v1/packages/{id}               — 详情（standard:read）
  POST   /api/v1/packages/{id}/refs          — 添加引用（standard:write）
  POST   /api/v1/packages/{id}/submit        — 提交+验证（standard:write）
  POST   /api/v1/packages/{id}/publish        — 发布（standard:publish）
  POST   /api/v1/packages/{id}/reject        — 拒绝（standard:publish）
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.standards.methods import MethodService
from packages.standards.packages import PackageService
from packages.standards.templates import TemplateService

#: 需 standard:write 权限的当前用户依赖。
WriteUserDep = Annotated[
    CurrentUser, Depends(require_permission("standard:write"))
]

#: 需 standard:read 权限的当前用户依赖。
ReadUserDep = Annotated[
    CurrentUser, Depends(require_permission("standard:read"))
]

#: 需 standard:publish 权限的当前用户依赖。
PublishUserDep = Annotated[
    CurrentUser, Depends(require_permission("standard:publish"))
]

# ---- DI 占位 ----


def get_template_service() -> TemplateService:
    """获取 TemplateService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_template_service must be overridden via dependency_overrides"
    )


def get_method_service() -> MethodService:
    """获取 MethodService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_method_service must be overridden via dependency_overrides"
    )


def get_package_service() -> PackageService:
    """获取 PackageService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_package_service must be overridden via dependency_overrides"
    )


TemplateServiceDep = Annotated[
    TemplateService, Depends(get_template_service)
]
MethodServiceDep = Annotated[
    MethodService, Depends(get_method_service)
]
PackageServiceDep = Annotated[
    PackageService, Depends(get_package_service)
]


# ---- 路由实例 ----

templates_router = APIRouter(prefix="/api/v1/templates", tags=["templates"])
methods_router = APIRouter(prefix="/api/v1/methods", tags=["methods"])
packages_router = APIRouter(prefix="/api/v1/packages", tags=["packages"])


# ---- 请求模型 ----


class CreateTemplateRequest(BaseModel):
    """创建事实模板请求。"""

    code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="模板编码，仅小写字母/数字/下划线",
    )
    display_name: str = Field(..., min_length=1, max_length=200)
    fact_type: Literal[
        "experiment_run",
        "simulation_run",
        "document_record",
        "model_execution",
    ]


class AddObservationRequest(BaseModel):
    """添加观测要求请求。"""

    variable_version_id: UUID = Field(..., description="标准变量版本 ID")
    required: bool = Field(True, description="是否必需")
    cardinality: Literal["one", "many"] = Field(
        "one", description="基数"
    )


class CreateMethodRequest(BaseModel):
    """创建方法请求。"""

    code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    display_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)


class CreatePackageRequest(BaseModel):
    """创建标准包请求。"""

    code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    display_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)


class AddPackageRefRequest(BaseModel):
    """添加包引用请求。"""

    ref_type: Literal["variable", "method", "template"] = Field(
        ..., description="引用类型"
    )
    ref_id: UUID = Field(..., description="被引用实体 ID")
    version: int = Field(..., ge=1, description="版本号")


class RejectRequest(BaseModel):
    """拒绝请求（模板/方法/包通用）。"""

    reason: str = Field(..., min_length=1, max_length=2000)


# ---- 响应模型 ----


class TemplateVersionResponse(BaseModel):
    """模板版本详情响应。"""

    id: str
    template_id: str
    version: int
    code: str
    display_name: str
    fact_type: str
    required_conditions: list[str]
    observations: list[dict[str, object]]
    required_artifact_roles: list[str]
    quality_rule_codes: list[str]
    status: str
    published_at: datetime | None
    published_by: str | None
    deprecated_at: datetime | None
    deprecated_by: str | None
    rejection_reason: str | None
    created_at: datetime
    lock_version: int


class TemplateDetailResponse(BaseModel):
    """模板详情响应。"""

    id: str
    organization_id: str
    code: str
    display_name: str
    fact_type: str
    status: str
    version_count: int
    created_at: datetime
    updated_at: datetime
    lock_version: int
    latest_version: TemplateVersionResponse | None


class TemplateListItem(BaseModel):
    """模板列表项。"""

    id: str
    code: str
    display_name: str
    fact_type: str
    status: str
    version_count: int
    created_at: datetime
    updated_at: datetime
    lock_version: int
    latest_version: TemplateVersionResponse | None


class TemplateListResponse(BaseModel):
    """模板分页列表响应。"""

    items: list[TemplateListItem]
    next_cursor: str | None


class MethodVersionResponse(BaseModel):
    """方法版本详情响应。"""

    id: str
    method_id: str
    version: int
    code: str
    display_name: str
    description: str | None
    status: str
    published_at: datetime | None
    published_by: str | None
    deprecated_at: datetime | None
    deprecated_by: str | None
    rejection_reason: str | None
    created_at: datetime
    lock_version: int


class MethodDetailResponse(BaseModel):
    """方法详情响应。"""

    id: str
    organization_id: str
    code: str
    display_name: str
    description: str | None
    status: str
    version_count: int
    created_at: datetime
    updated_at: datetime
    lock_version: int
    latest_version: MethodVersionResponse | None


class MethodListItem(BaseModel):
    """方法列表项。"""

    id: str
    code: str
    display_name: str
    description: str | None
    status: str
    version_count: int
    created_at: datetime
    updated_at: datetime
    lock_version: int
    latest_version: MethodVersionResponse | None


class MethodListResponse(BaseModel):
    """方法分页列表响应。"""

    items: list[MethodListItem]
    next_cursor: str | None


class PackageVersionResponse(BaseModel):
    """包版本详情响应。"""

    id: str
    package_id: str
    version: int
    code: str
    display_name: str
    description: str | None
    variable_refs: list[dict[str, object]]
    method_refs: list[dict[str, object]]
    template_refs: list[dict[str, object]]
    quality_rule_refs: list[dict[str, object]]
    status: str
    published_at: datetime | None
    published_by: str | None
    deprecated_at: datetime | None
    deprecated_by: str | None
    rejection_reason: str | None
    created_at: datetime
    lock_version: int


class PackageDetailResponse(BaseModel):
    """包详情响应。"""

    id: str
    organization_id: str
    code: str
    display_name: str
    description: str | None
    status: str
    version_count: int
    created_at: datetime
    updated_at: datetime
    lock_version: int
    latest_version: PackageVersionResponse | None


class PackageListItem(BaseModel):
    """包列表项。"""

    id: str
    code: str
    display_name: str
    description: str | None
    status: str
    version_count: int
    created_at: datetime
    updated_at: datetime
    lock_version: int
    latest_version: PackageVersionResponse | None


class PackageListResponse(BaseModel):
    """包分页列表响应。"""

    items: list[PackageListItem]
    next_cursor: str | None


class ValidationReportResponse(BaseModel):
    """验证报告响应。"""

    valid: bool
    codes: list[str]
    messages: list[str]


# ---- 辅助函数 ----


def _template_version_to_response(v: dict) -> TemplateVersionResponse:
    """将模板版本字典转为响应模型。"""
    return TemplateVersionResponse(
        id=v["id"],
        template_id=v["template_id"],
        version=v["version"],
        code=v["code"],
        display_name=v["display_name"],
        fact_type=v["fact_type"],
        required_conditions=v.get("required_conditions", []),
        observations=v.get("observations", []),
        required_artifact_roles=v.get("required_artifact_roles", []),
        quality_rule_codes=v.get("quality_rule_codes", []),
        status=v["status"],
        published_at=v["published_at"],
        published_by=v["published_by"],
        deprecated_at=v["deprecated_at"],
        deprecated_by=v["deprecated_by"],
        rejection_reason=v["rejection_reason"],
        created_at=v["created_at"],
        lock_version=v["lock_version"],
    )


def _method_version_to_response(v: dict) -> MethodVersionResponse:
    """将方法版本字典转为响应模型。"""
    return MethodVersionResponse(
        id=v["id"],
        method_id=v["method_id"],
        version=v["version"],
        code=v["code"],
        display_name=v["display_name"],
        description=v["description"],
        status=v["status"],
        published_at=v["published_at"],
        published_by=v["published_by"],
        deprecated_at=v["deprecated_at"],
        deprecated_by=v["deprecated_by"],
        rejection_reason=v["rejection_reason"],
        created_at=v["created_at"],
        lock_version=v["lock_version"],
    )


def _package_version_to_response(v: dict) -> PackageVersionResponse:
    """将包版本字典转为响应模型。"""
    return PackageVersionResponse(
        id=v["id"],
        package_id=v["package_id"],
        version=v["version"],
        code=v["code"],
        display_name=v["display_name"],
        description=v["description"],
        variable_refs=v.get("variable_refs", []),
        method_refs=v.get("method_refs", []),
        template_refs=v.get("template_refs", []),
        quality_rule_refs=v.get("quality_rule_refs", []),
        status=v["status"],
        published_at=v["published_at"],
        published_by=v["published_by"],
        deprecated_at=v["deprecated_at"],
        deprecated_by=v["deprecated_by"],
        rejection_reason=v["rejection_reason"],
        created_at=v["created_at"],
        lock_version=v["lock_version"],
    )


def _template_detail_to_response(d: dict) -> TemplateDetailResponse:
    """将模板详情字典转为响应模型。"""
    return TemplateDetailResponse(
        id=d["id"],
        organization_id=d["organization_id"],
        code=d["code"],
        display_name=d["display_name"],
        fact_type=d["fact_type"],
        status=d["status"],
        version_count=d["version_count"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        lock_version=d["lock_version"],
        latest_version=_template_version_to_response(d["latest_version"])
        if d.get("latest_version")
        else None,
    )


def _method_detail_to_response(d: dict) -> MethodDetailResponse:
    """将方法详情字典转为响应模型。"""
    return MethodDetailResponse(
        id=d["id"],
        organization_id=d["organization_id"],
        code=d["code"],
        display_name=d["display_name"],
        description=d["description"],
        status=d["status"],
        version_count=d["version_count"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        lock_version=d["lock_version"],
        latest_version=_method_version_to_response(d["latest_version"])
        if d.get("latest_version")
        else None,
    )


def _package_detail_to_response(d: dict) -> PackageDetailResponse:
    """将包详情字典转为响应模型。"""
    return PackageDetailResponse(
        id=d["id"],
        organization_id=d["organization_id"],
        code=d["code"],
        display_name=d["display_name"],
        description=d["description"],
        status=d["status"],
        version_count=d["version_count"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        lock_version=d["lock_version"],
        latest_version=_package_version_to_response(d["latest_version"])
        if d.get("latest_version")
        else None,
    )


def _method_version_orm_to_dict(version: object) -> dict:
    """将 MethodVersion ORM 实体转为字典。"""
    return {
        "id": str(version.id),  # type: ignore[attr-defined]
        "method_id": str(version.method_id),  # type: ignore[attr-defined]
        "version": version.version,  # type: ignore[attr-defined]
        "code": version.code,  # type: ignore[attr-defined]
        "display_name": version.display_name,  # type: ignore[attr-defined]
        "description": version.description,  # type: ignore[attr-defined]
        "status": version.status,  # type: ignore[attr-defined]
        "published_at": version.published_at,  # type: ignore[attr-defined]
        "published_by": str(version.published_by)  # type: ignore[attr-defined]
        if version.published_by  # type: ignore[attr-defined]
        else None,
        "deprecated_at": version.deprecated_at,  # type: ignore[attr-defined]
        "deprecated_by": str(version.deprecated_by)  # type: ignore[attr-defined]
        if version.deprecated_by  # type: ignore[attr-defined]
        else None,
        "rejection_reason": version.rejection_reason,  # type: ignore[attr-defined]
        "created_at": version.created_at,  # type: ignore[attr-defined]
        "lock_version": version.lock_version,  # type: ignore[attr-defined]
    }


def _template_version_orm_to_dict(version: object) -> dict:
    """将 FactTemplateVersion ORM 实体转为字典。"""
    return {
        "id": str(version.id),  # type: ignore[attr-defined]
        "template_id": str(version.template_id),  # type: ignore[attr-defined]
        "version": version.version,  # type: ignore[attr-defined]
        "code": version.code,  # type: ignore[attr-defined]
        "display_name": version.display_name,  # type: ignore[attr-defined]
        "fact_type": version.fact_type,  # type: ignore[attr-defined]
        "required_conditions": getattr(version, "required_conditions", None) or [],  # type: ignore[attr-defined]
        "observations": getattr(version, "observations", None) or [],  # type: ignore[attr-defined]
        "required_artifact_roles": getattr(version, "required_artifact_roles", None) or [],  # type: ignore[attr-defined]
        "quality_rule_codes": getattr(version, "quality_rule_codes", None) or [],  # type: ignore[attr-defined]
        "status": version.status,  # type: ignore[attr-defined]
        "published_at": version.published_at,  # type: ignore[attr-defined]
        "published_by": str(version.published_by)  # type: ignore[attr-defined]
        if version.published_by  # type: ignore[attr-defined]
        else None,
        "deprecated_at": version.deprecated_at,  # type: ignore[attr-defined]
        "deprecated_by": str(version.deprecated_by)  # type: ignore[attr-defined]
        if version.deprecated_by  # type: ignore[attr-defined]
        else None,
        "rejection_reason": version.rejection_reason,  # type: ignore[attr-defined]
        "created_at": version.created_at,  # type: ignore[attr-defined]
        "lock_version": version.lock_version,  # type: ignore[attr-defined]
    }


def _package_version_orm_to_dict(version: object) -> dict:
    """将 StandardPackageVersion ORM 实体转为字典。"""
    return {
        "id": str(version.id),  # type: ignore[attr-defined]
        "package_id": str(version.package_id),  # type: ignore[attr-defined]
        "version": version.version,  # type: ignore[attr-defined]
        "code": version.code,  # type: ignore[attr-defined]
        "display_name": version.display_name,  # type: ignore[attr-defined]
        "description": version.description,  # type: ignore[attr-defined]
        "variable_refs": getattr(version, "variable_refs", None) or [],  # type: ignore[attr-defined]
        "method_refs": getattr(version, "method_refs", None) or [],  # type: ignore[attr-defined]
        "template_refs": getattr(version, "template_refs", None) or [],  # type: ignore[attr-defined]
        "quality_rule_refs": getattr(version, "quality_rule_refs", None) or [],  # type: ignore[attr-defined]
        "status": version.status,  # type: ignore[attr-defined]
        "published_at": version.published_at,  # type: ignore[attr-defined]
        "published_by": str(version.published_by)  # type: ignore[attr-defined]
        if version.published_by  # type: ignore[attr-defined]
        else None,
        "deprecated_at": version.deprecated_at,  # type: ignore[attr-defined]
        "deprecated_by": str(version.deprecated_by)  # type: ignore[attr-defined]
        if version.deprecated_by  # type: ignore[attr-defined]
        else None,
        "rejection_reason": version.rejection_reason,  # type: ignore[attr-defined]
        "created_at": version.created_at,  # type: ignore[attr-defined]
        "lock_version": version.lock_version,  # type: ignore[attr-defined]
    }


# ---- 模板端点 ----


@templates_router.post(
    "", response_model=TemplateDetailResponse, status_code=201
)
async def create_template(
    body: CreateTemplateRequest,
    current_user: WriteUserDep,
    service: TemplateServiceDep,
) -> TemplateDetailResponse:
    """创建事实模板。

    创建后处于 draft 状态，version_count=0。编码在组织内唯一。
    """
    await service.create_template(
        code=body.code,
        display_name=body.display_name,
        fact_type=body.fact_type,
    )
    detail = await service.get_template_by_code(body.code)  # type: ignore[attr-defined]
    return _template_detail_to_response(detail)  # type: ignore[arg-type]


@templates_router.get("", response_model=TemplateListResponse)
async def list_templates(
    current_user: ReadUserDep,
    service: TemplateServiceDep,
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> TemplateListResponse:
    """分页查询事实模板列表。"""
    items, next_cursor = await service.list_templates(
        cursor=cursor, page_size=page_size
    )
    return TemplateListResponse(
        items=[
            TemplateListItem(
                id=item["id"],
                code=item["code"],
                display_name=item["display_name"],
                fact_type=item["fact_type"],
                status=item["status"],
                version_count=item["version_count"],
                created_at=item["created_at"],
                updated_at=item["updated_at"],
                lock_version=item["lock_version"],
                latest_version=_template_version_to_response(
                    item["latest_version"]
                )
                if item.get("latest_version")
                else None,
            )
            for item in items
        ],
        next_cursor=next_cursor,
    )


@templates_router.get(
    "/{template_id}", response_model=TemplateDetailResponse
)
async def get_template(
    template_id: UUID,
    current_user: ReadUserDep,
    service: TemplateServiceDep,
) -> TemplateDetailResponse:
    """查询单个事实模板详情。"""
    detail = await service.get_template(template_id)
    return _template_detail_to_response(detail)


@templates_router.post(
    "/{template_id}/observations",
    response_model=TemplateVersionResponse,
    status_code=201,
)
async def add_observation(
    template_id: UUID,
    body: AddObservationRequest,
    current_user: WriteUserDep,
    service: TemplateServiceDep,
) -> TemplateVersionResponse:
    """为模板添加观测要求。"""
    version = await service.add_observation(
        template_id=template_id,
        variable_version_id=body.variable_version_id,
        required=body.required,
        cardinality=body.cardinality,
    )
    return _template_version_to_response(
        _template_version_orm_to_dict(version)
    )


@templates_router.post(
    "/{template_id}/submit", response_model=TemplateVersionResponse
)
async def submit_template(
    template_id: UUID,
    current_user: WriteUserDep,
    service: TemplateServiceDep,
) -> TemplateVersionResponse:
    """提交模板审核（DRAFT → IN_REVIEW，验证后创建版本快照）。"""
    version = await service.submit_template(template_id)
    return _template_version_to_response(
        _template_version_orm_to_dict(version)
    )


@templates_router.post(
    "/{template_id}/publish", response_model=TemplateVersionResponse
)
async def publish_template(
    template_id: UUID,
    current_user: PublishUserDep,
    service: TemplateServiceDep,
) -> TemplateVersionResponse:
    """发布模板（IN_REVIEW → PUBLISHED，版本此后不可变）。"""
    version = await service.publish_template(template_id)
    return _template_version_to_response(
        _template_version_orm_to_dict(version)
    )


@templates_router.post(
    "/{template_id}/reject", response_model=TemplateVersionResponse
)
async def reject_template(
    template_id: UUID,
    body: RejectRequest,
    current_user: PublishUserDep,
    service: TemplateServiceDep,
) -> TemplateVersionResponse:
    """拒绝模板（IN_REVIEW → REJECTED，设置拒绝原因）。"""
    version = await service.reject_template(template_id, reason=body.reason)
    return _template_version_to_response(
        _template_version_orm_to_dict(version)
    )


@templates_router.post(
    "/{template_id}/deprecate", response_model=TemplateVersionResponse
)
async def deprecate_template(
    template_id: UUID,
    current_user: PublishUserDep,
    service: TemplateServiceDep,
) -> TemplateVersionResponse:
    """弃用模板（PUBLISHED → DEPRECATED）。"""
    version = await service.deprecate_template(template_id)
    return _template_version_to_response(
        _template_version_orm_to_dict(version)
    )


# ---- 方法端点 ----


@methods_router.post(
    "", response_model=MethodDetailResponse, status_code=201
)
async def create_method(
    body: CreateMethodRequest,
    current_user: WriteUserDep,
    service: MethodServiceDep,
) -> MethodDetailResponse:
    """创建方法。"""
    await service.create_method(
        code=body.code,
        display_name=body.display_name,
        description=body.description,
    )
    detail = await service.get_method_by_code(body.code)  # type: ignore[attr-defined]
    return _method_detail_to_response(detail)  # type: ignore[arg-type]


@methods_router.get("", response_model=MethodListResponse)
async def list_methods(
    current_user: ReadUserDep,
    service: MethodServiceDep,
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> MethodListResponse:
    """分页查询方法列表。"""
    items, next_cursor = await service.list_methods(
        cursor=cursor, page_size=page_size
    )
    return MethodListResponse(
        items=[
            MethodListItem(
                id=item["id"],
                code=item["code"],
                display_name=item["display_name"],
                description=item["description"],
                status=item["status"],
                version_count=item["version_count"],
                created_at=item["created_at"],
                updated_at=item["updated_at"],
                lock_version=item["lock_version"],
                latest_version=_method_version_to_response(
                    item["latest_version"]
                )
                if item.get("latest_version")
                else None,
            )
            for item in items
        ],
        next_cursor=next_cursor,
    )


@methods_router.get(
    "/{method_id}", response_model=MethodDetailResponse
)
async def get_method(
    method_id: UUID,
    current_user: ReadUserDep,
    service: MethodServiceDep,
) -> MethodDetailResponse:
    """查询单个方法详情。"""
    detail = await service.get_method(method_id)
    return _method_detail_to_response(detail)


@methods_router.post(
    "/{method_id}/submit", response_model=MethodVersionResponse
)
async def submit_method(
    method_id: UUID,
    current_user: WriteUserDep,
    service: MethodServiceDep,
) -> MethodVersionResponse:
    """提交方法审核（DRAFT → IN_REVIEW，创建版本快照）。"""
    version = await service.submit_method(method_id)
    return _method_version_to_response(
        _method_version_orm_to_dict(version)
    )


@methods_router.post(
    "/{method_id}/publish", response_model=MethodVersionResponse
)
async def publish_method(
    method_id: UUID,
    current_user: PublishUserDep,
    service: MethodServiceDep,
) -> MethodVersionResponse:
    """发布方法（IN_REVIEW → PUBLISHED，版本此后不可变）。"""
    version = await service.publish_method(method_id)
    return _method_version_to_response(
        _method_version_orm_to_dict(version)
    )


# ---- 标准包端点 ----


@packages_router.post(
    "", response_model=PackageDetailResponse, status_code=201
)
async def create_package(
    body: CreatePackageRequest,
    current_user: WriteUserDep,
    service: PackageServiceDep,
) -> PackageDetailResponse:
    """创建标准包。"""
    await service.create_package(
        code=body.code,
        display_name=body.display_name,
        description=body.description,
    )
    detail = await service.get_package_by_code(body.code)  # type: ignore[attr-defined]
    return _package_detail_to_response(detail)  # type: ignore[arg-type]


@packages_router.get("", response_model=PackageListResponse)
async def list_packages(
    current_user: ReadUserDep,
    service: PackageServiceDep,
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> PackageListResponse:
    """分页查询标准包列表。"""
    items, next_cursor = await service.list_packages(
        cursor=cursor, page_size=page_size
    )
    return PackageListResponse(
        items=[
            PackageListItem(
                id=item["id"],
                code=item["code"],
                display_name=item["display_name"],
                description=item["description"],
                status=item["status"],
                version_count=item["version_count"],
                created_at=item["created_at"],
                updated_at=item["updated_at"],
                lock_version=item["lock_version"],
                latest_version=_package_version_to_response(
                    item["latest_version"]
                )
                if item.get("latest_version")
                else None,
            )
            for item in items
        ],
        next_cursor=next_cursor,
    )


@packages_router.get(
    "/{package_id}", response_model=PackageDetailResponse
)
async def get_package(
    package_id: UUID,
    current_user: ReadUserDep,
    service: PackageServiceDep,
) -> PackageDetailResponse:
    """查询单个标准包详情。"""
    detail = await service.get_package(package_id)
    return _package_detail_to_response(detail)


@packages_router.post(
    "/{package_id}/refs", response_model=PackageDetailResponse
)
async def add_package_ref(
    package_id: UUID,
    body: AddPackageRefRequest,
    current_user: WriteUserDep,
    service: PackageServiceDep,
) -> PackageDetailResponse:
    """添加标准包引用。

    根据 ref_type 调用对应的 add_*_ref 方法。
    已发布的包不可添加引用。
    """
    if body.ref_type == "variable":
        await service.add_variable_ref(
            package_id, body.ref_id, body.version
        )
    elif body.ref_type == "method":
        await service.add_method_ref(
            package_id, body.ref_id, body.version
        )
    elif body.ref_type == "template":
        await service.add_template_ref(
            package_id, body.ref_id, body.version
        )
    detail = await service.get_package(package_id)
    return _package_detail_to_response(detail)


@packages_router.post(
    "/{package_id}/submit", response_model=PackageDetailResponse
)
async def submit_package(
    package_id: UUID,
    current_user: WriteUserDep,
    service: PackageServiceDep,
) -> PackageDetailResponse:
    """提交标准包审核（DRAFT → IN_REVIEW，验证所有引用已发布）。"""
    await service.submit_package(package_id)
    detail = await service.get_package(package_id)
    return _package_detail_to_response(detail)


@packages_router.post(
    "/{package_id}/publish", response_model=PackageVersionResponse
)
async def publish_package(
    package_id: UUID,
    current_user: PublishUserDep,
    service: PackageServiceDep,
) -> PackageVersionResponse:
    """发布标准包（IN_REVIEW → PUBLISHED，冻结所有引用）。"""
    version = await service.publish_package(package_id)
    return _package_version_to_response(
        _package_version_orm_to_dict(version)
    )


@packages_router.post(
    "/{package_id}/reject", response_model=PackageVersionResponse
)
async def reject_package(
    package_id: UUID,
    body: RejectRequest,
    current_user: PublishUserDep,
    service: PackageServiceDep,
) -> PackageVersionResponse:
    """拒绝标准包（IN_REVIEW → REJECTED，设置拒绝原因）。"""
    version = await service.reject_package(
        package_id, reason=body.reason
    )
    return _package_version_to_response(
        _package_version_orm_to_dict(version)
    )
