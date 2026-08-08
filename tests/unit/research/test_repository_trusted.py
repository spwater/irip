"""单元测试：ResearchRepositoryTrusted 可信执行数据访问层。

覆盖计划版本/Run/步骤/工件/记忆文档的全部 CRUD 静态方法。
使用 mock AsyncSession。
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from packages.research.execution.repository_trusted import ResearchRepositoryTrusted


def _make_result(scalar: Any = None, scalars_all: list[Any] | None = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = scalars_all or []
    result.scalars.return_value = scalars_mock
    return result


# ============================================================
# Plan version
# ============================================================


class TestPlanVersion:
    """计划版本 CRUD 测试。"""

    async def test_insert_plan_version(self) -> None:
        session = AsyncMock()
        plan = await ResearchRepositoryTrusted.insert_plan_version(
            session,
            workspace_id=uuid4(),
            version_number=1,
            dag_structure={"steps": []},
            created_by=uuid4(),
        )
        session.add.assert_called_once()
        session.flush.assert_awaited_once()
        assert plan.version_number == 1

    async def test_insert_plan_version_with_coverage(self) -> None:
        session = AsyncMock()
        plan = await ResearchRepositoryTrusted.insert_plan_version(
            session,
            workspace_id=uuid4(),
            version_number=2,
            dag_structure={},
            coverage_declaration={"mode": "regression"},
            created_by=uuid4(),
        )
        assert plan.coverage_declaration == {"mode": "regression"}

    async def test_get_plan(self) -> None:
        plan = MagicMock()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=plan))
        result = await ResearchRepositoryTrusted.get_plan(session, uuid4())
        assert result is plan

    async def test_get_plan_not_found(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=None))
        result = await ResearchRepositoryTrusted.get_plan(session, uuid4())
        assert result is None

    async def test_list_plans(self) -> None:
        plans = [MagicMock(), MagicMock()]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalars_all=plans))
        result = await ResearchRepositoryTrusted.list_plans(session, uuid4())
        assert result == plans

    async def test_list_plans_empty(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalars_all=[]))
        result = await ResearchRepositoryTrusted.list_plans(session, uuid4())
        assert result == []

    async def test_get_latest_plan_version(self) -> None:
        plan = MagicMock()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=plan))
        result = await ResearchRepositoryTrusted.get_latest_plan_version(session, uuid4())
        assert result is plan

    async def test_get_latest_plan_version_none(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=None))
        result = await ResearchRepositoryTrusted.get_latest_plan_version(session, uuid4())
        assert result is None

    async def test_update_plan_status(self) -> None:
        session = AsyncMock()
        await ResearchRepositoryTrusted.update_plan_status(session, uuid4(), "confirmed")
        session.execute.assert_awaited_once()

    async def test_update_plan_status_with_confirmed_at(self) -> None:
        session = AsyncMock()
        now = datetime.now(UTC)
        await ResearchRepositoryTrusted.update_plan_status(
            session, uuid4(), "confirmed", confirmed_at=now, confirmed_by=uuid4()
        )
        session.execute.assert_awaited_once()

    async def test_supersede_old_plans(self) -> None:
        session = AsyncMock()
        await ResearchRepositoryTrusted.supersede_old_plans(session, uuid4(), uuid4())
        session.execute.assert_awaited_once()


# ============================================================
# Run
# ============================================================


class TestRun:
    """Run CRUD 测试。"""

    async def test_insert_run(self) -> None:
        session = AsyncMock()
        run = await ResearchRepositoryTrusted.insert_run(
            session,
            workspace_id=uuid4(),
            plan_version_id=uuid4(),
            snapshot_id=uuid4(),
            run_number=1,
            image_digest="sha256:test",
            created_by=uuid4(),
        )
        session.add.assert_called_once()
        session.flush.assert_awaited_once()
        assert run.run_number == 1
        assert run.status == "queued"

    async def test_get_run(self) -> None:
        run = MagicMock()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=run))
        result = await ResearchRepositoryTrusted.get_run(session, uuid4())
        assert result is run

    async def test_get_run_not_found(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=None))
        result = await ResearchRepositoryTrusted.get_run(session, uuid4())
        assert result is None

    async def test_list_runs(self) -> None:
        runs = [MagicMock(), MagicMock()]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalars_all=runs))
        result = await ResearchRepositoryTrusted.list_runs(session, uuid4())
        assert result == runs

    async def test_list_runs_empty(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalars_all=[]))
        result = await ResearchRepositoryTrusted.list_runs(session, uuid4())
        assert result == []

    async def test_update_run_status(self) -> None:
        session = AsyncMock()
        await ResearchRepositoryTrusted.update_run_status(session, uuid4(), "succeeded")
        session.execute.assert_awaited_once()

    async def test_update_run_status_with_full_params(self) -> None:
        session = AsyncMock()
        now = datetime.now(UTC)
        await ResearchRepositoryTrusted.update_run_status(
            session,
            uuid4(),
            "succeeded",
            started_at=now,
            completed_at=now,
            coverage_summary={"x": 1},
        )
        session.execute.assert_awaited_once()

    async def test_update_run_status_cancelled(self) -> None:
        session = AsyncMock()
        now = datetime.now(UTC)
        await ResearchRepositoryTrusted.update_run_status(
            session,
            uuid4(),
            "cancelled",
            cancelled_at=now,
            cancelled_by=uuid4(),
            error_summary="error",
        )
        session.execute.assert_awaited_once()

    async def test_get_active_run_for_workspace(self) -> None:
        run = MagicMock()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=run))
        result = await ResearchRepositoryTrusted.get_active_run_for_workspace(session, uuid4())
        assert result is run

    async def test_get_active_run_for_workspace_none(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=None))
        result = await ResearchRepositoryTrusted.get_active_run_for_workspace(session, uuid4())
        assert result is None

    async def test_update_run_queue_position(self) -> None:
        session = AsyncMock()
        await ResearchRepositoryTrusted.update_run_queue_position(session, uuid4(), position=3)
        session.execute.assert_awaited_once()


# ============================================================
# Step
# ============================================================


class TestStep:
    """步骤 CRUD 测试。"""

    async def test_get_step(self) -> None:
        step = MagicMock()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=step))
        result = await ResearchRepositoryTrusted.get_step(session, uuid4())
        assert result is step

    async def test_get_step_not_found(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=None))
        result = await ResearchRepositoryTrusted.get_step(session, uuid4())
        assert result is None

    async def test_list_steps_by_run(self) -> None:
        steps = [MagicMock(), MagicMock()]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalars_all=steps))
        result = await ResearchRepositoryTrusted.list_steps_by_run(session, uuid4())
        assert result == steps

    async def test_list_steps_by_run_empty(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalars_all=[]))
        result = await ResearchRepositoryTrusted.list_steps_by_run(session, uuid4())
        assert result == []

    async def test_update_step_status(self) -> None:
        session = AsyncMock()
        await ResearchRepositoryTrusted.update_step_status(session, uuid4(), "succeeded")
        session.execute.assert_awaited_once()

    async def test_get_step_by_key(self) -> None:
        step = MagicMock()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=step))
        result = await ResearchRepositoryTrusted.get_step_by_key(session, uuid4(), "s1")
        assert result is step


# ============================================================
# Artifact
# ============================================================


class TestArtifact:
    """工件 CRUD 测试。"""

    async def test_get_artifact(self) -> None:
        artifact = MagicMock()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=artifact))
        result = await ResearchRepositoryTrusted.get_artifact(session, uuid4())
        assert result is artifact

    async def test_get_artifact_not_found(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=None))
        result = await ResearchRepositoryTrusted.get_artifact(session, uuid4())
        assert result is None

    async def test_list_artifacts_by_run(self) -> None:
        artifacts = [MagicMock(), MagicMock()]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalars_all=artifacts))
        result = await ResearchRepositoryTrusted.list_artifacts_by_run(session, uuid4())
        assert result == artifacts

    async def test_list_artifacts_by_step(self) -> None:
        artifacts = [MagicMock()]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalars_all=artifacts))
        result = await ResearchRepositoryTrusted.list_artifacts_by_step(session, uuid4())
        assert result == artifacts

    async def test_update_artifact_publishable(self) -> None:
        session = AsyncMock()
        await ResearchRepositoryTrusted.update_artifact_publishable(
            session, uuid4(), is_publishable=True
        )
        session.execute.assert_awaited_once()


# ============================================================
# Memory document
# ============================================================


class TestMemory:
    """记忆文档 CRUD 测试。"""

    async def test_get_memory(self) -> None:
        mem = MagicMock()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=mem))
        result = await ResearchRepositoryTrusted.get_memory(session, uuid4())
        assert result is mem

    async def test_get_memory_not_found(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=None))
        result = await ResearchRepositoryTrusted.get_memory(session, uuid4())
        assert result is None


class TestConversation:
    """对话 CRUD 测试。"""

    async def test_insert_conversation_message(self) -> None:
        session = AsyncMock()
        msg = await ResearchRepositoryTrusted.insert_conversation_message(
            session,
            workspace_id=uuid4(),
            run_id=uuid4(),
            role="user",
            content="你好",
        )
        session.add.assert_called_once()
        session.flush.assert_awaited_once()
        assert msg.role == "user"
