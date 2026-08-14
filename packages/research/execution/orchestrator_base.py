"""ResearchOrchestrator 基类：依赖注入、共享常量与跨 Mixin 接口。

拆分自 orchestrator.py（IRIP 拆分任务）。本模块定义 ``ResearchOrchestratorBase``，
承载构造器（含自动提交 session 包装）、模块级常量与日志器，以及
``ContextBuilderMixin`` 实现、供 ``StepExecutorMixin`` 跨 Mixin 调用的接口方法
（``_prepare_input_package`` / ``_load_snapshot_data`` / ``_build_research_context``）
的声明，避免循环导入并满足 mypy strict 类型检查。
"""

import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger("research.orchestrator")


class ResearchOrchestratorBase:
    """研究分析执行编排器基类。

    依赖注入 Repository / ModelGateway / ContextRouter /
    RunArtifactService / ResearchMemoryService / Scheduler / session_factory。

    Attributes:
        _repo: 数据访问层（ResearchRepositoryTrusted）。
        _model_gateway: 模型网关。
        _context_router: 上下文路由器。
        _artifact_service: 工件服务。
        _memory_service: 研究记忆服务。
        _scheduler: 调度器。
        _factory: 异步会话工厂（自动提交包装）。
        _lineage_writer: LineageWriterService 实例（可选，阶段 5 新增）。
    """

    _repo: Any
    _model_gateway: Any
    _context_router: Any
    _artifact_service: Any
    _memory_service: Any
    _scheduler: Any | None
    _factory: Any
    _lineage_writer: Any | None
    _dept_id: UUID | None

    def __init__(
        self,
        repo: Any,
        model_gateway: Any,
        context_router: Any,
        artifact_service: Any,
        memory_service: Any,
        scheduler: Any | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        lineage_writer: Any | None = None,
        department_id: UUID | None = None,
    ) -> None:
        """初始化编排器。

        Args:
            repo: 数据访问层。
            model_gateway: 模型网关。
            context_router: 上下文路由器。
            artifact_service: 工件服务。
            memory_service: 研究记忆服务。
            scheduler: 调度器（可选，Worker 中使用）。
            session_factory: 异步会话工厂（可选，Worker 中使用）。
            lineage_writer: LineageWriterService 实例（可选，阶段 5 新增）。
            department_id: 当前部门 ID（可选，用于审计记录）。
        """
        self._repo = repo
        self._model_gateway = model_gateway
        self._context_router = context_router
        self._artifact_service = artifact_service
        self._memory_service = memory_service
        self._scheduler = scheduler
        self._factory: Any = session_factory
        self._lineage_writer = lineage_writer
        self._dept_id = department_id

        # 包装 session_factory：退出时自动 commit（与 session_scope 行为一致）
        _original_factory = session_factory
        from contextlib import asynccontextmanager as _acm

        @_acm
        async def _auto_commit_session() -> AsyncIterator[AsyncSession]:
            if _original_factory is None:
                raise RuntimeError("session_factory is None")
            async with _original_factory() as session:
                async with session.begin():
                    yield session

        self._factory = _auto_commit_session

    # ============================================================
    # 跨 Mixin 接口：由 ContextBuilderMixin 实现，供 StepExecutorMixin 调用。
    # 此处仅声明签名以满足 mypy strict；实际实现见 context_builder.py。
    # ============================================================

    async def _load_snapshot_data(self, snapshot_id: UUID) -> str:
        """加载快照数据为文本（LLM 步骤使用）。由 ContextBuilderMixin 实现。"""
        raise NotImplementedError

    def _build_research_context(self, run_id: UUID, plan: object) -> str:
        """构建研究上下文。由 ContextBuilderMixin 实现。"""
        raise NotImplementedError

    async def _publish_event(
        self,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """发布 SSE 事件到 Redis pub/sub。由 ResultAssemblerMixin 实现。"""
        raise NotImplementedError
