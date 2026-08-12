"""Candidate extraction service: async Celery job for extracting conclusions.

Task 8: After a run succeeds, an independent Celery job extracts
candidate conclusions. The job survives page close, has heartbeat,
and can be retried. Frontend gets updates via SSE + polling fallback.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.errors import AppError
from packages.research.timeline.conclusion_repository import CandidateRepository
from packages.research.timeline.contracts import CandidateExtractionRef
from packages.research.timeline.repository import TimelineRepository
from packages.research.timeline.state_machine import ExtractionStateMachine

logger = logging.getLogger("research.extraction")

MAX_CANDIDATES = 20


class CandidateExtractionService:
    """Service for managing candidate extraction jobs.

    enqueue_for_completed_run: called by orchestrator in same transaction.
    execute: called by Celery worker.
    retry: called by user or reconciler.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        model_gateway: Any | None = None,
    ) -> None:
        self._factory = session_factory
        self._gateway = model_gateway

    @staticmethod
    async def enqueue_for_completed_run(
        session: AsyncSession,
        run_id: UUID,
    ) -> CandidateExtractionRef:
        """Create an extraction job for a completed run.

        Called within the orchestrator's completion transaction.
        The UNIQUE(run_id) constraint ensures exactly one job per run.

        Args:
            session: Active DB session (caller manages transaction).
            run_id: The completed run ID.

        Returns:
            CandidateExtractionRef for the created (or existing) job.
        """
        # Check if job already exists (idempotent)
        existing = await TimelineRepository.get_extraction_by_run(session, run_id)
        if existing is not None:
            return CandidateExtractionRef(
                extraction_id=existing.id,
                turn_id=existing.turn_id,
                run_id=run_id,
                status=existing.status,
            )

        # Load run to get workspace_id and turn_id
        from packages.research.execution.entities_trusted import (
            ResearchAnalysisRun,
        )

        run = await session.get(ResearchAnalysisRun, run_id)
        if run is None:
            raise AppError(
                code="not_found",
                message="Run not found",
                retryable=False,
                fields={"run_id": str(run_id)},
            )

        if run.status not in ("succeeded", "partially_succeeded"):
            raise AppError(
                code="state_conflict",
                message=f"Run status is '{run.status}', only succeeded can extract",
                retryable=True,
                fields={"run_id": str(run_id)},
            )

        if run.turn_id is None:
            raise AppError(
                code="state_conflict",
                message="Run has no turn_id (pre-timeline run)",
                retryable=True,
                fields={"run_id": str(run_id)},
            )

        job = await TimelineRepository.insert_extraction_job(
            session,
            workspace_id=run.workspace_id,
            turn_id=run.turn_id,
            run_id=run_id,
        )

        logger.info("enqueued extraction job %s for run %s", job.id, run_id)

        return CandidateExtractionRef(
            extraction_id=job.id,
            turn_id=run.turn_id,
            run_id=run_id,
            status="queued",
        )

    async def execute(self, extraction_id: UUID) -> CandidateExtractionRef:
        """Execute the extraction (call model, parse candidates).

        Flow:
          1. CAS: queued → running
          2. Load run result + context
          3. Call model with extraction prompt
          4. Parse structured output (0-20 candidates)
          5. Insert candidates
          6. CAS: running → succeeded
          7. Publish SSE events

        Args:
            extraction_id: Extraction job ID.

        Returns:
            Updated CandidateExtractionRef.
        """
        async with self._factory() as session:
            job = await TimelineRepository.get_extraction_job(session, extraction_id)
            if job is None:
                raise AppError(
                    code="not_found",
                    message="Extraction job not found",
                    retryable=False,
                    fields={"extraction_id": str(extraction_id)},
                )

            # Terminal states: return current
            if job.status in ("succeeded", "failed"):
                return CandidateExtractionRef(
                    extraction_id=extraction_id,
                    turn_id=job.turn_id,
                    run_id=job.run_id,
                    status=job.status,
                )

            # CAS: queued → running
            try:
                await TimelineRepository.update_extraction_status(
                    session,
                    extraction_id,
                    expected_status="queued",
                    new_status="running",
                )
            except AppError:
                return CandidateExtractionRef(
                    extraction_id=extraction_id,
                    turn_id=job.turn_id,
                    run_id=job.run_id,
                    status=job.status,
                )

            # Update heartbeat
            await TimelineRepository.update_heartbeat(session, extraction_id)

            # Load turn result
            result = await TimelineRepository.get_turn_result(session, job.turn_id)

            # Build extraction prompt
            from packages.research.timeline.prompts import (
                CANDIDATE_EXTRACTION_SYSTEM_PROMPT,
            )

            if result and result.summary:
                user_prompt = f"分析结果摘要:\n{result.summary}\n\n请提取候选结论。"
            else:
                user_prompt = "分析已完成，请提取候选结论。"

            # Call model (with gateway)
            candidates_data: list[dict[str, Any]] = []
            try:
                if self._gateway is not None:
                    raw = await self._gateway.call(
                        system_prompt=CANDIDATE_EXTRACTION_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                    )
                    if isinstance(raw, str):
                        data = json.loads(raw)
                    elif isinstance(raw, dict):
                        data = raw
                    else:
                        content = getattr(raw, "content", "{}")
                        data = json.loads(content) if isinstance(content, str) else {}

                    candidates_data = data.get("candidates", [])
                    if len(candidates_data) > MAX_CANDIDATES:
                        candidates_data = candidates_data[:MAX_CANDIDATES]
                else:
                    logger.warning(
                        "no gateway configured for extraction %s, returning 0 candidates",
                        extraction_id,
                    )

            except Exception as exc:
                logger.warning("extraction %s model call failed: %s", extraction_id, exc)
                await TimelineRepository.update_extraction_status(
                    session,
                    extraction_id,
                    expected_status="running",
                    new_status="failed",
                    error_code=type(exc).__name__,
                )
                await session.commit()
                return CandidateExtractionRef(
                    extraction_id=extraction_id,
                    turn_id=job.turn_id,
                    run_id=job.run_id,
                    status="failed",
                )

            # Insert candidates
            if candidates_data:
                await CandidateRepository.insert_candidates(
                    session,
                    extraction_id=extraction_id,
                    turn_id=job.turn_id,
                    candidates=candidates_data,
                )

            # Mark succeeded
            await TimelineRepository.update_extraction_status(
                session,
                extraction_id,
                expected_status="running",
                new_status="succeeded",
            )

            await session.commit()

            logger.info(
                "extraction %s succeeded with %d candidates",
                extraction_id,
                len(candidates_data),
            )

            return CandidateExtractionRef(
                extraction_id=extraction_id,
                turn_id=job.turn_id,
                run_id=job.run_id,
                status="succeeded",
            )

    async def retry(self, extraction_id: UUID) -> CandidateExtractionRef:
        """Retry a failed or task_lost extraction.

        Args:
            extraction_id: Extraction job ID.

        Returns:
            Updated CandidateExtractionRef.
        """
        async with self._factory() as session:
            job = await TimelineRepository.get_extraction_job(session, extraction_id)
            if job is None:
                raise AppError(
                    code="not_found",
                    message="Extraction job not found",
                    retryable=False,
                    fields={"extraction_id": str(extraction_id)},
                )

            if not ExtractionStateMachine.can_retry(job.status):
                raise AppError(
                    code="state_conflict",
                    message=f"Extraction in '{job.status}' cannot retry",
                    retryable=True,
                )

            await TimelineRepository.update_extraction_status(
                session,
                extraction_id,
                expected_status=job.status,
                new_status="queued",
                attempt=job.attempt + 1,
                error_code=None,
            )

            await session.commit()

            return CandidateExtractionRef(
                extraction_id=extraction_id,
                turn_id=job.turn_id,
                run_id=job.run_id,
                status="queued",
            )
