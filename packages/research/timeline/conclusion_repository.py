"""Conclusion repository: Candidate/Conclusion/Revision persistence
and conclusion library cursor pagination.

All methods are @staticmethod async, accept AsyncSession, and do not
manage transactions.
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
    ResearchConclusion,
    ResearchConclusionCandidate,
    ResearchConclusionRevision,
)

# ============================================================
# Conclusion library cursor (by updated_at, id)
# ============================================================


def encode_conclusion_cursor(updated_at: datetime, conclusion_id: UUID) -> str:
    """Encode a conclusion-library cursor."""
    payload = json.dumps(
        {"t": updated_at.isoformat(), "id": str(conclusion_id)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_conclusion_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode a conclusion-library cursor."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(payload["t"]), UUID(payload["id"])
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise AppError(
            code="validation_failed",
            message="Invalid conclusion cursor",
            retryable=False,
            fields={},
        ) from exc


# ============================================================
# Candidate repository
# ============================================================


class CandidateRepository:
    """Candidate persistence and queries."""

    @staticmethod
    async def insert_candidates(
        session: AsyncSession,
        *,
        extraction_id: UUID,
        turn_id: UUID,
        candidates: list[dict[str, object]],
    ) -> list[ResearchConclusionCandidate]:
        """Insert candidate rows for an extraction.

        Args:
            extraction_id: The extraction job ID.
            turn_id: The turn ID.
            candidates: List of candidate dicts (statement, scope, evidence_refs,
                        method_refs, confidence_level, limitations).
        """
        orm_candidates = []
        for i, c in enumerate(candidates):
            orm_c = ResearchConclusionCandidate(
                id=new_id(),
                extraction_id=extraction_id,
                turn_id=turn_id,
                ordinal=i,
                statement=c["statement"],
                scope=c.get("scope"),
                evidence_refs=c.get("evidence_refs", []),
                method_refs=c.get("method_refs", []),
                confidence_level=c.get("confidence_level"),
                limitations=c.get("limitations"),
                status="pending",
            )
            session.add(orm_c)
            orm_candidates.append(orm_c)
        await session.flush()
        return orm_candidates

    @staticmethod
    async def list_candidates_by_turn(
        session: AsyncSession,
        turn_id: UUID,
    ) -> list[ResearchConclusionCandidate]:
        """List all candidates for a turn, ordered by ordinal."""
        result = await session.execute(
            sa.select(ResearchConclusionCandidate)
            .where(ResearchConclusionCandidate.turn_id == turn_id)
            .order_by(ResearchConclusionCandidate.ordinal)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_candidate(
        session: AsyncSession,
        candidate_id: UUID,
    ) -> ResearchConclusionCandidate | None:
        """Get a candidate by ID."""
        result = await session.execute(
            sa.select(ResearchConclusionCandidate).where(
                ResearchConclusionCandidate.id == candidate_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_candidate_status(
        session: AsyncSession,
        candidate_id: UUID,
        status: str,
        saved_conclusion_id: UUID | None = None,
    ) -> None:
        """Update a candidate's status (saved or rejected)."""
        values: dict[str, object] = {"status": status}
        if saved_conclusion_id is not None:
            values["saved_conclusion_id"] = saved_conclusion_id
        await session.execute(
            sa.update(ResearchConclusionCandidate)
            .where(ResearchConclusionCandidate.id == candidate_id)
            .values(**values)
        )


# ============================================================
# Conclusion repository
# ============================================================


