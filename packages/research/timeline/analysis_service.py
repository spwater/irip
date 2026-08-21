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
import uuid
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.timeline.access import (
    require_owned_turn,
    require_owned_workspace,
)
from packages.research.timeline.entities import (
    ResearchTurn,
    ResearchTurnResult,
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
            turn = await require_owned_turn(
                session, workspace_id, turn_id, actor_id
            )

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
                    message=(
                        "No confirmed plan for this turn; "
                        "call start_planning first"
                    ),
                    retryable=True,
                    fields={"turn_id": str(turn_id)},
                )

            # 4. Check no active run in workspace
            active_run = (
                await ResearchRepositoryTrusted.get_active_run_for_workspace(
                    session, workspace_id
                )
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
            run_number = await ResearchRepositoryTrusted.get_next_run_number(
                session, workspace_id
            )

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

    async def run_analysis(
        self,
        workspace_id: UUID,
        turn_id: UUID,
    ) -> dict[str, Any]:
        """Run the full analysis flow for a turn.

        Args:
            workspace_id: Workspace ID.
            turn_id: Turn ID to analyze.

        Returns:
            Dict with turn_id, status, and result_summary.

        Raises:
            AppError: not_found if turn or AI config missing.
            AppError: analysis_failed if analysis fails.
        """
        # 1. Load turn (scoped session with RLS)
        async with self._scoped_session() as session:
            await require_owned_workspace(session, workspace_id, self._actor_id)
            turn = await session.get(ResearchTurn, turn_id)
            if turn is None or turn.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="Turn not found",
                    retryable=False,
                )
            if turn.status not in ("question_draft", "run_failed", "succeeded"):
                raise AppError(
                    code="state_conflict",
                    message=f"Turn in status '{turn.status}' cannot be analyzed",
                    retryable=True,
                )
            turn.status = "planning"
            await session.commit()
            snapshot_id = turn.evidence_snapshot_id

        # 2. Load AI config (scoped session)
        ai_config = await self._load_ai_config()
        if not ai_config or not ai_config.get("base_url") or not ai_config.get("api_key"):
            raise AppError(
                code="ai_config_missing",
                message="AI not configured",
                retryable=False,
            )

        # 3. Build AI provider + model gateway + PlanService
        from packages.ai.openai_compatible import OpenAICompatibleProvider
        from packages.research.execution.models_trusted import ModelConfig, TaskType
        from packages.research.planning.context_router import ContextRouter
        from packages.research.planning.model_gateway import ModelGateway
        from packages.research.planning.plan_core import PlanService

        research_model_name = ai_config.get("research_model_name") or ai_config.get(
            "model_name", ""
        )
        thinking = ai_config.get("research_thinking_enabled", False)
        ai_provider = OpenAICompatibleProvider(
            api_key=ai_config["api_key"],
            base_url=ai_config["base_url"],
            model=research_model_name,
            thinking_enabled=thinking,
        )

        model_registry = {
            task: ModelConfig(
                provider="openai_compatible",
                model=research_model_name,
                version="custom",
                context_limit=128000,
            )
            for task in TaskType
        }
        model_gateway = ModelGateway(
            provider=ai_provider,
            audit_recorder=None,
            model_registry=model_registry,
        )
        context_router = ContextRouter()

        # Use the injected session_factory with real actor identity
        from packages.facts.query_service import FactQueryService
        from packages.research.lineage.core_adapter import CoreFactProviderImpl

        plan_service = PlanService(
            session_factory=self._factory,
            department_id=self._dept_id,
            actor_id=self._actor_id,
            fact_provider=CoreFactProviderImpl(
                query_service=FactQueryService(
                    session_factory=self._factory,
                    department_id=self._dept_id,
                    actor_id=self._actor_id,
                    s3_repo=None,
                )
            ),
            model_gateway=model_gateway,
            context_router=context_router,
        )

        analysis_text = ""

        # 4. Generate plan -> auto-confirm -> analyze
        try:
            plan = await plan_service.generate_plan(
                workspace_id=workspace_id,
                snapshot_id=snapshot_id,
            )

            plan = await plan_service.confirm_plan(
                workspace_id=workspace_id,
                plan_id=plan.plan_id,
            )

            analysis_result = await plan_service.analyze_data(
                workspace_id=workspace_id,
                plan_id=plan.plan_id,
                snapshot_id=snapshot_id,
                turn_id=turn_id,
            )

            # 5. Persist result
            if isinstance(analysis_result, dict):
                analysis_text = analysis_result.get("analysis_result", "")
            elif isinstance(analysis_result, str):
                analysis_text = analysis_result
            else:
                analysis_text = str(analysis_result)

            async with self._scoped_session() as session:
                turn = await session.get(ResearchTurn, turn_id)
                if turn:
                    turn.status = "succeeded"

                result_row = await session.execute(
                    sa.select(ResearchTurnResult).where(
                        ResearchTurnResult.turn_id == turn_id
                    )
                )
                tr = result_row.scalar_one_or_none()
                if tr is None:
                    tr = ResearchTurnResult(
                        id=uuid.uuid4(),
                        turn_id=turn_id,
                        result_kind="analysis",
                    )
                    session.add(tr)
                tr.summary = analysis_text[:500]
                tr.structured_output = {
                    "analysis_markdown": analysis_text,
                }
                await session.commit()

        except Exception as e:
            logger.exception(
                "Analysis failed for turn %s (workspace %s)",
                turn_id,
                workspace_id,
            )
            async with self._scoped_session() as session:
                turn = await session.get(ResearchTurn, turn_id)
                if turn:
                    turn.status = "run_failed"
                    await session.commit()
            raise AppError(
                code="analysis_failed",
                message=(
                    e.message
                    if isinstance(e, AppError) and e.message
                    else f"Analysis failed: {e}"
                ),
                retryable=True,
            ) from e

        # 6. Auto-trigger followup recommendations
        try:
            from packages.jobs.outbox import OutboxDispatcher
            from packages.research.timeline.contracts import (
                RECOMMENDATION_OUTPUT_SCHEMA_VERSION,
                RECOMMENDATION_PROMPT_VERSION,
            )
            from packages.research.timeline.repository import TimelineRepository

            followup_key = f"followup:{turn_id}"
            async with self._scoped_session() as session:
                existing = await TimelineRepository.get_batch_by_idempotency(
                    session, workspace_id, followup_key
                )
                if existing is None:
                    batch = await TimelineRepository.insert_batch(
                        session,
                        workspace_id=workspace_id,
                        snapshot_id=snapshot_id,
                        mode="followup",
                        prompt_template_version=RECOMMENDATION_PROMPT_VERSION,
                        output_schema_version=RECOMMENDATION_OUTPUT_SCHEMA_VERSION,
                        idempotency_key=followup_key,
                    )

                    await OutboxDispatcher.enqueue(
                        session,
                        aggregate_type="research_recommendation_batch",
                        aggregate_id=batch.id,
                        event_type="research.recommendation.requested",
                        payload={
                            "batch_id": str(batch.id),
                            "mode": "followup",
                        },
                    )
                    await session.commit()
                    logger.info(
                        "enqueued followup recommendation batch %s", batch.id
                    )
        except Exception as exc:
            logger.warning(
                "Failed to enqueue followup recommendations: %s", exc
            )

        return {
            "turn_id": str(turn_id),
            "status": "succeeded",
            "result_summary": analysis_text[:500] if analysis_text else "",
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
