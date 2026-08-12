"""Recommendation service: initial and followup question recommendations.

This service creates RecommendationBatch rows and writes Outbox events for
Celery task delivery.  It does NOT call the model directly — that is done by
the Celery task (Task 4).

Key methods:
  - enqueue_initial: Called when the first evidence snapshot is confirmed.
  - request_followup: Called when the user clicks "帮我想下一步".
  - execute_batch: Called by the Celery worker to generate questions (Task 4).
  - retry_batch: Retry a failed batch.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from packages.research.timeline.contracts import (
    RECOMMENDATION_OUTPUT_SCHEMA_VERSION,
    RECOMMENDATION_PROMPT_VERSION,
    RecommendationBatchRef,
)
from packages.research.timeline.repository import TimelineRepository
from packages.research.timeline.state_machine import RecommendationBatchStateMachine

if TYPE_CHECKING:
    pass

logger = logging.getLogger("research.recommendation")


class RecommendationService:
    """Recommendation batch creation, execution and retry.

    This service is instantiated by the composition root with a
    session_factory and optional ModelGateway.  The enqueue methods
    run within the caller's transaction; the execute/retry methods
    manage their own sessions.
    """

    def __init__(
        self,
        session_factory: Any,
        model_gateway: Any | None = None,
    ) -> None:
        self._factory = session_factory
        self._gateway = model_gateway

    @staticmethod
    async def enqueue_initial(
        session: AsyncSession,
        workspace_id: UUID,
        snapshot_id: UUID,
    ) -> RecommendationBatchRef:
        """Create the initial recommendation batch for a first snapshot.

        This is called within the same transaction as the snapshot creation.
        Uses a deterministic idempotency key ``initial:{snapshot_id}``.

        Args:
            session: The active DB session (caller manages transaction).
            workspace_id: Workspace ID.
            snapshot_id: The newly created snapshot ID.

        Returns:
            RecommendationBatchRef for the created batch.
        """
        idempotency_key = f"initial:{snapshot_id}"

        # Check if batch already exists (idempotent)
        existing = await TimelineRepository.get_batch_by_idempotency(
            session, workspace_id, idempotency_key
        )
        if existing is not None:
            items = await TimelineRepository.list_recommendation_items(session, existing.id)
            return RecommendationBatchRef(
                batch_id=existing.id,
                workspace_id=workspace_id,
                status=existing.status,
                item_count=len(items),
            )

        batch = await TimelineRepository.insert_batch(
            session,
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
            mode="initial",
            prompt_template_version=RECOMMENDATION_PROMPT_VERSION,
            output_schema_version=RECOMMENDATION_OUTPUT_SCHEMA_VERSION,
            idempotency_key=idempotency_key,
        )

        logger.info(
            "enqueued initial recommendation batch %s for snapshot %s",
            batch.id,
            snapshot_id,
        )

        return RecommendationBatchRef(
            batch_id=batch.id,
            workspace_id=workspace_id,
            status=batch.status,
            item_count=0,
        )

    async def request_followup(
        self,
        workspace_id: UUID,
        snapshot_id: UUID,
        selected_revision_ids: tuple[UUID, ...],
        idempotency_key: str,
    ) -> RecommendationBatchRef:
        """Request followup recommendations.

        Args:
            workspace_id: Workspace ID.
            snapshot_id: Current snapshot ID.
            selected_revision_ids: Explicitly selected conclusion revisions.
            idempotency_key: Client-provided idempotency key.
        """
        async with self._factory() as session:
            existing = await TimelineRepository.get_batch_by_idempotency(
                session, workspace_id, idempotency_key
            )
            if existing is not None:
                items = await TimelineRepository.list_recommendation_items(session, existing.id)
                return RecommendationBatchRef(
                    batch_id=existing.id,
                    workspace_id=workspace_id,
                    status=existing.status,
                    item_count=len(items),
                )

            batch = await TimelineRepository.insert_batch(
                session,
                workspace_id=workspace_id,
                snapshot_id=snapshot_id,
                mode="followup",
                prompt_template_version=RECOMMENDATION_PROMPT_VERSION,
                output_schema_version=RECOMMENDATION_OUTPUT_SCHEMA_VERSION,
                idempotency_key=idempotency_key,
            )

            await session.commit()

            return RecommendationBatchRef(
                batch_id=batch.id,
                workspace_id=workspace_id,
                status=batch.status,
                item_count=0,
            )

    async def execute_batch(self, batch_id: UUID) -> RecommendationBatchRef:
        """Execute a recommendation batch (call the model).

        Flow:
          1. CAS batch queued → running
          2. Build prompt from snapshot + context
          3. Call ModelGateway
          4. Parse structured output (RecommendationOutput)
          5. NFKC normalize + dedup questions
          6. If count invalid or dedup leaves 0: retry once (attempt+1)
          7. If still fails: mark batch failed, save error_code
          8. If succeeds: insert items, mark batch succeeded

        Args:
            batch_id: Batch ID to execute.

        Returns:
            Updated RecommendationBatchRef with item_count.
        """
        import json
        import unicodedata

        from packages.research.timeline.contracts import (
            RecommendationOutput,
            RecommendedQuestion,
        )
        from packages.research.timeline.prompts import (
            RECOMMENDATION_SYSTEM_PROMPT,
            RECOMMENDATION_USER_TEMPLATE,
        )

        async with self._factory() as session:
            batch = await TimelineRepository.get_batch(session, batch_id)
            if batch is None:
                from packages.common.errors import AppError

                raise AppError(
                    code="not_found",
                    message="Recommendation batch not found",
                    retryable=False,
                    fields={"batch_id": str(batch_id)},
                )

            # Terminal states: already done — return existing
            if batch.status in ("succeeded", "failed"):
                items = await TimelineRepository.list_recommendation_items(session, batch_id)
                return RecommendationBatchRef(
                    batch_id=batch_id,
                    workspace_id=batch.workspace_id,
                    status=batch.status,
                    item_count=len(items),
                )

            # CAS: queued → running
            try:
                await TimelineRepository.update_batch_status(
                    session,
                    batch_id,
                    expected_status="queued",
                    new_status="running",
                )
            except AppError:
                # Already running or in unexpected state — return current
                return RecommendationBatchRef(
                    batch_id=batch_id,
                    workspace_id=batch.workspace_id,
                    status=batch.status,
                    item_count=0,
                )

            # Build prompt context — load actual snapshot data
            from packages.research.repository import ResearchRepository

            snapshot = await ResearchRepository.get_latest_snapshot(session, batch.workspace_id)
            if snapshot is not None:
                import json as _json

                field_manifest_str = _json.dumps(
                    snapshot.field_manifest, ensure_ascii=False, indent=2
                )[:4000]
                source_refs_str = _json.dumps(snapshot.source_refs, ensure_ascii=False, indent=2)[
                    :1000
                ]
                evidence_count = len(snapshot.source_refs)
                snapshot_number = snapshot.snapshot_number
            else:
                field_manifest_str = "[]"
                source_refs_str = "[]"
                evidence_count = 0
                snapshot_number = "unknown"

            # Load actual fact data (three-segment: metadata/points/series) for richer context
            fact_data_str = ""
            try:
                import os

                import sqlalchemy as sa

                from packages.common.database import build_session_factory
                from packages.research.entities import WorkspaceEvidenceRef

                analysis_db_url = os.environ.get(
                    "IRIP_ALEMBIC_DATABASE_URL",
                    "postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip",
                )
                analysis_factory = build_session_factory(analysis_db_url)

                async with analysis_factory() as fact_session:
                    refs_result = await fact_session.execute(
                        sa.select(WorkspaceEvidenceRef).where(
                            WorkspaceEvidenceRef.workspace_id == batch.workspace_id,
                            WorkspaceEvidenceRef.status == "active",
                        )
                    )
                    refs = refs_result.scalars().all()

                    from packages.facts.query_service import FactQueryService
                    from packages.research.lineage.core_adapter import CoreFactProviderImpl

                    user_result = await fact_session.execute(
                        sa.text(
                            "SELECT id, department_id FROM app_user WHERE email = 'admin@irip.local' LIMIT 1"
                        )
                    )
                    user_row = user_result.first()

                    if user_row and refs:
                        from apps.api.main import _build_s3_repo

                        s3_repo = _build_s3_repo()
                        fact_query = FactQueryService(
                            session_factory=analysis_factory,
                            department_id=user_row[1],
                            actor_id=user_row[0],
                            s3_repo=s3_repo,
                        )
                        fact_provider = CoreFactProviderImpl(query_service=fact_query)

                        fact_rows = []
                        for ref in refs:
                            fact_info = {"source_name": ref.source_name or ""}
                            try:
                                data = await fact_provider.get_fact_data(ref.source_id)
                                if isinstance(data, dict):
                                    fact_info["metadata"] = data.get("metadata", {})
                                    fact_info["points"] = data.get("points", [])
                                    # Pass series with columns + first 5 rows (enough to see data content, not too much)
                                    series_full = data.get("series", [])
                                    fact_info["series"] = [
                                        {
                                            "name": s.get("name", ""),
                                            "columns": s.get("columns", []),
                                            "rows_sample": (s.get("rows", []) or [])[:5],
                                        }
                                        for s in series_full
                                        if isinstance(s, dict)
                                    ]
                            except Exception as exc:
                                logger.warning(
                                    "Failed to load fact data for %s: %s", ref.source_id, exc
                                )
                            fact_rows.append(fact_info)

                        fact_data_str = _json.dumps(fact_rows, ensure_ascii=False, indent=2)[:10000]
                        logger.info(
                            "recommendation fact_data loaded: %d rows, %d chars",
                            len(fact_rows),
                            len(fact_data_str),
                        )
            except Exception as exc:
                logger.warning("recommendation fact_data loading failed: %s", exc)

            # Load followup context from outbox event payload (if followup mode)
            followup_ctx = ""
            if batch.mode == "followup":
                import sqlalchemy as sa

                from packages.jobs.outbox import OutboxEvent

                event_result = await session.execute(
                    sa.select(OutboxEvent)
                    .where(OutboxEvent.aggregate_id == batch.id)
                    .order_by(OutboxEvent.occurred_at.desc())
                    .limit(1)
                )
                event = event_result.scalar_one_or_none()
                if event and event.payload:
                    followup_ctx = event.payload.get("followup_context", "")

            user_prompt = RECOMMENDATION_USER_TEMPLATE.format(
                snapshot_number=snapshot_number,
                evidence_count=evidence_count,
                field_manifest=field_manifest_str,
                source_refs=source_refs_str,
                fact_data=fact_data_str or "（无实际数据，仅根据字段清单推断）",
                followup_context=f"\n上一轮分析结果:\n{followup_ctx}" if followup_ctx else "",
            )
            system_prompt = RECOMMENDATION_SYSTEM_PROMPT

            # Call model with retry
            max_attempts = 2
            last_error: str | None = None

            for attempt in range(max_attempts):
                try:
                    if self._gateway is None:
                        raise RuntimeError("ModelGateway not configured")

                    raw_content = await self._gateway.call(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                    )

                    # Extract JSON from raw content (AI may wrap in ```json ... ```)
                    raw_str = (
                        raw_content
                        if isinstance(raw_content, str)
                        else (
                            getattr(raw_content, "answer", None)
                            or getattr(raw_content, "content", None)
                            or str(raw_content)
                        )
                    )
                    # Strip Markdown code fences if present
                    raw_str = raw_str.strip()
                    if raw_str.startswith("```"):
                        lines = raw_str.split("\n")
                        # Remove first line (```json) and last line (```)
                        lines = [l for l in lines if not l.strip().startswith("```")]
                        raw_str = "\n".join(lines).strip()

                    data = json.loads(raw_str) if isinstance(raw_str, str) else raw_str

                    # Validate structured output
                    output = RecommendationOutput.model_validate(data)

                    # NFKC normalize + dedup
                    seen: set[str] = set()
                    unique_questions: list[RecommendedQuestion] = []
                    for q in output.questions:
                        normalized = unicodedata.normalize("NFKC", q.question).strip().casefold()
                        if normalized not in seen:
                            seen.add(normalized)
                            unique_questions.append(q)

                    if len(unique_questions) < 1:
                        raise ValueError("Deduplication left 0 questions")
                    if len(unique_questions) > 4:
                        raise ValueError(
                            f"Got {len(unique_questions)} questions after dedup, max is 4"
                        )

                    # Success — insert items
                    items_data: list[dict[str, object]] = [
                        {
                            "question": q.question,
                            "rationale": q.rationale,
                            "evidence_hints": list(q.evidence_hints),
                        }
                        for q in unique_questions
                    ]
                    await TimelineRepository.insert_recommendation_items(
                        session,
                        batch_id=batch_id,
                        items=items_data,
                    )

                    await TimelineRepository.update_batch_status(
                        session,
                        batch_id,
                        expected_status="running",
                        new_status="succeeded",
                    )

                    await session.commit()

                    logger.info(
                        "recommendation batch %s succeeded with %d questions",
                        batch_id,
                        len(unique_questions),
                    )

                    return RecommendationBatchRef(
                        batch_id=batch_id,
                        workspace_id=batch.workspace_id,
                        status="succeeded",
                        item_count=len(unique_questions),
                    )

                except Exception as exc:
                    last_error = type(exc).__name__
                    logger.warning(
                        "recommendation batch %s attempt %d failed: %s",
                        batch_id,
                        attempt + 1,
                        exc,
                    )
                    if attempt < max_attempts - 1:
                        continue

            # All attempts failed
            await TimelineRepository.update_batch_status(
                session,
                batch_id,
                expected_status="running",
                new_status="failed",
                error_code=last_error or "unknown",
            )

            await session.commit()

            return RecommendationBatchRef(
                batch_id=batch_id,
                workspace_id=batch.workspace_id,
                status="failed",
                item_count=0,
            )

    async def retry_batch(self, batch_id: UUID) -> RecommendationBatchRef:
        """Retry a failed batch.

        Args:
            batch_id: Batch ID to retry.

        Returns:
            Updated RecommendationBatchRef.
        """
        async with self._factory() as session:
            batch = await TimelineRepository.get_batch(session, batch_id)
            if batch is None:
                from packages.common.errors import AppError

                raise AppError(
                    code="not_found",
                    message="Recommendation batch not found",
                    retryable=False,
                    fields={"batch_id": str(batch_id)},
                )

            if not RecommendationBatchStateMachine.can_retry(batch.status):
                from packages.common.errors import AppError

                raise AppError(
                    code="state_conflict",
                    message=f"Batch in status '{batch.status}' cannot be retried",
                    retryable=True,
                    fields={"batch_id": str(batch_id)},
                )

            await TimelineRepository.update_batch_status(
                session,
                batch_id,
                expected_status="failed",
                new_status="queued",
                attempt=batch.attempt + 1,
                error_code=None,
            )

            await session.commit()

            return RecommendationBatchRef(
                batch_id=batch_id,
                workspace_id=batch.workspace_id,
                status="queued",
                item_count=0,
            )

    async def get_active(self, workspace_id: UUID) -> dict:
        """Get the latest recommendation batch and its items for a workspace.

        Returns a dict suitable for API response:
          - batch_id, workspace_id, status, items[]

        If no batch exists, returns status="none" with empty items.
        """
        import sqlalchemy as sa

        from packages.research.timeline.entities import (
            ResearchRecommendationBatch,
            ResearchRecommendationItem,
        )

        async with self._factory() as session:
            result = await session.execute(
                sa.select(ResearchRecommendationBatch)
                .where(ResearchRecommendationBatch.workspace_id == workspace_id)
                .order_by(ResearchRecommendationBatch.created_at.desc())
                .limit(1)
            )
            batch = result.scalar_one_or_none()
            if batch is None:
                return {
                    "batch_id": "",
                    "workspace_id": str(workspace_id),
                    "status": "none",
                    "items": [],
                }

            items_result = await session.execute(
                sa.select(ResearchRecommendationItem)
                .where(ResearchRecommendationItem.batch_id == batch.id)
                .order_by(ResearchRecommendationItem.position)
            )
            items = [
                {
                    "id": str(item.id),
                    "question": item.question,
                    "rationale": item.rationale or "",
                }
                for item in items_result.scalars()
            ]

            return {
                "batch_id": str(batch.id),
                "workspace_id": str(workspace_id),
                "status": batch.status,
                "items": items,
            }
