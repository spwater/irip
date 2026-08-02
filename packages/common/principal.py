"""Principal 和 DeptTenantId 值对象：可信身份上下文。

由认证依赖（get_current_user）构造，
传入所有应用服务方法，确保租户隔离。

使用约定（技术设计文档 §8.1）：
1. 所有应用服务方法**必须**接收 ``Principal`` 参数，
   **禁止**只传裸 ``user_id`` 或 ``dept_id``；
2. Repository 方法**必须**接收 ``(DeptTenantId, entity_id)`` 或
   ``(dept_id, entity_id)`` 复合键，**禁止**只按 ``entity_id`` 查询；
3. ``Principal`` 由 ``get_current_user`` 构造，
   构造失败必须 fail-closed（401/403）；
4. ``Principal`` 是 frozen dataclass，不可在服务中修改。
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from packages.common.query_scope import QueryScope


@runtime_checkable
class UserLike(Protocol):
    """当前认证用户的结构化协议（duck typing）。

    Phase 3 架构收敛（T3-3）：``packages/common`` 不得直接依赖
    ``apps.api.dependencies.auth.CurrentUser``。本 Protocol 以结构化子类型
    方式声明 ``Principal.from_current_user`` 所需的最小属性集合；
    任何具备这些属性的对象（包括 ``CurrentUser``）均自动满足此协议，
    无需显式继承，从而切断 packages→apps 的反向依赖。

    Attributes:
        user_id: 用户 UUID。
        email: 用户邮箱。
        roles: 用户角色列表（如 ``["admin"]``）。
        department_id: 用户主要部门 UUID。
    """

    user_id: UUID
    email: str
    roles: list[str]
    department_id: UUID


@dataclass(frozen=True)
class Principal:
    """可信身份上下文，由认证依赖构造，传入所有应用服务。

    封装当前用户的完整身份信息（user_id、department_id、roles、scope），
    确保所有服务调用都携带租户隔离上下文。

    Attributes:
        user_id: 用户 UUID。
        department_id: 用户主要部门 UUID（租户隔离基础，RLS 锚定此列）。
        email: 用户邮箱。
        roles: 用户角色列表（如 ``["admin"]``、``["analyst"]``）。
        scope: 查询范围（部门/对象根过滤）。
        token_version: JWT 令牌版本号，用于撤销检测。
            每次认证必须复核，不匹配时拒绝（token 已被撤销）。
        is_active: 用户是否活跃（默认 True）。
    """

    user_id: UUID
    department_id: UUID
    email: str
    roles: list[str]
    scope: QueryScope
    token_version: int = 0
    is_active: bool = True

    def has_permission(self, perm: str) -> bool:
        """检查当前用户是否拥有指定权限。

        通过角色列表解析权限（延迟导入避免循环依赖）。

        Args:
            perm: 权限字符串（如 ``"job:submit"``）。

        Returns:
            bool: 拥有该权限返回 True。
        """
        from packages.auth.permissions import has_role_permission

        return any(has_role_permission(role, perm) for role in self.roles)

    @staticmethod
    def from_current_user(
        user: UserLike,
        dept_id: UUID,
        scope: QueryScope,
    ) -> "Principal":
        """从当前认证用户构造 Principal。

        通过 ``UserLike`` Protocol 接收任意具备 ``user_id``/``email``/``roles``
        属性的用户对象（结构化子类型），无需直接依赖 ``apps`` 层的
        ``CurrentUser``。

        Args:
            user: 当前认证用户（满足 ``UserLike`` 协议，从 JWT 解析）。
            dept_id: 从数据库查询到的部门 ID（用户的主要 department_id）。
            scope: 查询范围（基于 dept_id 构造）。

        Returns:
            Principal: 可信身份上下文。
        """
        token_version: int = getattr(user, "token_version", 0)
        return Principal(
            user_id=user.user_id,
            department_id=dept_id,
            email=user.email,
            roles=user.roles,
            scope=scope,
            token_version=token_version,
        )

    def tenant_id(self) -> "DeptTenantId":
        """从 Principal 提取 DeptTenantId。

        Returns:
            DeptTenantId: 部门租户标识值对象。
        """
        return DeptTenantId(self.department_id)


@dataclass(frozen=True)
class DeptTenantId:
    """部门租户标识值对象，强制 (dept_id) 复合查询。

    用于 Repository 方法签名，确保所有查询都带有部门条件。

    Attributes:
        value: 部门 UUID。
    """

    value: UUID

    @staticmethod
    def from_principal(principal: Principal) -> "DeptTenantId":
        """从 Principal 构造 DeptTenantId。

        Args:
            principal: 可信身份上下文。

        Returns:
            DeptTenantId: 部门租户标识值对象。
        """
        return DeptTenantId(principal.department_id)
