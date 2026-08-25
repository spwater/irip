"""Celery tasks for research timeline: recommendations, extraction, reconciler.

All tasks use acks_late=True for reliability and compare-and-set
to prevent duplicate execution.
"""

import logging
from typing import Any
from uuid import UUID

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Build a session factory from env vars."""
    from packages.common.database import build_session_factory, get_database_url

    db_url = get_database_url("")
    return build_session_factory(db_url)


def _scoped_session(
    factory: async_sessionmaker[AsyncSession],
    *,
    actor_id: UUID | None,
    department_id: UUID | None,
) -> Any:
    """Build a GUC-scoped session for RLS-protected research tables.

    The research tables (research_workspace / research_turn /
    research_analysis_run / research_candidate_extraction_job …) are all
    ``FORCE ROW LEVEL SECURITY``; a bare session leaves ``app.current_user_id``
    and ``app.current_department_id`` as empty strings (fail-closed). This
    helper delegates to ``packages.common.database.scoped_session`` so every
    worker DB access carries the actor/department identity resolved from the
    Outbox principal, and commits the transaction on normal exit.
    """
    from packages.common.database import scoped_session

    return scoped_session(factory, department_id, actor_id)


def _get_research_scenario_config() -> Any:
    """Get the research scenario config from YAML (sync read).

    Returns:
        ScenarioConfig for the "research" scenario.

    Raises:
        KeyError, FileNotFoundError, ValueError if config unavailable.
    """
    from packages.ai.yaml_config import get_scenario_config

    return get_scenario_config("research")


async def _build_plan_service(
    factory: async_sessionmaker[AsyncSession],
    dept_uuid: UUID | None,
    actor_uuid: UUID | None,
) -> Any | None:
    """Build a PlanService wired to the active AI config and fact provider.

    Returns None when no active AI config exists.  This mirrors the setup in
    ``execute_analysis_run`` / ``extract_candidates`` so plan generation uses
    the same identity-aware ModelGateway + FactProvider stack.
    """
    try:
        config = _get_research_scenario_config()
    except (KeyError, FileNotFoundError, ValueError):
        return None

    from packages.ai.openai_compatible import OpenAICompatibleProvider
    from packages.research.execution.models_trusted import (
        ModelConfig,
        TaskType,
    )
    from packages.research.planning.context_router import ContextRouter
    from packages.research.planning.model_gateway import ModelGateway
    from packages.research.planning.plan_core import PlanService

    research_model_name = config.model
    thinking = config.thinking_enabled
    ai_provider = OpenAICompatibleProvider(
        api_key=config.api_key,
        base_url=config.base_url,
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

    from packages.facts.query_service import FactQueryService
    from packages.research.lineage.core_adapter import CoreFactProviderImpl

    try:
        from apps.api.main import _build_s3_repo
        s3_repo = _build_s3_repo()
    except Exception:
        s3_repo = None

    fact_provider = CoreFactProviderImpl(
        query_service=FactQueryService(
            session_factory=factory,
            department_id=dept_uuid,  # type: ignore[arg-type]
            actor_id=actor_uuid,
            s3_repo=s3_repo,
        )
    )

    return PlanService(
        session_factory=factory,
        department_id=dept_uuid,  # type: ignore[arg-type]
        actor_id=actor_uuid,
        model_gateway=model_gateway,
        context_router=ContextRouter(),
        fact_provider=fact_provider,
    )


@shared_task(
    name="research.recommendations.generate",
    bind=True,
    acks_late=True,
    soft_time_limit=300,
    time_limit=360,
)
def generate_recommendations(
    self: Any,
    batch_id: str,
    *,
    actor_id: str = "",
    department_id: str = "",
    workspace_id: str = "",
) -> dict[str, Any]:
    """Generate recommendation questions for a batch.

    Uses the principal kwargs (actor_id, department_id, workspace_id)
    passed by the Outbox dispatcher for identity-aware execution.

    Args:
        batch_id: Recommendation batch ID as string.
        actor_id: Actor user ID (from Outbox principal).
        department_id: Department ID (from Outbox principal).
        workspace_id: Workspace ID (from Outbox principal).

    Returns:
        Dict with batch_id, status, and item_count.
    """
    import asyncio

    actor_uuid: UUID | None = UUID(actor_id) if actor_id else None
    dept_uuid: UUID | None = UUID(department_id) if department_id else None

    async def _run() -> dict[str, Any]:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )
        from packages.research.timeline.simple_gateway import (
            build_gateway_from_config,
        )

        factory = _get_session_factory()
        gateway = await build_gateway_from_config()

        # Prefer the principal kwargs from the Outbox dispatcher; fall back to
        # resolving the batch workspace owner for direct invocations that do
        # not carry a validated principal.
        resolved_actor = actor_uuid
        resolved_dept = dept_uuid
        if resolved_actor is None or resolved_dept is None:
            import sqlalchemy as sa

            # Use admin URL to bypass RLS for owner resolution (chicken-and-egg:
            # we need the owner to set RLS context, but RLS blocks the query
            # without the owner's identity).
            from packages.common.database import get_database_admin_url

            admin_url = get_database_admin_url("")

            if admin_url:
                admin_engine = sa.create_async_engine(admin_url)
                async with admin_engine.connect() as conn:
                    row = await conn.execute(
                        sa.text(
                            "SELECT w.owner_user_id, w.department_id "
                            "FROM research_recommendation_batch b "
                            "JOIN research_workspace w ON w.id = b.workspace_id "
                            "WHERE b.id = :bid"
                        ),
                        {"bid": batch_id},
                    )
                    owner_row = row.first()
                await admin_engine.dispose()
            else:
                # Fallback: try with empty RLS (may fail on RLS-protected tables)
                async with _scoped_session(
                    factory,
                    actor_id=resolved_actor,
                    department_id=resolved_dept,
                ) as session:
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
            if resolved_actor is None:
                resolved_actor = owner_row[0]
            if resolved_dept is None:
                resolved_dept = owner_row[1]

        service = RecommendationService(
            session_factory=factory,
            department_id=resolved_dept,
            actor_id=resolved_actor,
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
        import sqlalchemy as sa

        from packages.common.errors import AppError
        from packages.research.execution.entities_trusted import (
            ResearchAnalysisPlanVersion,
        )
        from packages.research.timeline.entities import ResearchTurn
        from packages.research.timeline.repository import TimelineRepository

        factory = _get_session_factory()
        dept_uuid = UUID(department_id) if department_id else None
        actor_uuid = UUID(actor_id) if actor_id else None
        ws_uuid = UUID(workspace_id) if workspace_id else None
        turn_uuid = UUID(turn_id)

        # 1. Load the turn; handle not-found / workspace mismatch and
        #    redelivery idempotency (a plan may already exist for this turn).
        async with _scoped_session(
            factory, actor_id=actor_uuid, department_id=dept_uuid
        ) as session:
            turn = await session.get(ResearchTurn, turn_uuid)
            if turn is None or (ws_uuid is not None and turn.workspace_id != ws_uuid):
                return {"turn_id": turn_id, "status": "not_found", "plan_id": ""}

            existing_row = await session.execute(
                sa.select(ResearchAnalysisPlanVersion)
                .where(ResearchAnalysisPlanVersion.turn_id == turn_uuid)
                .order_by(ResearchAnalysisPlanVersion.version_number.desc())
                .limit(1)
            )
            existing = existing_row.scalar_one_or_none()
            if existing is not None:
                # Redelivery after a successful generation: plan already
                # persisted.  Only ensure the turn is moved forward.
                if turn.status == "planning":
                    try:
                        await TimelineRepository.update_turn_status(
                            session, turn_uuid, "planning", "plan_review"
                        )
                    except AppError:
                        pass
                    else:
                        turn.status = "plan_review"
                return {
                    "turn_id": turn_id,
                    "status": turn.status,
                    "plan_id": str(existing.id),
                }

            if turn.status != "planning":
                return {
                    "turn_id": turn_id,
                    "status": turn.status,
                    "plan_id": "",
                }

            workspace_uuid = turn.workspace_id
            snapshot_uuid = turn.evidence_snapshot_id

        # 2. Generate and persist the plan version via PlanService.
        plan_service = await _build_plan_service(factory, dept_uuid, actor_uuid)
        if plan_service is None:
            async with _scoped_session(
                factory, actor_id=actor_uuid, department_id=dept_uuid
            ) as session:
                try:
                    await TimelineRepository.update_turn_status(
                        session, turn_uuid, "planning", "planning_failed"
                    )
                except AppError:
                    pass
            return {
                "turn_id": turn_id,
                "status": "planning_failed",
                "plan_id": "",
            }

        try:
            plan_ref = await plan_service.generate_plan(
                workspace_id=workspace_uuid,
                snapshot_id=snapshot_uuid,
            )
        except Exception as exc:  # noqa: BLE001 - finalizer records failure
            logger.exception("plan generation failed for turn %s: %s", turn_id, exc)
            async with _scoped_session(
                factory, actor_id=actor_uuid, department_id=dept_uuid
            ) as session:
                try:
                    await TimelineRepository.update_turn_status(
                        session, turn_uuid, "planning", "planning_failed"
                    )
                except AppError:
                    pass
            return {
                "turn_id": turn_id,
                "status": "planning_failed",
                "plan_id": "",
            }

        plan_id = plan_ref.plan_id

        # 3. Bind the plan to the turn and advance planning -> plan_review.
        async with _scoped_session(
            factory, actor_id=actor_uuid, department_id=dept_uuid
        ) as session:
            await session.execute(
                sa.update(ResearchAnalysisPlanVersion)
                .where(ResearchAnalysisPlanVersion.id == plan_id)
                .values(turn_id=turn_uuid)
            )
            try:
                await TimelineRepository.update_turn_status(
                    session, turn_uuid, "planning", "plan_review"
                )
                new_status = "plan_review"
            except AppError:
                current = await session.get(ResearchTurn, turn_uuid)
                new_status = current.status if current is not None else "plan_review"

        logger.info("generated plan %s for turn %s", str(plan_id), turn_id)
        return {
            "turn_id": turn_id,
            "status": new_status,
            "plan_id": str(plan_id),
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
        run_uuid = UUID(run_id)

        finalizer = TimelineRunFinalizer(
            session_factory=factory,
            department_id=dept_uuid,  # type: ignore[arg-type]
            actor_id=actor_uuid,
        )

        # 1. Atomically claim the run (CAS: queued -> running) and load its
        #    turn/plan context.  The WHERE status='queued' guard ensures only
        #    one worker proceeds, preventing duplicate AI calls on redelivery.
        import sqlalchemy as sa

        async with _scoped_session(
            factory, actor_id=actor_uuid, department_id=dept_uuid
        ) as session:
            claimed = await session.execute(
                sa.update(ResearchAnalysisRun)
                .where(
                    ResearchAnalysisRun.id == run_uuid,
                    ResearchAnalysisRun.status == "queued",
                )
                .values(status="running", started_at=sa.func.now())
                .returning(ResearchAnalysisRun)
            )
            run = claimed.scalar_one_or_none()
            if run is None:
                existing = await session.get(ResearchAnalysisRun, run_uuid)
                if existing is None:
                    return {"run_id": run_id, "turn_id": "", "status": "not_found"}
                if existing.status == "succeeded":
                    return {
                        "run_id": run_id,
                        "turn_id": str(existing.turn_id or ""),
                        "status": "succeeded",
                    }
                # Already claimed (running) or terminal (failed/cancelled):
                # never re-enter, avoiding duplicate AI invocation/charging.
                return {
                    "run_id": run_id,
                    "turn_id": str(existing.turn_id or ""),
                    "status": existing.status,
                }
            turn_id = run.turn_id
            actual_ws_id = run.workspace_id
            plan_version_id = run.plan_version_id
            snapshot_id = run.snapshot_id

        # 2. Load AI config and build PlanService
        try:
            config = _get_research_scenario_config()
        except (KeyError, FileNotFoundError, ValueError):
            fail_result = await finalizer.fail(
                run_id=run_uuid,
                turn_id=turn_id,
                error_message="AI config not found",
            )
            return fail_result

        from packages.ai.openai_compatible import OpenAICompatibleProvider
        from packages.research.execution.models_trusted import (
            ModelConfig,
            TaskType,
        )
        from packages.research.planning.context_router import ContextRouter
        from packages.research.planning.model_gateway import ModelGateway
        from packages.research.planning.plan_core import PlanService

        research_model_name = config.model
        thinking = config.thinking_enabled
        ai_provider = OpenAICompatibleProvider(
            api_key=config.api_key,
            base_url=config.base_url,
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

        from packages.facts.query_service import FactQueryService
        from packages.research.lineage.core_adapter import CoreFactProviderImpl

        fact_provider = CoreFactProviderImpl(
            query_service=FactQueryService(
                session_factory=factory,
                department_id=dept_uuid,  # type: ignore[arg-type]
                actor_id=actor_uuid,
                s3_repo=None,
            )
        )

        plan_service = PlanService(
            session_factory=factory,
            department_id=dept_uuid,  # type: ignore[arg-type]
            actor_id=actor_uuid,
            model_gateway=model_gateway,
            context_router=ContextRouter(),
            fact_provider=fact_provider,
        )

        # 3. Execute analysis via PlanService.analyze_data
        try:
            analysis_result = await plan_service.analyze_data(
                workspace_id=actual_ws_id,
                plan_id=plan_version_id,
                snapshot_id=snapshot_id,
                turn_id=turn_id,
            )

            if isinstance(analysis_result, dict):
                analysis_text = analysis_result.get("analysis_result", "")
            elif isinstance(analysis_result, str):
                analysis_text = analysis_result
            else:
                analysis_text = str(analysis_result)

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

        from packages.common.errors import AppError
        from packages.research.execution.entities_trusted import (
            ResearchAnalysisPlanVersion,
        )
        from packages.research.timeline.entities import ResearchTurn
        from packages.research.timeline.repository import TimelineRepository

        factory = _get_session_factory()
        dept_uuid = UUID(department_id) if department_id else None
        actor_uuid = UUID(actor_id) if actor_id else None
        extraction_uuid = UUID(extraction_id)

        # 1. Atomically claim the job via real CAS (UPDATE ... WHERE status='queued').
        #    Guarantees a single worker owns the extraction, preventing duplicate
        #    AI calls (and double billing) on concurrent redelivery.
        async with _scoped_session(
            factory, actor_id=actor_uuid, department_id=dept_uuid
        ) as session:
            try:
                job = await TimelineRepository.update_extraction_status(
                    session,
                    extraction_uuid,
                    expected_status="queued",
                    new_status="running",
                )
            except AppError:
                existing = await TimelineRepository.get_extraction_job(session, extraction_uuid)
                if existing is None:
                    return {
                        "extraction_id": extraction_id,
                        "status": "not_found",
                        "candidate_count": 0,
                    }
                if existing.status == "succeeded":
                    logger.info("Extraction %s already done, skip", extraction_id)
                    return {
                        "extraction_id": extraction_id,
                        "status": "succeeded",
                        "candidate_count": 0,
                    }
                # Already claimed (running) or terminal (failed/cancelled):
                # never re-enter to avoid duplicate AI invocation/charging.
                return {
                    "extraction_id": extraction_id,
                    "status": existing.status,
                    "candidate_count": 0,
                }

            # Load turn to get workspace_id, snapshot_id, plan_version_id
            turn = await session.get(ResearchTurn, job.turn_id)
            if turn is None:
                await TimelineRepository.update_extraction_status(
                    session,
                    extraction_uuid,
                    expected_status="running",
                    new_status="failed",
                )
                return {
                    "extraction_id": extraction_id,
                    "status": "failed",
                    "candidate_count": 0,
                }

            ws_id = turn.workspace_id
            snapshot_id = turn.evidence_snapshot_id

            # Find the confirmed plan for this turn
            plan_result = await session.execute(
                sa.select(ResearchAnalysisPlanVersion)
                .where(ResearchAnalysisPlanVersion.turn_id == job.turn_id)
                .order_by(ResearchAnalysisPlanVersion.version_number.desc())
                .limit(1)
            )
            plan = plan_result.scalar_one_or_none()
            if plan is None:
                await TimelineRepository.update_extraction_status(
                    session,
                    extraction_uuid,
                    expected_status="running",
                    new_status="failed",
                )
                return {
                    "extraction_id": extraction_id,
                    "status": "failed",
                    "candidate_count": 0,
                }

            plan_version_id = plan.id

        # Load AI config and build PlanService
        try:
            config = _get_research_scenario_config()
        except (KeyError, FileNotFoundError, ValueError):
            async with _scoped_session(
                factory, actor_id=actor_uuid, department_id=dept_uuid
            ) as session:
                try:
                    await TimelineRepository.update_extraction_status(
                        session,
                        extraction_uuid,
                        expected_status="running",
                        new_status="failed",
                    )
                except AppError:
                    pass
            return {
                "extraction_id": extraction_id,
                "status": "failed",
                "candidate_count": 0,
            }

        from packages.ai.openai_compatible import OpenAICompatibleProvider
        from packages.research.execution.models_trusted import (
            ModelConfig,
            TaskType,
        )
        from packages.research.planning.context_router import ContextRouter
        from packages.research.planning.model_gateway import ModelGateway
        from packages.research.planning.plan_core import PlanService

        research_model_name = config.model
        ai_provider = OpenAICompatibleProvider(
            api_key=config.api_key,
            base_url=config.base_url,
            model=research_model_name,
            thinking_enabled=config.thinking_enabled,
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

        from packages.facts.query_service import FactQueryService
        from packages.research.lineage.core_adapter import CoreFactProviderImpl

        fact_provider = CoreFactProviderImpl(
            query_service=FactQueryService(
                session_factory=factory,
                department_id=dept_uuid,  # type: ignore[arg-type]
                actor_id=actor_uuid,
                s3_repo=None,
            )
        )

        plan_service = PlanService(
            session_factory=factory,
            department_id=dept_uuid,  # type: ignore[arg-type]
            actor_id=actor_uuid,
            model_gateway=model_gateway,
            context_router=ContextRouter(),
            fact_provider=fact_provider,
        )

        # Execute candidate extraction via PlanService.extract_insight
        try:
            result = await plan_service.extract_insight(
                workspace_id=ws_id,
                plan_id=plan_version_id,
                snapshot_id=snapshot_id,
                turn_id=job.turn_id,
            )
            candidate_id = result.get("insight_candidate_id")
            candidates_created = 1 if candidate_id else 0

            async with _scoped_session(
                factory, actor_id=actor_uuid, department_id=dept_uuid
            ) as session:
                try:
                    await TimelineRepository.update_extraction_status(
                        session,
                        extraction_uuid,
                        expected_status="running",
                        new_status="succeeded",
                    )
                except AppError:
                    pass

            return {
                "extraction_id": extraction_id,
                "status": "succeeded",
                "candidate_count": candidates_created,
            }
        except Exception as exc:
            logger.exception("Extraction %s failed: %s", extraction_id, exc)
            async with _scoped_session(
                factory, actor_id=actor_uuid, department_id=dept_uuid
            ) as session:
                try:
                    await TimelineRepository.update_extraction_status(
                        session,
                        extraction_uuid,
                        expected_status="running",
                        new_status="failed",
                    )
                except AppError:
                    pass
            return {
                "extraction_id": extraction_id,
                "status": "failed",
                "candidate_count": 0,
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

        # RLS context: reconciler is a Beat task with no user session, so it
        # uses the system sentinel GUC to see rows across departments. Without
        # this the research-table queries fail-closed and return no rows.
        from apps.worker.tasks import get_system_guc

        sys_dept, sys_user = get_system_guc()

        async with _scoped_session(factory, actor_id=sys_user, department_id=sys_dept) as session:
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
                            "workspace_id": str(job.workspace_id)
                            if hasattr(job, "workspace_id")
                            else "",
                        },
                    )
                    requeued += 1
                elif job.status == "running":
                    job.status = "failed"
                    marked_lost += 1

        return {"requeued": requeued, "marked_lost": marked_lost}

    return asyncio.run(_run())
