"""标准变量管理路由：创建 / 列表 / 详情 / 提交 / 发布 / 拒绝 / 弃用 / 别名 / 单位转换。

端点（IRIP Task 10）：
  POST   /api/v1/standards/variables              — 创建变量（standard:write）
  GET    /api/v1/standards/variables              — 分页列表（standard:read）
  GET    /api/v1/standards/variables/{id}          — 详情（standard:read）
  POST   /api/v1/standards/variables/{id}/submit   — 提交审核（standard:write）
  POST   /api/v1/standards/variables/{id}/publish  — 发布（standard:publish）
  POST   /api/v1/standards/variables/{id}/reject   — 拒绝（standard:publish）
  POST   /api/v1/standards/variables/{id}/deprecate — 弃用（standard:publish）
  POST   /api/v1/standards/variables/{id}/resubmit  — 重新提交审核（standard:write）
  POST   /api/v1/standards/variables/{id}/aliases  — 添加别名（standard:write）
  GET    /api/v1/standards/units/convert           — 单位转换（standard:read）

安全约定：
- 创建/提交/别名需 require_permission("standard:write")；
- 列表/详情/单位转换需 require_permission("standard:read")；
- 发布/拒绝/弃用需 require_permission("standard:publish")；
- code 创建后锁定：无编辑端点（新版本通过提交审核创建）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.standards.service import StandardService
from packages.standards.units import UnitConverter

#: 路由实例。
standards_router = APIRouter(prefix="/api/v1/standards", tags=["standards"])

#: 需 standard:write 权限的当前用户依赖。
WriteUserDep = Annotated[CurrentUser, Depends(require_permission("standard:write"))]

#: 需 standard:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("standard:read"))]

#: 需 standard:publish 权限的当前用户依赖。
PublishUserDep = Annotated[CurrentUser, Depends(require_permission("standard:publish"))]


def get_standard_service() -> StandardService:
    """获取 StandardService 实例（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入按请求构造的实例
    （需当前用户上下文查询 organization_id）。
    """
    raise NotImplementedError("get_standard_service must be overridden via dependency_overrides")


#: StandardService 依赖类型别名。
StandardServiceDep = Annotated[StandardService, Depends(get_standard_service)]


# ---- 请求模型 ----


class CreateVariableRequest(BaseModel):
    """创建标准变量请求。"""

    code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="变量编码，仅小写字母/数字/下划线，创建后锁定",
    )
    display_name: str = Field(..., min_length=1, max_length=200)
    data_type: Literal["number", "text", "boolean", "datetime"]
    canonical_unit: str | None = Field(None, max_length=32)
    quantity_kind: str | None = Field(None, max_length=32)
    valid_range: list[str] | None = Field(
        None,
        description="有效范围 [min, max]，Decimal 字符串数组",
    )


class RejectVariableRequest(BaseModel):
    """拒绝变量请求。"""

    reason: str = Field(..., min_length=1, max_length=2000)


class DeprecateVariableRequest(BaseModel):
    """弃用变量请求。"""

    reason: str = Field("", max_length=2000)


class AddAliasRequest(BaseModel):
    """添加别名请求。"""

    alias: str = Field(..., min_length=1, max_length=200)
    language: str = Field("zh", max_length=16)


# ---- 响应模型 ----


class VersionResponse(BaseModel):
    """版本详情响应。"""

    id: str
    variable_id: str
    version: int
    code: str
    display_name: str
    data_type: str
    canonical_unit: str | None
    quantity_kind: str | None
    valid_range: list[str] | None
    status: str
    published_at: datetime | None
    published_by: str | None
    deprecated_at: datetime | None
    deprecated_by: str | None
    rejection_reason: str | None
    created_at: datetime
    lock_version: int


class AliasResponse(BaseModel):
    """别名响应。"""

    alias: str
    language: str


class VariableDetailResponse(BaseModel):
    """变量详情响应（含最新版本 + 别名）。"""

    id: str
    organization_id: str
    code: str
    display_name: str
    data_type: str
    canonical_unit: str | None
    quantity_kind: str | None
    valid_range: list[str] | None
    status: str
    version_count: int
    created_at: datetime
    updated_at: datetime
    lock_version: int
    latest_version: VersionResponse | None
    aliases: list[AliasResponse]


class VariableListItem(BaseModel):
    """变量列表项。"""

    id: str
    code: str
    display_name: str
    data_type: str
    canonical_unit: str | None
    quantity_kind: str | None
    valid_range: list[str] | None
    status: str
    version_count: int
    created_at: datetime
    updated_at: datetime
    lock_version: int
    latest_version: VersionResponse | None


class VariableListResponse(BaseModel):
    """变量分页列表响应。"""

    items: list[VariableListItem]
    next_cursor: str | None


class UnitConvertResponse(BaseModel):
    """单位转换响应。"""

    value: str
    source: str
    target: str


# ---- 辅助函数 ----


def _valid_range_to_str_list(
    valid_range: tuple[Decimal, Decimal] | None,
) -> list[str] | None:
    """将 (min, max) Decimal 元组转为字符串列表。"""
    if valid_range is None:
        return None
    return [str(valid_range[0]), str(valid_range[1])]


def _version_to_response(version: dict) -> VersionResponse:
    """将版本字典转为响应模型。"""
    return VersionResponse(
        id=version["id"],
        variable_id=version["variable_id"],
        version=version["version"],
        code=version["code"],
        display_name=version["display_name"],
        data_type=version["data_type"],
        canonical_unit=version["canonical_unit"],
        quantity_kind=version["quantity_kind"],
        valid_range=_valid_range_to_str_list(version["valid_range"]),
        status=version["status"],
        published_at=version["published_at"],
        published_by=version["published_by"],
        deprecated_at=version["deprecated_at"],
        deprecated_by=version["deprecated_by"],
        rejection_reason=version["rejection_reason"],
        created_at=version["created_at"],
        lock_version=version["lock_version"],
    )


# ---- 端点：变量 CRUD ----


@standards_router.post("/variables", response_model=VariableDetailResponse, status_code=201)
async def create_variable(
    body: CreateVariableRequest,
    current_user: WriteUserDep,
    service: StandardServiceDep,
) -> VariableDetailResponse:
    """创建标准变量。

    创建后处于 draft 状态，version_count=0。编码在组织内唯一。

    Args:
        body: 创建请求体。
        current_user: 当前认证用户（需 standard:write 权限）。
        service: 标准变量服务。

    Returns:
        VariableDetailResponse: 新创建的变量详情（201 Created）。

    Raises:
        AppError: code="conflict"，当编码已存在时。
    """
    valid_range = None
    if body.valid_range is not None and len(body.valid_range) == 2:
        valid_range = (Decimal(body.valid_range[0]), Decimal(body.valid_range[1]))

    await service.create_variable(
        code=body.code,
        display_name=body.display_name,
        data_type=body.data_type,
        canonical_unit=body.canonical_unit,
        quantity_kind=body.quantity_kind,
        valid_range=valid_range,
    )
    detail = await service.get_variable_by_code(body.code)
    return _detail_to_response(detail)


@standards_router.get("/variables", response_model=VariableListResponse)
async def list_variables(
    current_user: ReadUserDep,
    service: StandardServiceDep,
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> VariableListResponse:
    """分页查询标准变量列表。

    Args:
        current_user: 当前认证用户（需 standard:read 权限）。
        service: 标准变量服务。
        cursor: 分页游标。
        page_size: 每页数量。

    Returns:
        VariableListResponse: 分页列表。
    """
    items, next_cursor = await service.list_variables(cursor=cursor, page_size=page_size)
    return VariableListResponse(
        items=[
            VariableListItem(
                id=item["id"],
                code=item["code"],
                display_name=item["display_name"],
                data_type=item["data_type"],
                canonical_unit=item["canonical_unit"],
                quantity_kind=item["quantity_kind"],
                valid_range=_valid_range_to_str_list(item["valid_range"]),
                status=item["status"],
                version_count=item["version_count"],
                created_at=item["created_at"],
                updated_at=item["updated_at"],
                lock_version=item["lock_version"],
                latest_version=_version_to_response(item["latest_version"])
                if item["latest_version"]
                else None,
            )
            for item in items
        ],
        next_cursor=next_cursor,
    )


@standards_router.get("/variables/{variable_id}", response_model=VariableDetailResponse)
async def get_variable(
    variable_id: UUID,
    current_user: ReadUserDep,
    service: StandardServiceDep,
) -> VariableDetailResponse:
    """查询单个变量详情。

    Args:
        variable_id: 变量 UUID。
        current_user: 当前认证用户（需 standard:read 权限）。
        service: 标准变量服务。

    Returns:
        VariableDetailResponse: 变量详情（含最新版本 + 别名）。

    Raises:
        AppError: code="not_found"，当变量不存在时。
    """
    detail = await service.get_variable(variable_id)
    return _detail_to_response(detail)


# ---- 端点：状态转换 ----


@standards_router.post("/variables/{variable_id}/submit", response_model=VersionResponse)
async def submit_for_review(
    variable_id: UUID,
    current_user: WriteUserDep,
    service: StandardServiceDep,
) -> VersionResponse:
    """提交审核（DRAFT → IN_REVIEW，创建版本快照）。

    Args:
        variable_id: 变量 UUID。
        current_user: 当前认证用户（需 standard:write 权限）。
        service: 标准变量服务。

    Returns:
        VersionResponse: 新创建的版本。

    Raises:
        AppError: code="not_found"，当变量不存在时。
        AppError: code="invalid_transition"，当状态非 draft 时。
    """
    version = await service.submit_for_review(variable_id)
    return _version_to_response(_version_orm_to_dict(version))


@standards_router.post("/variables/{variable_id}/publish", response_model=VersionResponse)
async def publish_variable(
    variable_id: UUID,
    current_user: PublishUserDep,
    service: StandardServiceDep,
) -> VersionResponse:
    """发布变量（IN_REVIEW → PUBLISHED，版本此后不可变）。

    Args:
        variable_id: 变量 UUID。
        current_user: 当前认证用户（需 standard:publish 权限）。
        service: 标准变量服务。

    Returns:
        VersionResponse: 已发布的版本。

    Raises:
        AppError: code="not_found"，当变量不存在时。
        AppError: code="invalid_transition"，当状态非 in_review 时。
    """
    version = await service.publish_variable(variable_id)
    return _version_to_response(_version_orm_to_dict(version))


@standards_router.post("/variables/{variable_id}/reject", response_model=VersionResponse)
async def reject_variable(
    variable_id: UUID,
    body: RejectVariableRequest,
    current_user: PublishUserDep,
    service: StandardServiceDep,
) -> VersionResponse:
    """拒绝变量（IN_REVIEW → REJECTED，设置拒绝原因）。

    Args:
        variable_id: 变量 UUID。
        body: 拒绝请求体（含 reason）。
        current_user: 当前认证用户（需 standard:publish 权限）。
        service: 标准变量服务。

    Returns:
        VersionResponse: 已拒绝的版本。

    Raises:
        AppError: code="not_found"，当变量不存在时。
        AppError: code="invalid_transition"，当状态非 in_review 时。
    """
    version = await service.reject_variable(variable_id, reason=body.reason)
    return _version_to_response(_version_orm_to_dict(version))


@standards_router.post("/variables/{variable_id}/deprecate", response_model=VersionResponse)
async def deprecate_variable(
    variable_id: UUID,
    body: DeprecateVariableRequest,
    current_user: PublishUserDep,
    service: StandardServiceDep,
) -> VersionResponse:
    """弃用变量（PUBLISHED → DEPRECATED，版本保留可读但阻止新引用）。

    Args:
        variable_id: 变量 UUID。
        body: 弃用请求体（含 reason，可选）。
        current_user: 当前认证用户（需 standard:publish 权限）。
        service: 标准变量服务。

    Returns:
        VersionResponse: 已弃用的版本。

    Raises:
        AppError: code="not_found"，当变量不存在时。
        AppError: code="invalid_transition"，当状态非 published 时。
    """
    version = await service.deprecate_variable(variable_id, reason=body.reason)
    return _version_to_response(_version_orm_to_dict(version))


@standards_router.post("/variables/{variable_id}/resubmit", response_model=VersionResponse)
async def resubmit_variable(
    variable_id: UUID,
    current_user: WriteUserDep,
    service: StandardServiceDep,
) -> VersionResponse:
    """重新提交审核（REJECTED → DRAFT → IN_REVIEW，创建新版本快照）。

    仅当变量处于 rejected 状态时可调用，调用后直接进入 in_review 状态
    并创建新版本。适用于审核被拒绝后修改重新提交的场景。

    Args:
        variable_id: 变量 UUID。
        current_user: 当前认证用户（需 standard:write 权限）。
        service: 标准变量服务。

    Returns:
        VersionResponse: 新创建的版本。

    Raises:
        AppError: code="not_found"，当变量不存在时。
        AppError: code="invalid_transition"，当状态非 rejected 时。
    """
    version = await service.resubmit(variable_id)
    return _version_to_response(_version_orm_to_dict(version))


# ---- 端点：别名 ----


@standards_router.post(
    "/variables/{variable_id}/aliases",
    response_model=AliasResponse,
    status_code=201,
)
async def add_alias(
    variable_id: UUID,
    body: AddAliasRequest,
    current_user: WriteUserDep,
    service: StandardServiceDep,
) -> AliasResponse:
    """为变量添加别名。

    Args:
        variable_id: 变量 UUID。
        body: 添加别名请求体。
        current_user: 当前认证用户（需 standard:write 权限）。
        service: 标准变量服务。

    Returns:
        AliasResponse: 新创建的别名（201 Created）。

    Raises:
        AppError: code="not_found"，当变量不存在时。
        AppError: code="conflict"，当别名已存在时。
    """
    alias = await service.add_alias(variable_id, alias=body.alias, language=body.language)
    return AliasResponse(alias=alias.alias, language=alias.language)


# ---- 端点：单位转换 ----


@standards_router.get("/units/convert", response_model=UnitConvertResponse)
async def convert_units(
    current_user: ReadUserDep,
    value: str = Query(..., description="要转换的数值（字符串，保留精度）"),
    source: str = Query(..., description="源单位代码"),
    target: str = Query(..., description="目标单位代码"),
) -> UnitConvertResponse:
    """单位转换（基于 Decimal 仿射变换，含维度检查）。

    Args:
        current_user: 当前认证用户（需 standard:read 权限）。
        value: 要转换的数值（字符串）。
        source: 源单位代码（如 "mm"）。
        target: 目标单位代码（如 "um"）。

    Returns:
        UnitConvertResponse: 转换结果。

    Raises:
        AppError: code="unknown_unit"，当单位代码未知时。
        AppError: code="incompatible_dimensions"，当源与目标维度不同时。
    """
    result = UnitConverter.convert(Decimal(value), source, target)
    return UnitConvertResponse(value=str(result), source=source, target=target)


# ---- 辅助函数 ----


def _detail_to_response(detail: dict) -> VariableDetailResponse:
    """将变量详情字典转为响应模型。"""
    return VariableDetailResponse(
        id=detail["id"],
        organization_id=detail["organization_id"],
        code=detail["code"],
        display_name=detail["display_name"],
        data_type=detail["data_type"],
        canonical_unit=detail["canonical_unit"],
        quantity_kind=detail["quantity_kind"],
        valid_range=_valid_range_to_str_list(detail["valid_range"]),
        status=detail["status"],
        version_count=detail["version_count"],
        created_at=detail["created_at"],
        updated_at=detail["updated_at"],
        lock_version=detail["lock_version"],
        latest_version=_version_to_response(detail["latest_version"])
        if detail["latest_version"]
        else None,
        aliases=[
            AliasResponse(alias=a["alias"], language=a["language"]) for a in detail["aliases"]
        ],
    )


def _version_orm_to_dict(version: object) -> dict:
    """将 VariableVersion ORM 实体转为字典（供 _version_to_response 使用）。"""
    from packages.standards.service import _valid_range_from_json

    return {
        "id": str(version.id),  # type: ignore[attr-defined]
        "variable_id": str(version.variable_id),  # type: ignore[attr-defined]
        "version": version.version,  # type: ignore[attr-defined]
        "code": version.code,  # type: ignore[attr-defined]
        "display_name": version.display_name,  # type: ignore[attr-defined]
        "data_type": version.data_type,  # type: ignore[attr-defined]
        "canonical_unit": version.canonical_unit,  # type: ignore[attr-defined]
        "quantity_kind": version.quantity_kind,  # type: ignore[attr-defined]
        "valid_range": _valid_range_from_json(
            version.valid_range  # type: ignore[attr-defined]
        ),
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
