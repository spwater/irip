"""AI 助手路由：对话管理 + 问答 + Provider 状态。

端点（IRIP V3-T01）：
  POST   /api/v1/assistant/conversations              — 创建对话（assistant:use）
  GET    /api/v1/assistant/conversations              — 列出对话（assistant:use）
  POST   /api/v1/assistant/conversations/{id}/messages — 发送消息（assistant:use）
  GET    /api/v1/assistant/conversations/{id}/messages — 列出消息（assistant:use）
  GET    /api/v1/assistant/provider-status             — 查看 Provider 状态（assistant:use）

安全约定：
- 所有端点需 require_permission("assistant:use")；
- 对话仅返回当前用户的对话（user_id 过滤）；
- 消息列表检查对话归属（非本人 → forbidden）；
- AI 回答经凭据脱敏，不泄露密钥。
"""

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.ai.service import AIService
from packages.common.errors import AppError

#: 模块级 logger。
logger = logging.getLogger("api.assistant")

# session_factory 由 main.py 注入
_ai_factory: Any = None


def set_ai_session_factory(factory: Any) -> None:
    """设置会话工厂（由 main.py lifespan 调用）。"""
    global _ai_factory
    _ai_factory = factory


def _get_ai_factory() -> Any:
    if _ai_factory is None:
        raise RuntimeError("AI session factory not set")
    return _ai_factory


#: 路由实例。
assistant_router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])

#: 需 assistant:use 权限的当前用户依赖。
AssistantUserDep = Annotated[CurrentUser, Depends(require_permission("assistant:use"))]


def get_ai_service() -> AIService:
    """获取 AIService 实例（由 DI 容器或测试覆盖提供）。

    生产环境通过 ``dependency_overrides`` 注入按请求构造的实例。
    """
    raise NotImplementedError("get_ai_service must be overridden via dependency_overrides")


#: AIService 依赖类型别名。
AIServiceDep = Annotated[AIService, Depends(get_ai_service)]


# ---- 请求模型 ----


class CreateConversationRequest(BaseModel):
    """创建对话请求。"""

    title: str = Field("", max_length=200, description="对话标题")
    provider_mode: str = Field("offline", max_length=64, description="Provider 模式")


class SendMessageRequest(BaseModel):
    """发送消息请求。"""

    question: str = Field(..., min_length=1, max_length=8000, description="用户问题")
    provider_name: str = Field("offline", max_length=64, description="Provider 名称")
    thinking_enabled: bool = Field(False, description="是否启用思考模式")
    system_context: str | None = Field(
        None, max_length=102400, description="系统上下文（如实验数据JSON）"
    )
    # irip-ai-collab: @ 人的 user_id 数组；协作对话中 "ai" 表示 @AI 助手
    mentions: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="@ 人的 user_id 数组；协作对话中包含 'ai' 表示触发 AI 回复",
    )


# ---- 响应模型 ----


class ConversationResponse(BaseModel):
    """对话响应。"""

    id: str
    user_id: str = ""  # irip-ai-collab: 创建者 ID（前端兼容判断 owner）
    title: str
    provider_mode: str
    pinned: bool = False
    archived: bool = False
    created_at: datetime
    updated_at: datetime
    system_context: str | None = None
    # irip-ai-collab: 参与者摘要
    participants: list[dict[str, str]] = Field(default_factory=list)


class ConversationListResponse(BaseModel):
    """对话列表响应。"""

    items: list[ConversationResponse]


class ToolCallSummary(BaseModel):
    """工具调用摘要。"""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    status: str = ""


class CitationItem(BaseModel):
    """引用项。"""

    object_type: str
    object_id: str
    version: str
    label: str
    href: str


class MessageResponse(BaseModel):
    """消息响应。"""

    id: str
    conversation_id: str
    role: str
    content: str
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    citations: list[CitationItem] = Field(default_factory=list)
    uncertainty: str | None = None
    created_at: datetime
    # irip-ai-collab: @ 人 + 发送者信息
    mentions: list[str] = Field(default_factory=list)
    sender_user_id: str | None = None
    sender_display_name: str | None = None
    sender_avatar_url: str | None = None


class MessageListResponse(BaseModel):
    """消息列表响应。"""

    items: list[MessageResponse]


class AskResponse(BaseModel):
    """问答响应。"""

    conversation_id: str
    answer: str
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    citations: list[CitationItem] = Field(default_factory=list)
    uncertainty: str | None = None
    provider_mode: str


class ToolInfoResponse(BaseModel):
    """工具信息响应。"""

    name: str
    display_name: str
    description: str
    required_permission: str
    candidate: bool