class ConclusionRepository:
    """Conclusion and revision persistence."""

    @staticmethod
    async def insert_conclusion(
        session: AsyncSession,
        *,
        workspace_id: UUID,
        source_turn_id: UUID | None,
        source_run_id: UUID | None,
        source_candidate_id: UUID | None,
        source_type: str,
        evidence_status: str,
        created_by: UUID,
    ) -> ResearchConclusion:
        """Insert a new conclusion (without current_revision_id yet)."""
        conclusion = ResearchConclusion(
            id=new_id(),
            workspace_id=workspace_id,
            source_turn_id=source_turn_id,
            source_run_id=source_run_id,
            source_candidate_id=source_candidate_id,
            source_type=source_type,
            evidence_status=evidence_status,
            status="active",
            created_by=created_by,
            lock_version=0,
        )
        session.add(conclusion)
        await session.flush()
        return conclusion

    @staticmethod
    async def insert_revision(
        session: AsyncSession,
        *,
        conclusion_id: UUID,
        revision_number: int,
        statement: str,
        scope: str | None,
        evidence_refs: list[Any],
        limitations: str | None,
        editor: UUID,
    ) -> ResearchConclusionRevision:
        """Insert an immutable conclusion revision."""
        revision = ResearchConclusionRevision(
            id=new_id(),
            conclusion_id=conclusion_id,
            revision_number=revision_number,
            statement=statement,
            scope=scope,
            evidence_refs=evidence_refs or [],
            limitations=limitations,
            editor=editor,
        )
        session.add(revision)
        await session.flush()
        return revision

    @staticmethod
    async def set_current_revision(
        session: AsyncSession,
        conclusion_id: UUID,
        revision_id: UUID,
    ) -> None:
        """Set the current revision pointer on a conclusion."""
        await session.execute(
            sa.update(ResearchConclusion)
            .where(ResearchConclusion.id == conclusion_id)
            .values(current_revision_id=revision_id, updated_at=sa.func.now())
        )

    @staticmethod
    async def get_conclusion(
        session: AsyncSession,
        conclusion_id: UUID,
    ) -> ResearchConclusion | None:
        """Get a conclusion by ID."""
        result = await session.execute(
            sa.select(ResearchConclusion).where(ResearchConclusion.id == conclusion_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_revision(
        session: AsyncSession,
        revision_id: UUID,
    ) -> ResearchConclusionRevision | None:
        """Get a revision by ID."""
        result = await session.execute(
            sa.select(ResearchConclusionRevision).where(
                ResearchConclusionRevision.id == revision_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_latest_revision(
        session: AsyncSession,
        conclusion_id: UUID,
    ) -> ResearchConclusionRevision | None:
        """Get the latest (highest-numbered) revision for a conclusion."""
        result = await session.execute(
            sa.select(ResearchConclusionRevision)
            .where(ResearchConclusionRevision.conclusion_id == conclusion_id)
            .order_by(ResearchConclusionRevision.revision_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_revisions(
        session: AsyncSession,
        conclusion_id: UUID,
    ) -> list[ResearchConclusionRevision]:
        """List all revisions for a conclusion, ordered by number."""
        result = await session.execute(
            sa.select(ResearchConclusionRevision)
            .where(ResearchConclusionRevision.conclusion_id == conclusion_id)
            .order_by(ResearchConclusionRevision.revision_number)
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_conclusion_lock(
        session: AsyncSession,
        conclusion_id: UUID,
        expected_lock_version: int,
    ) -> ResearchConclusion:
        """Increment lock_version with optimistic concurrency check.

        Raises:
            AppError: If the lock version doesn't match (state_conflict).
        """
        result = await session.execute(
            sa.update(ResearchConclusion)
            .where(
                ResearchConclusion.id == conclusion_id,
                ResearchConclusion.lock_version == expected_lock_version,
            )
            .values(
                lock_version=expected_lock_version + 1,
                updated_at=sa.func.now(),
            )
            .returning(ResearchConclusion)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise AppError(
                code="state_conflict",
                message=f"Conclusion {conclusion_id} lock version mismatch",
                retryable=True,
                fields={
                    "conclusion_id": str(conclusion_id),
                    "expected_lock": expected_lock_version,
                },
            )
        return row

    @staticmethod
    async def archive_conclusion(
        session: AsyncSession,
        conclusion_id: UUID,
        expected_lock_version: int,
    ) -> None:
        """Archive a conclusion (status -> archived).

        Raises:
            AppError: If the lock version doesn't match.
        """
        result = await session.execute(
            sa.update(ResearchConclusion)
            .where(
                ResearchConclusion.id == conclusion_id,
                ResearchConclusion.lock_version == expected_lock_version,
            )
            .values(
                status="archived",
                lock_version=expected_lock_version + 1,
                updated_at=sa.func.now(),
            )
            .returning(ResearchConclusion.id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise AppError(
                code="state_conflict",
                message=f"Conclusion {conclusion_id} lock version mismatch",
                retryable=True,
                fields={
                    "conclusion_id": str(conclusion_id),
                    "expected_lock": expected_lock_version,
                },
            )

    @staticmethod
    async def list_conclusions(
        session: AsyncSession,
        workspace_id: UUID,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> tuple[list[ResearchConclusion], str | None]:
        """List active conclusions in a workspace, cursor-paginated by (updated_at, id) descending."""
        if page_size < 1 or page_size > 50:
            raise AppError(
                code="validation_failed",
                message="page_size must be 1-50",
                retryable=False,
                fields={"page_size": page_size},
            )

        stmt = (
            sa.select(ResearchConclusion)
            .where(
                ResearchConclusion.workspace_id == workspace_id,
                ResearchConclusion.status == "active",
            )
            .order_by(
                ResearchConclusion.updated_at.desc(),
                ResearchConclusion.id.desc(),
            )
            .limit(page_size + 1)
        )

        if cursor is not None:
            cursor_time, cursor_id = decode_conclusion_cursor(cursor)
            stmt = stmt.where(
                sa.or_(
                    ResearchConclusion.updated_at < cursor_time,
                    sa.and_(
                        ResearchConclusion.updated_at == cursor_time,
                        ResearchConclusion.id < cursor_id,
                    ),
                )
            )

        result = await session.execute(stmt)
        rows = list(result.scalars().all())

        has_next = len(rows) > page_size
        if has_next:
            rows = rows[:page_size]

        next_cursor = None
        if has_next and rows:
            last = rows[-1]
            next_cursor = encode_conclusion_cursor(last.updated_at, last.id)

        return rows, next_cursor

    @staticmethod
    async def count_conclusions_by_workspace(
        session: AsyncSession,
        workspace_id: UUID,
    ) -> int:
        """Count active conclusions in a workspace."""
        result = await session.execute(
            sa.select(sa.func.count())
            .select_from(ResearchConclusion)
            .where(
                ResearchConclusion.workspace_id == workspace_id,
                ResearchConclusion.status == "active",
            )
        )
        return result.scalar_one()
