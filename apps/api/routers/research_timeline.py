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
from typing import Annotated, Any
from uuid import UUID

import fastapi
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.research.timeline.analysis_service import AnalysisService
from packages.research.timeline.conclusion_bar_service import ConclusionBarService
from packages.research.timeline.conclusion_service import ConclusionService
from packages.research.timeline.contracts import (
    CreateManualConclusionCommand,
    CreateSynthesisTurnCommand,
    CreateTurnCommand,
    ReviseConclusionCommand,
)
from packages.research.timeline.recommendation_service import RecommendationService
from packages.research.timeline.timeline_query_service import TimelineQueryService
from packages.research.timeline.turn_service import TurnService

logger = logging.getLogger(__name__)

ResearchUserDep = Annotated[CurrentUser, Depends(require_permission("research:use"))]

# ---- DI placeholders (overridden by composition/research.py) ----


def get_timeline_query_service() -> TimelineQueryService:
    raise NotImplementedError("overridden by composition")


def get_turn_service() -> TurnService:
    raise NotImplementedError("overridden by composition")


def get_conclusion_service() -> ConclusionService:
    raise NotImplementedError("overridden by composition")


def get_conclusion_bar_service() -> ConclusionBarService:
    raise NotImplementedError("overridden by composition")


def get_recommendation_service() -> RecommendationService:
    raise NotImplementedError("overridden by composition")


def get_analysis_service() -> AnalysisService:
    raise NotImplementedError("overridden by composition")


TimelineQueryDep = Annotated[TimelineQueryService, Depends(get_timeline_query_service)]
TurnServiceDep = Annotated[TurnService, Depends(get_turn_service)]
ConclusionServiceDep = Annotated[ConclusionService, Depends(get_conclusion_service)]
ConclusionBarServiceDep = Annotated[ConclusionBarService, Depends(get_conclusion_bar_service)]
RecommendationServiceDep = Annotated[RecommendationService, Depends(get_recommendation_service)]
AnalysisServiceDep = Annotated[AnalysisService, Depends(get_analysis_service)]


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
    selections: list[dict[str, Any]] = Field(..., min_length=1, max_length=20)
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


# ---- Conclusion bar & results models moved to research_timeline_bar.py ----


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
    current_revision_id: str


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
    service: RecommendationServiceDep,
) -> RecommendationBatchResponse:
    """Get the latest recommendation batch and its items for a workspace."""
    data = await service.get_active(workspace_id)
    return RecommendationBatchResponse(
        batch_id=data["batch_id"],
        workspace_id=data["workspace_id"],
        status=data["status"],
        items=[
            RecommendationItemResponse(
                id=item["id"],
                question=item["question"],
                rationale=item["rationale"],
            )
            for item in data["items"]
        ],
    )


@research_timeline_router.post(
    "/workspaces/{workspace_id}/recommendations/{batch_id}/retry",
    response_model=BatchRefResponse,
)
async def retry_recommendation(
    workspace_id: UUID,
    batch_id: UUID,
    current_user: ResearchUserDep,
    service: RecommendationServiceDep,
) -> BatchRefResponse:
    """Retry a failed recommendation batch."""
    ref = await service.retry_batch(batch_id)
    return BatchRefResponse(
        batch_id=str(ref.batch_id),
        workspace_id=str(workspace_id),
        status=ref.status,
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
        current_revision_id=str(ref.current_revision_id) if ref.current_revision_id else "",
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
        current_revision_id=str(ref.current_revision_id) if ref.current_revision_id else "",
    )


# ---- Turn detail + plan endpoints ----


@research_timeline_router.delete(
    "/workspaces/{workspace_id}/turns/{turn_id}",
)
async def delete_turn(
    workspace_id: UUID,
    turn_id: UUID,
    current_user: ResearchUserDep,
    service: TurnServiceDep,
) -> dict[str, Any]:
    """Delete a research turn and its related data (CASCADE)."""
    await service.delete_turn(workspace_id, turn_id)
    return {"ok": True}


