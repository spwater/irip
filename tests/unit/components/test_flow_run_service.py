"""流程运行服务单元测试。

覆盖 packages/components/flow/run_service.py：
- list_runs, create_run, get_run, delete_run, get_run_fact_ids,
  get_latest_node_execution, get_latest_node_executions, list_facts_by_flow

使用 mock session_factory + mock scoped_session 避免真实 DB 依赖。
"""

import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.components.flow.run_service import FlowRunService

# ---------------------------------------------------------------------------
# 辅助：mock FlowRunService._scoped_session
# ---------------------------------------------------------------------------


def _make_service(**kwargs: Any) -> FlowRunService:
    """构建 FlowRunService 实例，注入 mock 依赖。"""
    factory = MagicMock()
    return FlowRunService(
        session_factory=factory,
        department_id=kwargs.get("department_id", uuid4()),
        actor_id=kwargs.get("actor_id", uuid4()),
        job_service=kwargs.get("job_service", MagicMock()),
        clock=kwargs.get("clock", MagicMock()),
        definition_svc=kwargs.get("definition_svc", MagicMock()),
    )


def _patch_scoped_session(service: FlowRunService, session: AsyncMock) -> Any:
    """Patch _scoped_session 返回固定 mock session。"""

    @contextlib.asynccontextmanager
    async def _ctx(_self: Any):
        yield session

    return patch.object(type(service), "_scoped_session", _ctx)


# ---------------------------------------------------------------------------
# get_run
# ---------------------------------------------------------------------------


class TestGetRun:
    """get_run 查询。"""

    @pytest.mark.asyncio
    async def test_run_found(self) -> None:
        """存在的 run 返回 run + executions。"""
        service = _make_service()
        session = AsyncMock()
        run = MagicMock()
        run.id = uuid4()
        run.status = "succeeded"

        executions = [MagicMock(), MagicMock()]

        session.scalar = AsyncMock(return_value=run)
        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = executions
        session.execute = AsyncMock(return_value=execute_result)

        with _patch_scoped_session(service, session):
            result_run, result_execs = await service.get_run(run.id)

        assert result_run is run
        assert result_execs == executions

    @pytest.mark.asyncio
    async def test_run_not_found(self) -> None:
        """run 不存在 → not_found。"""
        service = _make_service()
        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)

        with _patch_scoped_session(service, session):
            with pytest.raises(AppError) as exc_info:
                await service.get_run(uuid4())
        assert exc_info.value.code == "not_found"


# ---------------------------------------------------------------------------
# list_runs
# ---------------------------------------------------------------------------


class TestListRuns:
    """list_runs 查询。"""

    @pytest.mark.asyncio
    async def test_list_runs_returns_list(self) -> None:
        """返回运行记录列表。"""
        service = _make_service()
        session = AsyncMock()
        runs = [MagicMock(), MagicMock(), MagicMock()]

        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = runs
        session.execute = AsyncMock(return_value=execute_result)

        with _patch_scoped_session(service, session):
            result = await service.list_runs(uuid4())

        assert result == runs
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_list_runs_empty(self) -> None:
        """无运行记录返回空列表。"""
        service = _make_service()
        session = AsyncMock()

        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=execute_result)

        with _patch_scoped_session(service, session):
            result = await service.list_runs(uuid4())

        assert result == []


# ---------------------------------------------------------------------------
# create_run
# ---------------------------------------------------------------------------


class TestCreateRun:
    """create_run 创建。"""

    @pytest.mark.asyncio
    async def test_create_run_success(self) -> None:
        """创建运行记录成功。"""
        service = _make_service()
        flow_def = MagicMock()
        flow_def.department_id = uuid4()
        version = MagicMock()

        service._definition_svc.get_definition_by_id = AsyncMock(return_value=(flow_def, version))

        job_ref = MagicMock()
        job_ref.job_id = uuid4()
        service._job_service.accept = AsyncMock(return_value=job_ref)

        session = AsyncMock()

        with _patch_scoped_session(service, session):
            run = await service.create_run(uuid4(), inputs={"key": "value"})

        assert run.status == "pending"
        assert run.input_snapshot == {"key": "value"}
        assert run.job_id == job_ref.job_id

    @pytest.mark.asyncio
    async def test_create_run_no_inputs(self) -> None:
        """无输入时 input_snapshot 为空 dict。"""
        service = _make_service()
        flow_def = MagicMock()
        flow_def.department_id = uuid4()
        version = MagicMock()

        service._definition_svc.get_definition_by_id = AsyncMock(return_value=(flow_def, version))

        job_ref = MagicMock()
        job_ref.job_id = uuid4()
        service._job_service.accept = AsyncMock(return_value=job_ref)

        session = AsyncMock()

        with _patch_scoped_session(service, session):
            run = await service.create_run(uuid4(), inputs=None)

        assert run.input_snapshot == {}


# ---------------------------------------------------------------------------
# delete_run
# ---------------------------------------------------------------------------


