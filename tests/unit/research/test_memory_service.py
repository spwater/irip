"""单元测试：ResearchMemoryService 研究记忆文档服务。

覆盖：
- get_or_create：已有文档返回 + 不存在创建空文档；
- update_from_event：run.started / run.completed / plan.confirmed / insight.accepted
  / insight.rejected / 未知事件；
- rebuild_from_events：从 plans + runs 重建文档；
- set_context：设置租户上下文。

使用 patched _scoped_session + mock ResearchRepositoryTrusted。
"""

import copy
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from packages.common.database import ScopedSessionMixin
from packages.research.memory_service import DEFAULT_DOCUMENT, ResearchMemoryService

# ============================================================
# Helpers
# ============================================================


@asynccontextmanager
async def _patch_scoped_session(mock_session: AsyncMock) -> Any:
    original = ScopedSessionMixin._scoped_session

    @asynccontextmanager
    async def fake_scoped_session(self: Any) -> Any:
        yield mock_session

    ScopedSessionMixin._scoped_session = fake_scoped_session  # type: ignore[method-assign]
    try:
        yield
    finally:
        ScopedSessionMixin._scoped_session = original  # type: ignore[method-assign]


def _make_service() -> ResearchMemoryService:
    return ResearchMemoryService(session_factory=MagicMock())


def _make_memory(document: dict[str, Any] | None = None) -> MagicMock:
    mem = MagicMock()
    mem.document = document if document is not None else copy.deepcopy(DEFAULT_DOCUMENT)
    return mem


# ============================================================
# set_context
# ============================================================


class TestSetContext:
    """set_context 测试。"""

    def test_set_context_sets_dept_and_actor(self) -> None:
        """set_context 设置 department_id 和 actor_id。"""
        svc = _make_service()
        dept = uuid4()
        actor = uuid4()
        svc.set_context(department_id=dept, actor_id=actor)
        assert svc._dept_id == dept
        assert svc._actor_id == actor


# ============================================================
# get_or_create
# ============================================================


class TestGetOrCreate:
    """get_or_create 测试。"""

    async def test_returns_existing_document(self) -> None:
        """已有文档时返回其内容。"""
        session = AsyncMock()
        doc = {"main_question": "问题", "completed_runs": []}
        svc = _make_service()

        with patch(
            "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
            new_callable=AsyncMock,
            return_value=_make_memory(doc),
        ):
            async with _patch_scoped_session(session):
                result = await svc.get_or_create(uuid4())

        assert result["main_question"] == "问题"

    async def test_creates_empty_document(self) -> None:
        """无文档时创建空文档。"""
        session = AsyncMock()
        svc = _make_service()

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ) as mock_upsert,
        ):
            async with _patch_scoped_session(session):
                result = await svc.get_or_create(uuid4())

        assert result == DEFAULT_DOCUMENT
        mock_upsert.assert_awaited_once()


# ============================================================
# update_from_event
# ============================================================


