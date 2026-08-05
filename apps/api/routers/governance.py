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

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.composition import lookup_dept_id
from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from apps.api.dependencies.dept_scope import get_visible_department_ids
from packages.audit.events import AuditEventData
from packages.audit.redaction import redact
from packages.audit.repository import AuditRecorder
from packages.auth.entities import AppUser
from packages.auth.passwords import hash_password
from packages.auth.permissions import BUILTIN_ROLES
from packages.common.database import session_scope, scoped_session
from packages.common.errors import AppError
from packages.departments.entities import AppUserDepartment

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


# ---- 请求/响应模型 ----


class UserResponse(BaseModel):
    """用户响应体。"""

    id: str
    email: str
    display_name: str
    roles: list[str]
    status: str
    department_id: str | None
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """用户分页列表响应。"""

    items: list[UserResponse]
    next_cursor: str | None
    has_more: bool


class AssignRolesRequest(BaseModel):
    """分配角色请求体。"""

    roles: list[str] = Field(..., min_length=1, description="要分配的角色代码列表")


class CreateUserRequest(BaseModel):
    """新建用户请求体。"""

    email: str = Field(..., description="用户邮箱（登录账号）")
    display_name: str = Field(..., description="显示名")
    password: str = Field(..., min_length=6, description="初始密码（至少 6 位）")
    roles: list[str] = Field(..., min_length=1, description="角色代码列表（可多选）")
    department_id: str | None = Field(None, description="所属实验室 ID")


class UpdateUserStatusRequest(BaseModel):
    """启用/禁用用户请求体。"""

    status: str = Field(..., description="目标状态：active 或 disabled")


class UpdateUserRequest(BaseModel):
    """编辑用户请求体（邮箱不可修改）。"""

    display_name: str | None = Field(None, description="显示名")
    password: str | None = Field(None, min_length=6, description="新密码（留空则不修改）")
    roles: list[str] | None = Field(None, min_length=1, description="角色代码列表")
    department_id: str | None = Field(None, description="所属实验室 ID")


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


