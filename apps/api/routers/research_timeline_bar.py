"""Research timeline conclusion bar and results routes.

Extracted from research_timeline.py to reduce file size.
Shares the same research_timeline_router to preserve URL contracts.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from apps.api.routers.research_timeline import (
    research_timeline_router,
)
from apps.api.routers.timeline_dependencies import ConclusionBarServiceDep
from apps.api.schemas.common import OkResponse
from packages.research.timeline.contracts import (
    AssembleFinalConclusionCommand,
    PushBarItemCommand,
)

logger = logging.getLogger(__name__)

ResearchUserDep = Annotated[CurrentUser, Depends(require_permission("research:use"))]


# ---- Bar item request/response models ----


class PushBarItemRequest(BaseModel):
    block_type: str = Field(..., min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=500)
    content_snapshot: dict[str, Any]
    block_index: int = Field(..., ge=0)
    source_info: dict[str, Any] = Field(default_factory=dict)


class FinalizeRequest(BaseModel):
    item_ids: list[str] = Field(..., min_length=1, max_length=20)
    title: str | None = None
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class BarItemResponse(BaseModel):
    id: str
    workspace_id: str
    turn_id: str
    block_type: str
    title: str
    content_snapshot: dict[str, Any]
    source_info: dict[str, Any]
    created_at: str


class BarItemListResponse(BaseModel):
    items: list[BarItemResponse]


class FinalizeResponse(BaseModel):
    conclusion_id: str
    statement: str
    item_count: int


# ---- Publish & Results request/response models ----


class PublishConclusionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class PublishConclusionResponse(BaseModel):
    result_id: str
    version_number: int


class ResultItemResponse(BaseModel):
    id: str
    name: str
    status: str
    current_version: int
    created_at: str


class ResultListResponse(BaseModel):
    items: list[ResultItemResponse]


class ResultVersionResponse(BaseModel):
    version_number: int
    title: str
    summary: dict[str, Any] | list[Any] | None
    source_conclusion_id: str
    published_at: str
    status: str


class ResultDetailResponse(BaseModel):
    id: str
    name: str
    status: str
    current_version: int
    created_at: str
    source_facts: list[dict[str, Any]] = Field(default_factory=list)
    version: ResultVersionResponse | None


# ---- Routes ----


@research_timeline_router.get(
    "/workspaces/{workspace_id}/conclusion-bar/items",
    response_model=BarItemListResponse,
)
async def list_conclusion_bar_items(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    service: ConclusionBarServiceDep,
) -> BarItemListResponse:
    """List conclusion-bar items for a workspace (newest first)."""
    data = await service.list_items(workspace_id)
    return BarItemListResponse(items=[BarItemResponse(**item) for item in data["items"]])


@research_timeline_router.post(
    "/workspaces/{workspace_id}/turns/{turn_id}/conclusion-bar/items",
    response_model=BarItemResponse,
    status_code=201,
)
async def push_conclusion_bar_item(
    workspace_id: UUID,
    turn_id: UUID,
    body: PushBarItemRequest,
    current_user: ResearchUserDep,
    service: ConclusionBarServiceDep,
) -> BarItemResponse:
    """Push a report block snapshot to the conclusion bar."""
    source_info = dict(body.source_info)
    source_info.setdefault("block_index", body.block_index)
    command = PushBarItemCommand(
        workspace_id=workspace_id,
        turn_id=turn_id,
        block_type=body.block_type,
        title=body.title,
        content_snapshot=body.content_snapshot,
        source_info=source_info,
    )
    ref = await service.push_item(command)
    return BarItemResponse(
        id=ref.id,
        workspace_id=ref.workspace_id,
        turn_id=ref.turn_id,
        block_type=ref.block_type,
        title=ref.title,
        content_snapshot=ref.content_snapshot,
        source_info=ref.source_info,
        created_at=ref.created_at,
    )


@research_timeline_router.delete(
    "/workspaces/{workspace_id}/conclusion-bar/items/{item_id}",
)
async def remove_conclusion_bar_item(
    workspace_id: UUID,
    item_id: UUID,
    current_user: ResearchUserDep,
    service: ConclusionBarServiceDep,
) -> dict[str, Any]:
    """Remove a bar item from the conclusion bar."""
    return await service.remove_item(workspace_id, item_id)


@research_timeline_router.post(
    "/workspaces/{workspace_id}/conclusion-bar/finalize",
    response_model=FinalizeResponse,
    status_code=201,
)
async def finalize_conclusion(
    workspace_id: UUID,
    body: FinalizeRequest,
    current_user: ResearchUserDep,
    service: ConclusionBarServiceDep,
) -> FinalizeResponse:
    """Assemble checked bar items into a final conclusion."""
    command = AssembleFinalConclusionCommand(
        workspace_id=workspace_id,
        item_ids=tuple(UUID(i) for i in body.item_ids),
        title=body.title or "",
        idempotency_key=body.idempotency_key,
    )
    result = await service.assemble_final_conclusion(command)
    return FinalizeResponse(
        conclusion_id=result["result_id"],
        statement=result["statement"],
        item_count=result["item_count"],
    )


# ---- Publish & Results endpoints ----


@research_timeline_router.post(
    "/workspaces/{workspace_id}/conclusions/{conclusion_id}/publish",
    response_model=PublishConclusionResponse,
    status_code=201,
)
async def publish_conclusion(
    workspace_id: UUID,
    conclusion_id: UUID,
    body: PublishConclusionRequest,
    current_user: ResearchUserDep,
    service: ConclusionBarServiceDep,
) -> PublishConclusionResponse:
    """Publish a ResearchConclusion as a simplified ResearchResult."""
    result = await service.publish_conclusion(
        workspace_id=workspace_id,
        conclusion_id=conclusion_id,
        title=body.title,
        idempotency_key=body.idempotency_key,
    )
    return PublishConclusionResponse(
        result_id=result["result_id"],
        version_number=result["version_number"],
    )


@research_timeline_router.get(
    "/workspaces/{workspace_id}/conclusion-results",
    response_model=ResultListResponse,
)
async def list_results(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    service: ConclusionBarServiceDep,
) -> ResultListResponse:
    """List all ResearchResults for a workspace (newest first)."""
    data = await service.list_results(workspace_id)
    return ResultListResponse(items=[ResultItemResponse(**item) for item in data["items"]])


@research_timeline_router.get(
    "/workspaces/{workspace_id}/conclusion-results/{result_id}",
    response_model=ResultDetailResponse,
)
async def get_result_detail(
    workspace_id: UUID,
    result_id: UUID,
    current_user: ResearchUserDep,
    service: ConclusionBarServiceDep,
) -> ResultDetailResponse:
    """Get a single ResearchResult detail + latest version summary."""
    data = await service.get_result_detail(workspace_id, result_id)
    version_data = data.get("version")
    version = (
        ResultVersionResponse(
            version_number=version_data["version_number"],
            title=version_data["title"],
            summary=version_data["summary"],
            source_conclusion_id=version_data["source_conclusion_id"],
            published_at=version_data["published_at"],
            status=version_data["status"],
        )
        if version_data
        else None
    )
    return ResultDetailResponse(
        id=data["id"],
        name=data["name"],
        status=data["status"],
        current_version=data["current_version"],
        created_at=data["created_at"],
        source_facts=data.get("source_facts", []),
        version=version,
    )


@research_timeline_router.patch(
    "/workspaces/{workspace_id}/conclusion-results/{result_id}/withdraw",
    response_model=OkResponse,
)
async def withdraw_result(
    workspace_id: UUID,
    result_id: UUID,
    current_user: ResearchUserDep,
    service: ConclusionBarServiceDep,
) -> dict[str, Any]:
    """Withdraw a published result (status -> withdrawn)."""
    await service.withdraw_result(workspace_id, result_id)
    return {"ok": True}


@research_timeline_router.patch(
    "/workspaces/{workspace_id}/conclusion-results/{result_id}/publish",
    response_model=OkResponse,
)
async def republish_result(
    workspace_id: UUID,
    result_id: UUID,
    current_user: ResearchUserDep,
    service: ConclusionBarServiceDep,
) -> dict[str, Any]:
    """Re-publish a withdrawn result (status -> published)."""
    await service.republish_result(workspace_id, result_id)
    return {"ok": True}


@research_timeline_router.delete(
    "/workspaces/{workspace_id}/conclusion-results/{result_id}",
    response_model=OkResponse,
)
async def delete_result(
    workspace_id: UUID,
    result_id: UUID,
    current_user: ResearchUserDep,
    service: ConclusionBarServiceDep,
) -> dict[str, Any]:
    """Delete a result permanently."""
    await service.delete_result(workspace_id, result_id)
    return {"ok": True}
