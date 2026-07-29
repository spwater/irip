"""工业对象图管理路由：创建 / 列表 / 详情 / 关系 / 后代遍历。

端点（IRIP Task 11）：
  POST   /api/v1/objects                      — 创建对象（standard:write）
  GET    /api/v1/objects                      — 列表，可选 ?type= 过滤（standard:read）
  GET    /api/v1/objects/{id}                 — 详情（standard:read）
  POST   /api/v1/objects/{id}/relations       — 添加关系（standard:write）
  DELETE /api/v1/objects/{id}/relations       — 移除关系（standard:write）
  GET    /api/v1/objects/{id}/descendants     — contains 层次后代（standard:read）
  GET    /api/v1/objects/{id}/relations        — 列出关系（standard:read）

安全约定：
- 创建/关系操作需 require_permission("standard:write")；
- 查询/后代遍历需 require_permission("standard:read")。
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from apps.api.dependencies.dept_scope import should_filter_by_department
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
    （需当前用户上下文查询 organization_id）。
    """
    raise NotImplementedError(
        "get_object_graph_service must be overridden via dependency_overrides"
    )


#: ObjectGraphService 依赖类型别名。
ObjectGraphServiceDep = Annotated[ObjectGraphService, Depends(get_object_graph_service)]


async def _check_object_ownership(
    current_user: CurrentUser,
    obj_department_id: UUID | None,
    service: ObjectGraphServiceDep,
) -> None:
    """检查当前用户是否可以编辑/删除对象（含后代继承）。

    上级单位自动拥有下级单位的编辑权限。
    可见单位的用户只能看不能改。平台管理员不受限制。
    """
    if not should_filter_by_department(current_user):
        return
    if obj_department_id is None:
        return
    if current_user.department_id is None:
        raise AppError(
            code="forbidden",
            message="只有所属单位的成员才能编辑/删除对象",
            retryable=False,
            fields={},
        )  # noqa: E501

    from apps.api.dependencies.dept_scope import get_visible_department_ids

    visible_ids = await get_visible_department_ids(current_user, service._factory)  # type: ignore[attr-defined]
    if obj_department_id not in visible_ids:
        raise AppError(
            code="forbidden",
            message="只有所属单位（或上级单位）的成员才能编辑/删除对象",
            retryable=False,
            fields={},
        )


# ---- 请求模型 ----


class CreateObjectRequest(BaseModel):
    """创建工业对象请求。"""

    object_type: str = Field(..., max_length=50, description="对象类型")
    display_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    parent_id: UUID | None = Field(None, description="父对象 ID")
    equipment_id: UUID | None = Field(None, description="关联设备 ID")
    department_id: str | None = Field(None, description="所属部门 UUID")
    visible_departments: list[str] = Field(default_factory=list, description="可见单位 UUID 列表")


class AddRelationRequest(BaseModel):
    """添加关系请求。"""

    source_id: UUID | None = Field(None, description="源对象 ID，留空时使用路径参数 {id}")
    target_id: UUID = Field(..., description="目标对象 ID")
    relation_type: Literal[
        "contains",
        "connected_to",
        "upstream_of",
        "downstream_of",
        "measures",
        "simulates",
        "equivalent_to",
    ] = Field(..., description="关系类型")


# ---- 响应模型 ----


class ObjectResponse(BaseModel):
    """工业对象详情响应。"""

    id: str
    organization_id: str
    object_type: str
    code: str
    display_name: str
    description: str | None
    parent_id: str | None
    equipment_id: str | None
    department_id: str | None
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
    parent_id: str | None
    equipment_id: str | None
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


class RelationResponse(BaseModel):
    """关系详情响应。"""

    id: str
    source_id: str
    target_id: str
    relation_type: str
    is_active: bool
    created_at: datetime


