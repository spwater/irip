"""工业对象管理路由：创建 / 列表 / 详情。

端点（IRIP Task 11）：
  POST   /api/v1/objects                      — 创建对象（standard:write）
  GET    /api/v1/objects                      — 列表，可选 ?type= 过滤（standard:read）
  GET    /api/v1/objects/{id}                 — 详情（standard:read）

安全约定：
- 创建/关系操作需 require_permission("standard:write")；
- 查询/后代遍历需 require_permission("standard:read")。
"""

from datetime import datetime
from typing import Any, Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.common.errors import AppError
from packages.standards.object_graph import ObjectGraphService

#: 路由实例。
objects_router = APIRouter(prefix="/api/v1/objects", tags=["objects"])

#: 需 standard:write 权限的当前用户依赖。
WriteUserDep = Annotated[CurrentUser, Depends(require_permission("standard:write"))]

#: 需 standard:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("standard:read"))]


def get_object_graph_service() -> ObjectGraphService:
    """获取 ObjectGraphService 实例（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入按请求构造的实例
    （需当前用户上下文查询 department_id）。
    """
    raise NotImplementedError(
        "get_object_graph_service must be overridden via dependency_overrides"
    )


#: ObjectGraphService 依赖类型别名。
ObjectGraphServiceDep = Annotated[ObjectGraphService, Depends(get_object_graph_service)]


async def _check_object_ownership(
    current_user: CurrentUser,
    obj_department_id: UUID | None,
    obj_owner_user_id: UUID | None,
    service: ObjectGraphServiceDep,
) -> None:
    """检查当前用户是否可以编辑/删除对象（所有者+上级模型）。

    权限规则：
    - 数据所有者可管理自己的数据；
    - 上级部门可管理下级部门的数据（单向向下，不含本部门）；
    - 同部门非所有者不可管理他人的数据；
    - 平台管理员不受限制。
    """
    from apps.api.dependencies.dept_scope import check_management_permission

    await check_management_permission(
        current_user=current_user,
        entity_department_id=obj_department_id,
        entity_owner_user_id=obj_owner_user_id,
        session_factory=service._factory,
    )


# ---- 请求模型 ----


class CreateObjectRequest(BaseModel):
    """创建工业对象请求。"""

    object_type: str = Field(..., max_length=50, description="对象类型")
    display_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    equipment_id: UUID | None = Field(None, description="关联设备 ID")
    component_id: str | None = Field(None, description="关联数据接口 ID（可选）")
    department_id: str | None = Field(None, description="所属部门 UUID")
    visible_departments: list[str] = Field(default_factory=list, description="可见单位 UUID 列表")


# ---- 响应模型 ----


class ObjectResponse(BaseModel):
    """工业对象详情响应。"""

    id: str
    department_id: str
    object_type: str
    code: str
    display_name: str
    description: str | None
    equipment_id: str | None
    component_id: str | None
    visible_departments: list[str]
    status: str
    created_at: datetime
    updated_at: datetime
    lock_version: int


class ObjectListItem(BaseModel):
    """工业对象列表项。"""

    id: str
    object_type: str
    code: str
    display_name: str
    description: str | None
    equipment_id: str | None
    component_id: str | None
    department_id: str | None
    visible_departments: list[str]
    status: str
    created_at: datetime
    updated_at: datetime
    lock_version: int


class ObjectListResponse(BaseModel):
    """工业对象分页列表响应。"""

    items: list[ObjectListItem]
    next_cursor: str | None


# ---- 端点：对象 CRUD ----


@objects_router.post("", response_model=ObjectResponse, status_code=201)
async def create_object(
    body: CreateObjectRequest,
    current_user: WriteUserDep,
    service: ObjectGraphServiceDep,
) -> ObjectResponse:
    """创建工业对象。

    创建后处于 active 状态。编码在组织内 + 类型内唯一。

    Args:
        body: 创建请求体。
        current_user: 当前认证用户（需 standard:write 权限）。
        service: 对象图服务。

    Returns:
        ObjectResponse: 新创建的对象详情（201 Created）。

    Raises:
        AppError: code="conflict"，当编码已存在时。
        AppError: code="not_found"，当父对象不存在时。
    """
    from packages.common.ids import gen_code

    obj = await service.add_object(
        object_type=body.object_type,
        code=gen_code("obj"),
        display_name=body.display_name,
        description=body.description,
        equipment_id=body.equipment_id,
        component_id=UUID(body.component_id) if body.component_id else None,
        department_id=UUID(body.department_id) if body.department_id else None,
        visible_departments=body.visible_departments,
    )
    return _object_to_response(obj)


