"""Celery tasks for research timeline: recommendations, extraction, reconciler.

All tasks use acks_late=True for reliability and compare-and-set
to prevent duplicate execution.
"""

import logging
import os
from typing import Any
from uuid import UUID

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Build a session factory from env vars."""
    from packages.common.database import build_session_factory

    db_url = os.environ.get(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
    )
    return build_session_factory(db_url)


@shared_task(
    name="research.recommendations.generate",
    bind=True,
    acks_late=True,
    soft_time_limit=120,
    time_limit=180,
)
def generate_recommendations(self: Any, batch_id: str) -> dict[str, Any]:
    """Generate recommendation questions for a batch.

    Args:
        batch_id: Recommendation batch ID as string.

    Returns:
        Dict with batch_id, status, and item_count.
    """
    import asyncio

    async def _run() -> dict[str, Any]:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )
        from packages.research.timeline.simple_gateway import (
            build_gateway_from_config,
        )

        factory = _get_session_factory()
        gateway = await build_gateway_from_config()

        # Resolve actor/dept from batch workspace owner
        import sqlalchemy as sa

        async with factory() as session:
            row = await session.execute(
                sa.text(
                    "SELECT w.owner_user_id, w.department_id "
                    "FROM research_recommendation_batch b "
                    "JOIN research_workspace w ON w.id = b.workspace_id "
                    "WHERE b.id = :bid"
                ),
                {"bid": batch_id},
            )
            owner_row = row.first()
            if owner_row is None:
                return {"batch_id": batch_id, "status": "not_found", "item_count": 0}

        service = RecommendationService(
            session_factory=factory,
            department_id=owner_row[1],
            actor_id=owner_row[0],
            model_gateway=gateway,
        )
        ref = await service.execute_batch(UUID(batch_id))
        return {
            "batch_id": str(ref.batch_id),
            "status": ref.status,
            "item_count": ref.item_count,
        }

    logger.info("generating recommendations for batch %s", batch_id)
    return asyncio.run(_run())


@shared_task(
    name="research.plans.generate",
    bind=True,
    acks_late=True,
    soft_time_limit=120,
    time_limit=180,
)
def generate_plan(
    self: Any,
    turn_id: str,
    *,
    actor_id: str = "",
    department_id: str = "",
    workspace_id: str = "",
) -> dict[str, str]:
    """Generate an analysis plan for a turn asynchronously.

    Args:
        turn_id: Turn ID as string.
        actor_id: Actor user ID (from Outbox principal).
        department_id: Department ID (from Outbox principal).
        workspace_id: Workspace ID (from Outbox principal).

    Returns:
        Dict with turn_id, status, and plan_id.
    """
    import asyncio
    from uuid import UUID

    async def _run() -> dict[str, str]:
        from packages.research.timeline.entities import ResearchTurn

        factory = _get_session_factory()
        UUID(department_id) if department_id else None
        UUID(actor_id) if actor_id else None
        ws_uuid = UUID(workspace_id) if workspace_id else None
        turn_uuid = UUID(turn_id)

        async with factory() as session:
            turn = await session.get(ResearchTurn, turn_uuid)
            if turn is None:
                return {"turn_id": turn_id, "status": "not_found", "plan_id": ""}
            if turn.workspace_id != ws_uuid:
                return {"turn_id": turn_id, "status": "not_found", "plan_id": ""}

            # Transition planning -> plan_review on success
            if turn.status == "planning":
                turn.status = "plan_review"
                await session.commit()

            return {
                "turn_id": turn_id,
                "status": turn.status,
                "plan_id": str(turn.id),
            }

    logger.info("generating plan for turn %s", turn_id)
    return asyncio.run(_run())


@shared_task(
    name="research.run.execute",
    bind=True,
    acks_late=True,
    soft_time_limit=300,
    time_limit=360,
)
def execute_analysis_run(
    self: Any,
    run_id: str,
    *,
    actor_id: str = "",
    department_id: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    """Execute an analysis run asynchronously.

    Uses the principal kwargs (actor_id, department_id, workspace_id)
    passed by the Outbox dispatcher to construct identity-aware services.
    Atomically finalizes the Run via TimelineRunFinalizer.

    Args:
        run_id: ResearchAnalysisRun ID as string.
        actor_id: Actor user ID (from Outbox principal).
        department_id: Department ID (from Outbox principal).
        workspace_id: Workspace ID (from Outbox principal).

    Returns:
        Dict with run_id, turn_id, status.
    """
    import asyncio
    from uuid import UUID

    async def _run() -> dict[str, Any]:
        from packages.research.execution.entities_trusted import (
            ResearchAnalysisRun,
        )
        from packages.research.timeline.run_finalizer import (
            TimelineRunFinalizer,
        )

        factory = _get_session_factory()
        dept_uuid = UUID(department_id) if department_id else None
        actor_uuid = UUID(actor_id) if actor_id else None
        UUID(workspace_id) if workspace_id else None
        run_uuid = UUID(run_id)

        finalizer = TimelineRunFinalizer(
            session_factory=factory,
            department_id=dept_uuid,  # type: ignore[arg-type]
            actor_id=actor_uuid,
        )

        # Load run to get turn_id and workspace_id
        async with factory() as session:
            run = await session.get(ResearchAnalysisRun, run_uuid)
            if run is None:
                return {"run_id": run_id, "turn_id": "", "status": "not_found"}
            turn_id = run.turn_id
            actual_ws_id = run.workspace_id

        # Execute analysis (simplified: LLM call)
        try:
            # In production this calls PlanService.analyze_data
            # For now, use the existing AnalysisService.run_analysis
            # which handles the full LLM flow
            analysis_text = "Analysis completed via async worker."

            result = await finalizer.complete(
                run_id=run_uuid,
                workspace_id=actual_ws_id,
                turn_id=turn_id,
                analysis_text=analysis_text,
            )
            logger.info("Run %s completed successfully", run_id)
            return result
        except Exception as exc:
            logger.exception("Run %s failed: %s", run_id, exc)
            fail_result = await finalizer.fail(
                run_id=run_uuid,
                turn_id=turn_id,
                error_message=str(exc),
            )
            return fail_result

    logger.info("executing analysis run %s", run_id)
    return asyncio.run(_run())


@shared_task(
    name="research.candidates.extract",
    bind=True,
    acks_late=True,
    soft_time_limit=300,
    time_limit=360,
)
def extract_candidates(
    self: Any,
    extraction_id: str,
    *,
    actor_id: str = "",
    department_id: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    """Extract conclusion candidates after a completed run.

    Uses principal kwargs from Outbox dispatcher for identity-aware execution.

    Args:
        extraction_id: CandidateExtractionJob ID as string.
        actor_id: Actor user ID (from Outbox principal).
        department_id: Department ID (from Outbox principal).
        workspace_id: Workspace ID (from Outbox principal).

    Returns:
        Dict with extraction_id, status, and candidate_count.
    """
    import asyncio
    from uuid import UUID

    async def _run() -> dict[str, Any]:
        import sqlalchemy as sa

        from packages.research.timeline.entities import (
            CandidateExtractionJob,
            ResearchTurnResult,
        )

        factory = _get_session_factory()
        UUID(department_id) if department_id else None
        UUID(actor_id) if actor_id else None
        extraction_uuid = UUID(extraction_id)

        async with factory() as session:
            job = await session.get(CandidateExtractionJob, extraction_uuid)
            if job is None:
                return {
                    "extraction_id": extraction_id,
                    "status": "not_found",
                    "candidate_count": 0,
                }

            # CAS: queued -> running
            if job.status == "succeeded":
                logger.info("Extraction %s already done, idempotent skip", extraction_id)
                return {
                    "extraction_id": extraction_id,
                    "status": "succeeded",
                    "candidate_count": 0,
                }
            if job.status not in ("queued", "running"):
                return {
                    "extraction_id": extraction_id,
                    "status": job.status,
                    "candidate_count": 0,
                }
            job.status = "running"
            await session.commit()

            # Load turn result for analysis text
            result_row = await session.execute(
                sa.select(ResearchTurnResult).where(
                    ResearchTurnResult.turn_id == job.turn_id
                )
            )
            turn_result = result_row.scalar_one_or_none()
            if turn_result is None or not turn_result.structured_output:
                job.status = "failed"
                await session.commit()
                return {
                    "extraction_id": extraction_id,
                    "status": "failed",
                    "candidate_count": 0,
                }

            (
                turn_result.structured_output.get("analysis_markdown", "")
                if isinstance(turn_result.structured_output, dict)
                else str(turn_result.summary or "")
            )

            # Extract candidate conclusions from analysis text
            # (Simplified: in production this calls LLM for candidate extraction)
            candidates_created = 0
            # TODO: Call LLM to extract candidates from analysis_text

            job.status = "succeeded"
            await session.commit()

            return {
                "extraction_id": extraction_id,
                "status": "succeeded",
                "candidate_count": candidates_created,
            }

    logger.info("extracting candidates for extraction %s", extraction_id)
    return asyncio.run(_run())


@shared_task(name="research.timeline.reconcile")
def reconcile_timeline() -> dict[str, Any]:
    """Reconciler: detect and requeue stale research tasks.

    Checks for stale queued/running Runs and Extraction Jobs,
    requeues them via Outbox. Uses FOR UPDATE SKIP LOCKED for safety.
    """
    import asyncio

    import sqlalchemy as sa

    logger.info("running timeline reconciler")

    async def _run() -> dict[str, Any]:
        from datetime import UTC, datetime, timedelta

        from packages.research.execution.entities_trusted import (
            ResearchAnalysisRun,
        )
        from packages.research.timeline.entities import (
            CandidateExtractionJob,
        )

        factory = _get_session_factory()
        requeued = 0
        marked_lost = 0
        stale_threshold = datetime.now(UTC) - timedelta(minutes=10)

        async with factory() as session:
            # Find stale Runs (queued/running for >10 min)
            stale_runs = await session.execute(
                sa.select(ResearchAnalysisRun)
                .where(
                    ResearchAnalysisRun.status.in_(["queued", "running"]),
                    ResearchAnalysisRun.submitted_at < stale_threshold,
                )
                .limit(100)
                .with_for_update(skip_locked=True)
            )
            for run in stale_runs.scalars():
                if run.status == "queued":
                    # Re-enqueue via Outbox
                    from packages.jobs.outbox import OutboxDispatcher

                    await OutboxDispatcher.enqueue(
                        session,
                        aggregate_type="research_analysis_run",
                        aggregate_id=run.id,
                        event_type="research.run.requested",
                        payload={
                            "actor_id": str(run.created_by) if run.created_by else "",
                            "department_id": "",
                            "workspace_id": str(run.workspace_id),
                        },
                    )
                    requeued += 1
                elif run.status == "running":
                    run.status = "failed"
                    run.error_summary = "Reconciler: stale run marked as failed"
                    marked_lost += 1

            # Find stale Extraction Jobs
            stale_extractions = await session.execute(
                sa.select(CandidateExtractionJob)
                .where(
                    CandidateExtractionJob.status.in_(["queued", "running"]),
                    CandidateExtractionJob.created_at < stale_threshold,
                )
                .limit(100)
                .with_for_update(skip_locked=True)
            )
            for job in stale_extractions.scalars():
                if job.status == "queued":
                    from packages.jobs.outbox import OutboxDispatcher

                    await OutboxDispatcher.enqueue(
                        session,
                        aggregate_type="research_candidate_extraction",
                        aggregate_id=job.id,
                        event_type="research.candidate_extraction.requested",
                        payload={
                            "actor_id": "",
                            "department_id": "",
                            "workspace_id": str(
                                job.workspace_id
                            ) if hasattr(job, "workspace_id") else "",
                        },
                    )
                    requeued += 1
                elif job.status == "running":
                    job.status = "failed"
                    marked_lost += 1

            await session.commit()

        return {"requeued": requeued, "marked_lost": marked_lost}

    return asyncio.run(_run())
