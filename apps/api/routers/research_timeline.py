"""Research timeline HTTP API router.

All paths under /api/v1/research.  Timeline routes are gated by
RESEARCH_MODULE_ENABLED && RESEARCH_TIMELINE_ENABLED.

New routes:
  GET    /workspaces/{id}/timeline          — paged timeline
  GET    /workspaces/{id}/turns/{turn_id}     — turn detail
  POST   /workspaces/{id}/turns               — create analysis turn
  POST   /workspaces/{id}/synthesis-turns     — create synthesis turn
  POST   /workspaces/{id}/recommendations/followup — followup recommendations
  GET    /workspaces/{id}/recommendation-batches/{batch_id} — batch status
  POST   /workspaces/{id}/recommendation-batches/{batch_id}/retry — retry
  POST   /workspaces/{id}/turns/{turn_id}/plan — start planning
  POST   /workspaces/{id}/turns/{turn_id}/conclusions/from-candidates — save
  POST   /workspaces/{id}/turns/{turn_id}/conclusion-review/complete — finish
  POST   /workspaces/{id}/conclusions/manual — manual conclusion
  PATCH  /workspaces/{id}/conclusions/{conclusion_id} — revise
  POST   /workspaces/{id}/conclusions/{conclusion_id}/archive — archive
  GET    /workspaces/{id}/conclusions — conclusion library
"""

import logging
from typing import Annotated
from uuid import UUID

import fastapi
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.research.timeline.contracts import (
    CreateManualConclusionCommand,
    CreateSynthesisTurnCommand,
    CreateTurnCommand,
    ReviseConclusionCommand,
)

ResearchUserDep = Annotated[CurrentUser, Depends(require_permission("research:use"))]

# ---- DI placeholders (overridden by composition/research.py) ----


def get_timeline_query_service() -> None:
    raise NotImplementedError("overridden by composition")


def get_turn_service() -> None:
    raise NotImplementedError("overridden by composition")


def get_conclusion_service() -> None:
    raise NotImplementedError("overridden by composition")


def get_recommendation_service() -> None:
    raise NotImplementedError("overridden by composition")


TimelineQueryDep = Annotated[object, Depends(get_timeline_query_service)]
TurnServiceDep = Annotated[object, Depends(get_turn_service)]
ConclusionServiceDep = Annotated[object, Depends(get_conclusion_service)]
RecommendationServiceDep = Annotated[object, Depends(get_recommendation_service)]


# ---- Router ----

research_timeline_router = APIRouter(prefix="/api/v1/research", tags=["research-timeline"])


# ---- Request models ----


class CreateTurnRequest(BaseModel):
    question_text: str = Field(..., min_length=1, max_length=4096)
    evidence_snapshot_id: str
    selected_conclusion_revision_ids: list[str] = Field(default_factory=list, max_length=20)
    recommendation_item_id: str | None = None
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class CreateSynthesisTurnRequest(BaseModel):
    evidence_snapshot_id: str
    selected_conclusion_revision_ids: list[str] = Field(..., min_length=2, max_length=20)
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class FollowupRecommendationRequest(BaseModel):
    snapshot_id: str
    selected_conclusion_revision_ids: list[str] = Field(default_factory=list, max_length=20)
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class SaveCandidatesRequest(BaseModel):
    selections: list[dict] = Field(..., min_length=1, max_length=20)
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class ManualConclusionRequest(BaseModel):
    statement: str = Field(..., min_length=1, max_length=12000)
    scope: str | None = None
    limitations: str | None = None
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class ReviseConclusionRequest(BaseModel):
    statement: str = Field(..., min_length=1, max_length=12000)
    scope: str | None = None
    limitations: str | None = None
    expected_lock_version: int


class ConclusionReviewCompleteRequest(BaseModel):
    pass


# ---- Response models ----


