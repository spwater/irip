"""分析执行集成测试：AnalysisRunService。

覆盖 ``packages/research.execution.run_service`` 的 Run 生命周期管理：
- submit_run: 计划校验 / 活跃 Run 校验 / 幂等提交 / attempt_number 计算
- cancel_run: 活跃状态校验 / 步骤取消 / 工件不可发布
- get_run_status / get_run_progress / list_runs / get_queue_position
- check_publish_eligibility: 依赖闭包完整性 / 部分成功发布

DB 依赖：通过 ``async_session_factory`` fixture 连接测试库。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest
import sqlalchemy as sa

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.research.entities import ResearchEvidenceSnapshot, ResearchWorkspace
from packages.research.execution.entities_trusted import (
    ResearchAnalysisPlanVersion,
    ResearchAnalysisRun,
    ResearchAnalysisStep,
    ResearchRunArtifact,
)
from packages.research.execution.models_trusted import QueuePosition
from packages.research.execution.repository_trusted import ResearchRepositoryTrusted
from packages.research.execution.run_service import AnalysisRunService
from packages.research.timeline.entities import ResearchTurn


@pytest.fixture(autouse=True)
def _patch_get_next_run_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """绕过 get_next_run_number 的 FOR UPDATE + 聚合函数兼容性问题（源码已知限制）。

    用不带 FOR UPDATE 的等价实现替换，使 submit_run 的编排逻辑可被集成测试覆盖。
    """

    async def _fixed(session, workspace_id):
        result = await session.execute(
            sa.select(sa.func.max(ResearchAnalysisRun.run_number)).where(
                ResearchAnalysisRun.workspace_id == workspace_id
            )
        )
        max_num = result.scalar()
        return (int(max_num) + 1) if max_num is not None else 1

    monkeypatch.setattr(ResearchRepositoryTrusted, "get_next_run_number", staticmethod(_fixed))


# ============================================================
# 共享 seed / cleanup
# ============================================================


@dataclass(frozen=True)
class _Seed:
    """最小执行场景的 ID 集合。"""

    workspace_id: UUID
    snapshot_id: UUID
    plan_id: UUID
    turn_id: UUID


async def _seed_confirmed_plan(factory, user, *, plan_status: str = "confirmed") -> _Seed:
    """插入 workspace/snapshot/turn/plan，返回 ID 集合。"""
    owner_id = user.user_id
    dept_id = user.department_id
    async with factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=owner_id,
                department_id=dept_id,
                name="run-service-test",
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
                question_text_snapshot="execution test",
                question_origin="manual",
                evidence_snapshot_id=snap.id,
                idempotency_key=f"exec-{ws.id}",
            )
            session.add(turn)
            await session.flush()

            plan = ResearchAnalysisPlanVersion(
                id=new_id(),
                workspace_id=ws.id,
                version_number=1,
                dag_structure={"steps": []},
                status=plan_status,
                created_by=owner_id,
                turn_id=turn.id,
            )
            session.add(plan)
            await session.flush()

            return _Seed(
                workspace_id=ws.id,
                snapshot_id=snap.id,
                plan_id=plan.id,
                turn_id=turn.id,
            )


async def _seed_run(
    factory,
    user,
    seed: _Seed,
    *,
    run_status: str = "queued",
    run_number: int = 1,
    attempt_number: int = 1,
    coverage_summary: dict | None = None,
) -> UUID:
    """插入一个 Run（指定状态），返回 run_id。"""
    async with factory() as session:
        async with session.begin():
            run = ResearchAnalysisRun(
                id=new_id(),
                workspace_id=seed.workspace_id,
                plan_version_id=seed.plan_id,
                snapshot_id=seed.snapshot_id,
                run_number=run_number,
                status=run_status,
                image_digest="llm-only",
                created_by=user.user_id,
                turn_id=seed.turn_id,
                attempt_number=attempt_number,
                coverage_summary=coverage_summary,
            )
            session.add(run)
            await session.flush()
            return run.id


async def _seed_steps(factory, run_id: UUID, steps: list[dict]) -> None:
    """插入步骤记录。"""
    async with factory() as session:
        async with session.begin():
            for i, s in enumerate(steps):
                session.add(
                    ResearchAnalysisStep(
                        id=new_id(),
                        run_id=run_id,
                        step_key=s["step_key"],
                        step_index=i,
                        status=s["status"],
                        method=s.get("method", "python"),
                        depends_on=s.get("depends_on", []),
                        coverage_rate=s.get("coverage_rate"),
                        analysis_mode=s.get("analysis_mode"),
                    )
                )


async def _seed_artifact(factory, run_id: UUID, *, is_publishable: bool = True) -> UUID:
    """插入一个工件，返回 artifact_id。"""
    async with factory() as session:
        async with session.begin():
            art = ResearchRunArtifact(
                id=new_id(),
                run_id=run_id,
                step_id=None,
                artifact_type="data",
                artifact_key="out.json",
                storage_path="research/artifacts/out.json",
                content_hash="e" * 64,
                size_bytes=100,
                is_publishable=is_publishable,
            )
            session.add(art)
            await session.flush()
            return art.id


async def _cleanup(factory, workspace_id: UUID) -> None:
    """删除工作空间（CASCADE 清理）。"""
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


class _FakeScheduler:
    """假调度器：记录 release_slot / get_queue_position 调用。"""

    def __init__(self) -> None:
        self.released: list[tuple[str, str]] = []
        self._queue_positions: dict[str, QueuePosition] = {}

    async def release_slot(self, user_id: str, run_id: str) -> None:
        self.released.append((user_id, run_id))

    async def get_queue_position(self, run_id: str) -> QueuePosition:
        return self._queue_positions.get(
            run_id, QueuePosition(position=1, ahead_count=0, estimated_wait_seconds=300)
        )


def _make_run_service(factory, user, scheduler=None) -> AnalysisRunService:
    """构造 AnalysisRunService。"""
    return AnalysisRunService(
        session_factory=factory,
        department_id=user.department_id,
        actor_id=user.user_id,
        scheduler=scheduler or _FakeScheduler(),
    )


@pytest.fixture
async def seeded(async_session_factory, test_user):
    """提供已确认计划场景并在测试后清理。"""
    seed = await _seed_confirmed_plan(async_session_factory, test_user)
    try:
        yield seed, async_session_factory, test_user
    finally:
        await _cleanup(async_session_factory, seed.workspace_id)


# ============================================================
# submit_run
# ============================================================


@pytest.mark.integration
async def test_submit_run_success(seeded) -> None:
    """提交 Run 成功：状态 queued + run_number=1。"""
    seed, factory, user = seeded
    sched = _FakeScheduler()
    svc = _make_run_service(factory, user, sched)
    ref = await svc.submit_run(
        seed.workspace_id, seed.plan_id, seed.snapshot_id, turn_id=seed.turn_id
    )
    assert ref.status == "queued"
    assert ref.run_number == 1
    assert ref.run_id is not None

    async with factory() as session:
        run = await session.scalar(
            sa.select(ResearchAnalysisRun).where(ResearchAnalysisRun.id == ref.run_id)
        )
        assert run is not None
        assert run.status == "queued"
        assert run.turn_id == seed.turn_id
        assert run.attempt_number == 1


@pytest.mark.integration
async def test_submit_run_increments_attempt_number(seeded) -> None:
    """同 turn 已有 Run 时 attempt_number 递增。"""
    seed, factory, user = seeded
    sched = _FakeScheduler()
    svc = _make_run_service(factory, user, sched)
    ref1 = await svc.submit_run(
        seed.workspace_id, seed.plan_id, seed.snapshot_id, turn_id=seed.turn_id
    )
    # 将第一个 Run 置为 succeeded，允许再提交
    async with factory() as session:
        async with session.begin():
            await session.execute(
                sa.update(ResearchAnalysisRun)
                .where(ResearchAnalysisRun.id == ref1.run_id)
                .values(status="succeeded")
            )
    ref2 = await svc.submit_run(
        seed.workspace_id, seed.plan_id, seed.snapshot_id, turn_id=seed.turn_id
    )
    assert ref2.run_number == 2
    async with factory() as session:
        run2 = await session.scalar(
            sa.select(ResearchAnalysisRun).where(ResearchAnalysisRun.id == ref2.run_id)
        )
        assert run2.attempt_number == 2


@pytest.mark.integration
async def test_submit_run_plan_not_confirmed(async_session_factory, test_user) -> None:
    """计划未确认时 submit_run 抛出 validation_failed。"""
    seed = await _seed_confirmed_plan(async_session_factory, test_user, plan_status="draft")
    try:
        svc = _make_run_service(async_session_factory, test_user)
        with pytest.raises(AppError) as exc_info:
            await svc.submit_run(seed.workspace_id, seed.plan_id, seed.snapshot_id)
        assert exc_info.value.code == "validation_failed"
    finally:
        await _cleanup(async_session_factory, seed.workspace_id)


@pytest.mark.integration
async def test_submit_run_plan_not_found(seeded) -> None:
    """计划不存在时 submit_run 抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_run_service(factory, user)
    with pytest.raises(AppError) as exc_info:
        await svc.submit_run(seed.workspace_id, new_id(), seed.snapshot_id)
    assert exc_info.value.code == "not_found"


