"""治理 API 路由：用户管理。

端点（IRIP V3-T02）：
  GET    /api/v1/governance/users                      — 列出用户
  POST   /api/v1/governance/users                      — 新建用户
  POST   /api/v1/governance/users/{id}/roles           — 分配角色
  DELETE /api/v1/governance/users/{id}/roles/{role}    — 移除角色
  PATCH  /api/v1/governance/users/{id}/status          — 启用/禁用用户

安全约定：
- 全部端点需 Authorization: Bearer <jwt> + require_permission("user:manage")；
- 错误响应统一格式：{"error": {"code", "message", "retryable", "fields"}}。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.composition import lookup_dept_id
from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from apps.api.dependencies.dept_scope import get_visible_department_ids
from apps.api.schemas.governance import (
    AssignRolesRequest,
    CreateUserRequest,
    DataTransferRequest,
    DataTransferResponse,
    RootDataStatsResponse,
    UpdateUserRequest,
    UpdateUserStatusRequest,
    UserListResponse,
    UserResponse,
)
from packages.auth.entities import AppUser
from packages.auth.permissions import BUILTIN_ROLES
from packages.common.errors import AppError
from packages.governance.governance_service import GovernanceService

#: 路由实例。
governance_router = APIRouter(prefix="/api/v1/governance", tags=["governance"])

#: irip-ai-collab: 允许 platform_administrator 和 lab_director 访问用户管理。
#: platform_administrator 需 user:manage 权限，lab_director 需 role:assign 权限。
ManageUserDep = Annotated[CurrentUser, Depends(require_permission("user:manage"))]
ManageRoleDep = Annotated[CurrentUser, Depends(require_permission("role:assign"))]


# ---- 依赖占位（由应用启动或测试覆盖）----


def get_governance_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取数据库会话工厂（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_governance_session_factory must be overridden via dependency_overrides"
    )


GovernanceSessionFactoryDep = Annotated[
    async_sessionmaker[AsyncSession], Depends(get_governance_session_factory)
]


# ---- 请求/响应模型已提取到 apps/api/schemas/governance.py ----


# ---- 辅助函数 ----


def _to_user_response(user: AppUser) -> UserResponse:
    """将 AppUser ORM 实体转换为响应模型。"""
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        roles=list(user.roles) if user.roles else [],
        status=user.status,
        department_id=str(user.department_id) if user.department_id is not None else None,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _validate_role_codes(roles: list[str]) -> None:
    """验证角色代码是否全部为内置角色。

    Args:
        roles: 角色代码列表。

    Raises:
        AppError: code="validation_failed"，当存在未知角色代码时。
    """
    for role_code in roles:
        if role_code not in BUILTIN_ROLES:
            raise AppError(
                code="validation_failed",
                message=f"未知角色代码: {role_code}",
                retryable=False,
                fields={"roles": role_code},
            )


def _is_platform_admin(user: CurrentUser) -> bool:
    """检查用户是否为 platform_administrator。

    Args:
        user: 当前用户。

    Returns:
        bool: 是否为平台管理员。
    """
    return "platform_administrator" in (user.roles or [])


def _is_lab_director(user: CurrentUser) -> bool:
    """检查用户是否为 lab_director。

    Args:
        user: 当前用户。

    Returns:
        bool: 是否为实验室负责人。
    """
    return "lab_director" in (user.roles or [])


def _can_manage_roles(user: CurrentUser) -> bool:
    """检查用户是否有权管理用户角色（platform_administrator 或 lab_director）。

    Args:
        user: 当前用户。

    Returns:
        bool: 是否有权管理角色。
    """
    return _is_platform_admin(user) or _is_lab_director(user)


def _get_assignable_roles(user: CurrentUser) -> list[str]:
    """获取当前用户可分配的角色列表。

    platform_administrator: 全部 5 个角色
    lab_director: 仅 lab_member / lab_viewer

    Args:
        user: 当前用户。

    Returns:
        list[str]: 可分配的角色代码列表。
    """
    if _is_platform_admin(user):
        return list(BUILTIN_ROLES.keys())
    # lab_director 只能分配 lab_member / lab_viewer
    return ["lab_member", "lab_viewer"]


def _validate_assignable_roles(user: CurrentUser, roles: list[str]) -> None:
    """验证角色代码在当前用户可分配范围内。

    Args:
        user: 当前用户。
        roles: 要分配的角色代码列表。

    Raises:
        AppError: code="forbidden"，当角色超出可分配范围时。
    """
    assignable = _get_assignable_roles(user)
    for role_code in roles:
        if role_code not in assignable:
            raise AppError(
                code="forbidden",
                message=f"无权分配角色: {role_code}（仅可分配 {', '.join(assignable)}）",
                retryable=False,
                fields={"roles": role_code},
            )


def _make_service(
    current_user: CurrentUser,
    session_factory: async_sessionmaker[AsyncSession],
) -> GovernanceService:
    """构建 GovernanceService 实例。"""
    return GovernanceService(
        session_factory=session_factory,
        department_id=current_user.department_id,
        actor_id=current_user.user_id,
    )


# ---- 用户管理端点 ----


@governance_router.get("/users", response_model=UserListResponse)
async def list_users(
    current_user: ManageRoleDep,
    session_factory: GovernanceSessionFactoryDep,
    status: str | None = Query(None, description="状态筛选（active / disabled）"),
    cursor: str | None = Query(None, description="分页游标"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
) -> UserListResponse:
    """列出用户（分页）。

    irip-ai-collab: 允许 platform_administrator（user:manage）和 lab_director（role:assign）访问。
    lab_director 只能查看同 organization 的用户。

    Args:
        current_user: 当前认证用户（需 role:assign 权限）。
        session_factory: 数据库会话工厂。
        status: 状态筛选。
        cursor: 分页游标（上一页最后一条记录的 created_at ISO 字符串）。
        limit: 每页数量（最大 100）。

    Returns:
        UserListResponse: 分页用户列表。
    """
    is_lab_director_only: bool = _is_lab_director(current_user) and not _is_platform_admin(
        current_user
    )

    # irip-ai-collab: lab_director 只能查看可见部门（含下级）的用户
    # 使用 get_visible_department_ids 做向下遍历，而非硬编码精确 department_id 匹配
    visible_dept_ids: list[UUID] = []
    if is_lab_director_only:
        if current_user.department_id is None:
            # 无 org 的 lab_director 返回空
            return UserListResponse(items=[], next_cursor=None, has_more=False)
        visible_dept_ids = await get_visible_department_ids(current_user, session_factory)

    service = _make_service(current_user, session_factory)
    page_items, has_more, next_cursor = await service.list_users(
        status=status,
        cursor=cursor,
        limit=limit,
        visible_dept_ids=visible_dept_ids if is_lab_director_only else None,
        filter_platform_users=is_lab_director_only,
    )

    return UserListResponse(
        items=[_to_user_response(u) for u in page_items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@governance_router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    body: CreateUserRequest,
    current_user: ManageUserDep,
    session_factory: GovernanceSessionFactoryDep,
) -> UserResponse:
    """新建用户（仅 platform_administrator）。

    Args:
        body: 新建用户请求体（邮箱、显示名、密码、角色）。
        current_user: 当前认证用户（需 user:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        UserResponse: 新建用户信息。

    Raises:
        AppError: code="conflict"，当邮箱已存在时。
        AppError: code="validation_failed"，当角色代码未知时。
    """
    # 验证角色代码
    _validate_role_codes(body.roles)

    # 解析实验室 ID（可选）
    department_uuid: UUID | None = None
    if body.department_id is not None:
        try:
            department_uuid = UUID(body.department_id)
        except ValueError as exc:
            raise AppError(
                code="validation_failed",
                message="无效的实验室 ID",
                retryable=False,
                fields={"department_id": body.department_id},
            ) from exc

    # 确定 department_id：优先从所选实验室获取，未选实验室则查当前管理员的
    admin_dept_id = await lookup_dept_id(session_factory, current_user.user_id)

    service = _make_service(current_user, session_factory)
    user = await service.create_user(
        email=body.email,
        display_name=body.display_name,
        password=body.password,
        roles=body.roles,
        department_uuid=department_uuid,
        admin_dept_id=admin_dept_id,
    )

    return _to_user_response(user)


@governance_router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    body: UpdateUserRequest,
    current_user: ManageRoleDep,
    session_factory: GovernanceSessionFactoryDep,
) -> UserResponse:
    """编辑用户信息（邮箱不可修改）。

    irip-ai-collab: 允许 platform_administrator 和 lab_director 访问。
    lab_director 只能编辑同 org 用户，且只能分配 lab_member / lab_viewer 角色。

    Args:
        user_id: 目标用户 UUID。
        body: 编辑用户请求体。
        current_user: 当前认证用户（需 role:assign 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        UserResponse: 更新后的用户信息。

    Raises:
        AppError: code="not_found"，当用户不存在时。
        AppError: code="validation_failed"，当角色代码未知时。
        AppError: code="forbidden"，当 lab_director 操作非同 org 用户或分配超出范围角色时。
    """
    # 验证角色代码
    if body.roles is not None:
        _validate_role_codes(body.roles)
        # irip-ai-collab: lab_director 只能分配 lab_member / lab_viewer
        if not _is_platform_admin(current_user):
            _validate_assignable_roles(current_user, body.roles)

    service = _make_service(current_user, session_factory)

    # lab_director 只能操作同 org 用户 — 需要先查用户再校验
    if not _is_platform_admin(current_user):
        # 先用 service 查用户做同 org 校验
        async with session_factory() as check_session:
            target = await check_session.get(AppUser, user_id)
            if target is not None:
                if (
                    current_user.department_id is None
                    or target.department_id != current_user.department_id
                ):
                    raise AppError(
                        code="forbidden",
                        message="只能管理本组织用户",
                        retryable=False,
                        fields={},
                    )

    user = await service.update_user(
        user_id=user_id,
        display_name=body.display_name,
        password=body.password,
        roles=body.roles,
        department_id=body.department_id,
    )

    return _to_user_response(user)


@governance_router.post("/users/{user_id}/roles", response_model=UserResponse)
async def assign_roles(
    user_id: UUID,
    body: AssignRolesRequest,
    current_user: ManageRoleDep,
    session_factory: GovernanceSessionFactoryDep,
) -> UserResponse:
    """分配角色给用户（合并到已有角色列表）。

    irip-ai-collab: 允许 platform_administrator 和 lab_director 访问。
    lab_director 只能操作同 org 用户，且只能分配 lab_member / lab_viewer 角色。

    Args:
        user_id: 目标用户 UUID。
        body: 角色分配请求体。
        current_user: 当前认证用户（需 role:assign 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        UserResponse: 更新后的用户信息。

    Raises:
        AppError: code="not_found"，当用户不存在时。
        AppError: code="validation_failed"，当角色代码未知时。
        AppError: code="forbidden"，当 lab_director 操作非同 org 用户或分配超出范围角色时。
    """
    _validate_role_codes(body.roles)
    # irip-ai-collab: lab_director 只能分配 lab_member / lab_viewer
    if not _is_platform_admin(current_user):
        _validate_assignable_roles(current_user, body.roles)

    service = _make_service(current_user, session_factory)

    # lab_director 只能操作同 org 用户
    if not _is_platform_admin(current_user):
        async with session_factory() as check_session:
            target = await check_session.get(AppUser, user_id)
            if target is not None:
                if (
                    current_user.department_id is None
                    or target.department_id != current_user.department_id
                ):
                    raise AppError(
                        code="forbidden",
                        message="只能管理本组织用户",
                        retryable=False,
                        fields={},
                    )

    user = await service.assign_roles(
        user_id=user_id,
        roles_to_add=body.roles,
    )

    return _to_user_response(user)


@governance_router.delete("/users/{user_id}/roles/{role}", response_model=UserResponse)
async def remove_role(
    user_id: UUID,
    role: str,
    current_user: ManageRoleDep,
    session_factory: GovernanceSessionFactoryDep,
) -> UserResponse:
    """移除用户的指定角色。

    irip-ai-collab: 允许 platform_administrator 和 lab_director 访问。
    lab_director 只能操作同 org 用户，且只能移除 lab_member / lab_viewer 角色。

    Args:
        user_id: 目标用户 UUID。
        role: 要移除的角色代码。
        current_user: 当前认证用户（需 role:assign 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        UserResponse: 更新后的用户信息。

    Raises:
        AppError: code="not_found"，当用户不存在时。
        AppError: code="forbidden"，当 lab_director 操作非同 org 用户或移除超出范围角色时。
    """
    # irip-ai-collab: lab_director 只能移除 lab_member / lab_viewer 角色
    if not _is_platform_admin(current_user):
        _validate_assignable_roles(current_user, [role])

    service = _make_service(current_user, session_factory)

    # lab_director 只能操作同 org 用户
    if not _is_platform_admin(current_user):
        async with session_factory() as check_session:
            target = await check_session.get(AppUser, user_id)
            if target is not None:
                if (
                    current_user.department_id is None
                    or target.department_id != current_user.department_id
                ):
                    raise AppError(
                        code="forbidden",
                        message="只能管理本组织用户",
                        retryable=False,
                        fields={},
                    )

    user = await service.remove_role(
        user_id=user_id,
        role=role,
    )

    return _to_user_response(user)


@governance_router.patch("/users/{user_id}/status", response_model=UserResponse)
async def update_user_status(
    user_id: UUID,
    body: UpdateUserStatusRequest,
    current_user: ManageUserDep,
    session_factory: GovernanceSessionFactoryDep,
) -> UserResponse:
    """启用/禁用用户（仅 platform_administrator）。

    Args:
        user_id: 目标用户 UUID。
        body: 状态切换请求体。
        current_user: 当前认证用户（需 user:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        UserResponse: 更新后的用户信息。

    Raises:
        AppError: code="not_found"，当用户不存在时。
        AppError: code="validation_failed"，当 status 非法时。
    """
    if body.status not in ("active", "disabled"):
        raise AppError(
            code="validation_failed",
            message=f"无效的用户状态: {body.status}",
            retryable=False,
            fields={"status": body.status},
        )

    service = _make_service(current_user, session_factory)
    user = await service.update_user_status(
        user_id=user_id,
        status=body.status,
    )

    return _to_user_response(user)


@governance_router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: UUID,
    current_user: ManageUserDep,
    session_factory: GovernanceSessionFactoryDep,
) -> None:
    """删除用户（物理删除，仅 platform_administrator）。

    Args:
        user_id: 目标用户 UUID。
        current_user: 当前认证用户（需 user:manage 权限）。
        session_factory: 数据库会话工厂。

    Raises:
        AppError: code="not_found"，当用户不存在时。
        AppError: code="forbidden"，当尝试删除自己时。
    """
    # 禁止删除自己
    if user_id == current_user.user_id:
        raise AppError(
            code="forbidden",
            message="不能删除当前登录的账号",
            retryable=False,
            fields={},
        )

    service = _make_service(current_user, session_factory)
    await service.delete_user(user_id)


# ============================================================
# P1-T1-03: 数据移交工具
# ============================================================


@governance_router.post("/data-transfer", response_model=DataTransferResponse)
async def data_transfer(
    body: DataTransferRequest,
    current_user: ManageUserDep,
    session_factory: GovernanceSessionFactoryDep,
) -> DataTransferResponse:
    """批量移交数据归属部门（仅 platform_administrator）。

    将指定表中 department_id = from_dept_id 的所有行更新为 to_dept_id。
    dry_run=True 时只返回影响行数，不执行 UPDATE。

    Args:
        body: 数据移交请求体。
        current_user: 当前认证用户（需 user:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        DataTransferResponse: 移交结果（含影响行数）。

    Raises:
        AppError: code="validation_failed"，当表名不在白名单或部门 ID 无效时。
    """
    # 验证 UUID
    try:
        from_uuid = UUID(body.from_dept_id)
        to_uuid = UUID(body.to_dept_id)
    except ValueError as exc:
        raise AppError(
            code="validation_failed",
            message="无效的部门 ID（需 UUID 格式）",
            retryable=False,
            fields={"from_dept_id": body.from_dept_id, "to_dept_id": body.to_dept_id},
        ) from exc

    service = _make_service(current_user, session_factory)
    affected_rows = await service.transfer_data(
        table=body.table,
        from_dept_id=from_uuid,
        to_dept_id=to_uuid,
        dry_run=body.dry_run,
    )

    return DataTransferResponse(
        table=body.table,
        from_dept_id=str(from_uuid),
        to_dept_id=str(to_uuid),
        dry_run=body.dry_run,
        affected_rows=affected_rows,
    )


# ============================================================
# P1-T1-05: root 部门数据量监控
# ============================================================


@governance_router.get("/root-data-stats", response_model=RootDataStatsResponse)
async def get_root_data_stats(
    current_user: ManageUserDep,
    session_factory: GovernanceSessionFactoryDep,
) -> RootDataStatsResponse:
    """统计 root 部门归属的各表数据量（仅 platform_administrator）。

    返回 fact/parameter/model/flow_definition/flow_run/equipment 各表中
    department_id = root 部门 ID 的行数。

    Args:
        current_user: 当前认证用户（需 user:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        RootDataStatsResponse: 各表 root 归属行数统计。

    Raises:
        AppError: code="not_found"，当 root 部门不存在时。
    """
    service = _make_service(current_user, session_factory)
    root_id, root_name, stats = await service.get_root_data_stats()

    return RootDataStatsResponse(
        root_department_id=root_id,
        root_department_name=root_name,
        stats=stats,
    )