class TestUpdateFromEvent:
    """update_from_event 测试。"""

    async def test_run_started(self) -> None:
        """run.started 事件添加 run 记录。"""
        session = AsyncMock()
        svc = _make_service()
        ws_id = uuid4()

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
                new_callable=AsyncMock,
                return_value=_make_memory(),
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.update_from_event(ws_id, "run.started", {"run_id": "run-001"})

        assert len(result["completed_runs"]) == 1
        assert result["completed_runs"][0]["run_id"] == "run-001"
        assert result["completed_runs"][0]["status"] == "started"

    async def test_run_started_no_run_id(self) -> None:
        """run.started 无 run_id 时不添加记录。"""
        session = AsyncMock()
        svc = _make_service()

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
                new_callable=AsyncMock,
                return_value=_make_memory(),
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.update_from_event(uuid4(), "run.started", {})

        assert len(result["completed_runs"]) == 0

    async def test_run_completed_new_run(self) -> None:
        """run.completed 新 run 添加记录。"""
        session = AsyncMock()
        svc = _make_service()

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
                new_callable=AsyncMock,
                return_value=_make_memory(),
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.update_from_event(
                    uuid4(),
                    "run.completed",
                    {"run_id": "run-002", "status": "succeeded", "coverage": {}},
                )

        assert len(result["completed_runs"]) == 1
        assert result["completed_runs"][0]["status"] == "succeeded"

    async def test_run_completed_updates_existing_run(self) -> None:
        """run.completed 更新已有 run 记录。"""
        session = AsyncMock()
        doc = copy.deepcopy(DEFAULT_DOCUMENT)
        doc["completed_runs"] = [{"run_id": "run-001", "status": "started"}]
        svc = _make_service()

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
                new_callable=AsyncMock,
                return_value=_make_memory(doc),
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.update_from_event(
                    uuid4(),
                    "run.completed",
                    {"run_id": "run-001", "status": "succeeded", "coverage": {"x": 1}},
                )

        assert len(result["completed_runs"]) == 1
        assert result["completed_runs"][0]["status"] == "succeeded"
        assert result["completed_runs"][0]["coverage"] == {"x": 1}

    async def test_run_completed_extracts_key_method(self) -> None:
        """run.completed 从 coverage.analysis_mode 提取关键方法。"""
        session = AsyncMock()
        svc = _make_service()

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
                new_callable=AsyncMock,
                return_value=_make_memory(),
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.update_from_event(
                    uuid4(),
                    "run.completed",
                    {
                        "run_id": "r1",
                        "status": "succeeded",
                        "coverage": {"analysis_mode": "regression"},
                    },
                )

        assert "regression" in result["key_methods"]

    async def test_plan_confirmed(self) -> None:
        """plan.confirmed 更新 confirmed_plan。"""
        session = AsyncMock()
        svc = _make_service()

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
                new_callable=AsyncMock,
                return_value=_make_memory(),
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.update_from_event(
                    uuid4(),
                    "plan.confirmed",
                    {"version_number": 2, "steps": [{"name": "s1"}]},
                )

        assert result["confirmed_plan"]["version"] == 2
        assert result["confirmed_plan"]["steps"] == [{"name": "s1"}]

    async def test_insight_accepted(self) -> None:
        """insight.accepted 加入 accepted_insights。"""
        session = AsyncMock()
        svc = _make_service()

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
                new_callable=AsyncMock,
                return_value=_make_memory(),
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.update_from_event(
                    uuid4(),
                    "insight.accepted",
                    {"insight_id": "ins-1", "conclusion": "结论"},
                )

        assert len(result["accepted_insights"]) == 1

    async def test_insight_accepted_no_insight_id(self) -> None:
        """insight.accepted 无 insight_id 不添加。"""
        session = AsyncMock()
        svc = _make_service()

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
                new_callable=AsyncMock,
                return_value=_make_memory(),
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.update_from_event(uuid4(), "insight.accepted", {})

        assert len(result["accepted_insights"]) == 0

    async def test_insight_rejected(self) -> None:
        """insight.rejected 加入 rejected_insights。"""
        session = AsyncMock()
        svc = _make_service()

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
                new_callable=AsyncMock,
                return_value=_make_memory(),
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.update_from_event(
                    uuid4(),
                    "insight.rejected",
                    {"insight_id": "ins-2"},
                )

        assert len(result["rejected_insights"]) == 1

    async def test_unknown_event_type(self) -> None:
        """未知事件类型不修改文档。"""
        session = AsyncMock()
        svc = _make_service()
        doc_full = copy.deepcopy(DEFAULT_DOCUMENT)
        doc_full["main_question"] = "Q"

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
                new_callable=AsyncMock,
                return_value=_make_memory(doc_full),
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.update_from_event(uuid4(), "unknown.event", {})

        assert result["main_question"] == "Q"

    async def test_creates_doc_when_none_exists(self) -> None:
        """无文档时创建默认文档并更新。"""
        session = AsyncMock()
        svc = _make_service()

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.get_memory",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.update_from_event(uuid4(), "run.started", {"run_id": "r1"})

        assert len(result["completed_runs"]) == 1


# ============================================================
# rebuild_from_events
# ============================================================


class TestRebuildFromEvents:
    """rebuild_from_events 测试。"""

    async def test_rebuild_with_confirmed_plan(self) -> None:
        """重建时从已确认 plan 提取 confirmed_plan。"""
        session = AsyncMock()
        svc = _make_service()

        plan = MagicMock()
        plan.status = "confirmed"
        plan.version_number = 1
        plan.dag_structure = {"steps": [{"name": "s1"}]}

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.list_plans",
                new_callable=AsyncMock,
                return_value=[plan],
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.list_runs",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.rebuild_from_events(uuid4())

        assert result["confirmed_plan"]["version"] == 1
        assert result["confirmed_plan"]["steps"] == [{"name": "s1"}]

    async def test_rebuild_with_completed_runs(self) -> None:
        """重建时从已完成 run 提取 completed_runs。"""
        session = AsyncMock()
        svc = _make_service()

        run = MagicMock()
        run.id = uuid4()
        run.status = "succeeded"
        run.run_number = 1
        run.coverage_summary = {"x": 1}

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.list_plans",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.list_runs",
                new_callable=AsyncMock,
                return_value=[run],
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.rebuild_from_events(uuid4())

        assert len(result["completed_runs"]) == 1
        assert result["completed_runs"][0]["status"] == "succeeded"
        assert result["completed_runs"][0]["coverage"] == {"x": 1}

    async def test_rebuild_skips_non_terminal_runs(self) -> None:
        """重建时跳过非终态 run。"""
        session = AsyncMock()
        svc = _make_service()

        run = MagicMock()
        run.id = uuid4()
        run.status = "running"
        run.run_number = 1
        run.coverage_summary = {}

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.list_plans",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.list_runs",
                new_callable=AsyncMock,
                return_value=[run],
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.rebuild_from_events(uuid4())

        assert len(result["completed_runs"]) == 0

    async def test_rebuild_empty(self) -> None:
        """无 plans 和 runs 时返回空文档。"""
        session = AsyncMock()
        svc = _make_service()

        with (
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.list_plans",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.list_runs",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "packages.research.memory_service.ResearchRepositoryTrusted.upsert_memory",
                new_callable=AsyncMock,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await svc.rebuild_from_events(uuid4())

        assert result["confirmed_plan"] is None
        assert result["completed_runs"] == []
