"""TimelineRunFinalizer: atomically complete a Run and write TurnResult.

Ensures: Run status CAS, TurnResult creation, Turn status transition,
and Candidate Extraction enqueue all happen in one transaction.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import ScopedSessionMixin
from packages.jobs.outbox import OutboxDispatcher
from packages.research.execution.entities_trusted import ResearchAnalysisRun
from packages.research.timeline.entities import ResearchTurn, ResearchTurnResult

logger = logging.getLogger("research.run_finalizer")


class TimelineRunFinalizer(ScopedSessionMixin):
    """Atomically finalize a Run: write Result + CAS Turn + enqueue Extraction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None = None,
    ) -> None:
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._rls_dept_id: UUID | None = None

    async def complete(
        self,
        run_id: UUID,
        workspace_id: UUID,
        turn_id: UUID,
        analysis_text: str,
        structured_output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically complete a Run.

        1. CAS Run status: queued/running -> succeeded (idempotent if done)
        2. Write immutable TurnResult (run_id non-null, unique)
        3. CAS Turn status: queued/running -> succeeded
        4. Enqueue Candidate Extraction via Outbox

        Returns dict with run_id, turn_id, status.
        """
        async with self._scoped_session() as session:
            # 1. CAS Run status
            run = await session.get(ResearchAnalysisRun, run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            if run.status == "succeeded":
                logger.info("Run %s already succeeded, idempotent skip", run_id)
                return {
                    "run_id": str(run_id),
                    "turn_id": str(turn_id),
                    "status": "succeeded",
                }
            if run.status not in ("queued", "running"):
                raise ValueError(
                    f"Run {run_id} in invalid state: {run.status}"
                )
            run.status = "succeeded"

            # 2. Write immutable TurnResult (idempotent: skip if exists)
            existing = await session.execute(
                sa.select(ResearchTurnResult).where(
                    ResearchTurnResult.run_id == run_id
                )
            )
            if existing.scalar_one_or_none() is None:
                result = ResearchTurnResult(
                    id=uuid.uuid4(),
                    turn_id=turn_id,
                    run_id=run_id,
                    result_kind="analysis",
                    summary=analysis_text[:500],
                    structured_output=structured_output
                    or {"analysis_markdown": analysis_text},
                )
                session.add(result)

            # 3. CAS Turn status
            turn = await session.get(ResearchTurn, turn_id)
            if turn and turn.status not in ("succeeded", "run_failed"):
                turn.status = "succeeded"

            # 4. Persist CandidateExtractionJob + enqueue via Outbox (same transaction).
            #    The Outbox aggregate_id MUST reference a persisted extraction job,
            #    otherwise the worker has nothing to claim when it consumes the event.
            from packages.research.timeline.repository import TimelineRepository

            extraction_job = await TimelineRepository.get_extraction_by_run(
                session, run_id
            )
            if extraction_job is None:
                extraction_job = await TimelineRepository.insert_extraction_job(
                    session,
                    workspace_id=workspace_id,
                    turn_id=turn_id,
                    run_id=run_id,
                )
            await OutboxDispatcher.enqueue(
                session,
                aggregate_type="research_candidate_extraction",
                aggregate_id=extraction_job.id,
                event_type="research.candidate_extraction.requested",
                payload={
                    "actor_id": str(self._actor_id) if self._actor_id else "",
                    "department_id": str(self._dept_id),
                    "workspace_id": str(workspace_id),
                },
            )

            return {
                "run_id": str(run_id),
                "turn_id": str(turn_id),
                "status": "succeeded",
            }

    async def fail(
        self,
        run_id: UUID,
        turn_id: UUID,
        error_message: str,
    ) -> dict[str, Any]:
        """Mark Run as failed and set Turn to run_failed."""
        async with self._scoped_session() as session:
            run = await session.get(ResearchAnalysisRun, run_id)
            if run and run.status not in ("succeeded", "failed"):
                run.status = "failed"
                run.error_summary = error_message[:500]
            turn = await session.get(ResearchTurn, turn_id)
            if turn and turn.status not in ("succeeded", "run_failed"):
                turn.status = "run_failed"
            return {
                "run_id": str(run_id),
                "turn_id": str(turn_id),
                "status": "failed",
            }
