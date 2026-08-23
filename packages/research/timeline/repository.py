"""Timeline repository: Turn/Recommendation/Result/Extraction persistence
and keyset-cursor pagination.

All methods are @staticmethod async, accept AsyncSession, and do not
manage transactions.  The Service layer manages the transaction boundary.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.research.timeline.entities import (
    CandidateExtractionJob,
    ResearchRecommendationBatch,
    ResearchRecommendationItem,
    ResearchTurn,
    ResearchTurnContext,
    ResearchTurnResult,
)

# ============================================================
# Cursor encoding/decoding (opaque, base64url of compact JSON)
# ============================================================


def encode_cursor(turn_number: int, turn_id: UUID) -> str:
    """Encode a keyset cursor as base64url(compact JSON).

    The cursor payload is ``{"n": turn_number, "id": "uuid"}``.
    """
    payload = json.dumps(
        {"n": turn_number, "id": str(turn_id)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple[int, UUID]:
    """Decode a keyset cursor, returning (turn_number, turn_id).

    Raises:
        AppError: If the cursor is malformed.
    """
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return payload["n"], UUID(payload["id"])
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise AppError(
            code="validation_failed",
            message="Invalid cursor",
            retryable=False,
            fields={},
        ) from exc


# ============================================================
# Page size validation
# ============================================================

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
MIN_PAGE_SIZE = 1


def validate_page_size(page_size: int) -> int:
    """Validate and return the page size.

    Raises:
        AppError: If out of range.
    """
    if page_size < MIN_PAGE_SIZE or page_size > MAX_PAGE_SIZE:
        raise AppError(
            code="validation_failed",
            message=f"page_size must be {MIN_PAGE_SIZE}-{MAX_PAGE_SIZE}",
            retryable=False,
            fields={"page_size": page_size},
        )
    return page_size


# ============================================================
# Turn repository
# ============================================================


class TimelineRepository:
    """Turn, recommendation, result, and extraction persistence."""

    # --- Turn CRUD ---

    @staticmethod
    async def insert_turn(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        turn_number: int,
        kind: str,
        status: str,
        question_text: str,
        question_origin: str,
        evidence_snapshot_id: UUID,
        recommendation_item_id: UUID | None,
        idempotency_key: str,
    ) -> ResearchTurn:
        """Insert a new turn row."""
        turn = ResearchTurn(
            id=new_id(),
            workspace_id=workspace_id,
            turn_number=turn_number,
            kind=kind,
            status=status,
            question_text_snapshot=question_text,
            question_origin=question_origin,
            evidence_snapshot_id=evidence_snapshot_id,
            recommendation_item_id=recommendation_item_id,
            idempotency_key=idempotency_key,
            lock_version=0,
        )
        session.add(turn)
        await session.flush()
        return turn

    @staticmethod
    async def get_turn(
        session: AsyncSession,
        turn_id: UUID,
    ) -> ResearchTurn | None:
        """Get a turn by ID (no ownership check)."""
        result = await session.execute(sa.select(ResearchTurn).where(ResearchTurn.id == turn_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_turn_by_idempotency(
        session: AsyncSession,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> ResearchTurn | None:
        """Get a turn by idempotency key."""
        result = await session.execute(
            sa.select(ResearchTurn).where(
                ResearchTurn.workspace_id == workspace_id,
                ResearchTurn.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_turn_status(
        session: AsyncSession,
        turn_id: UUID,
        expected_status: str,
        new_status: str,
    ) -> ResearchTurn:
        """Compare-and-set turn status.

        Raises:
            AppError: If the expected status does not match (state_conflict).
        """
        result = await session.execute(
            sa.update(ResearchTurn)
            .where(
                ResearchTurn.id == turn_id,
                ResearchTurn.status == expected_status,
            )
            .values(status=new_status, updated_at=sa.func.now())
            .returning(ResearchTurn)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise AppError(
                code="state_conflict",
                message=f"Turn {turn_id} is not in '{expected_status}' status",
                retryable=True,
                fields={"turn_id": str(turn_id), "expected": expected_status},
            )
        return row

    @staticmethod
    async def lock_turn_inputs(
        session: AsyncSession,
        turn_id: UUID,
        prompt_template_version: str,
        output_schema_version: str,
    ) -> ResearchTurn:
        """Fix prompt/schema versions on the turn (when planning starts).

        Raises:
            AppError: If the turn is not in question_draft or planning_failed.
        """
        result = await session.execute(
            sa.update(ResearchTurn)
            .where(
                ResearchTurn.id == turn_id,
                ResearchTurn.status.in_(["question_draft", "planning_failed"]),
            )
            .values(
                status="planning",
                prompt_template_version=prompt_template_version,
                output_schema_version=output_schema_version,
                updated_at=sa.func.now(),
            )
            .returning(ResearchTurn)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise AppError(
                code="state_conflict",
                message=f"Turn {turn_id} cannot enter planning",
                retryable=True,
                fields={"turn_id": str(turn_id)},
            )
        return row

    # --- Turn context ---

    @staticmethod
    async def insert_turn_context(
        session: AsyncSession,
        *,
        turn_id: UUID,
        conclusion_revision_ids: list[tuple[UUID, int]],
    ) -> None:
        """Insert turn context rows.

        Args:
            turn_id: The turn ID.
            conclusion_revision_ids: List of (revision_id, position) pairs.
        """
        for revision_id, position in conclusion_revision_ids:
            session.add(
                ResearchTurnContext(
                    turn_id=turn_id,
                    conclusion_revision_id=revision_id,
                    position=position,
                )
            )
        await session.flush()

    @staticmethod
    async def list_turn_context(
        session: AsyncSession,
        turn_id: UUID,
    ) -> list[ResearchTurnContext]:
        """List context rows for a turn, ordered by position."""
        result = await session.execute(
            sa.select(ResearchTurnContext)
            .where(ResearchTurnContext.turn_id == turn_id)
            .order_by(ResearchTurnContext.position)
        )
        return list(result.scalars().all())

    # --- Keyset pagination ---

    @staticmethod
    async def list_turns(
        session: AsyncSession,
        workspace_id: UUID,
        cursor: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[list[ResearchTurn], str | None]:
        """List turns in descending (turn_number, id) order with keyset pagination.

        Newest turns first — timeline renders newest question at the top.

        Args:
            session: Async DB session.
            workspace_id: Workspace ID.
            cursor: Opaque cursor from a previous page, or None for first page.
            page_size: Items per page (1-50).

        Returns:
            (turns, next_cursor) — next_cursor is None when there are no more items.
        """
        page_size = validate_page_size(page_size)

        # Build the base query
        stmt = (
            sa.select(ResearchTurn)
            .where(ResearchTurn.workspace_id == workspace_id)
            .order_by(
                ResearchTurn.turn_number.desc(),
                ResearchTurn.id.desc(),
            )
            .limit(page_size + 1)  # +1 to detect if there's a next page
        )

        # Apply cursor condition if provided
        if cursor is not None:
            cursor_turn_number, cursor_id = decode_cursor(cursor)
            stmt = stmt.where(
                sa.or_(
                    ResearchTurn.turn_number < cursor_turn_number,
                    sa.and_(
                        ResearchTurn.turn_number == cursor_turn_number,
                        ResearchTurn.id < cursor_id,
                    ),
                )
            )

        result = await session.execute(stmt)
        rows = list(result.scalars().all())

        # Determine if there's a next page
        has_next = len(rows) > page_size
        if has_next:
            rows = rows[:page_size]

        # Compute next cursor
        next_cursor = None
        if has_next and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.turn_number, last.id)

        return rows, next_cursor

    @staticmethod
    async def get_active_run_status(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> str | None:
        """Check if there's an active (queued/running) turn in the workspace."""
        result = await session.execute(
            sa.select(ResearchTurn.status)
            .where(
                ResearchTurn.workspace_id == workspace_id,
                ResearchTurn.status.in_(["queued", "running"]),
            )
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row if row else None

    # --- Recommendation batch ---

    @staticmethod
    async def insert_batch(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        snapshot_id: UUID,
        mode: str,
        prompt_template_version: str,
        output_schema_version: str,
        idempotency_key: str,
    ) -> ResearchRecommendationBatch:
        """Insert a recommendation batch."""
        batch = ResearchRecommendationBatch(
            id=new_id(),
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
            mode=mode,
            status="queued",
            prompt_template_version=prompt_template_version,
            output_schema_version=output_schema_version,
            idempotency_key=idempotency_key,
        )
        session.add(batch)
        await session.flush()
        return batch

    @staticmethod
    async def get_batch(
        session: AsyncSession,
        batch_id: UUID,
    ) -> ResearchRecommendationBatch | None:
        """Get a batch by ID."""
        result = await session.execute(
            sa.select(ResearchRecommendationBatch).where(ResearchRecommendationBatch.id == batch_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_batch_by_idempotency(
        session: AsyncSession,
        workspace_id: UUID,
        idempotency_key: str,
    ) -> ResearchRecommendationBatch | None:
        """Get a batch by idempotency key."""
        result = await session.execute(
            sa.select(ResearchRecommendationBatch).where(
                ResearchRecommendationBatch.workspace_id == workspace_id,
                ResearchRecommendationBatch.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_batch_status(
        session: AsyncSession,
        batch_id: UUID,
        expected_status: str,
        new_status: str,
        **extra_values: object,
    ) -> ResearchRecommendationBatch:
        """Compare-and-set batch status."""
        values: dict[str, object] = {
            "status": new_status,
            "updated_at": sa.func.now(),
        }
        values.update(extra_values)

        result = await session.execute(
            sa.update(ResearchRecommendationBatch)
            .where(
                ResearchRecommendationBatch.id == batch_id,
                ResearchRecommendationBatch.status == expected_status,
            )
            .values(**values)
            .returning(ResearchRecommendationBatch)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise AppError(
                code="state_conflict",
                message=f"Batch {batch_id} is not in '{expected_status}' status",
                retryable=True,
                fields={"batch_id": str(batch_id)},
            )
        return row

    @staticmethod
    async def insert_recommendation_items(
        session: AsyncSession,
        *,
        batch_id: UUID,
        items: list[dict[str, object]],
    ) -> list[ResearchRecommendationItem]:
        """Insert recommendation items for a batch."""
        orm_items = []
        for i, item in enumerate(items):
            orm_item = ResearchRecommendationItem(
                id=new_id(),
                batch_id=batch_id,
                position=i,
                question=item["question"],
                rationale=item["rationale"],
                evidence_hints=item.get("evidence_hints", []),
            )
            session.add(orm_item)
            orm_items.append(orm_item)
        await session.flush()
        return orm_items

    @staticmethod
    async def list_recommendation_items(
        session: AsyncSession,
        batch_id: UUID,
    ) -> list[ResearchRecommendationItem]:
        """List items for a batch, ordered by position."""
        result = await session.execute(
            sa.select(ResearchRecommendationItem)
            .where(ResearchRecommendationItem.batch_id == batch_id)
            .order_by(ResearchRecommendationItem.position)
        )
        return list(result.scalars().all())

    # --- Turn result ---

    @staticmethod
    async def insert_turn_result(
        session: AsyncSession,
        *,
        turn_id: UUID,
        run_id: UUID,
        result_kind: str,
        summary: str | None = None,
        structured_output: dict[str, Any] | None = None,
        method_summary: str | None = None,
        evidence_refs: list[Any] | None = None,
        limitations: str | None = None,
    ) -> ResearchTurnResult:
        """Insert a turn result (one per turn, one per run)."""
        result = ResearchTurnResult(
            id=new_id(),
            turn_id=turn_id,
            run_id=run_id,
            result_kind=result_kind,
            summary=summary,
            structured_output=structured_output,
            method_summary=method_summary,
            evidence_refs=evidence_refs or [],
            limitations=limitations,
        )
        session.add(result)
        await session.flush()
        return result

    @staticmethod
    async def get_turn_result(
        session: AsyncSession,
        turn_id: UUID,
    ) -> ResearchTurnResult | None:
        """Get the result for a turn."""
        result = await session.execute(
            sa.select(ResearchTurnResult).where(ResearchTurnResult.turn_id == turn_id)
        )
        return result.scalar_one_or_none()

    # --- Candidate extraction job ---

    @staticmethod
    async def insert_extraction_job(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        turn_id: UUID,
        run_id: UUID,
    ) -> CandidateExtractionJob:
        """Insert a candidate extraction job (one per run, enforced by unique)."""
        job = CandidateExtractionJob(
            id=new_id(),
            workspace_id=workspace_id,
            turn_id=turn_id,
            run_id=run_id,
            status="queued",
            attempt=1,
        )
        session.add(job)
        await session.flush()
        return job

    @staticmethod
    async def get_extraction_job(
        session: AsyncSession,
        extraction_id: UUID,
    ) -> CandidateExtractionJob | None:
        """Get an extraction job by ID."""
        result = await session.execute(
            sa.select(CandidateExtractionJob).where(CandidateExtractionJob.id == extraction_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_extraction_by_run(
        session: AsyncSession,
        run_id: UUID,
    ) -> CandidateExtractionJob | None:
        """Get the extraction job for a run."""
        result = await session.execute(
            sa.select(CandidateExtractionJob).where(CandidateExtractionJob.run_id == run_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_extraction_status(
        session: AsyncSession,
        extraction_id: UUID,
        expected_status: str,
        new_status: str,
        **extra_values: object,
    ) -> CandidateExtractionJob:
        """Compare-and-set extraction job status."""
        values: dict[str, object] = {
            "status": new_status,
            "updated_at": sa.func.now(),
        }
        values.update(extra_values)

        result = await session.execute(
            sa.update(CandidateExtractionJob)
            .where(
                CandidateExtractionJob.id == extraction_id,
                CandidateExtractionJob.status == expected_status,
            )
            .values(**values)
            .returning(CandidateExtractionJob)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise AppError(
                code="state_conflict",
                message=f"Extraction {extraction_id} is not in '{expected_status}' status",
                retryable=True,
                fields={"extraction_id": str(extraction_id)},
            )
        return row

    @staticmethod
    async def update_heartbeat(
        session: AsyncSession,
        extraction_id: UUID,
    ) -> None:
        """Update heartbeat timestamp for an extraction job."""
        await session.execute(
            sa.update(CandidateExtractionJob)
            .where(CandidateExtractionJob.id == extraction_id)
            .values(heartbeat_at=sa.func.now(), updated_at=sa.func.now())
        )

    # --- Stale job reconciler queries ---

    @staticmethod
    async def list_stale_running_extractions(
        session: AsyncSession,
        heartbeat_timeout_minutes: int = 10,
    ) -> list[CandidateExtractionJob]:
        """Find running extractions whose heartbeat is stale."""
        from datetime import UTC, timedelta

        cutoff = datetime.now(UTC) - timedelta(minutes=heartbeat_timeout_minutes)
        result = await session.execute(
            sa.select(CandidateExtractionJob).where(
                CandidateExtractionJob.status == "running",
                sa.or_(
                    CandidateExtractionJob.heartbeat_at.is_(None),
                    CandidateExtractionJob.heartbeat_at < cutoff,
                ),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_queued_extractions_without_delivery(
        session: AsyncSession,
        stale_minutes: int = 2,
    ) -> list[CandidateExtractionJob]:
        """Find queued extractions that may not have been delivered to a worker."""
        from datetime import UTC, timedelta

        cutoff = datetime.now(UTC) - timedelta(minutes=stale_minutes)
        result = await session.execute(
            sa.select(CandidateExtractionJob).where(
                CandidateExtractionJob.status == "queued",
                CandidateExtractionJob.created_at < cutoff,
            )
        )
        return list(result.scalars().all())
