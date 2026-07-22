"""设备仪器管理路由：创建、列表、详情、编辑、状态切换、物理量关联。

端点：
  POST   /api/v1/equipment                  — 创建设备（equipment:manage）
  GET    /api/v1/equipment                  — 分页列表（equipment:read）
  GET    /api/v1/equipment/{id}             — 详情（equipment:read）
  PATCH  /api/v1/equipment/{id}             — 编辑（equipment:manage，不含 code）
  PATCH  /api/v1/equipment/{id}/status      — 启用/禁用（equipment:manage）
  GET    /api/v1/equipment/{id}/variables   — 物理量列表（equipment:read）
  PUT    /api/v1/equipment/{id}/variables   — 设置物理量（equipment:manage）

安全约定：
- 创建/编辑/状态切换/设置物理量需 require_permission("equipment:manage")；
- 列表/详情/物理量列表需 require_permission("equipment:read")；
- code 创建后锁定：UpdateEquipmentBody 不含 code 字段；
- 乐观锁：编辑/状态切换请求必须携带 lock_version；
- 软禁用：status='disabled'，无 DELETE 端点。
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.equipment.service import EquipmentService

#: 路由实例。
equipment_router = APIRouter(prefix="/api/v1/equipment", tags=["equipment"])

#: 需 equipment:manage 权限的当前用户依赖。
ManageUserDep = Annotated[CurrentUser, Depends(require_permission("equipment:manage"))]

#: 需 equipment:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("equipment:read"))]


def get_equipment_service() -> EquipmentService:
    """获取 EquipmentService 实例（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入按请求构造的实例
    （需当前用户上下文查询 organization_id）。
    """
    raise NotImplementedError(
        "get_equipment_service must be overridden via dependency_overrides"
    )


#: EquipmentService 依赖类型别名。
EquipmentServiceDep = Annotated[
    EquipmentService, Depends(get_equipment_service)
]


# ---- 请求模型 ----


class CreateEquipmentBody(BaseModel):
    """创建设备请求。

    code 创建后锁定不可修改。
    """

    code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="设备编码，仅小写字母/数字/下划线，创建后锁定",
    )
    display_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    department_id: str = Field(..., description="所属部门 UUID")
    sort_order: int = Field(0, ge=0)


class UpdateEquipmentBody(BaseModel):
    """编辑设备请求（code 不可修改，不在请求体中）。"""

    display_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    department_id: str | None = Field(None, description="新部门 UUID（可选）")
    sort_order: int | None = Field(None, ge=0)
    lock_version: int = Field(..., ge=0)


class UpdateEquipmentStatusBody(BaseModel):
    """启用/禁用设备请求。"""

    status: Literal["active", "disabled"]
    lock_version: int = Field(..., ge=0)


class SetEquipmentVariablesBody(BaseModel):
    """设置设备物理量请求。"""

    variable_ids: list[str] = Field(default_factory=list)


# ---- 响应模型 ----


class EquipmentResponse(BaseModel):
    """设备详情响应。"""

    id: str
    organization_id: str
    code: str
    display_name: str
    description: str | None
    department_id: str
    status: str
    sort_order: int
    created_at: datetime
    updated_at: datetime
    lock_version: int


class EquipmentListItem(BaseModel):
    """设备列表项（含部门名 + 物理量数）。"""

    id: str
    code: str
    display_name: str
    department_id: str
    department_name: str
    status: str
    sort_order: int
    variable_count: int


class EquipmentListResponse(BaseModel):
    """设备分页列表响应。"""

    items: list[EquipmentListItem]
    next_cursor: str | None
    has_more: bool


class EquipmentVariableItem(BaseModel):
    """设备物理量列表项。"""

    id: str
    code: str
    name_zh: str
    name_en: str
    quantity_kind: str
    data_type: str
    status: str
    current_version: str | None


# ---- 辅助函数 ----


def _to_response(equip: object) -> EquipmentResponse:
    """将 Equipment ORM 实体转换为响应模型。"""
    return EquipmentResponse(
        id=str(equip.id),  # type: ignore[attr-defined]
        organization_id=str(equip.organization_id),  # type: ignore[attr-defined]
        code=equip.code,  # type: ignore[attr-defined]
        display_name=equip.display_name,  # type: ignore[attr-defined]
        description=equip.description,  # type: ignore[attr-defined]
        department_id=str(equip.department_id),  # type: ignore[attr-defined]
        status=equip.status,  # type: ignore[attr-defined]
        sort_order=equip.sort_order,  # type: ignore[attr-defined]
        created_at=equip.created_at,  # type: ignore[attr-defined]
        updated_at=equip.updated_at,  # type: ignore[attr-defined]
        lock_version=equip.lock_version,  # type: ignore[attr-defined]
    )


# ---- 端点 ----


@equipment_router.post("", response_model=EquipmentResponse, status_code=201)
async def create_equipment(
    body: CreateEquipmentBody,
    current_user: ManageUserDep,
    service: EquipmentServiceDep,
) -> EquipmentResponse:
    """创建设备仪器。

    code 创建后锁定不可修改。编码在组织内唯一。

    Args:
        body: 创建请求体。
        current_user: 当前认证用户（需 equipment:manage 权限）。
        service: 设备服务。

    Returns:
        EquipmentResponse: 新创建的设备（201 Created）。

    Raises:
        AppError: code="conflict"，当编码已存在时。
    """
    equipment = await service.create(
        department_id=UUID(body.department_id),
        code=body.code,
        display_name=body.display_name,
        description=body.description,
        sort_order=body.sort_order,
    )
    return _to_response(equipment)


@equipment_router.get("", response_model=EquipmentListResponse)
async def list_equipment(
    current_user: ReadUserDep,
    service: EquipmentServiceDep,
    department_id: str | None = Query(None, description="部门 ID 筛选"),
    status: str | None = Query(None, description="状态筛选"),
    cursor: str | None = Query(None, description="分页游标"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
) -> EquipmentListResponse:
    """分页查询设备列表（含部门名 + 物理量数）。

    Args:
        current_user: 当前认证用户（需 equipment:read 权限）。
        service: 设备服务。
        department_id: 部门 ID 筛选。
        status: 状态筛选（active / disabled）。
        cursor: 分页游标。
        limit: 每页数量。

    Returns:
        EquipmentListResponse: 分页列表。
    """
    dept_id = UUID(department_id) if department_id is not None else None
    result = await service.list(
        department_id=dept_id,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    items = [
        EquipmentListItem(
            id=str(equip.id),
            code=equip.code,
            display_name=equip.display_name,
            department_id=str(equip.department_id),
            department_name=dept_name,
            status=equip.status,
            sort_order=equip.sort_order,
            variable_count=var_count,
        )
        for equip, dept_name, var_count in result.items
    ]
    return EquipmentListResponse(
        items=items,
        next_cursor=result.next_cursor,
        has_more=result.has_more,
    )


@equipment_router.get("/{equipment_id}", response_model=EquipmentResponse)
async def get_equipment(
    equipment_id: UUID,
    current_user: ReadUserDep,
    service: EquipmentServiceDep,
) -> EquipmentResponse:
    """查询单个设备详情。

    Args:
        equipment_id: 设备 UUID。
        current_user: 当前认证用户（需 equipment:read 权限）。
        service: 设备服务。

    Returns:
        EquipmentResponse: 设备详情。

    Raises:
        AppError: code="not_found"，当设备不存在时。
    """
    equipment, _ = await service.get(equipment_id)
    return _to_response(equipment)


@equipment_router.patch("/{equipment_id}", response_model=EquipmentResponse)
async def update_equipment(
    equipment_id: UUID,
    body: UpdateEquipmentBody,
    current_user: ManageUserDep,
    service: EquipmentServiceDep,
) -> EquipmentResponse:
    """编辑设备（code 不可修改）。

    Args:
        equipment_id: 设备 UUID。
        body: 编辑请求体（不含 code）。
        current_user: 当前认证用户（需 equipment:manage 权限）。
        service: 设备服务。

    Returns:
        EquipmentResponse: 更新后的设备。

    Raises:
        AppError: code="not_found"，当设备不存在时。
        AppError: code="conflict"，当 lock_version 不匹配时。
    """
    # 若未提供 department_id，需先查询当前值
    department_id: UUID
    if body.department_id is not None:
        department_id = UUID(body.department_id)
    else:
        equipment, _ = await service.get(equipment_id)
        department_id = equipment.department_id

    sort_order = body.sort_order if body.sort_order is not None else 0

    equipment = await service.update(
        equipment_id=equipment_id,
        display_name=body.display_name,
        description=body.description,
        department_id=department_id,
        sort_order=sort_order,
        lock_version=body.lock_version,
    )
    return _to_response(equipment)


@equipment_router.patch(
    "/{equipment_id}/status", response_model=EquipmentResponse
)
async def update_equipment_status(
    equipment_id: UUID,
    body: UpdateEquipmentStatusBody,
    current_user: ManageUserDep,
    service: EquipmentServiceDep,
) -> EquipmentResponse:
    """启用/禁用设备（软禁用）。

    Args:
        equipment_id: 设备 UUID。
        body: 状态切换请求体。
        current_user: 当前认证用户（需 equipment:manage 权限）。
        service: 设备服务。

    Returns:
        EquipmentResponse: 更新后的设备。

    Raises:
        AppError: code="not_found"，当设备不存在时。
        AppError: code="conflict"，当 lock_version 不匹配时。
    """
    equipment = await service.set_status(
        equipment_id=equipment_id,
        status=body.status,
        lock_version=body.lock_version,
    )
    return _to_response(equipment)


@equipment_router.get(
    "/{equipment_id}/variables", response_model=list[EquipmentVariableItem]
)
async def list_equipment_variables(
    equipment_id: UUID,
    current_user: ReadUserDep,
    service: EquipmentServiceDep,
) -> list[EquipmentVariableItem]:
    """获取设备的物理量列表。

    Args:
        equipment_id: 设备 UUID。
        current_user: 当前认证用户（需 equipment:read 权限）。
        service: 设备服务。

    Returns:
        list[EquipmentVariableItem]: 物理量列表。

    Raises:
        AppError: code="not_found"，当设备不存在时。
    """
    variables = await service.list_variables(equipment_id)
    return [
        EquipmentVariableItem(
            id=v["id"],
            code=v["code"],
            name_zh=v["name_zh"],
            name_en=v["name_en"],
            quantity_kind=v["quantity_kind"],
            data_type=v["data_type"],
            status=v["status"],
            current_version=v["current_version"],
        )
        for v in variables
    ]


@equipment_router.put("/{equipment_id}/variables")
async def set_equipment_variables(
    equipment_id: UUID,
    body: SetEquipmentVariablesBody,
    current_user: ManageUserDep,
    service: EquipmentServiceDep,
) -> dict:
    """设置设备的物理量产出（全量替换）。

    Args:
        equipment_id: 设备 UUID。
        body: 设置请求体。
        current_user: 当前认证用户（需 equipment:manage 权限）。
        service: 设备服务。

    Returns:
        dict: {"ok": true}。

    Raises:
        AppError: code="not_found"，当设备不存在时。
    """
    variable_ids = [UUID(vid) for vid in body.variable_ids]
    await service.set_variables(equipment_id, variable_ids)
    return {"ok": True}
