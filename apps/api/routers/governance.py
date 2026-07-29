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

from apps.api.composition import lookup_org_id
from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.audit.events import AuditEventData
from packages.audit.redaction import redact
from packages.audit.repository import AuditRecorder
from packages.auth.entities import AppUser
from packages.auth.passwords import hash_password
from packages.auth.permissions import BUILTIN_ROLES
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.departments.entities import AppUserDepartment

#: 路由实例。
governance_router = APIRouter(prefix="/api/v1/governance", tags=["governance"])

#: 需 user:manage 权限的当前用户依赖。
ManageUserDep = Annotated[CurrentUser, Depends(require_permission("user:manage"))]


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
        organization_id=actor.user_id,  # V3 简化：暂用 user_id 作为 org 占位
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
    current_user: ManageUserDep,
    session_factory: GovernanceSessionFactoryDep,
    status: str | None = Query(None, description="状态筛选（active / disabled）"),
    cursor: str | None = Query(None, description="分页游标"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
) -> UserListResponse:
    """列出用户（分页）。

    Args:
        current_user: 当前认证用户（需 user:manage 权限）。
        session_factory: 数据库会话工厂。
        status: 状态筛选。
        cursor: 分页游标（上一页最后一条记录的 created_at ISO 字符串）。
        limit: 每页数量（最大 100）。

    Returns:
        UserListResponse: 分页用户列表。
    """
    async with session_factory() as session:
        stmt = sa.select(AppUser).order_by(AppUser.created_at.desc())

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

        stmt = stmt.limit(limit + 1)
        result = await session.execute(stmt)
        rows: list[AppUser] = list(result.scalars().all())

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
    """新建用户。

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

    async with session_scope(session_factory) as session:
        # 检查邮箱唯一性
        existing = await session.execute(sa.select(AppUser).where(AppUser.email == body.email))
        if existing.scalar_one_or_none() is not None:
            raise AppError(
                code="conflict",
                message=f"邮箱已存在: {body.email}",
                retryable=False,
                fields={"email": body.email},
            )

        # 确定 organization_id：优先从所选实验室获取，未选实验室则查当前管理员的
        admin_org_id = await lookup_org_id(session_factory, current_user.user_id)
        org_id = admin_org_id
        if department_uuid is not None:
            dept = await session.execute(
                sa.text("SELECT organization_id FROM department WHERE id = :dept_id"),
                {"dept_id": str(department_uuid)},
            )
            dept_row = dept.fetchone()
            if dept_row is not None and dept_row[0] is not None:
                org_id = UUID(str(dept_row[0]))

        # 创建用户
        user = AppUser(
            email=body.email,
            display_name=body.display_name,
            password_hash=hash_password(body.password),
            status="active",
            roles=list(body.roles),
            organization_id=org_id,
            department_id=department_uuid,
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
    current_user: ManageUserDep,
    session_factory: GovernanceSessionFactoryDep,
) -> UserResponse:
    """编辑用户信息（邮箱不可修改）。

    Args:
        user_id: 目标用户 UUID。
        body: 编辑用户请求体。
        current_user: 当前认证用户（需 user:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        UserResponse: 更新后的用户信息。

    Raises:
        AppError: code="not_found"，当用户不存在时。
        AppError: code="validation_failed"，当角色代码未知时。
    """
    # 验证角色代码
    if body.roles is not None:
        _validate_role_codes(body.roles)

    async with session_scope(session_factory) as session:
        user = await session.get(AppUser, user_id)
        if user is None:
            raise AppError(
                code="not_found",
                message="用户不存在",
                retryable=False,
                fields={"user_id": str(user_id)},
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
    current_user: ManageUserDep,
    session_factory: GovernanceSessionFactoryDep,
) -> UserResponse:
    """分配角色给用户（合并到已有角色列表）。

    Args:
        user_id: 目标用户 UUID。
        body: 角色分配请求体。
        current_user: 当前认证用户（需 user:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        UserResponse: 更新后的用户信息。

    Raises:
        AppError: code="not_found"，当用户不存在时。
        AppError: code="validation_failed"，当角色代码未知时。
    """
    _validate_role_codes(body.roles)

    async with session_scope(session_factory) as session:
        user: AppUser | None = await session.scalar(sa.select(AppUser).where(AppUser.id == user_id))
        if user is None:
            raise AppError(
                code="not_found",
                message=f"用户不存在: {user_id}",
                retryable=False,
                fields={"user_id": str(user_id)},
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
    current_user: ManageUserDep,
    session_factory: GovernanceSessionFactoryDep,
) -> UserResponse:
    """移除用户的指定角色。

    Args:
        user_id: 目标用户 UUID。
        role: 要移除的角色代码。
        current_user: 当前认证用户（需 user:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        UserResponse: 更新后的用户信息。

    Raises:
        AppError: code="not_found"，当用户不存在时。
    """
    async with session_scope(session_factory) as session:
        user: AppUser | None = await session.scalar(sa.select(AppUser).where(AppUser.id == user_id))
        if user is None:
            raise AppError(
                code="not_found",
                message=f"用户不存在: {user_id}",
                retryable=False,
                fields={"user_id": str(user_id)},
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
    """启用/禁用用户。

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

    async with session_scope(session_factory) as session:
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
    """删除用户（物理删除）。

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

    async with session_scope(session_factory) as session:
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