class TimelineItemResponse(BaseModel):
    turn_id: str
    turn_number: int
    kind: str
    status: str
    question_text: str
    question_origin: str
    snapshot_number: int
    selected_conclusion_count: int
    has_result: bool
    has_candidates: bool
    created_at: str


class TimelinePageResponse(BaseModel):
    items: list[TimelineItemResponse]
    next_cursor: str | None
    active_run_status: str | None


class TurnRefResponse(BaseModel):
    turn_id: str
    workspace_id: str
    turn_number: int
    kind: str
    status: str
    question_text: str
    question_origin: str
    evidence_snapshot_id: str


class BatchRefResponse(BaseModel):
    batch_id: str
    workspace_id: str
    status: str
    item_count: int


class RecommendationItemResponse(BaseModel):
    id: str
    question: str
    rationale: str


class RecommendationBatchResponse(BaseModel):
    batch_id: str
    workspace_id: str
    status: str
    items: list[RecommendationItemResponse]


class ConclusionRefResponse(BaseModel):
    conclusion_id: str
    workspace_id: str
    source_type: str
    evidence_status: str
    status: str
    revision_number: int
    statement: str


# ---- Endpoints ----


@research_timeline_router.get(
    "/workspaces/{workspace_id}/timeline",
    response_model=TimelinePageResponse,
)
async def list_timeline(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    service: TimelineQueryDep,
    cursor: str | None = Query(None),
    page_size: int = Query(20, ge=1, le=50),
) -> TimelinePageResponse:
    """Paged timeline of research turns (descending by turn_number, id)."""
    page = await service.list_timeline(workspace_id, cursor, page_size)
    return TimelinePageResponse(
        items=[
            TimelineItemResponse(
                turn_id=str(c.turn_id),
                turn_number=c.turn_number,
                kind=c.kind,
                status=c.status,
                question_text=c.question_text,
                question_origin=c.question_origin,
                snapshot_number=c.snapshot_number,
                selected_conclusion_count=c.selected_conclusion_count,
                has_result=c.has_result,
                has_candidates=c.has_candidates,
                created_at=c.created_at.isoformat() if c.created_at else "",
            )
            for c in page.items
        ],
        next_cursor=page.next_cursor,
        active_run_status=page.active_run_status,
    )


@research_timeline_router.get(
    "/workspaces/{workspace_id}/recommendations/active",
    response_model=RecommendationBatchResponse,
)
async def get_active_recommendation(
    workspace_id: UUID,
    current_user: ResearchUserDep,
) -> RecommendationBatchResponse:
    """Get the latest recommendation batch and its items for a workspace."""
    import os

    import sqlalchemy as sa

    from packages.common.database import build_session_factory
    from packages.research.timeline.entities import (
        ResearchRecommendationBatch,
        ResearchRecommendationItem,
    )

    db_url = os.environ.get(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
    )
    factory = build_session_factory(db_url)
    async with factory() as session:
        # Get latest batch
        result = await session.execute(
            sa.select(ResearchRecommendationBatch)
            .where(ResearchRecommendationBatch.workspace_id == workspace_id)
            .order_by(ResearchRecommendationBatch.created_at.desc())
            .limit(1)
        )
        batch = result.scalar_one_or_none()
        if batch is None:
            return RecommendationBatchResponse(
                batch_id="",
                workspace_id=str(workspace_id),
                status="none",
                items=[],
            )

        # Get items
        items_result = await session.execute(
            sa.select(ResearchRecommendationItem)
            .where(ResearchRecommendationItem.batch_id == batch.id)
            .order_by(ResearchRecommendationItem.position)
        )
        items = [
            RecommendationItemResponse(
                id=str(item.id),
                question=item.question,
                rationale=item.rationale or "",
            )
            for item in items_result.scalars()
        ]

        return RecommendationBatchResponse(
            batch_id=str(batch.id),
            workspace_id=str(batch.workspace_id),
            status=batch.status,
            items=items,
        )


