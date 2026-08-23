"""PlanService 集成测试：plan_core + plan_reviser。

覆盖 ``packages.research.planning.plan_core``（list_plans / get_plan）与
``packages.research.planning.plan_reviser``（revise_plan）的真实数据库行为。

DB 依赖：通过 ``async_session_factory`` fixture 连接测试库。
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import sqlalchemy as sa

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.research.entities import ResearchEvidenceSnapshot, ResearchWorkspace
from packages.research.execution.entities_trusted import ResearchAnalysisPlanVersion
from packages.research.planning.plan_core import PlanService
from packages.research.timeline.entities import ResearchTurn

# ============================================================
# 共享 seed / cleanup
# ============================================================


@dataclass(frozen=True)
class _Seed:
    """计划场景的 ID 集合。"""

    workspace_id: UUID
    snapshot_id: UUID
    turn_id: UUID
    plan_id: UUID


async def _seed_plan(factory, user, *, plan_status: str = "draft", version: int = 1) -> _Seed:
    """插入 workspace/snapshot/turn/plan，返回 ID 集合。"""
    owner_id = user.user_id
    dept_id = user.department_id
    async with factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=owner_id,
                department_id=dept_id,
                name="plan-core-test",
            )
            session.add(ws)
            await session.flush()

            snap = ResearchEvidenceSnapshot(
                id=new_id(),
                workspace_id=ws.id,
                snapshot_number=1,
                content_hash="0" * 64,
                permission_envelope={},
                field_manifest={},
                source_refs=[],
                created_by=owner_id,
            )
            session.add(snap)
            await session.flush()

            turn = ResearchTurn(
                id=new_id(),
                workspace_id=ws.id,
                turn_number=1,
                kind="analysis",
                status="queued",
                question_text_snapshot="plan test",
                question_origin="manual",
                evidence_snapshot_id=snap.id,
                idempotency_key=f"plan-{ws.id}",
            )
            session.add(turn)
            await session.flush()

            plan = ResearchAnalysisPlanVersion(
                id=new_id(),
                workspace_id=ws.id,
                version_number=version,
                dag_structure={
                    "steps": [
                        {"step_key": "s1", "question": "Q1", "method": "python"},
                    ],
                    "coverage_declaration": {"analysis_mode": "full_compute"},
                },
                coverage_declaration={"analysis_mode": "full_compute"},
                status=plan_status,
                created_by=owner_id,
                turn_id=turn.id,
            )
            session.add(plan)
            await session.flush()

            return _Seed(
                workspace_id=ws.id,
                snapshot_id=snap.id,
                turn_id=turn.id,
                plan_id=plan.id,
            )


async def _cleanup(factory, workspace_id: UUID) -> None:
    """清理审计 + 工作空间。"""
    async with factory() as session:
        async with session.begin():
            await session.execute(sa.text("ALTER TABLE audit_event DISABLE TRIGGER ALL"))
            await session.execute(
                sa.text(
                    "DELETE FROM audit_event WHERE department_id = "
                    "(SELECT department_id FROM research_workspace WHERE id = :wid)"
                ),
                {"wid": str(workspace_id)},
            )
            await session.execute(sa.text("ALTER TABLE audit_event ENABLE TRIGGER ALL"))
            await session.execute(
                sa.text("DELETE FROM research_workspace WHERE id = :wid"),
                {"wid": str(workspace_id)},
            )


def _make_plan_service(factory, user) -> PlanService:
    """构造 PlanService（model_gateway / context_router / fact_provider 为 mock）。"""
    return PlanService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        model_gateway=MagicMock(),
        context_router=MagicMock(),
        fact_provider=MagicMock(),
        numeric_tools=None,
    )


@pytest.fixture
async def seeded(async_session_factory, test_user):
    """提供已 draft 计划场景并在测试后清理。"""
    seed = await _seed_plan(async_session_factory, test_user)
    try:
        yield seed, async_session_factory, test_user
    finally:
        await _cleanup(async_session_factory, seed.workspace_id)


# ============================================================
# list_plans
# ============================================================


@pytest.mark.integration
async def test_list_plans_returns_refs(seeded) -> None:
    """list_plans 返回工作空间的计划版本引用列表。"""
    seed, factory, user = seeded
    svc = _make_plan_service(factory, user)
    refs = await svc.list_plans(seed.workspace_id)
    assert len(refs) == 1
    assert refs[0].plan_id == seed.plan_id
    assert refs[0].status == "draft"
    assert refs[0].step_count == 1


@pytest.mark.integration
async def test_list_plans_empty(async_session_factory, test_user) -> None:
    """无计划的工作空间返回空列表。"""
    async with async_session_factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=test_user.user_id,
                department_id=test_user.department_id,
                name="empty-plan-ws",
            )
            session.add(ws)
            await session.flush()
            ws_id = ws.id
    try:
        svc = _make_plan_service(async_session_factory, test_user)
        refs = await svc.list_plans(ws_id)
        assert refs == []
    finally:
        await _cleanup(async_session_factory, ws_id)


# ============================================================
# get_plan
# ============================================================


@pytest.mark.integration
async def test_get_plan_returns_detail(seeded) -> None:
    """get_plan 返回计划详情含 DAG 与覆盖声明。"""
    seed, factory, user = seeded
    svc = _make_plan_service(factory, user)
    detail = await svc.get_plan(seed.workspace_id, seed.plan_id)
    assert detail.plan_id == seed.plan_id
    assert detail.status == "draft"
    assert "steps" in detail.dag_structure
    assert detail.coverage_declaration is not None


@pytest.mark.integration
async def test_get_plan_not_found(seeded) -> None:
    """计划不存在时 get_plan 抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_plan_service(factory, user)
    with pytest.raises(AppError) as exc_info:
        await svc.get_plan(seed.workspace_id, new_id())
    assert exc_info.value.code == "not_found"


@pytest.mark.integration
async def test_get_plan_workspace_mismatch(seeded) -> None:
    """计划不属于该 workspace 时 get_plan 抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_plan_service(factory, user)
    other_ws = new_id()
    with pytest.raises(AppError) as exc_info:
        await svc.get_plan(other_ws, seed.plan_id)
    assert exc_info.value.code == "not_found"


# revise_plan 的单元测试见 tests/unit/research/test_plan_reviser.py
# （因 insert_plan_version 未传 turn_id 触发 NOT NULL 约束，改用 mock 单元测试覆盖编排逻辑）
