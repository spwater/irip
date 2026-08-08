"""PlanService 基类：依赖注入与共享基础设施。

拆分自 plan_service.py（IRIP 拆分任务）。本模块定义 ``PlanServiceBase``，
承载构造器、操作人校验以及各功能域 Mixin 共享的实例属性声明，
供 ``plan_generator`` / ``plan_confirmer`` / ``plan_reviser`` / ``plan_analyzer``
等 Mixin 继承，避免循环导入。

参照 packages/research/snapshots.py 的 ScopedSessionMixin 模式。
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.planning.context_router import ContextRouter


class PlanServiceBase(ScopedSessionMixin):
    """分析计划服务基类。

    依赖注入 session_factory / department_id / actor_id
    / model_gateway / context_router / fact_provider。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _model_gateway: 模型网关。
        _context_router: 上下文路由器。
        _fact_provider: CoreFactProvider 只读适配器。
        _numeric_tools: NumericToolFacade 实例（数值工具，可选）。
        _rls_dept_id: RLS 部门 ID（平台管理员绕过隔离，可选）。
    """

    _factory: async_sessionmaker[AsyncSession]
    _dept_id: UUID
    _actor_id: UUID | None
    _model_gateway: Any
    _context_router: ContextRouter
    _fact_provider: Any
    _numeric_tools: Any | None
    _rls_dept_id: UUID | None

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        model_gateway: Any,
        context_router: ContextRouter,
        fact_provider: Any,
        numeric_tools: Any | None = None,
    ) -> None:
        """初始化计划服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            model_gateway: 模型网关（ModelGateway 实例）。
            context_router: 上下文路由器。
            fact_provider: CoreFactProvider 只读适配器。
            numeric_tools: NumericToolFacade 实例（数值工具，可选）。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._model_gateway = model_gateway
        self._context_router = context_router
        self._fact_provider = fact_provider
        self._numeric_tools = numeric_tools
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
