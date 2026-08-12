"""Turn-aware Run adapter: binds runs to turns with attempt numbering.

Task 7: Runs are now scoped to a Turn with attempt numbering.
The active-run uniqueness constraint is maintained at the workspace level.
"""

from __future__ import annotations

import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.errors import AppError
from packages.research.execution.entities_trusted import (
    ResearchAnalysisPlanVersion,
    ResearchAnalysisRun,
)
from packages.research.timeline.repository import TimelineRepository
from packages.research.timeline.state_machine import TurnStateMachine

logger = logging.getLogger("research.turn_run_adapter")

DEFAULT_IMAGE_DIGEST = "sha256:research-default"


async def submit_run_for_turn(
    session: AsyncSession,
    workspace_id: UUID,
    turn_id: UUID,
    plan_version_id: UUID,
    idempotency_key: str,
) -> tuple[UUID, int]:
    """Submit a run for a confirmed turn plan.

    Returns (run_id, attempt_number). Raises AppError on conflict.
    """
    turn = await TimelineRepository.get_turn(session, turn_id)
    if turn is None or turn.workspace_id != workspace_id:
        raise AppError(
            code="not_found",
            message="Turn not found",
            retryable=False,
            fields={"turn_id": str(turn_id)},
        )

    if not TurnStateMachine.can_run(turn.status):
        raise AppError(
            code="state_conflict",
            message=f"Turn in '{turn.status}' cannot submit run",
            retryable=True,
            fields={"turn_id": str(turn_id)},
        )

    # Check no active run in workspace
    active = await TimelineRepository.get_active_run_status(session, workspace_id)
    if active is not None:
        raise AppError(
            code="analysis_busy",
            message="Workspace has an active run",
            retryable=True,
            fields={"workspace_id": str(workspace_id)},
        )

    # Verify plan belongs to turn and is confirmed
    plan = await session.get(ResearchAnalysisPlanVersion, plan_version_id)
    if plan is None or plan.turn_id != turn_id:
        raise AppError(
            code="not_found",
            message="Plan not found for this turn",
            retryable=False,
            fields={"plan_id": str(plan_version_id)},
        )
    if plan.status != "confirmed":
        raise AppError(
            code="state_conflict",
            message=f"Plan is '{plan.status}', only confirmed can run",
            retryable=True,
        )

    # Determine attempt number
    existing_runs = await session.execute(
        sa.select(sa.func.count())
        .select_from(ResearchAnalysisRun)
        .where(ResearchAnalysisRun.turn_id == turn_id)
    )
    attempt = existing_runs.scalar_one() + 1

    # Get run_number (workspace-level)
    from packages.research.execution.repository_trusted import (
        ResearchRepositoryTrusted,
    )

    run_number = await ResearchRepositoryTrusted.get_next_run_number(session, workspace_id)

    # Insert run
    from packages.common.ids import new_id

    run = ResearchAnalysisRun(
        id=new_id(),
        workspace_id=workspace_id,
        plan_version_id=plan_version_id,
        snapshot_id=turn.evidence_snapshot_id,
        run_number=run_number,
        status="queued",
        image_digest=DEFAULT_IMAGE_DIGEST,
        created_by=turn.created_by if hasattr(turn, "created_by") else None,
        turn_id=turn_id,
        attempt_number=attempt,
    )
    session.add(run)
    await session.flush()

    # Transition turn to queued
    await TimelineRepository.update_turn_status(
        session,
        turn_id,
        expected_status="plan_confirmed" if turn.status == "plan_confirmed" else "run_failed",
        new_status="queued",
    )

    logger.info("submitted run %s for turn %s attempt %d", run.id, turn_id, attempt)
    return run.id, attempt


async def complete_run_for_turn(
    session: AsyncSession,
    run_id: UUID,
    run_status: str,
) -> None:
    """Complete a run and transition the turn."""
    run = await session.get(ResearchAnalysisRun, run_id)
    if run is None:
        return

    turn_id = run.turn_id
    if turn_id is None:
        return

    if run_status in ("succeeded", "partially_succeeded"):
        # Write TurnResult
        await TimelineRepository.insert_turn_result(
            session,
            turn_id=turn_id,
            run_id=run_id,
            result_kind="partial" if run_status == "partially_succeeded" else "analysis",
            summary=getattr(run, "error_summary", None),
            structured_output=getattr(run, "coverage_summary", None),
        )

        # Create CandidateExtractionJob
        await TimelineRepository.insert_extraction_job(
            session,
            workspace_id=run.workspace_id,
            turn_id=turn_id,
            run_id=run_id,
        )

        # Transition turn
        await TimelineRepository.update_turn_status(
            session, turn_id, expected_status="running", new_status="succeeded"
        )

    elif run_status == "failed":
        await TimelineRepository.update_turn_status(
            session, turn_id, expected_status="running", new_status="run_failed"
        )

    elif run_status == "cancelled":
        await TimelineRepository.update_turn_status(
            session, turn_id, expected_status="running", new_status="cancelled"
        )