class TestDeleteRun:
    """delete_run 删除。"""

    @pytest.mark.asyncio
    async def test_delete_run_with_job(self) -> None:
        """删除 run 及关联 job。"""
        service = _make_service()
        session = AsyncMock()
        run = MagicMock()
        run.job_id = uuid4()

        session.scalar = AsyncMock(return_value=run)
        session.execute = AsyncMock()
        session.flush = AsyncMock()

        with _patch_scoped_session(service, session):
            await service.delete_run(uuid4())

        # session.execute 被调用 3 次（删除 node_executions, run, job）
        assert session.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_delete_run_no_job(self) -> None:
        """run 不存在时不删 job。"""
        service = _make_service()
        session = AsyncMock()

        session.scalar = AsyncMock(return_value=None)
        session.execute = AsyncMock()
        session.flush = AsyncMock()

        with _patch_scoped_session(service, session):
            await service.delete_run(uuid4())

        # session.execute 被调用 2 次（仅删 node_executions 和 run）
        assert session.execute.await_count == 2


# ---------------------------------------------------------------------------
# get_run_fact_ids
# ---------------------------------------------------------------------------


class TestGetRunFactIds:
    """get_run_fact_ids 批量查询。"""

    @pytest.mark.asyncio
    async def test_empty_run_ids(self) -> None:
        """空 run_ids 列表 → 空 dict。"""
        service = _make_service()
        result = await service.get_run_fact_ids([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_with_run_ids(self) -> None:
        """有 run_ids 时查询并映射。"""
        service = _make_service()
        session = AsyncMock()
        run_id1 = uuid4()
        run_id2 = uuid4()
        fact_id1 = uuid4()
        fact_id2 = uuid4()

        execute_result = MagicMock()
        execute_result.__iter__ = MagicMock(
            return_value=iter([(fact_id1, run_id1), (fact_id2, run_id2)])
        )
        session.execute = AsyncMock(return_value=execute_result)

        with _patch_scoped_session(service, session):
            result = await service.get_run_fact_ids([run_id1, run_id2])

        assert result == {run_id1: str(fact_id1), run_id2: str(fact_id2)}


# ---------------------------------------------------------------------------
# get_latest_node_execution
# ---------------------------------------------------------------------------


class TestGetLatestNodeExecution:
    """get_latest_node_execution 查询。"""

    @pytest.mark.asyncio
    async def test_returns_latest(self) -> None:
        """返回最新节点执行记录。"""
        service = _make_service()
        session = AsyncMock()
        node_exec = MagicMock()

        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = node_exec
        session.execute = AsyncMock(return_value=execute_result)

        with _patch_scoped_session(service, session):
            result = await service.get_latest_node_execution(uuid4())

        assert result is node_exec

    @pytest.mark.asyncio
    async def test_returns_none_when_no_records(self) -> None:
        """无记录时返回 None。"""
        service = _make_service()
        session = AsyncMock()

        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=execute_result)

        with _patch_scoped_session(service, session):
            result = await service.get_latest_node_execution(uuid4())

        assert result is None


# ---------------------------------------------------------------------------
# get_latest_node_executions
# ---------------------------------------------------------------------------


class TestGetLatestNodeExecutions:
    """get_latest_node_executions 批量查询。"""

    @pytest.mark.asyncio
    async def test_empty_run_ids(self) -> None:
        """空 run_ids → 空 dict。"""
        service = _make_service()
        result = await service.get_latest_node_executions([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_latest_per_run(self) -> None:
        """每个 run 返回第一条（最新）记录。"""
        service = _make_service()
        session = AsyncMock()
        run_id1 = uuid4()
        run_id2 = uuid4()

        # 模拟按 flow_run_id, completed_at DESC 排序的行
        node1_run1 = MagicMock()
        node1_run1.flow_run_id = run_id1
        node2_run1 = MagicMock()
        node2_run1.flow_run_id = run_id1
        node1_run2 = MagicMock()
        node1_run2.flow_run_id = run_id2

        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = [node1_run1, node2_run1, node1_run2]
        session.execute = AsyncMock(return_value=execute_result)

        with _patch_scoped_session(service, session):
            result = await service.get_latest_node_executions([run_id1, run_id2])

        # 每个 run 只保留第一条
        assert result[run_id1] is node1_run1
        assert result[run_id2] is node1_run2
        assert len(result) == 2


# ---------------------------------------------------------------------------
# list_facts_by_flow
# ---------------------------------------------------------------------------


class TestListFactsByFlow:
    """list_facts_by_flow 查询。"""

    @pytest.mark.asyncio
    async def test_returns_facts(self) -> None:
        """返回事实列表。"""
        service = _make_service()
        session = AsyncMock()
        facts = [MagicMock(), MagicMock()]

        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = facts
        session.execute = AsyncMock(return_value=execute_result)

        with _patch_scoped_session(service, session):
            result = await service.list_facts_by_flow(uuid4())

        assert result == facts

    @pytest.mark.asyncio
    async def test_empty_flow(self) -> None:
        """无事实返回空列表。"""
        service = _make_service()
        session = AsyncMock()

        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=execute_result)

        with _patch_scoped_session(service, session):
            result = await service.list_facts_by_flow(uuid4())

        assert result == []
