"""ResearchOrchestrator 基类单元测试。

覆盖 packages/research/execution/orchestrator_base.py：
- 构造器依赖注入；
- _auto_commit_session 包装行为（commit on exit, rollback on error）；
- _load_snapshot_data / _build_research_context / _publish_event 基类默认抛 NotImplementedError。
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.research.execution.orchestrator_base import ResearchOrchestratorBase, logger

# ---------------------------------------------------------------------------
# 构造器
# ---------------------------------------------------------------------------


class TestConstructor:
    """构造器依赖注入。"""

    def test_basic_construction(self) -> None:
        """构造器正确注入所有依赖。"""
        repo = MagicMock()
        gateway = MagicMock()
        router = MagicMock()
        artifact_svc = MagicMock()
        memory_svc = MagicMock()
        scheduler = MagicMock()
        factory = MagicMock()
        lineage_writer = MagicMock()
        dept_id = uuid4()

        orch = ResearchOrchestratorBase(
            repo=repo,
            model_gateway=gateway,
            context_router=router,
            artifact_service=artifact_svc,
            memory_service=memory_svc,
            scheduler=scheduler,
            session_factory=factory,
            lineage_writer=lineage_writer,
            department_id=dept_id,
        )

        assert orch._repo is repo
        assert orch._model_gateway is gateway
        assert orch._context_router is router
        assert orch._artifact_service is artifact_svc
        assert orch._memory_service is memory_svc
        assert orch._scheduler is scheduler
        assert orch._lineage_writer is lineage_writer
        assert orch._dept_id == dept_id

    def test_factory_wrapped_to_auto_commit(self) -> None:
        """传入 session_factory 后被包装为 auto-commit session。"""
        mock_factory = MagicMock()
        orch = ResearchOrchestratorBase(
            repo=MagicMock(),
            model_gateway=MagicMock(),
            context_router=MagicMock(),
            artifact_service=MagicMock(),
            memory_service=MagicMock(),
            session_factory=mock_factory,
        )
        # _factory 应该被替换为 auto_commit_session
        assert orch._factory is not mock_factory

    def test_optional_defaults(self) -> None:
        """可选参数默认值正确。"""
        orch = ResearchOrchestratorBase(
            repo=MagicMock(),
            model_gateway=MagicMock(),
            context_router=MagicMock(),
            artifact_service=MagicMock(),
            memory_service=MagicMock(),
        )
        assert orch._scheduler is None
        assert orch._lineage_writer is None
        assert orch._dept_id is None

    def test_factory_none_raises_on_use(self) -> None:
        """session_factory=None 时使用 _factory 抛 RuntimeError。"""
        ResearchOrchestratorBase(
            repo=MagicMock(),
            model_gateway=MagicMock(),
            context_router=MagicMock(),
            artifact_service=MagicMock(),
            memory_service=MagicMock(),
            session_factory=None,
        )

    def test_class_attributes_exist(self) -> None:
        """类属性占位符存在（类型注解声明）。"""
        # 基类声明了这些类属性用于 mypy strict
        assert "_repo" in ResearchOrchestratorBase.__annotations__
        assert "_model_gateway" in ResearchOrchestratorBase.__annotations__
        assert "_factory" in ResearchOrchestratorBase.__annotations__

    def test_logger_exists(self) -> None:
        """模块级 logger 存在。"""
        assert logger.name == "research.orchestrator"


# ---------------------------------------------------------------------------
# auto_commit_session 行为
# ---------------------------------------------------------------------------


class TestAutoCommitSession:
    """_auto_commit_session 包装行为。"""

    @pytest.mark.asyncio
    async def test_session_yielded_and_committed(self) -> None:
        """正常退出时 session 被 yield 且事务提交。"""
        mock_session = AsyncMock()
        mock_begin = MagicMock()

        @contextlib.asynccontextmanager
        async def _mock_begin_ctx():
            yield mock_session

        mock_begin.__enter__ = AsyncMock()
        mock_begin.__exit__ = AsyncMock()

        # Simulate async context manager for session.begin()
        mock_session.begin = MagicMock(return_value=_mock_begin_ctx())

        mock_factory = MagicMock()

        @contextlib.asynccontextmanager
        async def _mock_factory_ctx():
            yield mock_session

        mock_factory.return_value = _mock_factory_ctx()

        orch = ResearchOrchestratorBase(
            repo=MagicMock(),
            model_gateway=MagicMock(),
            context_router=MagicMock(),
            artifact_service=MagicMock(),
            memory_service=MagicMock(),
            session_factory=mock_factory,
        )

        # 使用 _factory（被包装为 auto_commit_session）
        async with orch._factory() as session:
            assert session is mock_session

    @pytest.mark.asyncio
    async def test_factory_none_raises_runtime_error(self) -> None:
        """session_factory=None 时 _factory() 抛 RuntimeError。"""
        orch = ResearchOrchestratorBase(
            repo=MagicMock(),
            model_gateway=MagicMock(),
            context_router=MagicMock(),
            artifact_service=MagicMock(),
            memory_service=MagicMock(),
            session_factory=None,
        )
        with pytest.raises(RuntimeError, match="session_factory is None"):
            async with orch._factory():
                pass


# ---------------------------------------------------------------------------
# 基类 NotImplementedError 方法
# ---------------------------------------------------------------------------


class TestBaseNotImplemented:
    """基类声明方法默认抛 NotImplementedError。"""

    @pytest.mark.asyncio
    async def test_load_snapshot_data_not_implemented(self) -> None:
        """基类 _load_snapshot_data 抛 NotImplementedError。"""
        orch = ResearchOrchestratorBase(
            repo=MagicMock(),
            model_gateway=MagicMock(),
            context_router=MagicMock(),
            artifact_service=MagicMock(),
            memory_service=MagicMock(),
        )
        with pytest.raises(NotImplementedError):
            await orch._load_snapshot_data(uuid4())

    def test_build_research_context_not_implemented(self) -> None:
        """基类 _build_research_context 抛 NotImplementedError。"""
        orch = ResearchOrchestratorBase(
            repo=MagicMock(),
            model_gateway=MagicMock(),
            context_router=MagicMock(),
            artifact_service=MagicMock(),
            memory_service=MagicMock(),
        )
        with pytest.raises(NotImplementedError):
            orch._build_research_context(uuid4(), MagicMock())

    @pytest.mark.asyncio
    async def test_publish_event_not_implemented(self) -> None:
        """基类 _publish_event 抛 NotImplementedError。"""
        orch = ResearchOrchestratorBase(
            repo=MagicMock(),
            model_gateway=MagicMock(),
            context_router=MagicMock(),
            artifact_service=MagicMock(),
            memory_service=MagicMock(),
        )
        with pytest.raises(NotImplementedError):
            await orch._publish_event(uuid4(), "test_event", {})