async def _record_audit(
    session: AsyncSession,
    actor: CurrentUser,
    action: str,
    resource_type: str | None,
    resource_id: UUID | None,
    payload: dict[str, Any] | None = None,
) -> None:
    """在当前事务中记录审计事件（脱敏后 INSERT）。

    Args:
        session: 数据库异步会话（由调用方管理事务）。
        actor: 当前操作用户。
        action: 审计动作字符串。
        resource_type: 资源类型。
        resource_id: 资源 ID。
        payload: 事件载荷（将被脱敏）。
    """
    redacted = redact(payload) if payload is not None else None
    event = AuditEventData(
        department_id=actor.department_id
        if actor.department_id is not None
        else actor.user_id,
        action=action,
        actor_user_id=actor.user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        payload=redacted,
    )
    await AuditRecorder.record(session, event)


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
    is_lab_director_only: bool = _is_lab_director(current_user) and not _is_platform_admin(current_user)

    # irip-ai-collab: lab_director 只能查看可见部门（含下级）的用户
    # 使用 get_visible_department_ids 做向下遍历，而非硬编码精确 department_id 匹配
    visible_dept_ids: list[UUID] = []
    if is_lab_director_only:
        if current_user.department_id is None:
            # 无 org 的 lab_director 返回空
            return UserListResponse(items=[], next_cursor=None, has_more=False)
        visible_dept_ids = await get_visible_department_ids(current_user, session_factory)

    async with session_factory() as session:
        stmt = sa.select(AppUser).order_by(AppUser.created_at.desc())

        # irip-ai-collab: lab_director 只能查看可见部门（含下级）的用户
        if is_lab_director_only:
            stmt = stmt.where(AppUser.department_id.in_(visible_dept_ids))

        if status is not None:
            stmt = stmt.where(AppUser.status == status)

        if cursor is not None:
            try:
                cursor_dt = datetime.fromisoformat(cursor)
            except ValueError as exc:
                raise AppError(
                    code="invalid_cursor",
                    message="无效的分页游标",
                    retryable=False,
                    fields={"cursor": cursor},
                ) from exc
            stmt = stmt.where(AppUser.created_at < cursor_dt)

        # lab_director 需要额外过滤掉平台级角色用户，所以多取一些行再过滤
        fetch_limit = limit + 1 if not is_lab_director_only else limit * 10 + 1
        stmt = stmt.limit(fetch_limit)
        result = await session.execute(stmt)
        rows: list[AppUser] = list(result.scalars().all())

    # irip-ai-collab: lab_director 不应看到平台管理员/监督员用户
    if is_lab_director_only:
        rows = [
            u for u in rows
            if not any(
                r in ("platform_administrator", "platform_auditor")
                for r in (u.roles if u.roles else [])
            )
        ]

    has_more: bool = len(rows) > limit
    page_items: list[AppUser] = rows[:limit]
    next_cursor: str | None = None
    if has_more and page_items:
        next_cursor = page_items[-1].created_at.isoformat()

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

    async with scoped_session(session_factory, current_user.department_id, current_user.user_id) as session:
        # 检查邮箱唯一性
        existing = await session.execute(sa.select(AppUser).where(AppUser.email == body.email))
        if existing.scalar_one_or_none() is not None:
            raise AppError(
                code="conflict",
                message=f"邮箱已存在: {body.email}",
                retryable=False,
                fields={"email": body.email},
            )

        # 确定 department_id：优先从所选实验室获取，未选实验室则查当前管理员的
        admin_dept_id = await lookup_dept_id(session_factory, current_user.user_id)
        dept_id = admin_dept_id
        if department_uuid is not None:
            dept = await session.execute(
                sa.text("SELECT id FROM department WHERE id = :dept_id"),
                {"dept_id": str(department_uuid)},
            )
            dept_row = dept.fetchone()
            if dept_row is not None and dept_row[0] is not None:
                dept_id = UUID(str(dept_row[0]))

        # 创建用户
        user = AppUser(
            email=body.email,
            display_name=body.display_name,
            password_hash=hash_password(body.password),
            status="active",
            roles=list(body.roles),
            department_id=dept_id,
        )
        session.add(user)
        await session.flush()

        # 同步写入 app_user_department 关联表（is_primary=True）
        # 确保"成员管理"抽屉（查 app_user_department）能看到该用户
        if department_uuid is not None:
            session.add(
                AppUserDepartment(
                    user_id=user.id,
                    department_id=department_uuid,
                    is_primary=True,
                )
            )
            await session.flush()

        # 记录审计
        await _record_audit(
            session,
            current_user,
            action="user.create",
            resource_type="user",
            resource_id=user.id,
            payload={"email": body.email, "display_name": body.display_name, "roles": body.roles},
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

    async with scoped_session(session_factory, current_user.department_id, current_user.user_id) as session:
        user = await session.get(AppUser, user_id)
        if user is None:
            raise AppError(
                code="not_found",
                message="用户不存在",
                retryable=False,
                fields={"user_id": str(user_id)},
            )

        # irip-ai-collab: lab_director 只能操作同 org 用户
        if not _is_platform_admin(current_user):
            if current_user.department_id is None or user.department_id != current_user.department_id:
                raise AppError(
                    code="forbidden",
                    message="只能管理本组织用户",
                    retryable=False,
                    fields={},
                )

        if body.display_name is not None:
            user.display_name = body.display_name
        if body.password is not None:
            user.password_hash = hash_password(body.password)
        if body.roles is not None:
            user.roles = list(body.roles)
        if body.department_id is not None:
            user.department_id = UUID(body.department_id)

        await session.flush()

        # 记录审计
        await _record_audit(
            session,
            actor=current_user,
            action="user.update",
            resource_type="user",
            resource_id=user.id,
            payload={
                "display_name": body.display_name,
                "roles": body.roles,
                "department_id": body.department_id,
                "password_changed": body.password is not None,
            },
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

    async with scoped_session(session_factory, current_user.department_id, current_user.user_id) as session:
        user: AppUser | None = await session.scalar(sa.select(AppUser).where(AppUser.id == user_id))
        if user is None:
            raise AppError(
                code="not_found",
                message=f"用户不存在: {user_id}",
                retryable=False,
                fields={"user_id": str(user_id)},
            )

        # irip-ai-collab: lab_director 只能操作同 org 用户
        if not _is_platform_admin(current_user):
            if current_user.department_id is None or user.department_id != current_user.department_id:
                raise AppError(
                    code="forbidden",
                    message="只能管理本组织用户",
                    retryable=False,
                    fields={},
                )

        existing_roles: set[str] = set(user.roles) if user.roles else set()
        new_roles_set: set[str] = existing_roles | set(body.roles)
        merged_roles: list[str] = sorted(new_roles_set)

        await session.execute(
            sa.update(AppUser)
            .values(
                roles=merged_roles,
                updated_at=sa.func.now(),
                lock_version=AppUser.lock_version + 1,
            )
            .where(AppUser.id == user_id)
        )

        await _record_audit(
            session,
            current_user,
            action="governance.user.assign_roles",
            resource_type="app_user",
            resource_id=user_id,
            payload={"roles_added": body.roles, "roles_after": merged_roles},
        )

        # 重新获取更新后的用户
        await session.refresh(user)

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

    async with scoped_session(session_factory, current_user.department_id, current_user.user_id) as session:
        user: AppUser | None = await session.scalar(sa.select(AppUser).where(AppUser.id == user_id))
        if user is None:
            raise AppError(
                code="not_found",
                message=f"用户不存在: {user_id}",
                retryable=False,
                fields={"user_id": str(user_id)},
            )

        # irip-ai-collab: lab_director 只能操作同 org 用户
        if not _is_platform_admin(current_user):
            if current_user.department_id is None or user.department_id != current_user.department_id:
                raise AppError(
                    code="forbidden",
                    message="只能管理本组织用户",
                    retryable=False,
                    fields={},
                )

        existing_roles: list[str] = list(user.roles) if user.roles else []
        updated_roles: list[str] = [r for r in existing_roles if r != role]

        await session.execute(
            sa.update(AppUser)
            .values(
                roles=updated_roles,
                updated_at=sa.func.now(),
                lock_version=AppUser.lock_version + 1,
            )
            .where(AppUser.id == user_id)
        )

        await _record_audit(
            session,
            current_user,
            action="governance.user.remove_role",
            resource_type="app_user",
            resource_id=user_id,
            payload={"role_removed": role, "roles_after": updated_roles},
        )

        await session.refresh(user)

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

    async with scoped_session(session_factory, current_user.department_id, current_user.user_id) as session:
        user: AppUser | None = await session.scalar(sa.select(AppUser).where(AppUser.id == user_id))
        if user is None:
            raise AppError(
                code="not_found",
                message=f"用户不存在: {user_id}",
                retryable=False,
                fields={"user_id": str(user_id)},
            )

        await session.execute(
            sa.update(AppUser)
            .values(
                status=body.status,
                updated_at=sa.func.now(),
                lock_version=AppUser.lock_version + 1,
            )
            .where(AppUser.id == user_id)
        )

        await _record_audit(
            session,
            current_user,
            action="governance.user.update_status",
            resource_type="app_user",
            resource_id=user_id,
            payload={"status": body.status},
        )

        await session.refresh(user)

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

    async with scoped_session(session_factory, current_user.department_id, current_user.user_id) as session:
        user = await session.get(AppUser, user_id)
        if user is None:
            raise AppError(
                code="not_found",
                message="用户不存在",
                retryable=False,
                fields={"user_id": str(user_id)},
            )

        # 记录审计（删除前记录）
        await _record_audit(
            session,
            actor=current_user,
            action="user.delete",
            resource_type="app_user",
            resource_id=user_id,
            payload={"email": user.email, "display_name": user.display_name},
        )

        # 先删除关联的 refresh_session（避免外键约束报错）
        await session.execute(
            sa.text("DELETE FROM refresh_session WHERE user_id = :uid"),
            {"uid": str(user_id)},
        )

        await session.delete(user)


# ============================================================
# P1-T1-03: 数据移交工具
# ============================================================

#: 允许移交的表白名单（均含 department_id 列）。
_TRANSFERABLE_TABLES: dict[str, str] = {
    "fact": "实验事实",
    "parameter": "参数",
    "model": "模型",
    "flow_definition": "流程定义",
    "flow_run": "流程运行",
    "equipment": "设备仪器",
}


class DataTransferRequest(BaseModel):
    """数据移交请求体。

    Attributes:
        table: 目标表名（必须在白名单中）。
        from_dept_id: 源部门 UUID。
        to_dept_id: 目标部门 UUID。
        dry_run: True 时只返回影响行数，不执行 UPDATE。
    """

    table: str = Field(..., description="目标表名（fact/parameter/model/flow_definition/flow_run/equipment）")
    from_dept_id: str = Field(..., description="源部门 UUID")
    to_dept_id: str = Field(..., description="目标部门 UUID")
    dry_run: bool = Field(False, description="True 时只返回影响行数，不执行")


class DataTransferResponse(BaseModel):
    """数据移交响应体。"""

    table: str
    from_dept_id: str
    to_dept_id: str
    dry_run: bool
    affected_rows: int


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
    # 验证表名
    if body.table not in _TRANSFERABLE_TABLES:
        raise AppError(
            code="validation_failed",
            message=f"不支持的数据表: {body.table}（允许: {', '.join(_TRANSFERABLE_TABLES.keys())}）",
            retryable=False,
            fields={"table": body.table},
        )

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

    # 不允许源和目标相同
    if from_uuid == to_uuid:
        raise AppError(
            code="validation_failed",
            message="源部门和目标部门不能相同",
            retryable=False,
            fields={},
        )

    async with scoped_session(session_factory, current_user.department_id, current_user.user_id) as session:
        # 统计影响行数
        count_stmt = sa.text(
            f"SELECT COUNT(*) FROM {body.table} WHERE department_id = :from_dept_id"
        )
        count_result = await session.execute(count_stmt, {"from_dept_id": str(from_uuid)})
        affected_rows: int = count_result.scalar() or 0

        if not body.dry_run and affected_rows > 0:
            # 执行 UPDATE
            update_stmt = sa.text(
                f"UPDATE {body.table} SET department_id = :to_dept_id "
                f"WHERE department_id = :from_dept_id"
            )
            await session.execute(
                update_stmt,
                {"to_dept_id": str(to_uuid), "from_dept_id": str(from_uuid)},
            )

            # 记录审计日志
            await _record_audit(
                session,
                current_user,
                action="governance.data_transfer",
                resource_type=body.table,
                resource_id=None,
                payload={
                    "table": body.table,
                    "from_dept_id": str(from_uuid),
                    "to_dept_id": str(to_uuid),
                    "affected_rows": affected_rows,
                },
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

#: 需统计 root 归属的表列表（表名 → 中文显示名）。
_ROOT_STATS_TABLES: dict[str, str] = {
    "fact": "实验事实",
    "parameter": "参数",
    "model": "模型",
    "flow_definition": "流程定义",
    "flow_run": "流程运行",
    "equipment": "设备仪器",
}


class RootDataStatsResponse(BaseModel):
    """root 部门数据量统计响应体。"""

    root_department_id: str
    root_department_name: str
    stats: list[dict[str, Any]]


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
    from packages.departments.entities import Department

    async with scoped_session(session_factory, current_user.department_id, current_user.user_id) as session:
        # 查找 root 部门
        dept_result = await session.execute(
            sa.select(Department).where(Department.code == "root")
        )
        root_dept = dept_result.scalar_one_or_none()
        if root_dept is None:
            raise AppError(
                code="not_found",
                message="root 部门不存在",
                retryable=False,
                fields={},
            )

        root_id = str(root_dept.id)
        root_name = root_dept.display_name

        # 统计各表行数
        stats: list[dict[str, Any]] = []
        for table_name, display_name in _ROOT_STATS_TABLES.items():
            count_stmt = sa.text(
                f"SELECT COUNT(*) FROM {table_name} WHERE department_id = :root_id"
            )
            count_result = await session.execute(count_stmt, {"root_id": root_id})
            count = count_result.scalar() or 0
            stats.append({
                "table": table_name,
                "display_name": display_name,
                "count": count,
            })

    return RootDataStatsResponse(
        root_department_id=root_id,
        root_department_name=root_name,
        stats=stats,
    )