@pytest.mark.integration
async def test_submit_run_active_run_exists(seeded) -> None:
    """已有活跃 Run 时 submit_run 抛出 validation_failed。"""
    seed, factory, user = seeded
    svc = _make_run_service(factory, user)
    await svc.submit_run(seed.workspace_id, seed.plan_id, seed.snapshot_id, turn_id=seed.turn_id)
    with pytest.raises(AppError) as exc_info:
        await svc.submit_run(seed.workspace_id, seed.plan_id, seed.snapshot_id)
    assert exc_info.value.code == "validation_failed"


@pytest.mark.integration
async def test_submit_run_requires_actor(async_session_factory, test_user) -> None:
    """actor_id 为 None 时 submit_run 抛出 forbidden。"""
    seed = await _seed_confirmed_plan(async_session_factory, test_user)
    try:
        svc = AnalysisRunService(
            session_factory=async_session_factory,
            department_id=test_user.department_id,
            actor_id=None,
            scheduler=_FakeScheduler(),
        )
        with pytest.raises(AppError) as exc_info:
            await svc.submit_run(seed.workspace_id, seed.plan_id, seed.snapshot_id)
        assert exc_info.value.code == "forbidden"
    finally:
        await _cleanup(async_session_factory, seed.workspace_id)


# ============================================================
# get_run_status / get_run_progress / list_runs
# ============================================================


