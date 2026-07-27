"""Principal 和 TenantId 值对象：可信身份上下文。

由认证依赖（get_current_user + _lookup_org_id）构造，
传入所有应用服务方法，确保租户隔离。

使用约定（技术设计文档 §8.1）：
1. 所有应用服务方法**必须**接收 ``Principal`` 参数，
   **禁止**只传裸 ``user_id`` 或 ``org_id``；
2. Repository 方法**必须**接收 ``(TenantId, entity_id)`` 或
   ``(org_id, entity_id)`` 复合键，**禁止**只按 ``entity_id`` 查询；
3. ``Principal`` 由 ``get_current_user`` + ``_lookup_org_id`` 构造，
   构造失败必须 fail-closed（401/403）；
4. ``Principal`` 是 frozen dataclass，不可在服务中修改。
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from packages.common.query_scope import QueryScope

if TYPE_CHECKING:
    from apps.api.dependencies.auth import CurrentUser


@dataclass(frozen=True)
class Principal:
    """可信身份上下文，由认证依赖构造，传入所有应用服务。

    封装当前用户的完整身份信息（user_id、organization_id、roles、scope），
    确保所有服务调用都携带租户隔离上下文。

    Attributes:
        user_id: 用户 UUID。
        organization_id: 当前组织 UUID（租户隔离基础）。
        email: 用户邮箱。
        roles: 用户角色列表（如 ``["admin"]``、``["analyst"]``）。
        scope: 查询范围（组织/部门/对象根过滤）。
        is_active: 用户是否活跃（默认 True）。
    """

    user_id: UUID
    organization_id: UUID
    email: str
    roles: list[str]
    scope: QueryScope
    is_active: bool = True

    @staticmethod
    def from_current_user(
        user: "CurrentUser",
        org_id: UUID,
        scope: QueryScope,
    ) -> "Principal":
        """从 CurrentUser 构造 Principal。

        Args:
            user: 当前认证用户（从 JWT 解析）。
            org_id: 从数据库查询到的组织 ID。
            scope: 查询范围（基于 org_id 构造）。

        Returns:
            Principal: 可信身份上下文。
        """
        return Principal(
            user_id=user.user_id,
            organization_id=org_id,
            email=user.email,
            roles=user.roles,
            scope=scope,
        )

    def tenant_id(self) -> "TenantId":
        """从 Principal 提取 TenantId。

        Returns:
            TenantId: 租户标识值对象。
        """
        return TenantId(self.organization_id)


@dataclass(frozen=True)
class TenantId:
    """租户标识值对象，强制 (org_id) 复合查询。

    用于 Repository 方法签名，确保所有查询都带有组织条件。

    Attributes:
        value: 组织 UUID。
    """

    value: UUID

    @staticmethod
    def from_principal(principal: Principal) -> "TenantId":
        """从 Principal 构造 TenantId。

        Args:
            principal: 可信身份上下文。

        Returns:
            TenantId: 租户标识值对象。
        """
        return TenantId(principal.organization_id)
