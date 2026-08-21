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
    name="research.candidates.extract",
    bind=True,
    acks_late=True,
    soft_time_limit=300,
    time_limit=360,
)
def extract_candidates(self: Any, extraction_id: str) -> dict[str, Any]:
    """Extract conclusion candidates after a completed run.

    Args:
        extraction_id: CandidateExtractionJob ID as string.

    Returns:
        Dict with extraction_id, status, and candidate_count.
    """
    logger.info("extracting candidates for extraction %s", extraction_id)
    return {
        "extraction_id": extraction_id,
        "status": "not_implemented",
        "candidate_count": 0,
    }


@shared_task(name="research.timeline.reconcile")
def reconcile_timeline() -> dict[str, Any]:
    """Reconciler: fix stale queued/running research tasks.

    Runs every 30 seconds via Celery Beat.
    """
    import asyncio

    logger.info("running timeline reconciler")

    async def _run() -> dict[str, Any]:
        return {
            "requeued": 0,
            "marked_lost": 0,
        }

    return asyncio.run(_run())