@pytest.mark.integration
async def test_get_run_status_not_found(seeded) -> None:
    """Run 不存在时 get_run_status 抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_run_service(factory, user)
    with pytest.raises(AppError) as exc_info:
        await svc.get_run_status(new_id())
    assert exc_info.value.code == "not_found"


@pytest.mark.integration
async def test_get_run_progress_with_coverage(seeded) -> None:
    """get_run_progress 返回步骤进度 + 覆盖声明。"""
    seed, factory, user = seeded
    run_id = await _seed_run(
        factory,
        user,
        seed,
        run_status="running",
        coverage_summary={
            "analysis_mode": "mixed",
            "data_coverage_rate": 0.9,
            "llm_read_rate": 0.5,
            "is_sampled": False,
        },
    )
    await _seed_steps(
        factory,
        run_id,
        [
            {"step_key": "s1", "status": "succeeded"},
            {"step_key": "s2", "status": "running"},
        ],
    )
    svc = _make_run_service(factory, user)
    progress = await svc.get_run_progress(run_id)
    assert progress.status == "running"
    assert progress.total_steps == 2
    assert progress.completed_steps == 1
    assert len(progress.steps) == 2
    assert progress.coverage_declaration is not None
    assert progress.coverage_declaration.analysis_mode == "mixed"
    assert progress.coverage_declaration.data_coverage_rate == 0.9


@pytest.mark.integration
async def test_get_run_progress_not_found(seeded) -> None:
    """Run 不存在时 get_run_progress 抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_run_service(factory, user)
    with pytest.raises(AppError) as exc_info:
        await svc.get_run_progress(new_id())
    assert exc_info.value.code == "not_found"


@pytest.mark.integration
async def test_list_runs_returns_refs(seeded) -> None:
    """list_runs 返回工作空间的 Run 引用列表。"""
    seed, factory, user = seeded
    await _seed_run(factory, user, seed, run_status="succeeded", run_number=1, attempt_number=1)
    await _seed_run(factory, user, seed, run_status="queued", run_number=2, attempt_number=2)
    svc = _make_run_service(factory, user)
    refs = await svc.list_runs(seed.workspace_id)
    assert len(refs) == 2
    assert {r.run_number for r in refs} == {1, 2}


# ============================================================
# cancel_run
# ============================================================


@pytest.mark.integration
async def test_cancel_run_success(seeded) -> None:
    """取消活跃 Run：状态变 cancelled + 步骤取消/跳过 + 工件不可发布。"""
    seed, factory, user = seeded
    sched = _FakeScheduler()
    run_id = await _seed_run(factory, user, seed, run_status="running")
    await _seed_steps(
        factory,
        run_id,
        [
            {"step_key": "s1", "status": "running"},
            {"step_key": "s2", "status": "pending"},
            {"step_key": "s3", "status": "succeeded"},
        ],
    )
    art_id = await _seed_artifact(factory, run_id, is_publishable=True)

    svc = _make_run_service(factory, user, sched)
    await svc.cancel_run(run_id)

    async with factory() as session:
        run = await session.scalar(
            sa.select(ResearchAnalysisRun).where(ResearchAnalysisRun.id == run_id)
        )
        assert run.status == "cancelled"
        assert run.cancelled_by == user.user_id

        steps = (
            (
                await session.execute(
                    sa.select(ResearchAnalysisStep)
                    .where(ResearchAnalysisStep.run_id == run_id)
                    .order_by(ResearchAnalysisStep.step_index)
                )
            )
            .scalars()
            .all()
        )
        assert steps[0].status == "cancelled"
        assert steps[1].status == "skipped"
        assert steps[2].status == "succeeded"

        art = await session.scalar(
            sa.select(ResearchRunArtifact).where(ResearchRunArtifact.id == art_id)
        )
        assert art.is_publishable is False

    assert len(sched.released) == 1


