"""PlanReviserMixin 单元测试：revise_plan 编排逻辑。

覆盖 ``packages.research.planning.plan_reviser`` 的业务分支：
- revise_plan 成功：旧版本 superseded + 新 draft 版本 + 审计
- 计划不存在 → not_found
- workspace 不匹配 → not_found
- coverage_declaration 保留
- actor_id 为 None → forbidden

使用 mock 替换 ResearchRepositoryTrusted 与 AuditRecorder，避免 DB NOT NULL 约束
（insert_plan_version 未传 turn_id），专注验证 revise_plan 的编排与分支逻辑。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.research.planning.plan_core import PlanService

DEPT_ID = uuid4()
ACTOR_ID = uuid4()
WORKSPACE_ID = uuid4()


def _make_plan_service() -> PlanService:
    """构造带 mock 依赖的 PlanService。"""
    return PlanService(
        session_factory=MagicMock(),
        department_id=DEPT_ID,
        actor_id=ACTOR_ID,
        model_gateway=MagicMock(),
        context_router=MagicMock(),
        fact_provider=MagicMock(),
        numeric_tools=None,
    )


def _fake_old_plan(plan_id=None, workspace_id=WORKSPACE_ID, version_number=1):
    """构造一个模拟的旧计划 ORM 对象。"""
    return SimpleNamespace(
        id=plan_id or uuid4(),
        workspace_id=workspace_id,
        version_number=version_number,
        status="confirmed",
        dag_structure={
            "steps": [{"step_key": "s1"}],
            "coverage_declaration": {"analysis_mode": "full_compute"},
        },
        coverage_declaration={"analysis_mode": "full_compute"},
    )


def _fake_new_version(version_number=2):
    """构造一个模拟的新版本 ORM 对象。"""
    return SimpleNamespace(
        id=uuid4(),
        version_number=version_number,
    )


class _ScopedSessionCtx:
    """模拟 _scoped_session 异步上下文管理器。"""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_revise_plan_success() -> None:
    """revise_plan 成功：标记旧版本 superseded + 创建新 draft 版本 + 审计。"""
    svc = _make_plan_service()
    old_plan = _fake_old_plan()
    new_version = _fake_new_version(version_number=2)
    mock_session = MagicMock()

    with (
        patch.object(svc, "_scoped_session", return_value=_ScopedSessionCtx(mock_session)),
        patch(
            "packages.research.planning.plan_reviser.ResearchRepositoryTrusted.get_plan",
            new=AsyncMock(return_value=old_plan),
        ) as mock_get_plan,
        patch(
            "packages.research.planning.plan_reviser.ResearchRepositoryTrusted.update_plan_status",
            new=AsyncMock(),
        ) as mock_update_status,
        patch(
            "packages.research.planning.plan_reviser.ResearchRepositoryTrusted.insert_plan_version",
            new=AsyncMock(return_value=new_version),
        ) as mock_insert,
        patch(
            "packages.research.planning.plan_reviser.AuditRecorder.record",
            new=AsyncMock(),
        ) as mock_audit,
    ):
        revised_steps = [
            {"step_key": "s1", "method": "python"},
            {"step_key": "s2", "method": "llm"},
        ]
        ref = await svc.revise_plan(WORKSPACE_ID, old_plan.id, revised_steps)

    assert ref.status == "draft"
    assert ref.version_number == 2
    assert ref.step_count == 2
    assert ref.workspace_id == WORKSPACE_ID
    mock_get_plan.assert_awaited_once()
    mock_update_status.assert_awaited_once_with(mock_session, old_plan.id, "superseded")
    mock_insert.assert_awaited_once()
    mock_audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_revise_plan_not_found() -> None:
    """计划不存在时 revise_plan 抛出 not_found。"""
    svc = _make_plan_service()
    mock_session = MagicMock()
    plan_id = uuid4()

    with (
        patch.object(svc, "_scoped_session", return_value=_ScopedSessionCtx(mock_session)),
        patch(
            "packages.research.planning.plan_reviser.ResearchRepositoryTrusted.get_plan",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "packages.research.planning.plan_reviser.AuditRecorder.record",
            new=AsyncMock(),
        ),
    ):
        with pytest.raises(AppError) as exc_info:
            await svc.revise_plan(WORKSPACE_ID, plan_id, [])
        assert exc_info.value.code == "not_found"


@pytest.mark.asyncio
async def test_revise_plan_workspace_mismatch() -> None:
    """计划不属于该 workspace 时 revise_plan 抛出 not_found。"""
    svc = _make_plan_service()
    mock_session = MagicMock()
    other_ws = uuid4()
    old_plan = _fake_old_plan(workspace_id=other_ws)

    with (
        patch.object(svc, "_scoped_session", return_value=_ScopedSessionCtx(mock_session)),
        patch(
            "packages.research.planning.plan_reviser.ResearchRepositoryTrusted.get_plan",
            new=AsyncMock(return_value=old_plan),
        ),
        patch(
            "packages.research.planning.plan_reviser.AuditRecorder.record",
            new=AsyncMock(),
        ),
    ):
        with pytest.raises(AppError) as exc_info:
            await svc.revise_plan(WORKSPACE_ID, old_plan.id, [])
        assert exc_info.value.code == "not_found"


@pytest.mark.asyncio
async def test_revise_plan_preserves_coverage_declaration() -> None:
    """revise_plan 保留旧版本的 coverage_declaration 并写入新 dag_structure。"""
    svc = _make_plan_service()
    old_plan = _fake_old_plan()
    new_version = _fake_new_version(version_number=2)
    mock_session = MagicMock()

    with (
        patch.object(svc, "_scoped_session", return_value=_ScopedSessionCtx(mock_session)),
        patch(
            "packages.research.planning.plan_reviser.ResearchRepositoryTrusted.get_plan",
            new=AsyncMock(return_value=old_plan),
        ),
        patch(
            "packages.research.planning.plan_reviser.ResearchRepositoryTrusted.update_plan_status",
            new=AsyncMock(),
        ),
        patch(
            "packages.research.planning.plan_reviser.ResearchRepositoryTrusted.insert_plan_version",
            new=AsyncMock(return_value=new_version),
        ) as mock_insert,
        patch(
            "packages.research.planning.plan_reviser.AuditRecorder.record",
            new=AsyncMock(),
        ),
    ):
        await svc.revise_plan(WORKSPACE_ID, old_plan.id, [{"step_key": "x"}])

    # insert_plan_version 的 dag_structure 应包含旧 coverage_declaration
    call_kwargs = mock_insert.await_args.kwargs
    assert call_kwargs["coverage_declaration"] == {"analysis_mode": "full_compute"}
    assert call_kwargs["dag_structure"]["coverage_declaration"] == {"analysis_mode": "full_compute"}
    assert call_kwargs["version_number"] == 2


@pytest.mark.asyncio
async def test_revise_plan_requires_actor() -> None:
    """actor_id 为 None 时 revise_plan 抛出 forbidden。"""
    svc = PlanService(
        session_factory=MagicMock(),
        department_id=DEPT_ID,
        actor_id=None,
        model_gateway=MagicMock(),
        context_router=MagicMock(),
        fact_provider=MagicMock(),
        numeric_tools=None,
    )
    with pytest.raises(AppError) as exc_info:
        await svc.revise_plan(WORKSPACE_ID, uuid4(), [])
    assert exc_info.value.code == "forbidden"


@pytest.mark.asyncio
async def test_revise_plan_passes_revised_steps_in_dag() -> None:
    """revise_plan 将修订后的 steps 写入新版本的 dag_structure。"""
    svc = _make_plan_service()
    old_plan = _fake_old_plan()
    new_version = _fake_new_version(version_number=2)
    mock_session = MagicMock()

    with (
        patch.object(svc, "_scoped_session", return_value=_ScopedSessionCtx(mock_session)),
        patch(
            "packages.research.planning.plan_reviser.ResearchRepositoryTrusted.get_plan",
            new=AsyncMock(return_value=old_plan),
        ),
        patch(
            "packages.research.planning.plan_reviser.ResearchRepositoryTrusted.update_plan_status",
            new=AsyncMock(),
        ),
        patch(
            "packages.research.planning.plan_reviser.ResearchRepositoryTrusted.insert_plan_version",
            new=AsyncMock(return_value=new_version),
        ) as mock_insert,
        patch(
            "packages.research.planning.plan_reviser.AuditRecorder.record",
            new=AsyncMock(),
        ),
    ):
        steps = [{"step_key": "a"}, {"step_key": "b"}, {"step_key": "c"}]
        await svc.revise_plan(WORKSPACE_ID, old_plan.id, steps)

    dag = mock_insert.await_args.kwargs["dag_structure"]
    assert dag["steps"] == steps
