"""AI 助手分析橱窗路由：橱窗卡片 CRUD + 排序 + 摘要生成。

端点（irip-ai-showcase）：
  POST   /api/v1/assistant/conversations/{id}/showcase   — 添加橱窗卡片（assistant:use）
  GET    /api/v1/assistant/conversations/{id}/showcase   — 列出橱窗卡片（assistant:use）
  PATCH  /api/v1/assistant/showcase/{item_id}            — 更新卡片标题（assistant:use）
  DELETE /api/v1/assistant/showcase/{item_id}            — 删除卡片（assistant:use）
  PATCH  /api/v1/assistant/showcase/{item_id}/reorder    — 批量重排序（assistant:use）
  POST   /api/v1/assistant/conversations/{id}/summary   — 生成分析摘要（assistant:use）

安全约定：
- 所有端点需 require_permission("assistant:use")；
- 橱窗卡片仅返回当前用户的卡片（user_id 过滤）；
- 操作前校验对话/卡片归属（非本人 → not_found）。
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from apps.api.routers.assistant import AIServiceDep, AssistantUserDep


#: 路由实例（前缀与 assistant_router 一致）。
showcase_router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])


# ---- 请求模型 ----


class CreateShowcaseItemRequest(BaseModel):
    """添加橱窗卡片请求。"""

    block_type: str = Field(
        ...,
        max_length=32,
        description="块类型：echarts / plotly / table / conclusion / formula / text",
    )
    title: str = Field("", max_length=200, description="卡片标题")
    content_snapshot: str = Field(..., description="块内容完整快照")
    source_message_id: str = Field(..., description="来源消息 ID（UUID）")
    source_block_index: int = Field(0, ge=0, description="来源块序号")
    data_source: dict[str, Any] = Field(
        default_factory=dict,
        description="数据来源信息（样品标签、任务名、字段等）",
    )


class UpdateShowcaseItemRequest(BaseModel):
    """更新橱窗卡片请求。"""

    title: str | None = Field(None, max_length=200, description="新标题")


class ReorderShowcaseRequest(BaseModel):
    """重排序请求。"""

    item_ids: list[str] = Field(
        ..., min_length=1, description="按新顺序排列的卡片 ID 列表"
    )


class ShowcaseItemResponse(BaseModel):
    """橱窗卡片响应。"""

    id: str
    conversation_id: str
    sort_order: int
    block_type: str
    title: str
    content_snapshot: str
    source_message_id: str
    source_block_index: int
    data_source: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ShowcaseListResponse(BaseModel):
    """橱窗卡片列表响应。"""

    items: list[ShowcaseItemResponse]


class SummaryResponse(BaseModel):
    """分析摘要响应。"""

    markdown: str
    item_count: int


# ---- 端点 ----


@showcase_router.post(
    "/conversations/{conversation_id}/showcase",
    response_model=ShowcaseItemResponse,
    status_code=201,
)
async def create_showcase_item(
    conversation_id: UUID,
    body: CreateShowcaseItemRequest,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> ShowcaseItemResponse:
    """向对话橱窗添加一个内容块卡片。

    Args:
        conversation_id: 对话 ID。
        body: 添加请求（块类型、标题、内容快照、来源信息）。
        current_user: 当前用户（需 assistant:use 权限）。
        service: AI 编排服务。

    Returns:
        ShowcaseItemResponse: 新增（或已存在）的卡片（201 Created）。
    """
    ref = await service.add_showcase_item(
        user_id=current_user.user_id,
        conversation_id=conversation_id,
        block_type=body.block_type,
        title=body.title,
        content_snapshot=body.content_snapshot,
        source_message_id=UUID(body.source_message_id),
        source_block_index=body.source_block_index,
        data_source=body.data_source,
    )
    return ShowcaseItemResponse(
        id=str(ref.id),
        conversation_id=str(ref.conversation_id),
        sort_order=ref.sort_order,
        block_type=ref.block_type,
        title=ref.title,
        content_snapshot=ref.content_snapshot,
        source_message_id=str(ref.source_message_id),
        source_block_index=ref.source_block_index,
        data_source=ref.data_source,
        created_at=ref.created_at,
        updated_at=ref.updated_at,
    )


@showcase_router.get(
    "/conversations/{conversation_id}/showcase",
    response_model=ShowcaseListResponse,
)
async def list_showcase_items(
    conversation_id: UUID,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> ShowcaseListResponse:
    """列出对话橱窗的卡片（按 sort_order 正序）。

    Args:
        conversation_id: 对话 ID。
        current_user: 当前用户。
        service: AI 编排服务。

    Returns:
        ShowcaseListResponse: 卡片列表。
    """
    refs = await service.list_showcase_items(
        conversation_id=conversation_id,
        user_id=current_user.user_id,
    )
    return ShowcaseListResponse(
        items=[
            ShowcaseItemResponse(
                id=str(r.id),
                conversation_id=str(r.conversation_id),
                sort_order=r.sort_order,
                block_type=r.block_type,
                title=r.title,
                content_snapshot=r.content_snapshot,
                source_message_id=str(r.source_message_id),
                source_block_index=r.source_block_index,
                data_source=r.data_source,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in refs
        ]
    )


@showcase_router.patch(
    "/showcase/{item_id}",
    response_model=ShowcaseItemResponse,
)
async def update_showcase_item(
    item_id: UUID,
    body: UpdateShowcaseItemRequest,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> ShowcaseItemResponse:
    """更新橱窗卡片标题。

    Args:
        item_id: 卡片 ID。
        body: 更新请求（新标题）。
        current_user: 当前用户。
        service: AI 编排服务。

    Returns:
        ShowcaseItemResponse: 更新后的卡片。
    """
    ref = await service.update_showcase_item(
        item_id=item_id,
        user_id=current_user.user_id,
        title=body.title,
    )
    return ShowcaseItemResponse(
        id=str(ref.id),
        conversation_id=str(ref.conversation_id),
        sort_order=ref.sort_order,
        block_type=ref.block_type,
        title=ref.title,
        content_snapshot=ref.content_snapshot,
        source_message_id=str(ref.source_message_id),
        source_block_index=ref.source_block_index,
        data_source=ref.data_source,
        created_at=ref.created_at,
        updated_at=ref.updated_at,
    )


@showcase_router.delete(
    "/showcase/{item_id}",
    status_code=204,
)
async def delete_showcase_item(
    item_id: UUID,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> None:
    """删除橱窗卡片。

    Args:
        item_id: 卡片 ID。
        current_user: 当前用户。
        service: AI 编排服务。
    """
    await service.delete_showcase_item(
        item_id=item_id,
        user_id=current_user.user_id,
    )


@showcase_router.patch(
    "/conversations/{conversation_id}/showcase/reorder",
    status_code=200,
)
async def reorder_showcase_items(
    conversation_id: UUID,
    body: ReorderShowcaseRequest,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> dict[str, str]:
    """批量更新橱窗卡片排序。

    按 body.item_ids 顺序重新分配 sort_order（从 0 开始递增）。

    Args:
        conversation_id: 对话 ID。
        body: 重排序请求（按新顺序排列的卡片 ID 列表）。
        current_user: 当前用户。
        service: AI 编排服务。

    Returns:
        dict: {"reordered": "true"}。
    """
    uuid_ids = [UUID(sid) for sid in body.item_ids]
    await service.reorder_showcase_items(
        conversation_id=conversation_id,
        user_id=current_user.user_id,
        item_ids=uuid_ids,
    )
    return {"reordered": "true"}


@showcase_router.post(
    "/conversations/{conversation_id}/summary",
    response_model=SummaryResponse,
)
async def generate_summary(
    conversation_id: UUID,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> SummaryResponse:
    """基于橱窗卡片生成 Markdown 分析摘要。

    Args:
        conversation_id: 对话 ID。
        current_user: 当前用户。
        service: AI 编排服务。

    Returns:
        SummaryResponse: Markdown 摘要 + 卡片数量。
    """
    markdown, item_count = await service.generate_summary(
        conversation_id=conversation_id,
        user_id=current_user.user_id,
    )
    return SummaryResponse(markdown=markdown, item_count=item_count)