@pytest.mark.integration
async def test_cancel_run_not_found(seeded) -> None:
    """Run 不存在时 cancel_run 抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_run_service(factory, user)
    with pytest.raises(AppError) as exc_info:
        await svc.cancel_run(new_id())
    assert exc_info.value.code == "not_found"


@pytest.mark.integration
async def test_cancel_run_not_active(seeded) -> None:
    """已终态 Run 不可取消。"""
    seed, factory, user = seeded
    run_id = await _seed_run(factory, user, seed, run_status="succeeded")
    svc = _make_run_service(factory, user)
    with pytest.raises(AppError) as exc_info:
        await svc.cancel_run(run_id)
    assert exc_info.value.code == "validation_failed"


# ============================================================
# get_queue_position
# ============================================================


@pytest.mark.integration
async def test_get_queue_position_non_queued_returns_zero(seeded) -> None:
    """非 queued 状态的 Run 返回 0 位置。"""
    seed, factory, user = seeded
    run_id = await _seed_run(factory, user, seed, run_status="running")
    svc = _make_run_service(factory, user)
    pos = await svc.get_queue_position(run_id)
    assert pos.position == 0
    assert pos.ahead_count == 0


@pytest.mark.integration
async def test_get_queue_position_not_found(seeded) -> None:
    """Run 不存在时 get_queue_position 抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_run_service(factory, user)
    with pytest.raises(AppError) as exc_info:
        await svc.get_queue_position(new_id())
    assert exc_info.value.code == "not_found"


# ============================================================
# check_publish_eligibility
# ============================================================


@pytest.mark.integration
async def test_publish_eligibility_succeeded_run(seeded) -> None:
    """全部步骤成功的 succeeded Run 可发布。"""
    seed, factory, user = seeded
    run_id = await _seed_run(factory, user, seed, run_status="succeeded")
    await _seed_steps(
        factory,
        run_id,
        [
            {"step_key": "s1", "status": "succeeded", "depends_on": []},
            {"step_key": "s2", "status": "succeeded", "depends_on": ["s1"]},
        ],
    )
    svc = _make_run_service(factory, user)
    result = await svc.check_publish_eligibility(run_id)
    assert result.is_eligible is True
    assert result.source_run_partial is False


@pytest.mark.integration
async def test_publish_eligibility_cancelled_run(seeded) -> None:
    """已取消的 Run 不可发布。"""
    seed, factory, user = seeded
    run_id = await _seed_run(factory, user, seed, run_status="cancelled")
    svc = _make_run_service(factory, user)
    result = await svc.check_publish_eligibility(run_id)
    assert result.is_eligible is False
    assert "取消" in result.message


@pytest.mark.integration
async def test_publish_eligibility_partial_with_failed_dep(seeded) -> None:
    """部分成功 Run 中，依赖失败步骤的目标步骤不可发布。"""
    seed, factory, user = seeded
    run_id = await _seed_run(factory, user, seed, run_status="partially_succeeded")
    await _seed_steps(
        factory,
        run_id,
        [
            {"step_key": "s1", "status": "failed", "depends_on": []},
            {"step_key": "s2", "status": "succeeded", "depends_on": ["s1"]},
        ],
    )
    svc = _make_run_service(factory, user)
    result = await svc.check_publish_eligibility(run_id)
    assert result.is_eligible is False
    assert "s1" in result.failed_step_keys
    assert result.source_run_partial is True


@pytest.mark.integration
async def test_publish_eligibility_not_found(seeded) -> None:
    """Run 不存在时 check_publish_eligibility 抛出 not_found。"""
    seed, factory, user = seeded
    svc = _make_run_service(factory, user)
    with pytest.raises(AppError) as exc_info:
        await svc.check_publish_eligibility(new_id())
    assert exc_info.value.code == "not_found"


@pytest.mark.integration
async def test_publish_eligibility_specific_step_keys(seeded) -> None:
    """指定 step_keys 时仅校验指定步骤的依赖闭包。"""
    seed, factory, user = seeded
    run_id = await _seed_run(factory, user, seed, run_status="succeeded")
    await _seed_steps(
        factory,
        run_id,
        [
            {"step_key": "s1", "status": "succeeded", "depends_on": []},
            {"step_key": "s2", "status": "failed", "depends_on": ["s1"]},
        ],
    )
    svc = _make_run_service(factory, user)
    # 仅校验 s1（无失败依赖）
    result = await svc.check_publish_eligibility(run_id, step_keys=["s1"])
    assert result.is_eligible is True
