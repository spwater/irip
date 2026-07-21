"""用户-实验室关联管理路由（P1）。

端点（docs/arch-department.md §5 T03）：
  PUT  /api/v1/users/{user_id}/departments       — 批量设置用户实验室（user:manage）
  GET  /api/v1/users/{user_id}/departments       — 查询用户实验室列表（user:manage 或本人）
  GET  /api/v1/departments/{department_id}/users  — 查询实验室下用户（department:read）

安全约定：
- 批量设置需 require_permission("user:manage")；
- 查询用户实验室：需 user:manage 权限，或当前用户即 path param user_id（本人）；
- 查询实验室下用户需 require_permission("department:read")。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.dependencies.authorization import require_permission
from apps.api.dependencies.departments import get_user_department_service
from packages.common.errors import AppError
from packages.departments.user_departments import (
    DepartmentUserItem,
    UserDepartmentItem,
    UserDepartmentService,
)

#: 用户-实验室关联路由实例。
user_departments_router = APIRouter(tags=["user-departments"])

#: 需 user:manage 权限的当前用户依赖。
ManageUserDep = Annotated[CurrentUser, Depends(require_permission("user:manage"))]

#: 需 department:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("department:read"))]

#: UserDepartmentService 依赖类型别名。
UserDepartmentServiceDep = Annotated[
    UserDepartmentService, Depends(get_user_department_service)
]


# ---- 请求/响应模型 ----


class SetUserDepartmentsRequest(BaseModel):
    """批量设置用户所属实验室请求。"""

    department_ids: list[str]
    primary_department_id: str | None = None


class SetUserDepartmentsResponse(BaseModel):
    """批量设置用户所属实验室响应。"""

    ok: bool = True


class UserDepartmentItemResponse(BaseModel):
    """用户-实验室关联项响应。"""

    user_id: str
    department_id: str
    department_code: str
    department_display_name: str
    is_primary: bool


class DepartmentUserItemResponse(BaseModel):
    """实验室下用户项响应。"""

    user_id: str
    email: str
    display_name: str
    is_primary: bool


# ---- 端点 ----


@user_departments_router.put(
    "/api/v1/users/{user_id}/departments",
    response_model=SetUserDepartmentsResponse,
)
async def set_user_departments(
    user_id: UUID,
    body: SetUserDepartmentsRequest,
    current_user: ManageUserDep,
    service: UserDepartmentServiceDep,
) -> SetUserDepartmentsResponse:
    """批量设置用户所属实验室。

    全量替换：不在列表中的关联将被移除，新列表中的关联将被添加。
    primary_department_id 指定主要实验室（同一 user 最多一个 primary）。

    Args:
        user_id: 用户 UUID。
        body: 设置请求体。
        current_user: 当前认证用户（需 user:manage 权限）。
        service: 用户-实验室关联服务。

    Returns:
        SetUserDepartmentsResponse: 操作结果。
    """
    department_ids = [UUID(did) for did in body.department_ids]
    primary_id: UUID | None = None
    if body.primary_department_id is not None:
        primary_id = UUID(body.primary_department_id)

    await service.set_user_departments(
        user_id=user_id,
        department_ids=department_ids,
        primary_department_id=primary_id,
    )
    return SetUserDepartmentsResponse(ok=True)


@user_departments_router.get(
    "/api/v1/users/{user_id}/departments",
    response_model=list[UserDepartmentItemResponse],
)
async def get_user_departments(
    user_id: UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: UserDepartmentServiceDep,
) -> list[UserDepartmentItemResponse]:
    """查询用户所属实验室列表。

    权限：需 user:manage 权限，或当前用户即 path param user_id（本人）。

    Args:
        user_id: 用户 UUID。
        current_user: 当前认证用户。
        service: 用户-实验室关联服务。

    Returns:
        list[UserDepartmentItemResponse]: 用户-实验室关联列表。

    Raises:
        AppError: code="forbidden"，当无权访问时。
    """
    # 权限检查：user:manage 或本人
    is_self = current_user.user_id == user_id
    has_manage = _has_permission(current_user, "user:manage")
    if not is_self and not has_manage:
        raise AppError(
            code="forbidden",
            message="无权查看其他用户的实验室关联",
            retryable=False,
            fields={},
        )

    items: list[UserDepartmentItem] = await service.get_user_departments(user_id)
    return [
        UserDepartmentItemResponse(
            user_id=str(item.user_id),
            department_id=str(item.department_id),
            department_code=item.department_code,
            department_display_name=item.department_display_name,
            is_primary=item.is_primary,
        )
        for item in items
    ]


@user_departments_router.get(
    "/api/v1/departments/{department_id}/users",
    response_model=list[DepartmentUserItemResponse],
)
async def get_department_users(
    department_id: UUID,
    current_user: ReadUserDep,
    service: UserDepartmentServiceDep,
) -> list[DepartmentUserItemResponse]:
    """查询实验室下用户列表。

    Args:
        department_id: 实验室 UUID。
        current_user: 当前认证用户（需 department:read 权限）。
        service: 用户-实验室关联服务。

    Returns:
        list[DepartmentUserItemResponse]: 实验室下用户列表。
    """
    items: list[DepartmentUserItem] = await service.get_department_users(department_id)
    return [
        DepartmentUserItemResponse(
            user_id=str(item.user_id),
            email=item.email,
            display_name=item.display_name,
            is_primary=item.is_primary,
        )
        for item in items
    ]


def _has_permission(user: CurrentUser, action: str) -> bool:
    """检查用户角色是否拥有指定权限（基于 BUILTIN_ROLES）。

    Args:
        user: 当前用户。
        action: 权限字符串。

    Returns:
        bool: 有权返回 True。
    """
    from packages.auth.permissions import BUILTIN_ROLES

    for role_code in user.roles:
        role_def = BUILTIN_ROLES.get(role_code)
        if role_def is not None:
            permissions = role_def["permissions"]
            if isinstance(permissions, list) and action in permissions:
                return True
    return False