@research_timeline_router.post(
    "/workspaces/{workspace_id}/recommendations/{batch_id}/retry",
    response_model=BatchRefResponse,
)
async def retry_recommendation(
    workspace_id: UUID,
    batch_id: UUID,
    current_user: ResearchUserDep,
) -> BatchRefResponse:
    """Retry a failed recommendation batch."""
    import os

    import sqlalchemy as sa

    from packages.common.database import build_session_factory
    from packages.research.timeline.entities import ResearchRecommendationBatch
    from packages.research.timeline.state_machine import (
        RecommendationBatchStateMachine,
    )

    db_url = os.environ.get(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
    )
    factory = build_session_factory(db_url)
    async with factory() as session:
        result = await session.execute(
            sa.select(ResearchRecommendationBatch).where(
                ResearchRecommendationBatch.id == batch_id,
                ResearchRecommendationBatch.workspace_id == workspace_id,
            )
        )
        batch = result.scalar_one_or_none()
        if batch is None:
            from packages.common.errors import AppError

            raise AppError(
                code="not_found",
                message="Recommendation batch not found",
                retryable=False,
            )

        # Transition to queued for retry
        batch.status = RecommendationBatchStateMachine.transition(batch.status, "retry")
        await session.commit()

        # Create outbox event
        from packages.jobs.outbox import OutboxEvent

        async with factory() as session2:
            await OutboxEvent.enqueue(
                session2,
                aggregate_type="research_recommendation_batch",
                aggregate_id=batch.id,
                event_type="research.recommendation.requested",
                payload={"batch_id": str(batch.id), "mode": batch.mode},
            )
            await session2.commit()

        return BatchRefResponse(
            batch_id=str(batch.id),
            workspace_id=str(batch.workspace_id),
            status=batch.status,
            item_count=0,
        )


@research_timeline_router.post(
    "/workspaces/{workspace_id}/turns",
    response_model=TurnRefResponse,
    status_code=201,
)
async def create_turn(
    workspace_id: UUID,
    body: CreateTurnRequest,
    current_user: ResearchUserDep,
    service: TurnServiceDep,
) -> TurnRefResponse:
    """Create an analysis research turn."""
    command = CreateTurnCommand(
        workspace_id=workspace_id,
        question_text=body.question_text,
        evidence_snapshot_id=UUID(body.evidence_snapshot_id),
        selected_conclusion_revision_ids=tuple(
            UUID(rid) for rid in body.selected_conclusion_revision_ids
        ),
        recommendation_item_id=UUID(body.recommendation_item_id)
        if body.recommendation_item_id
        else None,
        idempotency_key=body.idempotency_key,
    )
    ref = await service.create_analysis_turn(command)
    return TurnRefResponse(
        turn_id=str(ref.turn_id),
        workspace_id=str(ref.workspace_id),
        turn_number=ref.turn_number,
        kind=ref.kind,
        status=ref.status,
        question_text=ref.question_text,
        question_origin=ref.question_origin,
        evidence_snapshot_id=str(ref.evidence_snapshot_id),
    )


@research_timeline_router.post(
    "/workspaces/{workspace_id}/synthesis-turns",
    response_model=TurnRefResponse,
    status_code=201,
)
async def create_synthesis_turn(
    workspace_id: UUID,
    body: CreateSynthesisTurnRequest,
    current_user: ResearchUserDep,
    service: TurnServiceDep,
) -> TurnRefResponse:
    """Create a synthesis research turn."""
    command = CreateSynthesisTurnCommand(
        workspace_id=workspace_id,
        evidence_snapshot_id=UUID(body.evidence_snapshot_id),
        selected_conclusion_revision_ids=tuple(
            UUID(rid) for rid in body.selected_conclusion_revision_ids
        ),
        idempotency_key=body.idempotency_key,
    )
    ref = await service.create_synthesis_turn(command)
    return TurnRefResponse(
        turn_id=str(ref.turn_id),
        workspace_id=str(ref.workspace_id),
        turn_number=ref.turn_number,
        kind=ref.kind,
        status=ref.status,
        question_text=ref.question_text,
        question_origin=ref.question_origin,
        evidence_snapshot_id=str(ref.evidence_snapshot_id),
    )