class DescendantsResponse(BaseModel):
    """后代遍历响应。"""

    root_id: str
    descendant_ids: list[str]


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
        parent_id=body.parent_id,
        equipment_id=body.equipment_id,
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
    # 实验室级数据隔离：非管理员按 department_id + visible_departments 过滤
    # 可见性规则：department_id == 用户实验室 OR visible_departments 包含用户实验室
    if should_filter_by_department(current_user):
        if current_user.department_id is None:
            return ObjectListResponse(items=[], next_cursor=None)
        filter_dept_id = current_user.department_id
        filter_visible_dept_id = current_user.department_id
    else:
        filter_dept_id = None
        filter_visible_dept_id = None

    # 多类型过滤：逗号分隔 → list 传给 service 做 IN 查询
    if object_type and "," in object_type:
        types = [t.strip() for t in object_type.split(",") if t.strip()]
        items, next_cursor = await service.list_objects(
            object_type=types,
            cursor=cursor,
            page_size=page_size,
            department_id=filter_dept_id,
            visible_dept_id=filter_visible_dept_id,
        )
        return ObjectListResponse(
            items=[_object_to_list_item(obj) for obj in items],
            next_cursor=next_cursor,
        )

    items, next_cursor = await service.list_objects(
        object_type=object_type,
        cursor=cursor,
        page_size=page_size,
        department_id=filter_dept_id,
        visible_dept_id=filter_visible_dept_id,
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
    # 归属检查：只有所属单位的成员才能编辑
    existing = await service.get_object(object_id)
    await _check_object_ownership(current_user, existing.department_id, service)

    obj = await service.update_object(
        object_id=object_id,
        display_name=body.display_name,
        description=body.description,
        object_type=body.object_type,
        equipment_id=body.equipment_id,
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
    # 归属检查：只有所属单位的成员才能操作
    existing = await service.get_object(object_id)
    await _check_object_ownership(current_user, existing.department_id, service)

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
    # 归属检查：只有所属单位的成员才能删除
    existing = await service.get_object(object_id)
    await _check_object_ownership(current_user, existing.department_id, service)

    await service.delete_object(object_id)


# ---- 端点：关系管理 ----


@objects_router.post("/{object_id}/relations", response_model=RelationResponse, status_code=201)
async def add_relation(
    object_id: UUID,
    body: AddRelationRequest,
    current_user: WriteUserDep,
    service: ObjectGraphServiceDep,
) -> RelationResponse:
    """添加对象间关系。

    若 source_id 留空，则使用路径参数 object_id 作为源对象。
    层次型关系（contains / upstream_of / downstream_of）会进行环检测。

    Args:
        object_id: 路径中的对象 ID（作为默认 source_id）。
        body: 添加关系请求体。
        current_user: 当前认证用户（需 standard:write 权限）。
        service: 对象图服务。

    Returns:
        RelationResponse: 关系详情（201 Created，幂等时 200）。

    Raises:
        AppError: code="self_relation"，当源与目标相同时。
        AppError: code="not_found"，当对象不存在时。
        AppError: code="object_cycle"，当层次型关系形成环时。
    """
    source_id = body.source_id if body.source_id is not None else object_id
    relation = await service.add_relation(
        source_id=source_id,
        target_id=body.target_id,
        relation_type=body.relation_type,
    )
    return _relation_to_response(relation)


@objects_router.delete("/{object_id}/relations", status_code=204)
async def remove_relation(
    object_id: UUID,
    current_user: WriteUserDep,
    service: ObjectGraphServiceDep,
    target_id: UUID = Query(..., description="目标对象 ID"),  # noqa: B008
    relation_type: str = Query(..., description="关系类型"),
) -> None:
    """移除对象间关系（标记 is_active=false）。

    Args:
        object_id: 源对象 ID（路径参数）。
        current_user: 当前认证用户（需 standard:write 权限）。
        service: 对象图服务。
        target_id: 目标对象 ID。
        relation_type: 关系类型。

    Raises:
        AppError: code="not_found"，当关系不存在时。
    """
    await service.remove_relation(
        source_id=object_id,
        target_id=target_id,
        relation_type=relation_type,
    )


@objects_router.get("/{object_id}/relations", response_model=list[RelationResponse])
async def list_relations(
    object_id: UUID,
    current_user: ReadUserDep,
    service: ObjectGraphServiceDep,
    relation_type: str | None = Query(None, description="关系类型过滤"),
) -> list[RelationResponse]:
    """列出对象的所有活跃关系。

    Args:
        object_id: 对象 UUID。
        current_user: 当前认证用户（需 standard:read 权限）。
        service: 对象图服务。
        relation_type: 可选关系类型过滤。

    Returns:
        list[RelationResponse]: 活跃关系列表。
    """
    relations = await service.get_relations(
        object_id=object_id,
        relation_type=relation_type,
    )
    return [_relation_to_response(r) for r in relations]


# ---- 端点：后代遍历 ----


@objects_router.get("/{object_id}/descendants", response_model=DescendantsResponse)
async def get_descendants(
    object_id: UUID,
    current_user: ReadUserDep,
    service: ObjectGraphServiceDep,
) -> DescendantsResponse:
    """获取 contains 层次的全部后代对象 ID。

    从指定对象出发，沿活跃的 contains 关系递归向下遍历，
    返回所有后代对象 ID（不含根对象自身）。

    Args:
        object_id: 根对象 UUID。
        current_user: 当前认证用户（需 standard:read 权限）。
        service: 对象图服务。

    Returns:
        DescendantsResponse: 根对象 ID + 后代 ID 列表。
    """
    descendant_ids = await service.descendants(object_id)
    return DescendantsResponse(
        root_id=str(object_id),
        descendant_ids=[str(d) for d in descendant_ids],
    )


# ---- 辅助函数 ----


def _object_to_response(obj: object) -> ObjectResponse:
    """将 IndustrialObject ORM 实体转为响应模型。"""
    return ObjectResponse(
        id=str(obj.id),  # type: ignore[attr-defined]
        organization_id=str(obj.organization_id),  # type: ignore[attr-defined]
        object_type=obj.object_type,  # type: ignore[attr-defined]
        code=obj.code,  # type: ignore[attr-defined]
        display_name=obj.display_name,  # type: ignore[attr-defined]
        description=obj.description,  # type: ignore[attr-defined]
        parent_id=str(obj.parent_id) if obj.parent_id else None,  # type: ignore[attr-defined]
        equipment_id=str(obj.equipment_id) if obj.equipment_id else None,  # type: ignore[attr-defined]
        department_id=str(obj.department_id) if getattr(obj, "department_id", None) else None,  # type: ignore[attr-defined]
        visible_departments=list(getattr(obj, "visible_departments", []) or []),  # type: ignore[attr-defined]
        status=obj.status,  # type: ignore[attr-defined]
        created_at=obj.created_at,  # type: ignore[attr-defined]
        updated_at=obj.updated_at,  # type: ignore[attr-defined]
        lock_version=obj.lock_version,  # type: ignore[attr-defined]
    )


def _object_to_list_item(obj: object) -> ObjectListItem:
    """将 IndustrialObject ORM 实体转为列表项。"""
    return ObjectListItem(
        id=str(obj.id),  # type: ignore[attr-defined]
        object_type=obj.object_type,  # type: ignore[attr-defined]
        code=obj.code,  # type: ignore[attr-defined]
        display_name=obj.display_name,  # type: ignore[attr-defined]
        description=obj.description,  # type: ignore[attr-defined]
        parent_id=str(obj.parent_id) if obj.parent_id else None,  # type: ignore[attr-defined]
        equipment_id=str(obj.equipment_id) if obj.equipment_id else None,  # type: ignore[attr-defined]
        department_id=str(obj.department_id) if getattr(obj, "department_id", None) else None,  # type: ignore[attr-defined]
        visible_departments=list(getattr(obj, "visible_departments", []) or []),  # type: ignore[attr-defined]
        status=obj.status,  # type: ignore[attr-defined]
        created_at=obj.created_at,  # type: ignore[attr-defined]
        updated_at=obj.updated_at,  # type: ignore[attr-defined]
        lock_version=obj.lock_version,  # type: ignore[attr-defined]
    )


def _relation_to_response(relation: object) -> RelationResponse:
    """将 ObjectRelation ORM 实体转为响应模型。"""
    return RelationResponse(
        id=str(relation.id),  # type: ignore[attr-defined]
        source_id=str(relation.source_id),  # type: ignore[attr-defined]
        target_id=str(relation.target_id),  # type: ignore[attr-defined]
        relation_type=relation.relation_type,  # type: ignore[attr-defined]
        is_active=relation.is_active,  # type: ignore[attr-defined]
        created_at=relation.created_at,  # type: ignore[attr-defined]
    )