class ProviderStatusResponse(BaseModel):
    """Provider 状态响应。"""

    provider_mode: str
    whitelist_tools: list[ToolInfoResponse]
    candidate_tools: list[ToolInfoResponse]


# ---- 端点 ----


@assistant_router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=201,
)
async def create_conversation(
    body: CreateConversationRequest,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> ConversationResponse:
    """创建新对话。

    Args:
        body: 创建请求（标题、provider 模式）。
        current_user: 当前用户（需 assistant:use 权限）。
        service: AI 编排服务。

    Returns:
        ConversationResponse: 新对话（201 Created）。
    """
    org_id = await service.resolve_dept_id(
        current_user.user_id, getattr(current_user, "department_id", None)
    )

    ref = await service.create_conversation(
        user_id=current_user.user_id,
        department_id=org_id,
        title=body.title,
        provider_mode=body.provider_mode,
    )
    return ConversationResponse(
        id=str(ref.id),
        user_id=str(ref.user_id),
        title=ref.title,
        provider_mode=ref.provider_mode,
        pinned=ref.pinned,
        archived=ref.archived,
        created_at=ref.created_at,
        updated_at=ref.updated_at,
        system_context=ref.system_context,
        participants=getattr(ref, "participants", []),
    )


@assistant_router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    current_user: AssistantUserDep,
    service: AIServiceDep,
    limit: int = Query(50, ge=1, le=200, description="最大返回数"),
    include_archived: bool = Query(False, description="是否包含已归档对话"),
    archived_only: bool = Query(False, description="是否只返回已归档对话"),
    keyword: str | None = Query(None, description="搜索关键词（标题 + 消息内容）"),
    tab: str | None = Query(None, description="筛选标签（private / collaborative）"),
) -> ConversationListResponse:
    """列出当前用户的对话（支持关键词搜索 + 三栏筛选）。

    有 keyword 时走搜索逻辑（ILIKE 标题 + 子查询消息内容），无 keyword 时走原逻辑。
    irip-ai-collab: 有 tab 参数时走 list_conversations_with_tab 协作筛选逻辑。

    Args:
        current_user: 当前用户。
        service: AI 编排服务。
        limit: 最大返回数。
        include_archived: 是否包含已归档对话。
        archived_only: 是否只返回已归档对话。
        keyword: 搜索关键词（可选）。
        tab: 筛选标签（可选，private / collaborative）。

    Returns:
        ConversationListResponse: 对话列表。
    """
    org_id = await service.resolve_dept_id(
        current_user.user_id, getattr(current_user, "department_id", None)
    )

    # tab 参数走协作筛选逻辑
    if tab is not None and tab in ("private", "collaborative", "same_dept", "cross_dept"):
        refs = await service.list_conversations_with_tab(
            user_id=current_user.user_id,
            department_id=org_id,
            tab=tab,
            limit=limit,
            include_archived=include_archived,
            archived_only=archived_only,
            keyword=keyword,
        )
    elif keyword and keyword.strip():
        refs = await service.search_conversations(
            user_id=current_user.user_id,
            department_id=org_id,
            keyword=keyword.strip(),
            include_archived=include_archived,
            archived_only=archived_only,
            limit=limit,
        )
    else:
        refs = await service.list_conversations(
            user_id=current_user.user_id,
            department_id=org_id,
            limit=limit,
            include_archived=include_archived,
            archived_only=archived_only,
        )
    return ConversationListResponse(
        items=[
            ConversationResponse(
                id=str(r.id),
                user_id=str(r.user_id),
                title=r.title,
                provider_mode=r.provider_mode,
                pinned=r.pinned,
                archived=r.archived,
                created_at=r.created_at,
                updated_at=r.updated_at,
                system_context=r.system_context,
                participants=getattr(r, "participants", []),
            )
            for r in refs
        ]
    )


