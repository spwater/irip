"""实验室管理路由：创建、列表、详情、编辑、状态切换、删除。

端点（docs/arch-department.md §5 T03）：
  POST   /api/v1/departments           — 创建实验室（department:manage）
  GET    /api/v1/departments           — 分页列表（department:read）
  GET    /api/v1/departments/{id}      — 详情（department:read）
  PATCH  /api/v1/departments/{id}      — 编辑（department:manage，不含 code）
  PATCH  /api/v1/departments/{id}/status — 启用/禁用（department:manage）
  DELETE /api/v1/departments/{id}      — 删除（department:manage，子部门数和仪器数均为0时）

安全约定：
- 创建/编辑/状态切换/删除需 require_permission("department:manage")；
- 列表/详情需 require_permission("department:read")；
- code 创建后锁定：UpdateDepartmentRequest 不含 code 字段；
- 乐观锁：编辑/状态切换请求必须携带 lock_version；
- 软禁用：status='disabled'；
- 物理删除：DELETE 端点，前置条件子部门数为0且仪器数为0。
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from apps.api.dependencies.departments import get_department_service
from packages.departments.service import DepartmentService

#: 路由实例。
departments_router = APIRouter(prefix="/api/v1/departments", tags=["departments"])

#: 需 department:manage 权限的当前用户依赖。
ManageUserDep = Annotated[CurrentUser, Depends(require_permission("department:manage"))]

#: 需 department:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("department:read"))]


#: DepartmentService 依赖类型别名。
DepartmentServiceDep = Annotated[
    DepartmentService, Depends(get_department_service)
]


# ---- 请求模型 ----


class CreateDepartmentRequest(BaseModel):
    """创建实验室请求。

    code 创建后锁定不可修改。
    """

    code: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="实验室编码，仅小写字母/数字/下划线，创建后锁定",
    )
    display_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    sort_order: int = Field(0, ge=0)
    parent_id: str | None = Field(
        None, description="上级部门ID，顶级部门为null"
    )


class UpdateDepartmentRequest(BaseModel):
    """编辑实验室请求（code 不可修改，不在请求体中）。"""

    display_name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    sort_order: int = Field(0, ge=0)
    lock_version: int = Field(..., ge=0)
    parent_id: str | None = Field(
        None, description="上级部门ID，顶级部门为null"
    )


class UpdateDepartmentStatusRequest(BaseModel):
    """启用/禁用实验室请求。"""

    status: Literal["active", "disabled"]
    lock_version: int = Field(..., ge=0)


# ---- 响应模型 ----


class DepartmentResponse(BaseModel):
    """实验室详情响应。"""

    id: str
    organization_id: str
    code: str
    display_name: str
    description: str | None
    status: str
    sort_order: int
    created_at: datetime
    updated_at: datetime
    lock_version: int
    parent_id: str | None


class DepartmentListItem(BaseModel):
    """实验室列表项（含成员数、子部门数、仪器数）。"""

    id: str
    code: str
    display_name: str
    status: str
    sort_order: int
    member_count: int
    parent_id: str | None
    children_count: int
    equipment_count: int


class DepartmentListResponse(BaseModel):
    """实验室分页列表响应。"""

    items: list[DepartmentListItem]
    next_cursor: str | None
    has_more: bool


# ---- 辅助函数 ----


def _to_response(dept: object) -> DepartmentResponse:
    """将 Department ORM 实体转换为响应模型。"""
    parent_id_val = getattr(dept, "parent_id", None)
    return DepartmentResponse(
        id=str(dept.id),  # type: ignore[attr-defined]
        organization_id=str(dept.organization_id),  # type: ignore[attr-defined]
        code=dept.code,  # type: ignore[attr-defined]
        display_name=dept.display_name,  # type: ignore[attr-defined]
        description=dept.description,  # type: ignore[attr-defined]
        status=dept.status,  # type: ignore[attr-defined]
        sort_order=dept.sort_order,  # type: ignore[attr-defined]
        created_at=dept.created_at,  # type: ignore[attr-defined]
        updated_at=dept.updated_at,  # type: ignore[attr-defined]
        lock_version=dept.lock_version,  # type: ignore[attr-defined]
        parent_id=str(parent_id_val) if parent_id_val is not None else None,
    )


# ---- 端点 ----


@departments_router.post("", response_model=DepartmentResponse, status_code=201)
async def create_department(
    body: CreateDepartmentRequest,
    current_user: ManageUserDep,
    service: DepartmentServiceDep,
) -> DepartmentResponse:
    """创建实验室。

    code 创建后锁定不可修改。编码在组织内唯一。

    Args:
        body: 创建请求体。
        current_user: 当前认证用户（需 department:manage 权限）。
        service: 实验室服务。

    Returns:
        DepartmentResponse: 新创建的实验室（201 Created）。

    Raises:
        AppError: code="conflict"，当编码已存在时。
    """
    dept = await service.create(
        code=body.code,
        display_name=body.display_name,
        description=body.description,
        sort_order=body.sort_order,
        parent_id=UUID(body.parent_id) if body.parent_id else None,
    )
    return _to_response(dept)


@departments_router.get("", response_model=DepartmentListResponse)
async def list_departments(
    current_user: ReadUserDep,
    service: DepartmentServiceDep,
    status: str | None = Query(None, description="状态筛选"),
    cursor: str | None = Query(None, description="分页游标"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
) -> DepartmentListResponse:
    """分页查询实验室列表（含成员数）。

    Args:
        current_user: 当前认证用户（需 department:read 权限）。
        service: 实验室服务。
        status: 状态筛选（active / disabled）。
        cursor: 分页游标。
        limit: 每页数量。

    Returns:
        DepartmentListResponse: 分页列表。
    """
    result = await service.list(status=status, cursor=cursor, limit=limit)
    items = [
        DepartmentListItem(
            id=str(dept.id),
            code=dept.code,
            display_name=dept.display_name,
            status=dept.status,
            sort_order=dept.sort_order,
            member_count=member_count,
            parent_id=str(dept.parent_id) if dept.parent_id is not None else None,
            children_count=children_count,
            equipment_count=equipment_count,
        )
        for dept, member_count, children_count, equipment_count in result.items
    ]
    return DepartmentListResponse(
        items=items,
        next_cursor=result.next_cursor,
        has_more=result.has_more,
    )


@departments_router.get("/{department_id}", response_model=DepartmentResponse)
async def get_department(
    department_id: UUID,
    current_user: ReadUserDep,
    service: DepartmentServiceDep,
) -> DepartmentResponse:
    """查询单个实验室详情。

    Args:
        department_id: 实验室 UUID。
        current_user: 当前认证用户（需 department:read 权限）。
        service: 实验室服务。

    Returns:
        DepartmentResponse: 实验室详情。

    Raises:
        AppError: code="not_found"，当实验室不存在时。
    """
    dept = await service.get(department_id)
    return _to_response(dept)


@departments_router.patch("/{department_id}", response_model=DepartmentResponse)
async def update_department(
    department_id: UUID,
    body: UpdateDepartmentRequest,
    current_user: ManageUserDep,
    service: DepartmentServiceDep,
) -> DepartmentResponse:
    """编辑实验室（code 不可修改）。

    Args:
        department_id: 实验室 UUID。
        body: 编辑请求体（不含 code）。
        current_user: 当前认证用户（需 department:manage 权限）。
        service: 实验室服务。

    Returns:
        DepartmentResponse: 更新后的实验室。

    Raises:
        AppError: code="not_found"，当实验室不存在时。
        AppError: code="conflict"，当 lock_version 不匹配时。
    """
    dept = await service.update(
        department_id=department_id,
        display_name=body.display_name,
        description=body.description,
        sort_order=body.sort_order,
        lock_version=body.lock_version,
        parent_id=UUID(body.parent_id) if body.parent_id else None,
    )
    return _to_response(dept)


@departments_router.patch(
    "/{department_id}/status", response_model=DepartmentResponse
)
async def update_department_status(
    department_id: UUID,
    body: UpdateDepartmentStatusRequest,
    current_user: ManageUserDep,
    service: DepartmentServiceDep,
) -> DepartmentResponse:
    """启用/禁用实验室（软禁用）。

    Args:
        department_id: 实验室 UUID。
        body: 状态切换请求体。
        current_user: 当前认证用户（需 department:manage 权限）。
        service: 实验室服务。

    Returns:
        DepartmentResponse: 更新后的实验室。

    Raises:
        AppError: code="not_found"，当实验室不存在时。
        AppError: code="conflict"，当 lock_version 不匹配时。
    """
    dept = await service.set_status(
        department_id=department_id,
        status=body.status,
        lock_version=body.lock_version,
    )
    return _to_response(dept)


@departments_router.delete("/{department_id}", status_code=204)
async def delete_department(
    department_id: UUID,
    current_user: ManageUserDep,
    service: DepartmentServiceDep,
) -> None:
    """删除实验室（物理删除）。

    前置条件：子部门数为 0 且仪器数为 0。

    Raises:
        AppError: code="not_found"，当实验室不存在时。
        AppError: code="conflict"，当存在子部门或仪器时不允许删除。
    """
    await service.delete(department_id)
