"""分析执行生命周期集成测试（Program Gate 2 Step 1）。

证明以下契约成立（非空壳，真实连接测试数据库）：
1. 每个成功 Run 恰有一个 ``run_id`` 非空的 ``ResearchTurnResult``（唯一约束 + 幂等）。
2. 每个 Run 最多一个 ``CandidateExtractionJob``（``run_id`` 唯一约束 + 幂等入队）。
3. 提取重试不产生重复候选（``retry`` 只重置状态 + ``execute`` CAS 幂等）。

DB 依赖：通过 ``IRIP_TEST_DATABASE_URL`` 连接测试库（tests/integration/conftest.py
的 ``sync_engine`` / ``async_session_factory`` fixture）；未设置时由 testcontainers
回退或 skip（环境原因，非空壳）。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest
import sqlalchemy as sa

from packages.common.ids import new_id
from packages.research.entities import ResearchEvidenceSnapshot, ResearchWorkspace
from packages.research.execution.entities_trusted import (
    ResearchAnalysisPlanVersion,
    ResearchAnalysisRun,
)
from packages.research.timeline.entities import (
    CandidateExtractionJob,
    ResearchConclusionCandidate,
    ResearchTurn,
    ResearchTurnResult,
)
from packages.research.timeline.extraction_service import CandidateExtractionService
from packages.research.timeline.repository import TimelineRepository
from packages.research.timeline.run_finalizer import TimelineRunFinalizer


@dataclass(frozen=True)
class _SeededTurn:
    """最小时间线场景：workspace + snapshot + plan + turn + run 的 ID 集合。"""

    workspace_id: UUID
    snapshot_id: UUID
    plan_version_id: UUID
    turn_id: UUID
    run_id: UUID


async def _seed_minimal_turn(session_factory, user, *, run_status: str = "queued") -> _SeededTurn:
    """插入最小可运行场景（workspace/snapshot/plan/turn/run），返回 ID 集合。

    直接经 ORM 写入测试库；``test_user`` fixture 保证 department / app_user 存在。
    按 FK 依赖分层 flush，确保父表（workspace → snapshot/plan → turn → run）先落库。
    """
    owner_id: UUID = user.user_id
    dept_id: UUID = user.department_id
    async with session_factory() as session:
        async with session.begin():
            ws = ResearchWorkspace(
                id=new_id(),
                owner_user_id=owner_id,
                department_id=dept_id,
                name="analysis-lifecycle-test",
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
                question_text_snapshot="lifecycle test question",
                question_origin="manual",
                evidence_snapshot_id=snap.id,
                idempotency_key=f"lifecycle-{ws.id}",
            )
            session.add(turn)
            await session.flush()

            plan = ResearchAnalysisPlanVersion(
                id=new_id(),
                workspace_id=ws.id,
                version_number=1,
                dag_structure={"steps": []},
                status="confirmed",
                created_by=owner_id,
                turn_id=turn.id,
            )
            session.add(plan)
            await session.flush()

            run = ResearchAnalysisRun(
                id=new_id(),
                workspace_id=ws.id,
                plan_version_id=plan.id,
                snapshot_id=snap.id,
                run_number=1,
                status=run_status,
                image_digest="llm-only",
                created_by=owner_id,
                turn_id=turn.id,
                attempt_number=1,
            )
            session.add(run)
            await session.flush()

            return _SeededTurn(
                workspace_id=ws.id,
                snapshot_id=snap.id,
                plan_version_id=plan.id,
                turn_id=turn.id,
                run_id=run.id,
            )


async def _cleanup_research(session_factory, workspace_id: UUID) -> None:
    """删除工作空间（CASCADE 清理其下 turn/snapshot/run/result/extraction）。"""
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sa.text("DELETE FROM research_workspace WHERE id = :id"),
                {"id": workspace_id},
            )


@pytest.fixture
async def seeded_turn(async_session_factory, test_user):
    """提供已插入的最小场景，并在测试后清理（避免污染 test_user 清理路径）。"""
    seeded = await _seed_minimal_turn(async_session_factory, test_user, run_status="queued")
    try:
        yield seeded, async_session_factory, test_user
    finally:
        await _cleanup_research(async_session_factory, seeded.workspace_id)


async def _count(session, model) -> int:
    """统计指定表行数。"""
    result = await session.execute(sa.select(sa.func.count()).select_from(model))
    return int(result.scalar_one())


@pytest.mark.integration
async def test_finalize_writes_single_result_with_non_null_run_id(seeded_turn) -> None:
    """成功 Run 恰有一个 run_id 非空的 Result；重复 complete 幂等不重复。"""
    seeded, factory, user = seeded_turn
    finalizer = TimelineRunFinalizer(
        factory,
        department_id=user.department_id,
        actor_id=user.user_id,
    )

    # 第一次 complete（queued -> succeeded）
    outcome = await finalizer.complete(
        seeded.run_id,
        seeded.workspace_id,
        seeded.turn_id,
        "analysis text",
    )
    assert outcome["status"] == "succeeded"

    # 第二次 complete（幂等：Run 已是 succeeded，直接返回）
    outcome2 = await finalizer.complete(
        seeded.run_id,
        seeded.workspace_id,
        seeded.turn_id,
        "analysis text",
    )
    assert outcome2["status"] == "succeeded"

    async with factory() as session:
        rows = (
            (
                await session.execute(
                    sa.select(ResearchTurnResult).where(ResearchTurnResult.run_id == seeded.run_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, "每个 Run 必须恰好一个 Result"
        assert rows[0].run_id == seeded.run_id
        # run_id 列在 DB 层非空（nullable=False），实值非 None 由唯一约束保证
        assert rows[0].turn_id == seeded.turn_id


@pytest.mark.integration
async def test_run_id_unique_and_non_null_at_orm_level() -> None:
    """Result 与 ExtractionJob 的 run_id 在 ORM 层声明非空且唯一。"""
    result_col = ResearchTurnResult.__table__.c.run_id
    assert result_col.nullable is False
    assert result_col.unique is True

    job_col = CandidateExtractionJob.__table__.c.run_id
    assert job_col.nullable is False
    assert job_col.unique is True


@pytest.mark.integration
async def test_at_most_one_extraction_job_per_run(seeded_turn) -> None:
    """每个 Run 最多一个 Extraction Job：finalize 入队再重复入队仍只有一个。"""
    seeded, factory, user = seeded_turn
    finalizer = TimelineRunFinalizer(
        factory,
        department_id=user.department_id,
        actor_id=user.user_id,
    )
    await finalizer.complete(
        seeded.run_id,
        seeded.workspace_id,
        seeded.turn_id,
        "analysis text",
    )

    # finalize 已入队一个 extraction job；再次调用 enqueue_for_completed_run 应幂等
    async with factory() as session:
        async with session.begin():
            await CandidateExtractionService.enqueue_for_completed_run(session, seeded.run_id)
            await CandidateExtractionService.enqueue_for_completed_run(session, seeded.run_id)

    async with factory() as session:
        jobs = (
            (
                await session.execute(
                    sa.select(CandidateExtractionJob).where(
                        CandidateExtractionJob.run_id == seeded.run_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 1, "每个 Run 最多一个 Extraction Job"


@pytest.mark.integration
async def test_retry_does_not_duplicate_candidates(async_session_factory, test_user) -> None:
    """提取重试不产生重复候选：retry 只重置状态，execute CAS 幂等。"""
    seeded = await _seed_minimal_turn(async_session_factory, test_user, run_status="succeeded")
    try:

        class _FakeGateway:
            """返回 2 条候选的假模型网关。"""

            def __init__(self) -> None:
                self._candidates = [
                    {"statement": "候选结论 A", "scope": None},
                    {"statement": "候选结论 B", "scope": None},
                ]

            async def call(self, system_prompt: str, user_prompt: str) -> dict:
                return {"candidates": self._candidates}

        service = CandidateExtractionService(
            async_session_factory,
            model_gateway=_FakeGateway(),
        )

        # 入队 extraction job（Run 已 succeeded）
        async with async_session_factory() as session:
            async with session.begin():
                ref = await CandidateExtractionService.enqueue_for_completed_run(
                    session, seeded.run_id
                )
        extraction_id = ref.extraction_id

        # 模拟失败后重试
        async with async_session_factory() as session:
            async with session.begin():
                await TimelineRepository.update_extraction_status(
                    session, extraction_id, expected_status="queued", new_status="failed"
                )

        retry_ref = await service.retry(extraction_id)
        assert retry_ref.status == "queued"

        # retry 之后不应存在任何候选（retry 只改状态，不制造候选）
        async with async_session_factory() as session:
            cand_count = await _count(session, ResearchConclusionCandidate)
            assert cand_count == 0

        # 首次 execute 成功，插入 2 条候选
        done_ref = await service.execute(extraction_id)
        assert done_ref.status == "succeeded"

        async with async_session_factory() as session:
            first_cands = (
                (
                    await session.execute(
                        sa.select(ResearchConclusionCandidate).where(
                            ResearchConclusionCandidate.turn_id == seeded.turn_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(first_cands) == 2

        # 再次 execute（terminal succeeded）不重复插入
        again_ref = await service.execute(extraction_id)
        assert again_ref.status == "succeeded"

        async with async_session_factory() as session:
            final_cands = (
                (
                    await session.execute(
                        sa.select(ResearchConclusionCandidate).where(
                            ResearchConclusionCandidate.turn_id == seeded.turn_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(final_cands) == 2, "重复执行不得产生重复候选"
    finally:
        await _cleanup_research(async_session_factory, seeded.workspace_id)
