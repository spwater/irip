"""治理 API 路由：用户管理 + 范围授权。

端点（IRIP V3-T02）：
  GET    /api/v1/governance/users                      — 列出用户
  POST   /api/v1/governance/users/{id}/roles           — 分配角色
  DELETE /api/v1/governance/users/{id}/roles/{role}    — 移除角色
  PATCH  /api/v1/governance/users/{id}/status          — 启用/禁用用户
  GET    /api/v1/governance/scope-grants               — 列出范围授权
  POST   /api/v1/governance/scope-grants               — 创建范围授权
  DELETE /api/v1/governance/scope-grants/{id}          — 移除范围授权

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

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.audit.events import AuditEventData
from packages.audit.redaction import redact
from packages.audit.repository import AuditRecorder
from packages.auth.entities import AppUser
from packages.auth.permissions import BUILTIN_ROLES
from packages.auth.scope_grants import ScopeGrant
from packages.common.database import session_scope
from packages.common.errors import AppError

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
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """用户分页列表响应。"""

    items: list[UserResponse]
    next_cursor: str | None
    has_more: bool


class AssignRolesRequest(BaseModel):
    """分配角色请求体。"""

    roles: list[str] = Field(
        ..., min_length=1, description="要分配的角色代码列表"
    )


class UpdateUserStatusRequest(BaseModel):
    """启用/禁用用户请求体。"""

    status: str = Field(..., description="目标状态：active 或 disabled")


class ScopeGrantResponse(BaseModel):
    """范围授权响应体。"""

    id: str
    user_id: str | None
    role_id: str | None
    organization_id: str
    object_root_id: str | None
    department_id: str | None
    resource_type: str
    action: str
    effective_from: datetime | None
    effective_to: datetime | None


class ScopeGrantListResponse(BaseModel):
    """范围授权分页列表响应。"""

    items: list[ScopeGrantResponse]
    next_cursor: str | None
    has_more: bool


class CreateScopeGrantRequest(BaseModel):
    """创建范围授权请求体。"""

    user_id: str | None = Field(None, description="用户 ID（与 role_id 二选一）")
    role_id: str | None = Field(None, description="角色 ID（与 user_id 二选一）")
    organization_id: str = Field(..., description="组织 ID")
    object_root_id: str | None = Field(None, description="对象根 ID，NULL 表示全组织")
    department_id: str | None = Field(None, description="部门 ID，NULL 表示全组织")
    resource_type: str = Field(..., description="资源类型或通配符 *")
    action: str = Field(..., description="权限字符串")
    effective_from: datetime | None = Field(None, description="生效起始时间")
    effective_to: datetime | None = Field(None, description="生效截止时间")


# ---- 辅助函数 ----


def _to_user_response(user: AppUser) -> UserResponse:
    """将 AppUser ORM 实体转换为响应模型。"""
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        roles=list(user.roles) if user.roles else [],
        status=user.status,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _to_grant_response(grant: ScopeGrant) -> ScopeGrantResponse:
    """将 ScopeGrant ORM 实体转换为响应模型。"""
    return ScopeGrantResponse(
        id=str(grant.id),
        user_id=str(grant.user_id) if grant.user_id is not None else None,
        role_id=str(grant.role_id) if grant.role_id is not None else None,
        organization_id=str(grant.organization_id),
        object_root_id=str(grant.object_root_id) if grant.object_root_id is not None else None,
        department_id=str(grant.department_id) if grant.department_id is not None else None,
        resource_type=grant.resource_type,
        action=grant.action,
        effective_from=grant.effective_from,
        effective_to=grant.effective_to,
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


@governance_router.post(
    "/users/{user_id}/roles", response_model=UserResponse
)
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
        user: AppUser | None = await session.scalar(
            sa.select(AppUser).where(AppUser.id == user_id)
        )
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


@governance_router.delete(
    "/users/{user_id}/roles/{role}", response_model=UserResponse
)
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
        user: AppUser | None = await session.scalar(
            sa.select(AppUser).where(AppUser.id == user_id)
        )
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


@governance_router.patch(
    "/users/{user_id}/status", response_model=UserResponse
)
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
        user: AppUser | None = await session.scalar(
            sa.select(AppUser).where(AppUser.id == user_id)
        )
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


# ---- 范围授权端点 ----


@governance_router.get("/scope-grants", response_model=ScopeGrantListResponse)
async def list_scope_grants(
    current_user: ManageUserDep,
    session_factory: GovernanceSessionFactoryDep,
    user_id: str | None = Query(None, description="按用户 ID 筛选"),
    resource_type: str | None = Query(None, description="按资源类型筛选"),
    action: str | None = Query(None, description="按权限操作筛选"),
    cursor: str | None = Query(None, description="分页游标"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
) -> ScopeGrantListResponse:
    """列出范围授权（分页）。

    Args:
        current_user: 当前认证用户（需 user:manage 权限）。
        session_factory: 数据库会话工厂。
        user_id: 按用户 ID 筛选。
        resource_type: 按资源类型筛选。
        action: 按权限操作筛选。
        cursor: 分页游标。
        limit: 每页数量（最大 100）。

    Returns:
        ScopeGrantListResponse: 分页范围授权列表。
    """
    async with session_factory() as session:
        stmt = sa.select(ScopeGrant).order_by(ScopeGrant.id.desc())

        if user_id is not None:
            try:
                user_uuid = UUID(user_id)
            except ValueError as exc:
                raise AppError(
                    code="validation_failed",
                    message="无效的用户 ID",
                    retryable=False,
                    fields={"user_id": user_id},
                ) from exc
            stmt = stmt.where(ScopeGrant.user_id == user_uuid)

        if resource_type is not None:
            stmt = stmt.where(ScopeGrant.resource_type == resource_type)

        if action is not None:
            stmt = stmt.where(ScopeGrant.action == action)

        if cursor is not None:
            try:
                cursor_uuid = UUID(cursor)
            except ValueError as exc:
                raise AppError(
                    code="invalid_cursor",
                    message="无效的分页游标",
                    retryable=False,
                    fields={"cursor": cursor},
                ) from exc
            stmt = stmt.where(ScopeGrant.id < cursor_uuid)

        stmt = stmt.limit(limit + 1)
        result = await session.execute(stmt)
        rows: list[ScopeGrant] = list(result.scalars().all())

    has_more: bool = len(rows) > limit
    page_items: list[ScopeGrant] = rows[:limit]
    next_cursor: str | None = None
    if has_more and page_items:
        next_cursor = str(page_items[-1].id)

    return ScopeGrantListResponse(
        items=[_to_grant_response(g) for g in page_items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@governance_router.post(
    "/scope-grants", response_model=ScopeGrantResponse, status_code=201
)
async def create_scope_grant(
    body: CreateScopeGrantRequest,
    current_user: ManageUserDep,
    session_factory: GovernanceSessionFactoryDep,
) -> ScopeGrantResponse:
    """创建范围授权。

    user_id 与 role_id 二选一，至少指定一个。

    Args:
        body: 创建请求体。
        current_user: 当前认证用户（需 user:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        ScopeGrantResponse: 新创建的授权（201 Created）。

    Raises:
        AppError: code="validation_failed"，当 user_id 和 role_id 同时为空或同时非空时。
    """
    if body.user_id is None and body.role_id is None:
        raise AppError(
            code="validation_failed",
            message="user_id 和 role_id 必须二选一",
            retryable=False,
            fields={"user_id": "required", "role_id": "required"},
        )
    if body.user_id is not None and body.role_id is not None:
        raise AppError(
            code="validation_failed",
            message="user_id 和 role_id 不能同时指定",
            retryable=False,
            fields={"user_id": "exclusive", "role_id": "exclusive"},
        )

    try:
        org_uuid = UUID(body.organization_id)
    except ValueError as exc:
        raise AppError(
            code="validation_failed",
            message="无效的组织 ID",
            retryable=False,
            fields={"organization_id": body.organization_id},
        ) from exc

    user_uuid: UUID | None = None
    if body.user_id is not None:
        try:
            user_uuid = UUID(body.user_id)
        except ValueError as exc:
            raise AppError(
                code="validation_failed",
                message="无效的用户 ID",
                retryable=False,
                fields={"user_id": body.user_id},
            ) from exc

    role_uuid: UUID | None = None
    if body.role_id is not None:
        try:
            role_uuid = UUID(body.role_id)
        except ValueError as exc:
            raise AppError(
                code="validation_failed",
                message="无效的角色 ID",
                retryable=False,
                fields={"role_id": body.role_id},
            ) from exc

    object_root_uuid: UUID | None = None
    if body.object_root_id is not None:
        try:
            object_root_uuid = UUID(body.object_root_id)
        except ValueError as exc:
            raise AppError(
                code="validation_failed",
                message="无效的对象根 ID",
                retryable=False,
                fields={"object_root_id": body.object_root_id},
            ) from exc

    department_uuid: UUID | None = None
    if body.department_id is not None:
        try:
            department_uuid = UUID(body.department_id)
        except ValueError as exc:
            raise AppError(
                code="validation_failed",
                message="无效的部门 ID",
                retryable=False,
                fields={"department_id": body.department_id},
            ) from exc

    grant = ScopeGrant(
        user_id=user_uuid,
        role_id=role_uuid,
        organization_id=org_uuid,
        object_root_id=object_root_uuid,
        department_id=department_uuid,
        resource_type=body.resource_type,
        action=body.action,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
    )

    async with session_scope(session_factory) as session:
        session.add(grant)
        await session.flush()

        await _record_audit(
            session,
            current_user,
            action="governance.scope_grant.create",
            resource_type="scope_grant",
            resource_id=grant.id,
            payload={
                "user_id": body.user_id,
                "role_id": body.role_id,
                "resource_type": body.resource_type,
                "action": body.action,
            },
        )

        await session.refresh(grant)

    return _to_grant_response(grant)


@governance_router.delete("/scope-grants/{grant_id}", status_code=204)
async def delete_scope_grant(
    grant_id: UUID,
    current_user: ManageUserDep,
    session_factory: GovernanceSessionFactoryDep,
) -> None:
    """移除范围授权。

    Args:
        grant_id: 授权 UUID。
        current_user: 当前认证用户（需 user:manage 权限）。
        session_factory: 数据库会话工厂。

    Raises:
        AppError: code="not_found"，当授权不存在时。
    """
    async with session_scope(session_factory) as session:
        grant: ScopeGrant | None = await session.scalar(
            sa.select(ScopeGrant).where(ScopeGrant.id == grant_id)
        )
        if grant is None:
            raise AppError(
                code="not_found",
                message=f"范围授权不存在: {grant_id}",
                retryable=False,
                fields={"grant_id": str(grant_id)},
            )

        await session.execute(
            sa.delete(ScopeGrant).where(ScopeGrant.id == grant_id)
        )

        await _record_audit(
            session,
            current_user,
            action="governance.scope_grant.delete",
            resource_type="scope_grant",
            resource_id=grant_id,
            payload={"resource_type": grant.resource_type, "action": grant.action},
        )