@research_timeline_router.get(
    "/workspaces/{workspace_id}/turns/{turn_id}",
)
async def get_turn_detail(
    workspace_id: UUID,
    turn_id: UUID,
    current_user: ResearchUserDep,
    service: TimelineQueryDep,
) -> dict[str, Any]:
    """Get detailed information about a single research turn."""
    return await service.get_turn_detail_api(workspace_id, turn_id)


@research_timeline_router.post(
    "/workspaces/{workspace_id}/turns/{turn_id}/plan",
)
async def start_planning(
    workspace_id: UUID,
    turn_id: UUID,
    current_user: ResearchUserDep,
    service: TurnServiceDep,
) -> dict[str, Any]:
    """Start generating an analysis plan for a turn."""
    ref = await service.start_planning(workspace_id, turn_id)
    return {"turn_id": str(ref.turn_id), "status": ref.status}


@research_timeline_router.post("/extract-text")
async def extract_text_from_file(
    current_user: ResearchUserDep,
    file: Annotated[bytes, fastapi.Form()],
) -> dict[str, Any]:
    """Extract text from uploaded file for background context."""
    import os
    import tempfile

    from anyio import to_thread

    with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tmp:
        tmp.write(file)
        tmp_path = tmp.name

    try:
        text = await to_thread.run_sync(_read_text_file, tmp_path)
        if len(text) > 10000:
            text = text[:10000]
        return {"text": text}
    finally:
        os.unlink(tmp_path)


def _read_text_file(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


@research_timeline_router.delete(
    "/workspaces/{workspace_id}/conclusions/{conclusion_id}",
)
async def delete_conclusion(
    workspace_id: UUID,
    conclusion_id: UUID,
    current_user: ResearchUserDep,
    service: ConclusionServiceDep,
) -> dict[str, Any]:
    """Delete a conclusion (mark as archived)."""
    return await service.delete_conclusion(workspace_id, conclusion_id)


@research_timeline_router.get(
    "/workspaces/{workspace_id}/conclusions",
)
async def list_conclusions(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    service: ConclusionServiceDep,
) -> dict[str, Any]:
    """List all active conclusions for a workspace."""
    return await service.list_conclusions(workspace_id)


@research_timeline_router.post(
    "/workspaces/{workspace_id}/turns/{turn_id}/save-conclusion",
    status_code=201,
)
async def save_as_conclusion(
    workspace_id: UUID,
    turn_id: UUID,
    current_user: ResearchUserDep,
    service: ConclusionServiceDep,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Save a table/chart/structured data block as a conclusion."""
    statement = body.get("statement", "")
    if not statement.strip():
        from packages.common.errors import AppError

        raise AppError(code="validation_failed", message="结论内容不能为空")
    return await service.save_from_block(
        workspace_id, turn_id, statement, body.get("block_type", "table")
    )


@research_timeline_router.post(
    "/workspaces/{workspace_id}/turns/{turn_id}/analyze",
    status_code=202,
)
async def run_analysis(
    workspace_id: UUID,
    turn_id: UUID,
    current_user: ResearchUserDep,
    service: AnalysisServiceDep,
) -> dict[str, Any]:
    """Submit an analysis run for async execution via Outbox.

    Returns 202 with run_id, turn_id, status=queued.  The actual
    analysis is executed asynchronously by the Worker through the
    ``research.run.requested`` Outbox event.

    P0 data isolation guard: RESEARCH_ANALYSIS_ENABLED defaults to
    fail-closed (503 feature_disabled when disabled).  Read-only
    history pages (timeline / turn detail) are unaffected.
    """
    from packages.common.feature_flags import (
        RESEARCH_ANALYSIS_ENABLED,
        require_feature_enabled,
    )

    require_feature_enabled(RESEARCH_ANALYSIS_ENABLED, "research_analysis")
    return await service.submit_run(workspace_id, turn_id)


# ---- Conclusion bar & results routes moved to research_timeline_bar.py ----
# Import to register routes on the shared research_timeline_router:
from apps.api.routers.research_timeline_bar import *  # noqa: F403, E402
