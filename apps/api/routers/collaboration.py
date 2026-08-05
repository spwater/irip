"""协作 API 路由：参与者 CRUD、@人列表、退出对话。

端点（irip-ai-collab，基础路径 /api/v1）：
  POST   /collaboration/conversations/{id}/participants — 邀请成员
  GET    /collaboration/conversations/{id}/participants — 列出参与者
  DELETE /collaboration/conversations/{id}/participants/{uid} — 移除成员
  POST   /collaboration/conversations/{id}/leave — 退出对话
  GET    /collaboration/mentionable-users — 可 @ 用户列表

安全约定：
- 协作端点需 require_permission("conversation:invite" / "conversation:remove_member")；
- 列出/退出端点需 require_permission("assistant:use")；
- 错误响应统一格式：{"error": {"code", "message", "retryable", "fields"}}。
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.ai.service import AIService
from packages.common.errors import AppError

#: 路由实例。
collaboration_router = APIRouter(prefix="/api/v1/collaboration", tags=["collaboration"])

#: 需 assistant:use 权限的当前用户依赖。
AssistantUserDep = Annotated[CurrentUser, Depends(require_permission("assistant:use"))]

#: 需 conversation:invite 权限的当前用户依赖。
InviteUserDep = Annotated[CurrentUser, Depends(require_permission("conversation:invite"))]

#: 需 conversation:remove_member 权限的当前用户依赖。
RemoveMemberDep = Annotated[CurrentUser, Depends(require_permission("conversation:remove_member"))]


def get_ai_service() -> AIService:
    """获取 AIService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_ai_service must be overridden via dependency_overrides")


#: AIService 依赖类型别名。
AIServiceDep = Annotated[AIService, Depends(get_ai_service)]


# ---- 请求/响应模型 ----


class InviteMemberRequest(BaseModel):
    """邀请成员请求。"""

    user_id: str = Field(..., description="被邀请用户的 UUID")


class ParticipantResponse(BaseModel):
    """参与者响应。"""

    user_id: str
    display_name: str
    avatar_url: str | None = None
    role: str
    joined_at: datetime


class ParticipantListResponse(BaseModel):
    """参与者列表响应。"""

    items: list[ParticipantResponse]


class MentionableUserResponse(BaseModel):
    """可 @ 用户响应。"""

    id: str
    display_name: str
    avatar_url: str | None = None
    roles: list[str] = Field(default_factory=list)


class MentionableUserListResponse(BaseModel):
    """可 @ 用户列表响应。"""

    items: list[MentionableUserResponse]


# ---- 端点 ----


@collaboration_router.post(
    "/conversations/{conversation_id}/participants",
    response_model=ParticipantResponse,
    status_code=201,
)
async def invite_participant(
    conversation_id: UUID,
    body: InviteMemberRequest,
    current_user: InviteUserDep,
    service: AIServiceDep,
) -> ParticipantResponse:
    """邀请用户加入对话。

    需 conversation:invite 权限。仅对话 owner 可邀请。

    Args:
        conversation_id: 对话 ID。
        body: 邀请请求（被邀请用户 ID）。
        current_user: 当前用户（需 conversation:invite 权限）。
        service: AI 编排服务。

    Returns:
        ParticipantResponse: 新参与者信息（201 Created）。

    Raises:
        AppError: code="not_found"，对话不存在或目标用户不存在。
        AppError: code="forbidden"，邀请者非 owner。
        AppError: code="conflict"，目标用户已是参与者。
        AppError: code="validation_failed"，跨 org 邀请。
    """
    try:
        target_user_id = UUID(body.user_id)
    except ValueError as exc:
        raise AppError(
            code="validation_failed",
            message="无效的用户 ID",
            retryable=False,
            fields={"user_id": body.user_id},
        ) from exc

    ref = await service.add_participant(
        conversation_id=conversation_id,
        inviter_user_id=current_user.user_id,
        target_user_id=target_user_id,
    )
    return ParticipantResponse(
        user_id=str(ref.user_id),
        display_name=ref.display_name,
        avatar_url=ref.avatar_url,
        role=ref.role,
        joined_at=ref.joined_at,
    )


@collaboration_router.get(
    "/conversations/{conversation_id}/participants",
    response_model=ParticipantListResponse,
)
async def list_participants(
    conversation_id: UUID,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> ParticipantListResponse:
    """列出对话参与者。

    需 assistant:use 权限。仅对话创建者或参与者可查看。

    Args:
        conversation_id: 对话 ID。
        current_user: 当前用户。
        service: AI 编排服务。

    Returns:
        ParticipantListResponse: 参与者列表。
    """
    refs = await service.list_participants(
        conversation_id=conversation_id,
        user_id=current_user.user_id,
    )
    return ParticipantListResponse(
        items=[
            ParticipantResponse(
                user_id=str(r.user_id),
                display_name=r.display_name,
                avatar_url=r.avatar_url,
                role=r.role,
                joined_at=r.joined_at,
            )
            for r in refs
        ]
    )


@collaboration_router.delete(
    "/conversations/{conversation_id}/participants/{user_id}",
    status_code=204,
)
async def remove_participant(
    conversation_id: UUID,
    user_id: UUID,
    current_user: RemoveMemberDep,
    service: AIServiceDep,
) -> None:
    """移除对话参与者。

    需 conversation:remove_member 权限。仅对话 owner 可移除成员。

    Args:
        conversation_id: 对话 ID。
        user_id: 被移除用户 ID。
        current_user: 当前用户（需 conversation:remove_member 权限）。
        service: AI 编排服务。

    Raises:
        AppError: code="not_found"，对话不存在或目标非参与者。
        AppError: code="forbidden"，操作者非 owner。
    """
    await service.remove_participant(
        conversation_id=conversation_id,
        owner_user_id=current_user.user_id,
        target_user_id=user_id,
    )


@collaboration_router.post(
    "/conversations/{conversation_id}/leave",
    status_code=204,
)
async def leave_conversation(
    conversation_id: UUID,
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> None:
    """退出对话。

    需 assistant:use 权限。owner 不能退出。

    Args:
        conversation_id: 对话 ID。
        current_user: 当前用户。
        service: AI 编排服务。

    Raises:
        AppError: code="not_found"，非参与者。
        AppError: code="forbidden"，owner 不能退出。
    """
    await service.leave_conversation(
        conversation_id=conversation_id,
        user_id=current_user.user_id,
    )


@collaboration_router.get(
    "/mentionable-users",
    response_model=MentionableUserListResponse,
)
async def list_mentionable_users(
    current_user: AssistantUserDep,
    service: AIServiceDep,
) -> MentionableUserListResponse:
    """列出可 @ 的用户（同 org active 用户）。

    需 assistant:use 权限。

    Args:
        current_user: 当前用户。
        service: AI 编排服务。

    Returns:
        MentionableUserListResponse: 可 @ 用户列表。
    """
    from apps.api.routers.assistant import _resolve_dept_id

    org_id = await _resolve_dept_id(current_user)
    refs = await service.list_mentionable_users(
        user_id=current_user.user_id,
        department_id=org_id,
        roles=current_user.roles,
    )
    return MentionableUserListResponse(
        items=[
            MentionableUserResponse(
                id=str(r.id),
                display_name=r.display_name,
                avatar_url=r.avatar_url,
                roles=r.roles,
            )
            for r in refs
        ]
    )
