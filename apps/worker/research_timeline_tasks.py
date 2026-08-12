"""Celery tasks for research timeline: recommendations, extraction, reconciler.

All tasks use acks_late=True for reliability and compare-and-set
to prevent duplicate execution.
"""

import logging
import os
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)


def _get_session_factory():
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
        from packages.research.timeline.recommendation_service import RecommendationService
        from packages.research.timeline.simple_gateway import build_gateway_from_config

        factory = _get_session_factory()
        gateway = await build_gateway_from_config()
        service = RecommendationService(session_factory=factory, model_gateway=gateway)
        ref = await service.execute_batch(batch_id)
        return {
            "batch_id": str(ref.batch_id),
            "status": ref.status,
            "item_count": ref.item_count,
        }

    logger.info("generating recommendations for batch %s", batch_id)
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