@research_timeline_router.post(
    "/workspaces/{workspace_id}/recommendations/followup",
    response_model=BatchRefResponse,
)
async def request_followup(
    workspace_id: UUID,
    body: FollowupRecommendationRequest,
    current_user: ResearchUserDep,
    service: RecommendationServiceDep,
) -> BatchRefResponse:
    """Request followup recommendations (帮我想下一步)."""
    ref = await service.request_followup(
        workspace_id=workspace_id,
        snapshot_id=UUID(body.snapshot_id),
        selected_revision_ids=tuple(UUID(rid) for rid in body.selected_conclusion_revision_ids),
        idempotency_key=body.idempotency_key,
    )
    return BatchRefResponse(
        batch_id=str(ref.batch_id),
        workspace_id=str(ref.workspace_id),
        status=ref.status,
        item_count=ref.item_count,
    )


@research_timeline_router.post(
    "/workspaces/{workspace_id}/conclusions/manual",
    response_model=ConclusionRefResponse,
    status_code=201,
)
async def create_manual_conclusion(
    workspace_id: UUID,
    body: ManualConclusionRequest,
    current_user: ResearchUserDep,
    service: ConclusionServiceDep,
) -> ConclusionRefResponse:
    """Create a manual (no-evidence) conclusion."""
    command = CreateManualConclusionCommand(
        workspace_id=workspace_id,
        statement=body.statement,
        idempotency_key=body.idempotency_key,
        scope=body.scope,
        limitations=body.limitations,
    )
    ref = await service.create_manual(command)
    return ConclusionRefResponse(
        conclusion_id=str(ref.conclusion_id),
        workspace_id=str(ref.workspace_id),
        source_type=ref.source_type,
        evidence_status=ref.evidence_status,
        status=ref.status,
        revision_number=ref.revision_number,
        statement=ref.statement,
    )


@research_timeline_router.patch(
    "/workspaces/{workspace_id}/conclusions/{conclusion_id}",
    response_model=ConclusionRefResponse,
)
async def revise_conclusion(
    workspace_id: UUID,
    conclusion_id: UUID,
    body: ReviseConclusionRequest,
    current_user: ResearchUserDep,
    service: ConclusionServiceDep,
) -> ConclusionRefResponse:
    """Revise a conclusion (creates a new immutable revision)."""
    command = ReviseConclusionCommand(
        workspace_id=workspace_id,
        conclusion_id=conclusion_id,
        statement=body.statement,
        expected_lock_version=body.expected_lock_version,
        scope=body.scope,
        limitations=body.limitations,
    )
    ref = await service.revise(command)
    return ConclusionRefResponse(
        conclusion_id=str(ref.conclusion_id),
        workspace_id=str(ref.workspace_id),
        source_type=ref.source_type,
        evidence_status=ref.evidence_status,
        status=ref.status,
        revision_number=ref.revision_number,
        statement=ref.statement,
    )


# ---- Turn detail + plan endpoints ----


