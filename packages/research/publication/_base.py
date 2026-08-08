"""PublicationService 各功能域 mixin 的共享基类。

PublicationService 按功能域拆分为多个 mixin（发布 / ACL / 版本 / 复用），
这些 mixin 共享同一组实例属性与会话/身份辅助方法。``_PublicationBase`` 集中
声明这些共享成员，避免 mixin 间相互依赖，同时继承 ``ScopedSessionMixin``
以获得带租户 GUC 的事务会话上下文 ``_scoped_session``。
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.entities import ResearchResult
from packages.research.lineage import LineageEdgeService


class _PublicationBase(ScopedSessionMixin):
    """PublicationService 各功能域 mixin 的共享基类。

    声明由 ``__init__`` 注入的实例属性，提供 ``_require_actor`` 与
    ``_check_result_visible`` 等跨功能域复用的辅助方法。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _product_service: ProductService 实例。
        _lineage_service: LineageEdgeService 实例。
        _rls_dept_id: RLS 部门 ID（可选）。
    """

    # -- 由 __init__ 注入的实例属性（类型声明供 mypy 严格检查）--
    _factory: async_sessionmaker[AsyncSession]
    _dept_id: UUID
    _actor_id: UUID | None
    _product_service: Any
    _lineage_service: LineageEdgeService
    _rls_dept_id: UUID | None

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        product_service: Any,
        lineage_service: LineageEdgeService,
    ) -> None:
        """初始化成果包服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            product_service: ProductService 实例（获取产物详情和版本）。
            lineage_service: LineageEdgeService 实例（溯源边记录）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._product_service = product_service
        self._lineage_service = lineage_service
        self._rls_dept_id: UUID | None = None

    def _require_actor(self) -> UUID:
        """获取当前操作人 ID，为空时抛出异常。"""
        if self._actor_id is None:
            raise AppError(
                code="forbidden",
                message="操作需要已认证用户",
                retryable=False,
                fields={},
            )
        return self._actor_id

    def _check_result_visible(
        self,
        result: ResearchResult,
        principal_id: UUID,
    ) -> bool:
        """校验当前用户是否有权查看成果包（基于 ACL）。

        Args:
            result: ResearchResult ORM 实体。
            principal_id: 当前用户 ID。

        Returns:
            bool: 是否有权查看。
        """
        # private: 仅 owner 可见
        if result.current_acl_type == "private":
            return bool(result.owner_user_id == principal_id)

        # tree: 同部门可见（首期简化为部门内可见，实际需查询部门树）
        if result.current_acl_type == "tree":
            return True  # 首期简化：同部门用户可见（RLS 已过滤跨部门）

        # explicit: 指定用户可见
        if result.current_acl_type == "explicit":
            explicit_ids = result.current_explicit_user_ids or []
            return (
                str(principal_id) in [str(uid) for uid in explicit_ids]
                or result.owner_user_id == principal_id
            )

        # all: 全部可见
        if result.current_acl_type == "all":
            return True

        # 未知 ACL 类型，保守为不可见
        return False
