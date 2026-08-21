"""Research timeline turn, plan, conclusion, and analysis routes.

Extracted from research_timeline.py to reduce file size.
Shares the same research_timeline_router to preserve URL contracts.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

import fastapi
from fastapi import Depends

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from apps.api.routers.research_timeline import (
    research_timeline_router,
)
from apps.api.routers.timeline_dependencies import (
    AnalysisServiceDep,
    ConclusionServiceDep,
    TimelineQueryDep,
    TurnServiceDep,
)

logger = logging.getLogger(__name__)

ResearchUserDep = Annotated[CurrentUser, Depends(require_permission("research:use"))]


# ---- Turn detail + plan + conclusion + analysis endpoints ----


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

        raise AppError(code="validation_failed", message="content cannot be empty")
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

    Returns 202 with run_id, turn_id, status=queued.
    """
    from packages.common.feature_flags import (
        RESEARCH_ANALYSIS_ENABLED,
        require_feature_enabled,
    )

    require_feature_enabled(RESEARCH_ANALYSIS_ENABLED, "research_analysis")
    return await service.submit_run(workspace_id, turn_id)