@research_timeline_router.get(
    "/workspaces/{workspace_id}/turns/{turn_id}",
)
async def get_turn_detail(
    workspace_id: UUID,
    turn_id: UUID,
    current_user: ResearchUserDep,
) -> dict:
    """Get detailed information about a single research turn."""
    import os

    import sqlalchemy as sa

    from packages.common.database import build_session_factory
    from packages.research.timeline.entities import (
        ResearchConclusion,
        ResearchConclusionCandidate,
        ResearchConclusionRevision,
        ResearchTurn,
        ResearchTurnContext,
        ResearchTurnResult,
    )

    db_url = os.environ.get(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
    )
    factory = build_session_factory(db_url)
    async with factory() as session:
        turn = await session.get(ResearchTurn, turn_id)
        if turn is None or turn.workspace_id != workspace_id:
            from packages.common.errors import AppError

            raise AppError(code="not_found", message="Turn not found", retryable=False)

        # Selected conclusions
        ctx_result = await session.execute(
            sa.select(ResearchTurnContext)
            .where(ResearchTurnContext.turn_id == turn_id)
            .order_by(ResearchTurnContext.position)
        )
        selected = []
        for ctx in ctx_result.scalars():
            rev = await session.get(ResearchConclusionRevision, ctx.conclusion_revision_id)
            if rev is not None:
                concl = await session.get(ResearchConclusion, rev.conclusion_id)
                selected.append(
                    {
                        "revision_id": str(ctx.conclusion_revision_id),
                        "statement": rev.statement,
                        "source_type": concl.source_type if concl else "manual",
                        "evidence_status": concl.evidence_status if concl else "manual_unverified",
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

        # Saved conclusions
        concl_result = await session.execute(
            sa.select(ResearchConclusion).where(
                ResearchConclusion.workspace_id == workspace_id,
                ResearchConclusion.status == "active",
            )
        )
        saved = []
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

        # Get turn result
        result = None
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

        # Load fact_data for chart-ref rendering (ChartRefBlock needs systemContext)
        fact_context = None
        try:
            import json as _json

            from packages.common.database import build_session_factory as _bsf
            from packages.research.entities import WorkspaceEvidenceRef

            analysis_db_url = os.environ.get(
                "IRIP_ALEMBIC_DATABASE_URL",
                "postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip",
            )
            analysis_factory = _bsf(analysis_db_url)
            async with analysis_factory() as fact_session:
                refs_result = await fact_session.execute(
                    sa.select(WorkspaceEvidenceRef).where(
                        WorkspaceEvidenceRef.workspace_id == workspace_id,
                        WorkspaceEvidenceRef.status == "active",
                    )
                )
                refs = refs_result.scalars().all()

                if refs:
                    from apps.api.main import _build_s3_repo
                    from packages.facts.query_service import FactQueryService
                    from packages.research.lineage.core_adapter import CoreFactProviderImpl

                    user_result = await fact_session.execute(
                        sa.text(
                            "SELECT id, department_id FROM app_user WHERE email = 'admin@irip.local' LIMIT 1"
                        )
                    )
                    user_row = user_result.first()

                    if user_row:
                        s3_repo = _build_s3_repo()
                        fact_query = FactQueryService(
                            session_factory=analysis_factory,
                            department_id=user_row[1],
                            actor_id=user_row[0],
                            s3_repo=s3_repo,
                        )
                        fact_provider = CoreFactProviderImpl(query_service=fact_query)

                        # Build systemContext format: "### 样品: XXX\n```json\n{...}\n```"
                        context_parts = []
                        for ref in refs:
                            data = await fact_provider.get_fact_data(ref.source_id)
                            if isinstance(data, dict):
                                label = ref.source_name or str(ref.source_id)
                                context_parts.append(
                                    f"### 样品: {label}\n```json\n{_json.dumps(data, ensure_ascii=False)}\n```"
                                )
                        if context_parts:
                            fact_context = "\n\n".join(context_parts)
        except Exception:
            pass

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
            "fact_context": fact_context,
            "extraction_status": None,
            "candidates": candidates,
            "saved_conclusions": saved,
            "access_restricted": False,
        }


@research_timeline_router.post(
    "/workspaces/{workspace_id}/turns/{turn_id}/plan",
)
async def start_planning(
    workspace_id: UUID,
    turn_id: UUID,
    current_user: ResearchUserDep,
) -> dict:
    """Start generating an analysis plan for a turn."""
    import os

    from packages.common.database import build_session_factory
    from packages.research.timeline.entities import ResearchTurn

    db_url = os.environ.get(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
    )
    factory = build_session_factory(db_url)
    async with factory() as session:
        turn = await session.get(ResearchTurn, turn_id)
        if turn is None or turn.workspace_id != workspace_id:
            from packages.common.errors import AppError

            raise AppError(code="not_found", message="Turn not found", retryable=False)

        if turn.status == "question_draft":
            turn.status = "running"
            await session.commit()

        return {"turn_id": str(turn.id), "status": turn.status}


@research_timeline_router.post("/extract-text")
async def extract_text_from_file(
    current_user: ResearchUserDep,
    file: Annotated[bytes, fastapi.Form()],
) -> dict:
    """Extract text from uploaded file for background context."""
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tmp:
        tmp.write(file)
        tmp_path = tmp.name

    try:
        with open(tmp_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if len(text) > 10000:
            text = text[:10000]
        return {"text": text}
    finally:
        os.unlink(tmp_path)


@research_timeline_router.delete(
    "/workspaces/{workspace_id}/conclusions/{conclusion_id}",
)
async def delete_conclusion(
    workspace_id: UUID,
    conclusion_id: UUID,
    current_user: ResearchUserDep,
) -> dict:
    """Delete a conclusion (mark as archived)."""
    import os

    import sqlalchemy as sa

    from packages.common.database import build_session_factory
    from packages.research.timeline.entities import ResearchConclusion

    db_url = os.environ.get(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
    )
    factory = build_session_factory(db_url)

    async with factory() as session:
        result = await session.execute(
            sa.select(ResearchConclusion).where(
                ResearchConclusion.id == conclusion_id,
                ResearchConclusion.workspace_id == workspace_id,
            )
        )
        concl = result.scalar_one_or_none()
        if concl is None:
            from packages.common.errors import AppError

            raise AppError(code="not_found", message="Conclusion not found")

        concl.status = "archived"
        await session.commit()

    return {"conclusion_id": str(conclusion_id), "status": "archived"}


@research_timeline_router.get(
    "/workspaces/{workspace_id}/conclusions",
)
async def list_conclusions(
    workspace_id: UUID,
    current_user: ResearchUserDep,
) -> dict:
    """List all active conclusions for a workspace."""
    import os

    import sqlalchemy as sa

    from packages.common.database import build_session_factory
    from packages.research.timeline.entities import (
        ResearchConclusion,
        ResearchConclusionRevision,
    )

    db_url = os.environ.get(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
    )
    factory = build_session_factory(db_url)

    async with factory() as session:
        result = await session.execute(
            sa.select(ResearchConclusion).where(
                ResearchConclusion.workspace_id == workspace_id,
                ResearchConclusion.status == "active",
            )
        )
        items = []
        for concl in result.scalars():
            rev = None
            if concl.current_revision_id:
                rev = await session.get(ResearchConclusionRevision, concl.current_revision_id)
            items.append(
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

        return {"items": items}


@research_timeline_router.post(
    "/workspaces/{workspace_id}/turns/{turn_id}/save-conclusion",
    status_code=201,
)
async def save_as_conclusion(
    workspace_id: UUID,
    turn_id: UUID,
    current_user: ResearchUserDep,
    body: dict,
) -> dict:
    """Save a table/chart/structured data block as a conclusion."""
    import os
    import uuid

    import sqlalchemy as sa

    from packages.common.database import build_session_factory
    from packages.research.timeline.entities import (
        ResearchConclusion,
        ResearchConclusionRevision,
        ResearchTurn,
    )

    db_url = os.environ.get(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
    )
    factory = build_session_factory(db_url)

    statement = body.get("statement", "")
    if not statement.strip():
        from packages.common.errors import AppError

        raise AppError(code="validation_failed", message="结论内容不能为空")

    scope = body.get("scope")
    limitations = body.get("limitations")
    block_type = body.get("block_type", "table")  # table | chart | structured

    async with factory() as session:
        # Verify turn belongs to workspace
        turn = await session.get(ResearchTurn, turn_id)
        if turn is None or turn.workspace_id != workspace_id:
            from packages.common.errors import AppError

            raise AppError(code="not_found", message="Turn not found")

        # Get admin user ID
        user_result = await session.execute(
            sa.text("SELECT id FROM app_user WHERE email = 'admin@irip.local' LIMIT 1")
        )
        user_row = user_result.first()
        if not user_row:
            from packages.common.errors import AppError

            raise AppError(code="not_found", message="User not found")

        # Create conclusion
        concl_id = uuid.uuid4()
        rev_id = uuid.uuid4()

        conclusion = ResearchConclusion(
            id=concl_id,
            workspace_id=workspace_id,
            source_turn_id=turn_id,
            source_type="ai_original",
            evidence_status="data_supported",
            status="active",
            created_by=user_row[0],
            lock_version=0,
        )
        session.add(conclusion)
        await session.flush()

        revision = ResearchConclusionRevision(
            id=rev_id,
            conclusion_id=concl_id,
            revision_number=1,
            statement=statement,
            scope=scope,
            limitations=limitations,
            editor=user_row[0],
        )
        session.add(revision)
        await session.flush()

        # Set current revision
        await session.execute(
            sa.update(ResearchConclusion)
            .where(ResearchConclusion.id == concl_id)
            .values(current_revision_id=rev_id)
        )
        await session.commit()

    return {
        "conclusion_id": str(concl_id),
        "statement": statement,
        "status": "saved",
    }


@research_timeline_router.post(
    "/workspaces/{workspace_id}/turns/{turn_id}/analyze",
)
async def run_analysis(
    workspace_id: UUID,
    turn_id: UUID,
    current_user: ResearchUserDep,
) -> dict:
    """Run analysis using old PlanService flow: generate plan → confirm → analyze_data."""
    import os

    import sqlalchemy as sa

    from packages.common.database import build_session_factory
    from packages.research.timeline.entities import ResearchTurn, ResearchTurnResult

    db_url = os.environ.get(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
    )
    factory = build_session_factory(db_url)

    # 1. Load turn
    async with factory() as session:
        turn = await session.get(ResearchTurn, turn_id)
        if turn is None or turn.workspace_id != workspace_id:
            from packages.common.errors import AppError

            raise AppError(code="not_found", message="Turn not found", retryable=False)
        snapshot_id = turn.evidence_snapshot_id
        if turn.status in ("question_draft", "run_failed"):
            turn.status = "running"
            await session.commit()

    # 2. Build PlanService with same deps as old workspace
    analysis_db_url = os.environ.get(
        "IRIP_ALEMBIC_DATABASE_URL",
        "postgresql+psycopg://irip:irip_dev_password@localhost:5432/irip",
    )
    analysis_factory = build_session_factory(analysis_db_url)

    async with analysis_factory() as session:
        user_result = await session.execute(
            sa.text(
                "SELECT id, department_id FROM app_user WHERE email = 'admin@irip.local' LIMIT 1"
            )
        )
        user_row = user_result.first()

    if not user_row:
        from packages.common.errors import AppError

        raise AppError(code="not_found", message="Admin user not found", retryable=False)

    from apps.api.main import _build_s3_repo
    from packages.facts.query_service import FactQueryService
    from packages.research.execution.models_trusted import ModelConfig, TaskType
    from packages.research.lineage.core_adapter import CoreFactProviderImpl
    from packages.research.planning.model_gateway import ModelGateway
    from packages.research.planning.plan_core import PlanService

    s3_repo = _build_s3_repo()
    fact_query = FactQueryService(
        session_factory=analysis_factory,
        department_id=user_row[1],
        actor_id=user_row[0],
        s3_repo=s3_repo,
    )
    fact_provider = CoreFactProviderImpl(query_service=fact_query)

    # Build AI provider
    from apps.api.routers.ai_config import get_active_ai_config, set_session_factory

    set_session_factory(analysis_factory)

    ai_config = await get_active_ai_config()
    if not ai_config or not ai_config.get("base_url") or not ai_config.get("api_key"):
        async with factory() as session:
            turn = await session.get(ResearchTurn, turn_id)
            if turn:
                turn.status = "run_failed"
                await session.commit()
        from packages.common.errors import AppError

        raise AppError(code="ai_config_missing", message="AI not configured", retryable=False)

    from packages.ai.openai_compatible import OpenAICompatibleProvider

    research_model_name = ai_config.get("research_model_name") or ai_config.get("model_name", "")
    thinking = ai_config.get("thinking_enabled", False)
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

    from packages.research.planning.context_router import ContextRouter

    context_router = ContextRouter()

    plan_service = PlanService(
        session_factory=analysis_factory,
        department_id=user_row[1],
        actor_id=user_row[0],
        fact_provider=fact_provider,
        model_gateway=model_gateway,
        context_router=context_router,
    )

    # 3. Generate plan → auto-confirm → analyze
    try:
        plan_ref = await plan_service.generate_plan(workspace_id, snapshot_id)
        await plan_service.confirm_plan(workspace_id, plan_ref.plan_id)
        result = await plan_service.analyze_data(workspace_id, plan_ref.plan_id, snapshot_id)

        analysis_text = result.get("analysis_result", "")

        # 4. Write TurnResult
        async with factory() as session:
            old_result = await session.execute(
                sa.select(ResearchTurnResult).where(ResearchTurnResult.turn_id == turn_id)
            )
            old = old_result.scalar_one_or_none()
            if old is not None:
                await session.delete(old)
                await session.flush()

            result_row = ResearchTurnResult(
                turn_id=turn_id,
                run_id=None,
                result_kind="analysis",
                summary=analysis_text[:500],
                structured_output={"analysis_markdown": analysis_text},
                method_summary="PlanService generate_plan + analyze_data",
                evidence_refs=[],
            )
            session.add(result_row)

            turn = await session.get(ResearchTurn, turn_id)
            if turn:
                turn.status = "succeeded"
            await session.commit()

    except Exception as e:
        async with factory() as session:
            turn = await session.get(ResearchTurn, turn_id)
            if turn:
                turn.status = "run_failed"
                await session.commit()
        from packages.common.errors import AppError

        raise AppError(
            code="analysis_failed",
            message=f"Analysis failed: {e}",
            retryable=True,
        )

    # 5. Auto-trigger followup recommendations
    try:
        from packages.jobs.outbox import OutboxDispatcher
        from packages.research.timeline.contracts import (
            RECOMMENDATION_OUTPUT_SCHEMA_VERSION,
            RECOMMENDATION_PROMPT_VERSION,
        )
        from packages.research.timeline.repository import TimelineRepository

        followup_key = f"followup:{turn_id}"
        async with factory() as session:
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

                followup_context = (
                    f"上一轮分析问题: {turn.question_text_snapshot}\n"
                    f"分析摘要: {analysis_text[:2000]}"
                )

                await OutboxDispatcher.enqueue(
                    session,
                    aggregate_type="research_recommendation_batch",
                    aggregate_id=batch.id,
                    event_type="research.recommendation.requested",
                    payload={
                        "batch_id": str(batch.id),
                        "mode": "followup",
                        "followup_context": followup_context[:4000],
                    },
                )
                await session.commit()
                logger.info("enqueued followup recommendation batch %s", batch.id)
    except Exception as exc:
        logger.warning("Failed to enqueue followup recommendations: %s", exc)

    return {"turn_id": str(turn_id), "status": "succeeded", "summary": analysis_text[:200]}