@objects_router.get("", response_model=ObjectListResponse)
async def list_objects(
    current_user: ReadUserDep,
    service: ObjectGraphServiceDep,
    object_type: str | None = Query(
        None,
        alias="type",
        description="对象类型过滤，逗号分隔支持多类型（如 material,sample,product）",
    ),  # noqa: E501
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> ObjectListResponse:
    """分页查询工业对象列表。

    支持多类型过滤：``?type=material,sample,product`` 将返回三种类型的对象。

    Args:
        current_user: 当前认证用户（需 standard:read 权限）。
        service: 对象图服务。
        object_type: 可选类型过滤（alias ``type``），逗号分隔支持多类型。
        cursor: 分页游标。
        page_size: 每页数量。

    Returns:
        ObjectListResponse: 分页列表。
    """
    # 可见性由 service 层通过 compute_visible_dept_ids() 处理（含后代向下遍历），
    # 路由层不再做硬编码 department_id 过滤。
    # 多类型过滤：逗号分隔 → list 传给 service 做 IN 查询
    if object_type and "," in object_type:
        types = [t.strip() for t in object_type.split(",") if t.strip()]
        items, next_cursor = await service.list_objects(
            object_type=types,
            cursor=cursor,
            page_size=page_size,
        )
        return ObjectListResponse(
            items=[_object_to_list_item(obj) for obj in items],
            next_cursor=next_cursor,
        )

    items, next_cursor = await service.list_objects(
        object_type=object_type,
        cursor=cursor,
        page_size=page_size,
    )
    return ObjectListResponse(
        items=[_object_to_list_item(obj) for obj in items],
        next_cursor=next_cursor,
    )


@objects_router.get("/{object_id}", response_model=ObjectResponse)
async def get_object(
    object_id: UUID,
    current_user: ReadUserDep,
    service: ObjectGraphServiceDep,
) -> ObjectResponse:
    """查询单个工业对象详情。

    Args:
        object_id: 对象 UUID。
        current_user: 当前认证用户（需 standard:read 权限）。
        service: 对象图服务。

    Returns:
        ObjectResponse: 对象详情。

    Raises:
        AppError: code="not_found"，当对象不存在时。
    """
    obj = await service.get_object(object_id)
    return _object_to_response(obj)


class UpdateObjectRequest(BaseModel):
    """编辑工业对象请求（code 不可修改）。"""

    display_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    object_type: str | None = Field(None, max_length=50, description="对象类型（可选，修改时传入）")
    equipment_id: UUID | None = Field(None, description="关联设备 ID")
    component_id: str | None = Field(None, description="关联数据接口 ID（可选，None 表示清空关联）")
    department_id: str | None = Field(None, description="新所属部门 UUID（None 表示不修改）")
    visible_departments: list[str] | None = Field(
        None, description="新可见单位 UUID 列表（None 表示不修改）"
    )


class UpdateObjectStatusRequest(BaseModel):
    """启用/禁用工业对象请求。"""

    status: Literal["active", "inactive"]


@objects_router.patch("/{object_id}", response_model=ObjectResponse)
async def update_object(
    object_id: UUID,
    body: UpdateObjectRequest,
    current_user: WriteUserDep,
    service: ObjectGraphServiceDep,
) -> ObjectResponse:
    """编辑工业对象（code 不可修改）。

    Args:
        object_id: 对象 UUID。
        body: 编辑请求体。
        current_user: 当前认证用户（需 standard:write 权限）。
        service: 对象图服务。

    Returns:
        ObjectResponse: 更新后的对象。

    Raises:
        AppError: code="not_found"，当对象不存在时。
    """
    # 归属检查：所有者+上级模型
    existing = await service.get_object(object_id)
    await _check_object_ownership(
        current_user, existing.department_id, existing.owner_user_id, service
    )

    obj = await service.update_object(
        object_id=object_id,
        display_name=body.display_name,
        description=body.description,
        object_type=body.object_type,
        equipment_id=body.equipment_id,
        component_id=UUID(body.component_id) if body.component_id else None,
        department_id=UUID(body.department_id) if body.department_id else None,
        visible_departments=body.visible_departments,
    )
    return _object_to_response(obj)


@objects_router.patch("/{object_id}/status", response_model=ObjectResponse)
async def update_object_status(
    object_id: UUID,
    body: UpdateObjectStatusRequest,
    current_user: WriteUserDep,
    service: ObjectGraphServiceDep,
) -> ObjectResponse:
    """启用/禁用工业对象。

    Args:
        object_id: 对象 UUID。
        body: 状态切换请求体。
        current_user: 当前认证用户（需 standard:write 权限）。
        service: 对象图服务。

    Returns:
        ObjectResponse: 更新后的对象。
    """
    # 归属检查：所有者+上级模型
    existing = await service.get_object(object_id)
    await _check_object_ownership(
        current_user, existing.department_id, existing.owner_user_id, service
    )

    obj = await service.set_object_status(
        object_id=object_id,
        status=body.status,
    )
    return _object_to_response(obj)


@objects_router.delete("/{object_id}", status_code=204)
async def delete_object(
    object_id: UUID,
    current_user: WriteUserDep,
    service: ObjectGraphServiceDep,
) -> None:
    """删除工业对象（物理删除）。

    前置条件：对象没有活跃的关系和子对象。

    Raises:
        AppError: code="not_found"，当对象不存在时。
        AppError: code="conflict"，当存在活跃关系或子对象时。
    """
    # 归属检查：所有者+上级模型
    existing = await service.get_object(object_id)
    await _check_object_ownership(
        current_user, existing.department_id, existing.owner_user_id, service
    )

    # 检查是否有关联的 fact 数据（外键约束保护）
    async with service._scoped_session() as session:  # noqa: SLF001
        import sqlalchemy as sa

        from packages.facts.entities import Fact

        count_result = await session.execute(
            sa.select(sa.func.count(Fact.id)).where(Fact.object_id == object_id)
        )
        fact_count = int(count_result.scalar() or 0)
        if fact_count > 0:
            raise AppError(
                code="conflict",
                message=(
                    f"无法删除该实验对象，仍有 {fact_count} 条原始数据关联。"
                    "请先删除或转移关联数据后再操作。"
                ),
                retryable=False,
                fields={"object_id": str(object_id), "fact_count": fact_count},
            )

    await service.delete_object(object_id)


# ---- 辅助函数 ----


def _object_to_response(obj: object) -> ObjectResponse:
    """将 IndustrialObject ORM 实体转为响应模型。"""
    return ObjectResponse(
        id=str(obj.id),
        department_id=str(obj.department_id),
        object_type=obj.object_type,
        code=obj.code,
        display_name=obj.display_name,
        description=obj.description,
        equipment_id=str(obj.equipment_id) if obj.equipment_id else None,
        component_id=str(obj.component_id) if obj.component_id else None,
        visible_departments=list(getattr(obj, "visible_departments", []) or []),
        status=obj.status,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
        lock_version=obj.lock_version,
    )


def _object_to_list_item(obj: object) -> ObjectListItem:
    """将 IndustrialObject ORM 实体转为列表项。"""
    return ObjectListItem(
        id=str(obj.id),
        department_id=str(obj.department_id) if obj.department_id else None,
        object_type=obj.object_type,
        code=obj.code,
        display_name=obj.display_name,
        description=obj.description,
        equipment_id=str(obj.equipment_id) if obj.equipment_id else None,
        component_id=str(obj.component_id) if obj.component_id else None,
        visible_departments=list(getattr(obj, "visible_departments", []) or []),
        status=obj.status,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
        lock_version=obj.lock_version,
    )
