"""Timeline query service: assembles timeline pages and turn details.

Uses two-phase pagination to avoid JOIN amplification:
  Phase 1: keyset query for Turn IDs only (page_size + 1 probe)
  Phase 2: batch-load related data for the current page only

Default page returns card summaries; full detail via get_turn_detail().
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.timeline.access import require_owned_workspace
from packages.research.timeline.contracts import (
    ConclusionRef,
    FixedConclusionInput,
    FixedTurnContext,
    TimelinePage,
    TimelineTurnCard,
    TurnDetail,
    TurnRef,
)
from packages.research.timeline.entities import (
    CandidateExtractionJob,
    ResearchConclusion,
    ResearchConclusionCandidate,
    ResearchConclusionRevision,
    ResearchTurnContext,
    ResearchTurnResult,
)
from packages.research.timeline.repository import (
    DEFAULT_PAGE_SIZE,
    TimelineRepository,
    validate_page_size,
)

logger = logging.getLogger("research.timeline_query")


class TimelineQueryService(ScopedSessionMixin):
    """Read-only service for timeline pages and turn details.

    Depends on session_factory, department_id, actor_id — identity-aware
    to enforce workspace ownership via RLS.
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

    async def list_timeline(
        self,
        workspace_id: UUID,
        cursor: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> TimelinePage:
        """List timeline turns (card summaries) with cursor pagination.

        Two-phase loading:
          1. Keyset query for Turn rows (page_size + 1 probe)
          2. For each turn: count context rows and check for results

        Args:
            workspace_id: Workspace ID.
            cursor: Opaque cursor from previous page.
            page_size: Items per page (1-50).

        Returns:
            TimelinePage with items, next_cursor, and active_run_status.
        """
        page_size = validate_page_size(page_size)

        async with self._scoped_session() as session:
            await require_owned_workspace(session, workspace_id, self._actor_id)
            # Phase 1: keyset query for turns (1 query)
            turns, next_cursor = await TimelineRepository.list_turns(
                session,
                workspace_id=workspace_id,
                cursor=cursor,
                page_size=page_size,
            )

            if not turns:
                return TimelinePage(
                    items=[], next_cursor=None, active_run_status=None
                )

            turn_ids = [t.id for t in turns]

            # Phase 2: batch-load card metadata (4 queries, not 4*N)
            # 2a: context counts grouped by turn_id
            ctx_rows = await session.execute(
                sa.select(
                    ResearchTurnContext.turn_id,
                    sa.func.count().label("cnt"),
                )
                .where(ResearchTurnContext.turn_id.in_(turn_ids))
                .group_by(ResearchTurnContext.turn_id)
            )
            ctx_counts: dict[UUID, int] = {
                row[0]: row[1] for row in ctx_rows
            }

            # 2b: result existence (turn_ids with at least one result)
            result_rows = await session.execute(
                sa.select(ResearchTurnResult.turn_id)
                .where(ResearchTurnResult.turn_id.in_(turn_ids))
                .distinct()
            )
            has_result_set: set[UUID] = {row[0] for row in result_rows}

            # 2c: candidate existence
            cand_rows = await session.execute(
                sa.select(ResearchConclusionCandidate.turn_id)
                .where(ResearchConclusionCandidate.turn_id.in_(turn_ids))
                .distinct()
            )
            has_candidates_set: set[UUID] = {row[0] for row in cand_rows}

            # 2d: snapshot numbers
            from packages.research.entities import ResearchEvidenceSnapshot

            snapshot_ids = [t.evidence_snapshot_id for t in turns]
            snap_rows = await session.execute(
                sa.select(
                    ResearchEvidenceSnapshot.id,
                    ResearchEvidenceSnapshot.snapshot_number,
                ).where(ResearchEvidenceSnapshot.id.in_(snapshot_ids))
            )
            snap_map: dict[UUID, int] = {row[0]: row[1] for row in snap_rows}

            # Build cards (pure Python, no DB queries)
            cards: list[TimelineTurnCard] = []
            for turn in turns:
                cards.append(
                    TimelineTurnCard(
                        turn_id=turn.id,
                        turn_number=turn.turn_number,
                        kind=turn.kind,
                        status=turn.status,
                        question_text=turn.question_text_snapshot,
                        question_origin=turn.question_origin,
                        snapshot_number=snap_map.get(
                            turn.evidence_snapshot_id, 0
                        ),
                        selected_conclusion_count=ctx_counts.get(turn.id, 0),
                        created_at=turn.created_at,
                        has_result=turn.id in has_result_set,
                        has_candidates=turn.id in has_candidates_set,
                    )
                )

            # Check active run status
            active_status = await TimelineRepository.get_active_run_status(session, workspace_id)

            return TimelinePage(
                items=cards,
                next_cursor=next_cursor,
                active_run_status=active_status,
            )

    async def get_turn_detail(
        self,
        workspace_id: UUID,
        turn_id: UUID,
    ) -> TurnDetail:
        """Get full turn detail for recovery and expanded view.

        Includes: fixed inputs, plan, run status, result, extraction status,
        candidates, and saved conclusions.

        Args:
            workspace_id: Workspace ID (for ownership check).
            turn_id: Turn ID.

        Returns:
            TurnDetail with all related data.

        Raises:
            AppError: not_found if turn doesn't exist or doesn't belong to workspace.
        """
        async with self._scoped_session() as session:
            await require_owned_workspace(session, workspace_id, self._actor_id)
            turn = await TimelineRepository.get_turn(session, turn_id)
            if turn is None or turn.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="Turn not found",
                    retryable=False,
                    fields={"turn_id": str(turn_id)},
                )

            # Load context
            context_rows = await TimelineRepository.list_turn_context(session, turn_id)

            # Load selected conclusions
            selected_conclusions: list[FixedConclusionInput] = []
            for ctx_row in context_rows:
                revision = await session.get(
                    ResearchConclusionRevision, ctx_row.conclusion_revision_id
                )
                if revision is None:
                    continue
                conclusion = await session.get(ResearchConclusion, revision.conclusion_id)
                if conclusion is None:
                    continue
                selected_conclusions.append(
                    FixedConclusionInput(
                        revision_id=revision.id,
                        statement=revision.statement,
                        scope=revision.scope,
                        limitations=revision.limitations,
                        source_type=conclusion.source_type,
                        evidence_status=conclusion.evidence_status,
                        source_turn_id=conclusion.source_turn_id,
                        source_run_id=conclusion.source_run_id,
                        source_snapshot_id=None,
                    )
                )

            # Load result
            result_row = await TimelineRepository.get_turn_result(session, turn_id)
            result_dict: dict[str, Any] | None = None
            if result_row is not None:
                result_dict = {
                    "result_kind": result_row.result_kind,
                    "summary": result_row.summary,
                    "structured_output": result_row.structured_output,
                    "method_summary": result_row.method_summary,
                    "evidence_refs": result_row.evidence_refs,
                    "limitations": result_row.limitations,
                }

            # Load extraction job

            extraction_row = await session.execute(
                sa.select(CandidateExtractionJob)
                .where(CandidateExtractionJob.turn_id == turn_id)
                .limit(1)
            )
            extraction = extraction_row.scalar_one_or_none()
            extraction_status = extraction.status if extraction else None

            # Load candidates
            candidates: list[dict[str, Any]] = []
            cand_result = await session.execute(
                sa.select(ResearchConclusionCandidate)
                .where(ResearchConclusionCandidate.turn_id == turn_id)
                .order_by(ResearchConclusionCandidate.ordinal)
            )
            for cand in cand_result.scalars():
                candidates.append(
                    {
                        "candidate_id": str(cand.id),
                        "ordinal": cand.ordinal,
                        "statement": cand.statement,
                        "scope": cand.scope,
                        "confidence_level": cand.confidence_level,
                        "limitations": cand.limitations,
                        "status": cand.status,
                    }
                )

            # Load saved conclusions for this turn
            saved_result = await session.execute(
                sa.select(ResearchConclusion).where(ResearchConclusion.source_turn_id == turn_id)
            )
            saved_conclusions: list[ConclusionRef] = []
            for concl in saved_result.scalars():
                rev_result = await session.execute(
                    sa.select(ResearchConclusionRevision)
                    .where(ResearchConclusionRevision.conclusion_id == concl.id)
                    .order_by(ResearchConclusionRevision.revision_number.desc())
                    .limit(1)
                )
                rev = rev_result.scalar_one_or_none()
                saved_conclusions.append(
                    ConclusionRef(
                        conclusion_id=concl.id,
                        workspace_id=concl.workspace_id,
                        source_type=concl.source_type,
                        evidence_status=concl.evidence_status,
                        status=concl.status,
                        revision_number=rev.revision_number if rev else 0,
                        statement=rev.statement if rev else "",
                    )
                )

            turn_ref = TurnRef(
                turn_id=turn.id,
                workspace_id=turn.workspace_id,
                turn_number=turn.turn_number,
                kind=turn.kind,
                status=turn.status,
                question_text=turn.question_text_snapshot,
                question_origin=turn.question_origin,
                evidence_snapshot_id=turn.evidence_snapshot_id,
            )

            context = FixedTurnContext(
                turn_id=turn.id,
                question_text=turn.question_text_snapshot,
                question_origin=turn.question_origin,
                evidence_snapshot_id=turn.evidence_snapshot_id,
                prompt_template_version=turn.prompt_template_version,
                output_schema_version=turn.output_schema_version,
            )

            return TurnDetail(
                turn=turn_ref,
                context=context,
                selected_conclusions=selected_conclusions,
                plan=None,  # Task 6 will populate
                run_status=None,  # Task 7 will populate
                result=result_dict,
                extraction_status=extraction_status,
                candidates=candidates,
                saved_conclusions=saved_conclusions,
                access_restricted=False,
            )

    async def get_turn_detail_api(
        self,
        workspace_id: UUID,
        turn_id: UUID,
    ) -> dict[str, Any]:
        """Get turn detail as a dict for API response (includes fact_context).

        This is the API-facing version that returns a plain dict with
        fact_context for ChartRefBlock rendering. Use get_turn_detail() for
        the typed contract version.

        Args:
            workspace_id: Workspace ID (for ownership check).
            turn_id: Turn ID.

        Returns:
            Dict with turn, selected_conclusions, result, fact_context,
            extraction_status, candidates, saved_conclusions, access_restricted.

        Raises:
            AppError: not_found if turn doesn't exist or doesn't belong to workspace.
        """
        async with self._scoped_session() as session:
            await require_owned_workspace(session, workspace_id, self._actor_id)
            turn = await TimelineRepository.get_turn(session, turn_id)
            if turn is None or turn.workspace_id != workspace_id:
                raise AppError(
                    code="not_found",
                    message="Turn not found",
                    retryable=False,
                    fields={"turn_id": str(turn_id)},
                )

            # Selected conclusions
            ctx_result = await session.execute(
                sa.select(ResearchTurnContext)
                .where(ResearchTurnContext.turn_id == turn_id)
                .order_by(ResearchTurnContext.position)
            )
            selected: list[dict[str, Any]] = []
            for ctx in ctx_result.scalars():
                rev = await session.get(ResearchConclusionRevision, ctx.conclusion_revision_id)
                if rev is not None:
                    concl = await session.get(ResearchConclusion, rev.conclusion_id)
                    selected.append(
                        {
                            "revision_id": str(ctx.conclusion_revision_id),
                            "statement": rev.statement,
                            "source_type": concl.source_type if concl else "manual",
                            "evidence_status": concl.evidence_status
                            if concl
                            else "manual_unverified",
                        }
                    )

            # Candidates
            cand_result = await session.execute(
                sa.select(ResearchConclusionCandidate)
                .where(ResearchConclusionCandidate.turn_id == turn_id)
                .order_by(ResearchConclusionCandidate.ordinal)
            )
            candidates = [
                {
                    "candidate_id": str(c.id),
                    "ordinal": c.ordinal,
                    "statement": c.statement,
                    "scope": c.scope,
                    "confidence_level": c.confidence_level,
                    "limitations": c.limitations,
                    "status": c.status,
                }
                for c in cand_result.scalars()
            ]

            # Saved conclusions (for this workspace, active only)
            concl_result = await session.execute(
                sa.select(ResearchConclusion).where(
                    ResearchConclusion.workspace_id == workspace_id,
                    ResearchConclusion.status == "active",
                )
            )
            saved: list[dict[str, Any]] = []
            for concl in concl_result.scalars():
                rev = (
                    await session.get(ResearchConclusionRevision, concl.current_revision_id)
                    if concl.current_revision_id
                    else None
                )
                saved.append(
                    {
                        "conclusion_id": str(concl.id),
                        "workspace_id": str(concl.workspace_id),
                        "source_type": concl.source_type,
                        "evidence_status": concl.evidence_status,
                        "status": concl.status,
                        "revision_number": rev.revision_number if rev else 0,
                        "statement": rev.statement if rev else "",
                    }
                )

            # Turn result
            result: dict[str, Any] | None = None
            result_row = await session.execute(
                sa.select(ResearchTurnResult).where(ResearchTurnResult.turn_id == turn_id)
            )
            tr = result_row.scalar_one_or_none()
            if tr is not None:
                result = {
                    "summary": tr.summary,
                    "structured_output": tr.structured_output,
                    "method_summary": tr.method_summary,
                }

            # Load fact_samples (structured) + fact_context (text, for backward compat)
            from packages.research.timeline.fact_data_loader import FactDataLoader

            fact_loader = FactDataLoader(
                self._factory, self._dept_id, self._actor_id
            )
            fact_samples = await fact_loader.load_fact_samples(session, workspace_id)

            return {
                "turn": {
                    "turn_id": str(turn.id),
                    "workspace_id": str(turn.workspace_id),
                    "turn_number": turn.turn_number,
                    "kind": turn.kind,
                    "status": turn.status,
                    "question_text": turn.question_text_snapshot,
                    "question_origin": turn.question_origin,
                    "evidence_snapshot_id": str(turn.evidence_snapshot_id),
                },
                "selected_conclusions": selected,
                "result": result,
                "fact_samples": fact_samples,
                "extraction_status": None,
                "candidates": candidates,
                "saved_conclusions": saved,
                "access_restricted": False,
            }
