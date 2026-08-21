"""Analysis service: orchestrates the full analysis flow for a research turn.

Identity-aware: uses department_id and actor_id from the composition root
instead of reading IRIP_DATABASE_URL or admin@irip.local.

Flow:
  1. Load turn + snapshot (scoped session with RLS)
  2. Build AI provider + model gateway
  3. Call PlanService (generate_plan -> confirm_plan -> analyze_data)
  4. Persist result to ResearchTurnResult
  5. Auto-trigger followup recommendations
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.timeline.access import (
    require_owned_turn,
)

logger = logging.getLogger("research.analysis_service")


class AnalysisService(ScopedSessionMixin):
    """Orchestrates the full analysis flow for a research turn.

    Identity-aware: constructed with (session_factory, department_id, actor_id).
    Does NOT read IRIP_DATABASE_URL, IRIP_ALEMBIC_DATABASE_URL, or admin@irip.local.
    """

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

    def _require_actor(self) -> UUID:
        """Return the actor_id, raising forbidden if unauthenticated."""
        if self._actor_id is None:
            raise AppError(
                code="forbidden",
                message="Authenticated user required to submit run",
                retryable=False,
            )
        return self._actor_id

    async def submit_run(
        self,
        workspace_id: UUID,
        turn_id: UUID,
    ) -> dict[str, Any]:
        """Submit a Run for async execution via Outbox.

        Creates a ``ResearchAnalysisRun`` record (status=queued) and
        enqueues a ``research.run.requested`` Outbox event in the **same
        transaction**.  Returns a 202-compatible dict with run_id,
        turn_id, and status=queued.

        Does NOT execute the analysis synchronously — the Worker picks
        up the Outbox event and calls ``research.run.execute``.

        Args:
            workspace_id: Workspace ID.
            turn_id: Turn ID to analyze.

        Returns:
            Dict with run_id, turn_id, status=queued.

        Raises:
            AppError: forbidden if no authenticated actor.
            AppError: not_found if the turn does not exist.
            AppError: state_conflict if the turn cannot submit a run
                (no confirmed plan or wrong status).
            AppError: analysis_busy if the workspace already has an
                active run.
        """
        from packages.common.ids import new_id
        from packages.jobs.outbox import OutboxDispatcher
        from packages.research.execution.entities_trusted import (
            ResearchAnalysisPlanVersion,
            ResearchAnalysisRun,
        )
        from packages.research.execution.repository_trusted import (
            ResearchRepositoryTrusted,
        )
        from packages.research.timeline.state_machine import TurnStateMachine

        actor_id = self._require_actor()

        async with self._scoped_session() as session:
            # 1. Load + verify turn ownership
            turn = await require_owned_turn(session, workspace_id, turn_id, actor_id)

            # 2. Check turn is in a runnable state
            if not TurnStateMachine.can_run(turn.status):
                raise AppError(
                    code="state_conflict",
                    message=(
                        f"Turn in status '{turn.status}' cannot submit "
                        f"run; call start_planning first"
                    ),
                    retryable=True,
                    fields={"turn_id": str(turn_id), "status": turn.status},
                )

            # 3. Find the confirmed plan for this turn
            plan_result = await session.execute(
                sa.select(ResearchAnalysisPlanVersion)
                .where(
                    ResearchAnalysisPlanVersion.turn_id == turn_id,
                    ResearchAnalysisPlanVersion.status == "confirmed",
                )
                .order_by(ResearchAnalysisPlanVersion.version_number.desc())
                .limit(1)
            )
            plan = plan_result.scalar_one_or_none()
            if plan is None:
                raise AppError(
                    code="state_conflict",
                    message=("No confirmed plan for this turn; call start_planning first"),
                    retryable=True,
                    fields={"turn_id": str(turn_id)},
                )

            # 4. Check no active run in workspace
            active_run = await ResearchRepositoryTrusted.get_active_run_for_workspace(
                session, workspace_id
            )
            if active_run is not None:
                raise AppError(
                    code="analysis_busy",
                    message="Workspace has an active run",
                    retryable=True,
                    fields={
                        "workspace_id": str(workspace_id),
                        "active_run_id": str(active_run.id),
                    },
                )

            # 5. Compute run_number (workspace-level) + attempt_number
            run_number = await ResearchRepositoryTrusted.get_next_run_number(session, workspace_id)

            attempt_result = await session.execute(
                sa.select(sa.func.count())
                .select_from(ResearchAnalysisRun)
                .where(ResearchAnalysisRun.turn_id == turn_id)
            )
            attempt_number = attempt_result.scalar_one() + 1

            # 6. Create Run record
            run_id = new_id()
            run = ResearchAnalysisRun(
                id=run_id,
                workspace_id=workspace_id,
                plan_version_id=plan.id,
                snapshot_id=turn.evidence_snapshot_id,
                run_number=run_number,
                status="queued",
                image_digest="llm-only",
                created_by=actor_id,
                turn_id=turn_id,
                attempt_number=attempt_number,
            )
            session.add(run)
            await session.flush()

            # 7. Enqueue async execution via Outbox (same transaction)
            await OutboxDispatcher.enqueue(
                session,
                aggregate_type="research_analysis_run",
                aggregate_id=run_id,
                event_type="research.run.requested",
                payload={
                    "actor_id": str(actor_id),
                    "department_id": str(self._dept_id),
                    "workspace_id": str(workspace_id),
                },
            )

            # 8. Transition turn status to queued
            turn.status = "queued"

            logger.info(
                "submitted run %s for turn %s (attempt %d, run_number %d)",
                run_id,
                turn_id,
                attempt_number,
                run_number,
            )

            return {
                "run_id": str(run_id),
                "turn_id": str(turn_id),
                "status": "queued",
            }

    async def _load_ai_config(self) -> dict[str, Any] | None:
        """Load the active AI configuration from the database (scoped session)."""
        async with self._scoped_session() as session:
            result = await session.execute(
                sa.text(
                    "SELECT base_url, api_key, model_name, "
                    "research_model_name, research_thinking_enabled "
                    "FROM ai_config WHERE enabled = true "
                    "ORDER BY updated_at DESC LIMIT 1"
                )
            )
            row = result.first()
            if row is None:
                return None
            from packages.common.crypto import EnvelopeCrypto

            crypto = EnvelopeCrypto.from_env()
            decrypted_key = crypto.decrypt(row[1])
            return {
                "base_url": row[0],
                "api_key": decrypted_key,
                "model_name": row[2],
                "research_model_name": row[3],
                "research_thinking_enabled": row[4],
            }