@assistant_router.patch(
    "/conversations/{conversation_id}/pin",
    response_model=ConversationResponse,
)
async def toggle_pin(
    conversation_id: UUID,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> ConversationResponse:
    """切换对话置顶状态。"""
    await service.toggle_pin(
        conversation_id=conversation_id,
        user_id=current_user.user_id,
    )
    # 直接查询单条对话（避免 O(n) 全量重查）
    ref = await service.get_conversation(
        conversation_id=conversation_id,
        user_id=current_user.user_id,
    )
    if ref is None:
        raise AppError(code="not_found", message="对话不存在", retryable=False, fields={})
    return ConversationResponse(
        id=str(ref.id),
        user_id=str(ref.user_id),
        title=ref.title,
        provider_mode=ref.provider_mode,
        pinned=ref.pinned,
        archived=ref.archived,
        created_at=ref.created_at,
        updated_at=ref.updated_at,
    )


@assistant_router.patch(
    "/conversations/{conversation_id}/archive",
    response_model=ConversationResponse,
)
async def toggle_archive(
    conversation_id: UUID,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> ConversationResponse:
    """切换对话归档状态。"""
    await service.toggle_archive(
        conversation_id=conversation_id,
        user_id=current_user.user_id,
    )
    # 直接查询单条对话（避免 O(n) 全量重查）
    ref = await service.get_conversation(
        conversation_id=conversation_id,
        user_id=current_user.user_id,
    )
    if ref is None:
        raise AppError(code="not_found", message="对话不存在", retryable=False, fields={})
    return ConversationResponse(
        id=str(ref.id),
        user_id=str(ref.user_id),
        title=ref.title,
        provider_mode=ref.provider_mode,
        pinned=ref.pinned,
        archived=ref.archived,
        created_at=ref.created_at,
        updated_at=ref.updated_at,
    )


@assistant_router.post(
    "/conversations/{conversation_id}/cancel",
    status_code=200,
)
async def cancel_request(
    conversation_id: UUID,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> dict[str, str]:
    """取消正在进行的 AI 请求。"""
    cancelled = service.cancel_request(conversation_id)
    return {"cancelled": str(cancelled).lower()}


@assistant_router.delete(
    "/conversations/{conversation_id}",
    status_code=204,
)
async def delete_conversation(
    conversation_id: UUID,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> None:
    """永久删除对话（仅允许删除已归档的对话）。"""
    await service.delete_conversation(
        conversation_id=conversation_id,
        user_id=current_user.user_id,
    )


@assistant_router.post(
    "/conversations/{conversation_id}/messages",
    response_model=AskResponse,
)
async def send_message(
    conversation_id: UUID,
    body: SendMessageRequest,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> AskResponse:
    """发送消息并获取 AI 回答。

    Args:
        conversation_id: 对话 ID。
        body: 发送请求（问题、provider 名称）。
        current_user: 当前用户。
        service: AI 编排服务。

    Returns:
        AskResponse: AI 回答（含工具调用、引用、不确定性）。

    Raises:
        AppError: code="not_found"，当对话不存在时。
        AppError: code="forbidden"，当对话不属于当前用户时。
    """
    response = await service.ask(
        user=current_user,
        question=body.question,
        conversation_id=conversation_id,
        provider_name=body.provider_name,
        thinking_enabled=body.thinking_enabled,
        system_context=body.system_context,
        mentions=body.mentions,
    )
    return AskResponse(
        conversation_id=str(conversation_id),
        answer=response.answer,
        tool_calls=[
            ToolCallSummary(
                tool=str(tc.get("tool", "")),
                args=tc.get("args", {}) if isinstance(tc.get("args"), dict) else {},
                summary=str(tc.get("summary", "")),
                status=str(tc.get("status", "")),
            )
            for tc in response.tool_calls
        ],
        citations=[
            CitationItem(
                object_type=str(c.get("object_type", "")),
                object_id=str(c.get("object_id", "")),
                version=str(c.get("version", "")),
                label=str(c.get("label", "")),
                href=str(c.get("href", "")),
            )
            for c in ([ct.to_dict() if hasattr(ct, "to_dict") else ct for ct in response.citations])
            if c.get("object_type")  # 跳过 SignedCitation（无 object_type key）
        ],
        uncertainty=response.uncertainty,
        provider_mode=response.provider_mode,
    )


@assistant_router.post(
    "/conversations/{conversation_id}/messages/stream",
)
async def send_message_stream(
    conversation_id: UUID,
    body: SendMessageRequest,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> StreamingResponse:
    """流式发送消息并获取 AI 回答（SSE）。

    使用 Server-Sent Events 协议，逐 chunk 推送文本增量，流结束后
    推送包含完整回答、工具调用和引用的 done 事件。

    SSE 事件格式：
        - ``data: {"type": "chunk", "content": "文本增量"}\\n\\n`` — 文本块
        - ``data: {"type": "done", "answer": "...", "tool_calls": [...],
          "citations": [...], "uncertainty": ...}\\n\\n`` — 完成
        - ``data: {"type": "error", "message": "错误信息"}\\n\\n`` — 错误
        - ``data: [DONE]\\n\\n`` — 结束信号

    Args:
        conversation_id: 对话 ID。
        body: 发送请求（问题、provider 名称）。
        current_user: 当前用户。
        service: AI 编排服务。

    Returns:
        StreamingResponse: text/event-stream 响应。
    """

    async def event_stream() -> AsyncIterator[str]:
        """SSE 事件流生成器。"""
        try:
            async for event in service.stream_ask(
                user=current_user,
                question=body.question,
                conversation_id=conversation_id,
                provider_name=body.provider_name,
                thinking_enabled=body.thinking_enabled,
                system_context=body.system_context,
                mentions=body.mentions,
            ):
                event_type = event.get("type", "")
                if event_type == "chunk":
                    data = json.dumps(
                        {"type": "chunk", "content": event.get("content", "")},
                        ensure_ascii=False,
                    )
                    yield f"data: {data}\n\n"
                elif event_type == "done":
                    data = json.dumps(
                        {
                            "type": "done",
                            "answer": event.get("answer", ""),
                            "tool_calls": event.get("tool_calls", []),
                            "citations": event.get("citations", []),
                            "uncertainty": event.get("uncertainty"),
                        },
                        ensure_ascii=False,
                    )
                    yield f"data: {data}\n\n"
                    yield "data: [DONE]\n\n"
                elif event_type == "error":
                    data = json.dumps(
                        {"type": "error", "message": event.get("message", "")},
                        ensure_ascii=False,
                    )
                    yield f"data: {data}\n\n"
                    yield "data: [DONE]\n\n"
        except Exception:
            logging.getLogger("api").exception("SSE stream error")
            error_data = json.dumps(
                {"type": "error", "message": "处理请求时发生内部错误"},
                ensure_ascii=False,
            )
            yield f"data: {error_data}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@assistant_router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessageListResponse,
)
async def list_messages(
    conversation_id: UUID,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> MessageListResponse:
    """列出对话中的消息。

    Args:
        conversation_id: 对话 ID。
        current_user: 当前用户。
        service: AI 编排服务。

    Returns:
        MessageListResponse: 消息列表（按时间正序）。

    Raises:
        AppError: code="not_found"，当对话不存在时。
        AppError: code="forbidden"，当对话不属于当前用户时。
    """
    refs = await service.list_messages(
        conversation_id=conversation_id,
        user_id=current_user.user_id,
    )
    return MessageListResponse(
        items=[
            MessageResponse(
                id=str(r.id),
                conversation_id=str(r.conversation_id),
                role=r.role,
                content=r.content,
                tool_calls=[
                    ToolCallSummary(
                        tool=str(tc.get("tool", "")),
                        args=tc.get("args", {}) if isinstance(tc.get("args"), dict) else {},
                        summary=str(tc.get("summary", "")),
                        status=str(tc.get("status", "")),
                    )
                    for tc in r.tool_calls
                ],
                citations=[
                    CitationItem(
                        object_type=str(c.get("object_type", "")),
                        object_id=str(c.get("object_id", "")),
                        version=str(c.get("version", "")),
                        label=str(c.get("label", "")),
                        href=str(c.get("href", "")),
                    )
                    for c in r.citations
                    if c.get("object_type")  # 跳过 SignedCitation（无 object_type key）
                ],
                uncertainty=r.uncertainty,
                created_at=r.created_at,
                mentions=getattr(r, "mentions", [])
                if isinstance(getattr(r, "mentions", None), list)
                else [],
                sender_user_id=str(r.sender_user_id)
                if getattr(r, "sender_user_id", None) is not None
                else None,
                sender_display_name=getattr(r, "sender_display_name", None),
                sender_avatar_url=getattr(r, "sender_avatar_url", None),
            )
            for r in refs
        ]
    )


@assistant_router.get("/provider-status", response_model=ProviderStatusResponse)
async def get_provider_status(
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> ProviderStatusResponse:
    """查看可用 Provider 状态与工具列表（仅返回已启用工具）。

    Args:
        current_user: 当前用户。
        service: AI 编排服务。

    Returns:
        ProviderStatusResponse: Provider 模式 + 白名单工具 + 候选工具。
    """
    # 先从 DB reload 工具注册表，确保反映最新的启用/禁用状态
    await service.reload_tools()
    status = service.get_provider_status()
    return ProviderStatusResponse(
        provider_mode=str(status.get("provider_mode", "unknown")),
        whitelist_tools=[
            ToolInfoResponse(
                name=str(t["name"]),
                display_name=str(t["display_name"]),
                description=str(t["description"]),
                required_permission=str(t["required_permission"]),
                candidate=False,
            )
            for t in status.get("whitelist_tools", [])
        ],
        candidate_tools=[
            ToolInfoResponse(
                name=str(t["name"]),
                display_name=str(t["display_name"]),
                description=str(t["description"]),
                required_permission=str(t["required_permission"]),
                candidate=True,
            )
            for t in status.get("candidate_tools", [])
        ],
    )
