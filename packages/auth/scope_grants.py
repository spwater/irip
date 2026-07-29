"""对象级授权：ResourceRef + ScopeGrant + AuthorizationService。

授权流程（docs/arch-v0.md §3.1 第 266-278 行）：

1. 查 user_id 直连的 scope_grant（优先）；
2. 查 user 的 role 对应的 scope_grant；
3. 匹配条件：
   - organization_id 相同；
   - resource_type 匹配或通配（``"*"``）；
   - action 精确匹配；
   - 当前时刻在 effective_from / effective_to 区间内；
4. object_root_id：
   - NULL = 全组织通配；
   - 非 NULL = 需匹配 object_id（V0 简化：子树展开在 V1+ 实现）。

AuthorizationService.require(user, action, resource) 在无权时抛出
AppError("forbidden", "无权访问该对象", False, {})。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from packages.auth.permissions import Role
from packages.common.clock import Clock
from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.errors import AppError
from packages.common.ids import new_id

#: 资源类型通配符。
WILDCARD_RESOURCE_TYPE: str = "*"


@dataclass(frozen=True)
class ResourceRef:
    """资源引用（授权检查的目标）。

    Attributes:
        organization_id: 资源所属组织 ID。
        object_id: 资源对象 ID（None 表示组织级操作，如列表查询）。
        resource_type: 资源类型（如 ``"fact"``、``"artifact"``、``"job"``）。
        department_id: 部门/实验室 ID（P1新增）。None = 不按部门过滤；
            非 None = 需匹配部门（scope_grant.department_id IS NULL 或精确匹配）。
    """

    organization_id: UUID
    object_id: UUID | None
    resource_type: str
    department_id: UUID | None = None


class _AuthorizedUser(Protocol):
    """授权用户协议（CurrentUser 满足此协议）。

    避免包层 → 应用层依赖：packages.auth 不导入 apps.api。
    """

    user_id: UUID
    roles: list[str]


class ScopeGrant(Base):
    """对象级授权实体（对应 scope_grant 表）。

    user_id 与 role_id 二选一（CHECK 约束保证）：
    - user_id 非空：直接授予该用户的对象级权限；
    - role_id 非空：授予该角色在指定范围内的权限。

    Attributes:
        id: 授权 UUID。
        user_id: 用户 ID（FK→app_user.id），与 role_id 二选一。
        role_id: 角色 ID（FK→role.id），与 user_id 二选一。
        organization_id: 组织 ID（NOT NULL）。
        object_root_id: 对象根 ID（NULL = 全组织；非 NULL = 子树根）。
        department_id: 部门/实验室 ID（P1新增，NULL = 全组织；非 NULL = 特定实验室）。
        resource_type: 资源类型（如 ``"fact"``）或通配符 ``"*"``。
        action: 权限字符串（如 ``"fact:read"``）。
        effective_from: 生效起始时间（NULL = 无下限）。
        effective_to: 生效截止时间（NULL = 无上限）。
    """

    __tablename__ = "scope_grant"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    user_id: Mapped[UUID | None] = mapped_column(GUID, sa.ForeignKey("app_user.id"), nullable=True)
    role_id: Mapped[UUID | None] = mapped_column(GUID, sa.ForeignKey("role.id"), nullable=True)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    object_root_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    department_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    resource_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    action: Mapped[str] = mapped_column(sa.Text, nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    def __repr__(self) -> str:
        return (
            f"ScopeGrant(id={self.id!r}, user_id={self.user_id!r}, "
            f"role_id={self.role_id!r}, department_id={self.department_id!r}, "
            f"resource_type={self.resource_type!r}, action={self.action!r})"
        )


class AuthorizationService:
    """对象级授权服务。

    依赖 AsyncSession 查询 scope_grant 表，依赖 Clock 获取当前时刻。
    授权逻辑：先查 user 直连 grant，再查 role grant；任一命中即放行。

    Attributes:
        session: 数据库异步会话（由调用方管理事务边界）。
        clock: 时钟依赖（用于检查 effective_from/effective_to）。
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        """初始化授权服务。

        Args:
            session: 异步会话（事务由调用方管理）。
            clock: 时钟依赖。
        """
        self._session = session
        self._clock = clock

    async def require(
        self,
        user: _AuthorizedUser,
        action: str,
        resource: ResourceRef,
    ) -> None:
        """检查用户是否有权对指定资源执行指定操作，无权时抛出 AppError。

        Args:
            user: 当前用户（需有 user_id 和 roles 属性）。
            action: 权限字符串（如 ``"fact:read"``）。
            resource: 资源引用。

        Raises:
            AppError: code="forbidden"，当用户无权访问该对象时。
        """
        allowed = await self.has_grant(user, action, resource)
        if not allowed:
            raise AppError(
                code="forbidden",
                message="无权访问该对象",
                retryable=False,
                fields={},
            )

    async def has_grant(
        self,
        user: _AuthorizedUser,
        action: str,
        resource: ResourceRef,
    ) -> bool:
        """检查用户是否拥有指定授权。

        授权查询顺序：
        1. user_id 直连 grant（优先）；
        2. role grant（基于 user.roles 中的角色代码）。

        匹配条件：
        - organization_id 与 resource.organization_id 相同；
        - resource_type 与 resource.resource_type 相同或为通配符 ``"*"``；
        - action 精确匹配；
        - 当前时刻在 effective 区间内；
        - object_root_id：NULL = 全组织；非 NULL = 需匹配 resource.object_id。

        Args:
            user: 当前用户。
            action: 权限字符串。
            resource: 资源引用。

        Returns:
            bool: 有权返回 True。
        """
        now = self._clock.now()

        # 构建 object_root_id 匹配条件
        if resource.object_id is not None:
            object_condition = sa.or_(
                ScopeGrant.object_root_id.is_(None),
                ScopeGrant.object_root_id == resource.object_id,
            )
        else:
            # object_id 为 None（组织级操作）时，只有全组织 grant 匹配
            object_condition = ScopeGrant.object_root_id.is_(None)

        # 公共匹配条件
        common_conditions = [
            ScopeGrant.organization_id == resource.organization_id,
            sa.or_(
                ScopeGrant.resource_type == resource.resource_type,
                ScopeGrant.resource_type == WILDCARD_RESOURCE_TYPE,
            ),
            ScopeGrant.action == action,
            sa.or_(
                ScopeGrant.effective_from.is_(None),
                ScopeGrant.effective_from <= now,
            ),
            sa.or_(
                ScopeGrant.effective_to.is_(None),
                ScopeGrant.effective_to >= now,
            ),
            object_condition,
        ]

        # P1: department_id 匹配条件
        # resource.department_id 非 None 时，需匹配部门：
        # scope_grant.department_id IS NULL（全组织范围，兼容）或精确匹配实验室
        if resource.department_id is not None:
            department_condition = sa.or_(
                ScopeGrant.department_id.is_(None),
                ScopeGrant.department_id == resource.department_id,
            )
            common_conditions.append(department_condition)

        # 1. 查 user 直连 grant（优先）
        user_result = await self._session.execute(
            sa.select(sa.literal(1))
            .select_from(ScopeGrant)
            .where(
                ScopeGrant.user_id == user.user_id,
                *common_conditions,
            )
            .limit(1)
        )
        if user_result.first() is not None:
            return True

        # 2. 查 role grant（基于 user.roles 角色代码）
        if not user.roles:
            return False

        role_result = await self._session.execute(
            sa.select(sa.literal(1))
            .select_from(ScopeGrant)
            .join(Role, ScopeGrant.role_id == Role.id)
            .where(
                Role.code.in_(user.roles),
                *common_conditions,
            )
            .limit(1)
        )
        return role_result.first() is not None
