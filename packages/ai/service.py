"""AI 编排服务：对话管理、工具执行、权限检查、持久化。

AIService 是 AI 助手的业务编排层，职责：
1. **对话管理**：创建对话、追加消息、列出对话与消息；
2. **工具执行**：调用白名单工具（只读），候选工具仅记录建议不执行；
3. **权限检查**：工具执行前通过 AuthorizationService 检查用户权限
   （与 REST API 相同的授权服务）；
4. **持久化**：对话与消息持久化到 ai_conversation / ai_message 表；
5. **凭据隔离**：user_context 不包含凭据，AI 回答不泄露密钥。

核心不变量：
- tool_permission_checked: 每个工具调用前必须检查 required_permission；
- no_credential_leak: AI 回答中不包含密码、密钥、令牌等凭据信息。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from packages.ai.citation import CitationGenerator, SignedCitation
from packages.ai.citations import Citation
from packages.ai.collaboration_entities import (
    ConversationParticipant,
    MentionableUserRef,
    ParticipantRef,
)
from packages.ai.providers import AIProvider, AIRequest, AIResponse
from packages.ai.showcase_entities import ShowcaseItem, ShowcaseItemRef
from packages.ai.tools import ToolRegistry
from packages.common.clock import Clock, SystemClock
from packages.common.database import Base, session_scope
from packages.common.db_types import GUID, UTCDateTime
from packages.common.errors import AppError
from packages.common.ids import new_id


class AIConversation(Base):
    """AI 对话实体（对应 ai_conversation 表）。

    Attributes:
        id: 对话 UUID。
        organization_id: 组织 ID。
        user_id: 创建用户 ID。
        title: 对话标题。
        provider_mode: Provider 模式（offline / openai_compatible）。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    __tablename__ = "ai_conversation"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    user_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    provider_mode: Mapped[str] = mapped_column(sa.Text, nullable=False, default="offline")
    pinned: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    archived: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    system_context: Mapped[str | None] = mapped_column(sa.Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=lambda: SystemClock().now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=lambda: SystemClock().now()
    )


class AIMessage(Base):
    """AI 消息实体（对应 ai_message 表）。

    Attributes:
        id: 消息 UUID。
        conversation_id: 对话 ID（FK→ai_conversation.id）。
        role: 消息角色（user / assistant / tool）。
        content: 消息文本内容。
        tool_calls_json: 工具调用 JSONB（工具名、参数、结果摘要）。
        citations_json: 引用 JSONB（object_type / object_id / version / label / href）。
        mentions: @ 人的 user_id 数组（JSONB，irip-ai-collab 新增）。
        sender_user_id: 发送者用户 ID（user 消息填用户 ID，assistant/tool 消息为 NULL）。
        sender_display_name: 发送者显示名（写入时从 app_user 快照，避免 JOIN）。
        sender_avatar_url: 发送者头像 URL（写入时从 app_user 快照）。
        uncertainty: 不确定性说明（可空）。
        created_at: 创建时间。
    """

    __tablename__ = "ai_message"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    conversation_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("ai_conversation.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    tool_calls_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    citations_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    # irip-ai-collab: @ 人 user_id 数组
    mentions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    # irip-ai-collab: 发送者冗余字段（避免每次 JOIN 查用户表）
    sender_user_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    sender_display_name: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    sender_avatar_url: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    uncertainty: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=lambda: SystemClock().now()
    )


@dataclass(frozen=True)
class ConversationRef:
    """对话引用（不可变值对象）。

    Attributes:
        id: 对话 UUID。
        title: 对话标题。
        provider_mode: Provider 模式。
        created_at: 创建时间。
        updated_at: 更新时间。
        participants: 参与者摘要列表（irip-ai-collab 新增）。
    """

    id: UUID
    title: str
    provider_mode: str
    pinned: bool
    archived: bool
    created_at: datetime
    updated_at: datetime
    system_context: str | None = None
    user_id: UUID = UUID(int=0)  # irip-ai-collab: 创建者 ID（有默认值避免 dataclass 顺序问题）
    participants: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class MessageRef:
    """消息引用（不可变值对象）。

    Attributes:
        id: 消息 UUID。
        conversation_id: 对话 UUID。
        role: 消息角色。
        content: 消息文本。
        tool_calls: 工具调用列表。
        citations: 引用列表。
        mentions: @ 人的 user_id 数组（irip-ai-collab 新增）。
        sender_user_id: 发送者用户 ID（user 消息有值，AI 消息为 None）。
        sender_display_name: 发送者显示名。
        sender_avatar_url: 发送者头像 URL。
        uncertainty: 不确定性说明。
        created_at: 创建时间。
    """

    id: UUID
    conversation_id: UUID
    role: str
    content: str
    tool_calls: list[dict[str, Any]]
    citations: list[dict[str, str]]
    uncertainty: str | None
    created_at: datetime
    mentions: list[str] = field(default_factory=list)
    sender_user_id: UUID | None = None
    sender_display_name: str | None = None
    sender_avatar_url: str | None = None


# ---- 模块级取消注册表 ----
# conversation_id → asyncio.Event，用于取消正在进行的 AI 请求
_active_requests: dict[UUID, asyncio.Event] = {}


class AIService:
    """AI 编排服务。

    依赖注入：
    - provider: AI Provider（OfflineProvider / OpenAICompatibleProvider）；
    - tool_registry: 工具注册表（白名单 + 候选）；
    - fact_service: 事实服务（工具 search_facts / compare_experiments 执行）；
    - parameter_service: 参数服务（工具 search_parameters 执行）；
    - model_service: 模型服务（工具 run_published_model 执行）；
    - provenance_service: 溯源服务（工具 explain_provenance 执行）；
    - auth_service: 授权服务（工具调用前权限检查，与 REST API 相同）；
    - session_factory: 异步会话工厂（对话持久化）；
    - clock: 时钟依赖。

    Attributes:
        _provider: AI Provider 实例。
        _tool_registry: 工具注册表。
        _fact_service: 事实服务。
        _parameter_service: 参数服务。
        _model_service: 模型服务。
        _provenance_service: 溯源服务。
        _auth_service: 授权服务。
        _factory: 异步会话工厂。
        _clock: 时钟依赖。
    """

    def __init__(
        self,
        provider: AIProvider,
        tool_registry: ToolRegistry,
        fact_service: Any | None = None,
        parameter_service: Any | None = None,
        model_service: Any | None = None,
        provenance_service: Any | None = None,
        auth_service: Any | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        clock: Clock | None = None,
    ) -> None:
        """初始化 AI 编排服务。

        Args:
            provider: AI Provider 实例。
            tool_registry: 工具注册表。
            fact_service: 事实服务（工具执行用，可选）。
            parameter_service: 参数服务（工具执行用，可选）。
            model_service: 模型服务（工具执行用，可选）。
            provenance_service: 溯源服务（工具执行用，可选）。
            auth_service: 授权服务（工具权限检查，与 REST API 相同，
                可选）。None 时仅检查角色级权限。
            session_factory: 异步会话工厂（对话持久化，可选）。
            clock: 时钟依赖，默认 SystemClock。
        """
        self._provider = provider
        self._tool_registry = tool_registry
        self._fact_service = fact_service
        self._parameter_service = parameter_service
        self._model_service = model_service
        self._provenance_service = provenance_service
        self._auth_service = auth_service
        self._factory = session_factory
        self._clock = clock or SystemClock()

    # ---- 对话管理 ----

    async def create_conversation(
        self,
        user_id: UUID,
        organization_id: UUID,
        title: str = "",
        provider_mode: str = "offline",
    ) -> ConversationRef:
        """创建新对话。

        Args:
            user_id: 用户 ID。
            organization_id: 组织 ID。
            title: 对话标题（空时自动生成）。
            provider_mode: Provider 模式。

        Returns:
            ConversationRef: 新对话引用。
        """
        conv_id = new_id()
        now = self._clock.now()
        if not title:
            title = f"对话 {now.strftime('%Y-%m-%d %H:%M')}"

        async with session_scope(self._factory) as session:
            conv = AIConversation(
                id=conv_id,
                organization_id=organization_id,
                user_id=user_id,
                title=title,
                provider_mode=provider_mode,
                created_at=now,
                updated_at=now,
            )
            session.add(conv)
            await session.flush()  # 确保 conv.id 已生成，避免 participant 外键违例
            # irip-ai-collab: 创建者自动成为 owner 参与者
            participant = ConversationParticipant(
                conversation_id=conv.id,
                user_id=user_id,
                role="owner",
                joined_at=now,
            )
            session.add(participant)
            await session.flush()
            return ConversationRef(
                id=conv.id,
                user_id=conv.user_id,
                title=conv.title,
                provider_mode=conv.provider_mode,
                pinned=False,
                archived=False,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                participants=[
                    {
                        "user_id": str(user_id),
                        "display_name": "",
                        "avatar_url": "",
                    }
                ],
            )

    async def list_conversations(
        self,
        user_id: UUID,
        organization_id: UUID,
        limit: int = 50,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> list[ConversationRef]:
        """列出用户的对话（置顶优先，然后按更新时间倒序）。

        Args:
            user_id: 用户 ID（仅返回该用户的对话）。
            organization_id: 组织 ID。
            limit: 最大返回数。
            include_archived: 是否包含已归档对话（默认不含）。
            archived_only: 是否只返回已归档对话（优先于 include_archived）。

        Returns:
            list[ConversationRef]: 对话引用列表。
        """
        conditions = [
            AIConversation.user_id == user_id,
            AIConversation.organization_id == organization_id,
        ]
        if archived_only:
            conditions.append(AIConversation.archived == sa.true())
        elif not include_archived:
            conditions.append(AIConversation.archived == sa.false())

        async with self._factory() as session:
            result = await session.execute(
                sa.select(AIConversation)
                .where(*conditions)
                .order_by(
                    sa.desc(AIConversation.pinned),
                    sa.desc(AIConversation.updated_at),
                )
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                ConversationRef(
                    id=r.id,
                    user_id=r.user_id,
                    title=r.title,
                    provider_mode=r.provider_mode,
                    pinned=r.pinned,
                    archived=r.archived,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    system_context=r.system_context,
                )
                for r in rows
            ]

    async def toggle_pin(
        self,
        conversation_id: UUID,
        user_id: UUID,
        pinned: bool | None = None,
    ) -> bool:
        """切换对话置顶状态。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID（权限检查）。
            pinned: 目标状态，None 时切换当前值。

        Returns:
            bool: 新的置顶状态。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            conv = await session.scalar(
                sa.select(AIConversation).where(
                    AIConversation.id == conversation_id,
                    AIConversation.user_id == user_id,
                )
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在或无权操作",
                    retryable=False,
                    fields={},
                )
            new_val = (not conv.pinned) if pinned is None else pinned
            conv.pinned = new_val
            conv.updated_at = now
            return new_val

    async def toggle_archive(
        self,
        conversation_id: UUID,
        user_id: UUID,
        archived: bool | None = None,
    ) -> bool:
        """切换对话归档状态。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID（权限检查）。
            archived: 目标状态，None 时切换当前值。

        Returns:
            bool: 新的归档状态。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            conv = await session.scalar(
                sa.select(AIConversation).where(
                    AIConversation.id == conversation_id,
                    AIConversation.user_id == user_id,
                )
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在或无权操作",
                    retryable=False,
                    fields={},
                )
            new_val = (not conv.archived) if archived is None else archived
            conv.archived = new_val
            conv.updated_at = now
            return new_val

    async def delete_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> None:
        """永久删除对话及其所有消息。

        仅允许删除已归档的对话，防止误删活跃对话。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID（权限检查）。

        Raises:
            AppError: code="not_found"，对话不存在或无权操作。
            AppError: code="forbidden"，对话未归档，不允许删除。
        """
        async with session_scope(self._factory) as session:
            conv = await session.scalar(
                sa.select(AIConversation).where(
                    AIConversation.id == conversation_id,
                    AIConversation.user_id == user_id,
                )
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在或无权操作",
                    retryable=False,
                    fields={},
                )
            if not conv.archived:
                raise AppError(
                    code="forbidden",
                    message="仅允许删除已归档的对话",
                    retryable=False,
                    fields={},
                )
            # 消息通过外键 CASCADE 自动删除
            await session.delete(conv)

    async def list_messages(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> list[MessageRef]:
        """列出对话中的消息（按创建时间正序）。

        安全检查：对话必须属于当前用户或当前用户是参与者，否则抛 forbidden。
        irip-ai-collab: 访问权从 `conv.user_id == user_id` 扩展为
        `conv.user_id == user_id OR EXISTS(participant WHERE user_id == user_id)`。

        Args:
            conversation_id: 对话 ID。
            user_id: 当前用户 ID（权限检查）。

        Returns:
            list[MessageRef]: 消息引用列表。

        Raises:
            AppError: code="forbidden"，当对话不属于当前用户且非参与者时。
            AppError: code="not_found"，当对话不存在时。
        """
        async with self._factory() as session:
            conv = await session.scalar(
                sa.select(AIConversation).where(AIConversation.id == conversation_id)
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在",
                    retryable=False,
                    fields={},
                )
            # irip-ai-collab: 创建者或参与者均可访问
            if conv.user_id != user_id:
                participant = await session.scalar(
                    sa.select(ConversationParticipant).where(
                        ConversationParticipant.conversation_id == conversation_id,
                        ConversationParticipant.user_id == user_id,
                    )
                )
                if participant is None:
                    raise AppError(
                        code="forbidden",
                        message="无权访问该对话",
                        retryable=False,
                        fields={},
                    )

            result = await session.execute(
                sa.select(AIMessage)
                .where(AIMessage.conversation_id == conversation_id)
                .order_by(sa.asc(AIMessage.created_at))
            )
            rows = result.scalars().all()
            return [
                MessageRef(
                    id=r.id,
                    conversation_id=r.conversation_id,
                    role=r.role,
                    content=r.content,
                    tool_calls=r.tool_calls_json if isinstance(r.tool_calls_json, list) else [],
                    citations=r.citations_json if isinstance(r.citations_json, list) else [],
                    uncertainty=r.uncertainty,
                    created_at=r.created_at,
                    mentions=r.mentions if isinstance(r.mentions, list) else [],
                    sender_user_id=r.sender_user_id,
                    sender_display_name=r.sender_display_name,
                    sender_avatar_url=r.sender_avatar_url,
                )
                for r in rows
            ]

    # ---- 对话搜索 ----

    async def search_conversations(
        self,
        user_id: UUID,
        organization_id: UUID,
        keyword: str,
        include_archived: bool = False,
        archived_only: bool = False,
        limit: int = 50,
    ) -> list[ConversationRef]:
        """按关键词搜索对话（ILIKE 标题 + 子查询消息内容）。

        搜索范围：对话标题 + 该对话下所有消息的文本内容。
        使用 ILIKE 实现模糊匹配，首期数据量可控。

        Args:
            user_id: 用户 ID（仅返回该用户的对话）。
            organization_id: 组织 ID。
            keyword: 搜索关键词。
            include_archived: 是否包含已归档对话。
            archived_only: 是否只返回已归档对话。
            limit: 最大返回数。

        Returns:
            list[ConversationRef]: 匹配的对话引用列表。
        """
        conditions = [
            AIConversation.user_id == user_id,
            AIConversation.organization_id == organization_id,
        ]
        if archived_only:
            conditions.append(AIConversation.archived == sa.true())
        elif not include_archived:
            conditions.append(AIConversation.archived == sa.false())

        pattern = f"%{keyword}%"
        # 子查询：有消息匹配关键词的 conversation_id 集合
        matched_conv_ids = (
            sa.select(AIMessage.conversation_id)
            .where(AIMessage.content.ilike(pattern))
            .distinct()
            .subquery()
        )
        # 对话标题匹配 或 对话的消息内容匹配
        title_or_msg_cond = sa.or_(
            AIConversation.title.ilike(pattern),
            AIConversation.id.in_(sa.select(matched_conv_ids.c.conversation_id)),
        )
        conditions.append(title_or_msg_cond)

        async with self._factory() as session:
            result = await session.execute(
                sa.select(AIConversation)
                .where(*conditions)
                .order_by(
                    sa.desc(AIConversation.pinned),
                    sa.desc(AIConversation.updated_at),
                )
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                ConversationRef(
                    id=r.id,
                    user_id=r.user_id,
                    title=r.title,
                    provider_mode=r.provider_mode,
                    pinned=r.pinned,
                    archived=r.archived,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    system_context=r.system_context,
                )
                for r in rows
            ]

    # ---- irip-ai-collab: 协作管理 ----

    async def list_conversations_with_tab(
        self,
        user_id: UUID,
        organization_id: UUID,
        tab: str = "private",
        limit: int = 50,
        include_archived: bool = False,
        archived_only: bool = False,
        keyword: str | None = None,
    ) -> list[ConversationRef]:
        """按 tab 筛选列出对话（irip-ai-collab 新增）。

        tab 分类逻辑（架构文档 §7.2）：
        - private: user_id == me AND 无其他参与者的对话
        - same_org: (user_id == me OR participant) AND org == my_org
        - cross_org: 返回空列表（二期实现）

        结果附带参与者摘要（批量查询 conversation_participant JOIN app_user）。

        Args:
            user_id: 当前用户 ID。
            organization_id: 当前用户组织 ID。
            tab: 筛选标签（private / same_org / cross_org）。
            limit: 最大返回数。
            include_archived: 是否包含已归档对话。
            archived_only: 是否只返回已归档对话。
            keyword: 搜索关键词（可选，复用 search 逻辑）。

        Returns:
            list[ConversationRef]: 对话引用列表（含参与者摘要）。
        """
        # cross_org 一期返回空列表
        if tab == "cross_org":
            return []

        from packages.auth.entities import AppUser

        async with self._factory() as session:
            # 构建 base 条件
            conditions: list[sa.ColumnElement[bool]] = []

            if archived_only:
                conditions.append(AIConversation.archived == sa.true())
            elif not include_archived:
                conditions.append(AIConversation.archived == sa.false())

            if tab == "private":
                # 我创建的 + 无其他参与者的对话
                conditions.append(AIConversation.user_id == user_id)
                # 排除有其他参与者的对话（子查询：存在 user_id != me 的参与者）
                other_participant_exists = (
                    sa.select(ConversationParticipant.conversation_id)
                    .where(
                        ConversationParticipant.conversation_id == AIConversation.id,
                        ConversationParticipant.user_id != user_id,
                    )
                    .exists()
                )
                conditions.append(sa.not_(other_participant_exists))
            elif tab == "same_org":
                # (user_id == me OR participant) AND org == my_org
                conditions.append(AIConversation.organization_id == organization_id)
                participant_or_owner = sa.or_(
                    AIConversation.user_id == user_id,
                    sa.select(ConversationParticipant.conversation_id)
                    .where(
                        ConversationParticipant.conversation_id == AIConversation.id,
                        ConversationParticipant.user_id == user_id,
                    )
                    .exists(),
                )
                conditions.append(participant_or_owner)

            # 关键词搜索
            if keyword and keyword.strip():
                pattern = f"%{keyword.strip()}%"
                matched_conv_ids = (
                    sa.select(AIMessage.conversation_id)
                    .where(AIMessage.content.ilike(pattern))
                    .distinct()
                    .subquery()
                )
                conditions.append(
                    sa.or_(
                        AIConversation.title.ilike(pattern),
                        AIConversation.id.in_(sa.select(matched_conv_ids.c.conversation_id)),
                    )
                )

            result = await session.execute(
                sa.select(AIConversation)
                .where(*conditions)
                .order_by(
                    sa.desc(AIConversation.pinned),
                    sa.desc(AIConversation.updated_at),
                )
                .limit(limit)
            )
            rows = result.scalars().all()

            if not rows:
                return []

            # 批量查询参与者
            conv_ids = [r.id for r in rows]
            participant_result = await session.execute(
                sa.select(
                    ConversationParticipant.conversation_id,
                    ConversationParticipant.user_id,
                    ConversationParticipant.role,
                    AppUser.display_name,
                    AppUser.avatar_url,
                )
                .select_from(ConversationParticipant)
                .join(AppUser, AppUser.id == ConversationParticipant.user_id)
                .where(ConversationParticipant.conversation_id.in_(conv_ids))
            )
            participant_rows = participant_result.fetchall()

            # 按对话 ID 分组参与者
            participants_map: dict[UUID, list[dict[str, str]]] = {}
            for prow in participant_rows:
                conv_id = UUID(str(prow[0]))
                if conv_id not in participants_map:
                    participants_map[conv_id] = []
                participants_map[conv_id].append(
                    {
                        "user_id": str(prow[1]),
                        "display_name": str(prow[3] or ""),
                        "avatar_url": str(prow[4] or ""),
                    }
                )

            return [
                ConversationRef(
                    id=r.id,
                    user_id=r.user_id,
                    title=r.title,
                    provider_mode=r.provider_mode,
                    pinned=r.pinned,
                    archived=r.archived,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    system_context=r.system_context,
                    participants=participants_map.get(r.id, []),
                )
                for r in rows
            ]

    async def add_participant(
        self,
        conversation_id: UUID,
        inviter_user_id: UUID,
        target_user_id: UUID,
    ) -> ParticipantRef:
        """邀请用户加入对话（irip-ai-collab 新增）。

        校验：
        - 对话存在
        - inviter 是对话的 owner（或创建者）
        - target 用户与对话属于同一 organization
        - target 未已是参与者

        Args:
            conversation_id: 对话 ID。
            inviter_user_id: 邀请者用户 ID（需为 owner）。
            target_user_id: 被邀请用户 ID。

        Returns:
            ParticipantRef: 新参与者引用。

        Raises:
            AppError: code="not_found"，对话不存在。
            AppError: code="forbidden"，邀请者非 owner。
            AppError: code="conflict"，目标用户已是参与者。
            AppError: code="validation_failed"，跨 org 邀请。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            conv = await session.scalar(
                sa.select(AIConversation).where(AIConversation.id == conversation_id)
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在",
                    retryable=False,
                    fields={},
                )

            # 校验邀请者是 owner（或创建者）
            inviter_participant = await session.scalar(
                sa.select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conversation_id,
                    ConversationParticipant.user_id == inviter_user_id,
                )
            )
            is_owner = (
                conv.user_id == inviter_user_id
                or (inviter_participant is not None and inviter_participant.role == "owner")
            )
            if not is_owner:
                raise AppError(
                    code="forbidden",
                    message="仅对话创建者/管理员可邀请成员",
                    retryable=False,
                    fields={},
                )

            # 校验目标用户未已是参与者
            existing = await session.scalar(
                sa.select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conversation_id,
                    ConversationParticipant.user_id == target_user_id,
                )
            )
            if existing is not None:
                raise AppError(
                    code="conflict",
                    message="该用户已是对话参与者",
                    retryable=False,
                    fields={},
                )

            # 校验同 org（target 用户的 organization_id 必须与对话的 organization_id 一致）
            from packages.auth.entities import AppUser

            target_user = await session.scalar(
                sa.select(AppUser).where(AppUser.id == target_user_id)
            )
            if target_user is None:
                raise AppError(
                    code="not_found",
                    message="目标用户不存在",
                    retryable=False,
                    fields={},
                )
            if target_user.organization_id != conv.organization_id:
                raise AppError(
                    code="validation_failed",
                    message="不能邀请跨组织用户加入对话",
                    retryable=False,
                    fields={},
                )

            participant = ConversationParticipant(
                conversation_id=conversation_id,
                user_id=target_user_id,
                role="member",
                joined_at=now,
            )
            session.add(participant)

            # 更新对话 updated_at
            await session.execute(
                sa.update(AIConversation)
                .values(updated_at=now)
                .where(AIConversation.id == conversation_id)
            )

            return ParticipantRef(
                conversation_id=conversation_id,
                user_id=target_user_id,
                role="member",
                joined_at=now,
                display_name=target_user.display_name,
                avatar_url=target_user.avatar_url,
            )

    async def remove_participant(
        self,
        conversation_id: UUID,
        owner_user_id: UUID,
        target_user_id: UUID,
    ) -> None:
        """移除对话参与者（irip-ai-collab 新增）。

        校验：
        - 操作者是 owner（或创建者）
        - 目标用户是参与者

        Args:
            conversation_id: 对话 ID。
            owner_user_id: 操作者用户 ID（需为 owner）。
            target_user_id: 被移除用户 ID。

        Raises:
            AppError: code="not_found"，对话不存在或目标非参与者。
            AppError: code="forbidden"，操作者非 owner。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            conv = await session.scalar(
                sa.select(AIConversation).where(AIConversation.id == conversation_id)
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在",
                    retryable=False,
                    fields={},
                )

            # 校验操作者是 owner
            owner_participant = await session.scalar(
                sa.select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conversation_id,
                    ConversationParticipant.user_id == owner_user_id,
                )
            )
            is_owner = (
                conv.user_id == owner_user_id
                or (owner_participant is not None and owner_participant.role == "owner")
            )
            if not is_owner:
                raise AppError(
                    code="forbidden",
                    message="仅对话创建者/管理员可移除成员",
                    retryable=False,
                    fields={},
                )

            # 查找并删除目标参与者
            target = await session.scalar(
                sa.select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conversation_id,
                    ConversationParticipant.user_id == target_user_id,
                )
            )
            if target is None:
                raise AppError(
                    code="not_found",
                    message="目标用户不是对话参与者",
                    retryable=False,
                    fields={},
                )

            await session.delete(target)
            await session.execute(
                sa.update(AIConversation)
                .values(updated_at=now)
                .where(AIConversation.id == conversation_id)
            )

    async def leave_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> None:
        """退出对话（irip-ai-collab 新增）。

        owner 不能退出（需先转让或删除对话）。

        Args:
            conversation_id: 对话 ID。
            user_id: 当前用户 ID。

        Raises:
            AppError: code="not_found"，对话不存在或非参与者。
            AppError: code="forbidden"，owner 不能退出。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            participant = await session.scalar(
                sa.select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conversation_id,
                    ConversationParticipant.user_id == user_id,
                )
            )
            if participant is None:
                raise AppError(
                    code="not_found",
                    message="你不是该对话的参与者",
                    retryable=False,
                    fields={},
                )
            if participant.role == "owner":
                raise AppError(
                    code="forbidden",
                    message="对话创建者不能退出，请先删除对话或移除其他成员",
                    retryable=False,
                    fields={},
                )

            await session.delete(participant)
            await session.execute(
                sa.update(AIConversation)
                .values(updated_at=now)
                .where(AIConversation.id == conversation_id)
            )

    async def list_participants(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> list[ParticipantRef]:
        """列出对话参与者（irip-ai-collab 新增）。

        校验访问权：创建者或参与者可查看。

        Args:
            conversation_id: 对话 ID。
            user_id: 当前用户 ID。

        Returns:
            list[ParticipantRef]: 参与者列表（含 display_name, avatar_url）。

        Raises:
            AppError: code="not_found"，对话不存在。
            AppError: code="forbidden"，无权访问。
        """
        async with self._factory() as session:
            from packages.auth.entities import AppUser

            conv = await session.scalar(
                sa.select(AIConversation).where(AIConversation.id == conversation_id)
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在",
                    retryable=False,
                    fields={},
                )

            # 校验访问权
            if conv.user_id != user_id:
                participant = await session.scalar(
                    sa.select(ConversationParticipant).where(
                        ConversationParticipant.conversation_id == conversation_id,
                        ConversationParticipant.user_id == user_id,
                    )
                )
                if participant is None:
                    raise AppError(
                        code="forbidden",
                        message="无权访问该对话",
                        retryable=False,
                        fields={},
                    )

            result = await session.execute(
                sa.select(
                    ConversationParticipant.conversation_id,
                    ConversationParticipant.user_id,
                    ConversationParticipant.role,
                    ConversationParticipant.joined_at,
                    AppUser.display_name,
                    AppUser.avatar_url,
                )
                .select_from(ConversationParticipant)
                .join(AppUser, AppUser.id == ConversationParticipant.user_id)
                .where(ConversationParticipant.conversation_id == conversation_id)
                .order_by(sa.asc(ConversationParticipant.joined_at))
            )
            rows = result.fetchall()

            # 如果没有参与者记录（现有对话兼容），返回创建者作为隐式 owner
            if not rows:
                creator = await session.scalar(
                    sa.select(AppUser).where(AppUser.id == conv.user_id)
                )
                if creator is not None:
                    return [
                        ParticipantRef(
                            conversation_id=conversation_id,
                            user_id=conv.user_id,
                            role="owner",
                            joined_at=conv.created_at,
                            display_name=creator.display_name,
                            avatar_url=creator.avatar_url,
                        )
                    ]
                return []

            return [
                ParticipantRef(
                    conversation_id=UUID(str(r[0])),
                    user_id=UUID(str(r[1])),
                    role=str(r[2]),
                    joined_at=r[3],
                    display_name=str(r[4] or ""),
                    avatar_url=str(r[5]) if r[5] is not None else None,
                )
                for r in rows
            ]

    async def list_mentionable_users(
        self,
        user_id: UUID,
        organization_id: UUID,
        department_id: UUID | None = None,
        roles: list[str] | None = None,
    ) -> list[MentionableUserRef]:
        """列出可 @ 的用户（irip-ai-collab 新增）。

        查询同 organization 的 active 用户，返回 id / display_name / avatar_url / roles。
        irip-ai-collab: platform_administrator 不做 department 过滤（可见全租户），
        其他角色按 department_id 过滤（只可见同实验室）。

        Args:
            user_id: 当前用户 ID（排除自己）。
            organization_id: 当前用户组织 ID。
            department_id: 当前用户部门 ID（非管理员时按此过滤）。
            roles: 当前用户角色列表（判断是否为管理员）。

        Returns:
            list[MentionableUserRef]: 可 @ 用户列表。
        """
        from packages.auth.entities import AppUser

        is_admin = roles is not None and "platform_administrator" in roles

        async with self._factory() as session:
            stmt = sa.select(
                AppUser.id,
                AppUser.display_name,
                AppUser.avatar_url,
                AppUser.roles,
            ).where(
                AppUser.organization_id == organization_id,
                AppUser.status == "active",
                AppUser.id != user_id,
            )

            # 非平台管理员：只返回同 department 的用户
            if not is_admin and department_id is not None:
                stmt = stmt.where(AppUser.department_id == department_id)

            stmt = stmt.order_by(sa.asc(AppUser.display_name))
            result = await session.execute(stmt)
            rows = result.fetchall()
            return [
                MentionableUserRef(
                    id=UUID(str(r[0])),
                    display_name=str(r[1] or ""),
                    avatar_url=str(r[2]) if r[2] is not None else None,
                    roles=list(r[3]) if isinstance(r[3], list) else [],
                )
                for r in rows
            ]

    # ---- 橱窗卡片管理 ----

    async def add_showcase_item(
        self,
        user_id: UUID,
        conversation_id: UUID,
        block_type: str,
        title: str,
        content_snapshot: str,
        source_message_id: UUID,
        source_block_index: int,
        data_source: dict[str, Any] | None = None,
    ) -> ShowcaseItemRef:
        """向对话橱窗添加一个内容块卡片。

        校验对话归属 + 唯一约束去重 + 自动分配 sort_order。
        若 (conversation_id, source_message_id, source_block_index) 已存在则返回已有卡片。

        Args:
            user_id: 用户 ID（权限校验）。
            conversation_id: 对话 ID。
            block_type: 块类型（echarts / plotly / table / conclusion / formula / text）。
            title: 卡片标题。
            content_snapshot: 块内容完整快照。
            source_message_id: 来源消息 ID。
            source_block_index: 来源块序号。
            data_source: 数据来源信息（可选）。

        Returns:
            ShowcaseItemRef: 新增（或已存在）的卡片引用。

        Raises:
            AppError: code="not_found"，对话不存在或无权操作。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            # 校验对话归属
            conv = await session.scalar(
                sa.select(AIConversation).where(
                    AIConversation.id == conversation_id,
                    AIConversation.user_id == user_id,
                )
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在或无权操作",
                    retryable=False,
                    fields={},
                )

            # 检查唯一约束：是否已加入
            existing = await session.scalar(
                sa.select(ShowcaseItem).where(
                    ShowcaseItem.conversation_id == conversation_id,
                    ShowcaseItem.source_message_id == source_message_id,
                    ShowcaseItem.source_block_index == source_block_index,
                )
            )
            if existing is not None:
                return ShowcaseItemRef(
                    id=existing.id,
                    conversation_id=existing.conversation_id,
                    sort_order=existing.sort_order,
                    block_type=existing.block_type,
                    title=existing.title,
                    content_snapshot=existing.content_snapshot,
                    source_message_id=existing.source_message_id,
                    source_block_index=existing.source_block_index,
                    data_source=existing.data_source if isinstance(existing.data_source, dict) else {},
                    created_at=existing.created_at,
                    updated_at=existing.updated_at,
                )

            # 自动分配 sort_order = 当前最大值 + 1
            max_order_result = await session.execute(
                sa.select(sa.func.max(ShowcaseItem.sort_order)).where(
                    ShowcaseItem.conversation_id == conversation_id
                )
            )
            max_order = max_order_result.scalar()
            sort_order = (max_order or 0) if max_order is not None else 0
            # 如果已有卡片，新卡片排在最前面（sort_order = 0，其余 +1）
            # 简化实现：新卡片追加到末尾，sort_order = max + 1
            sort_order = (max_order or 0) + 1 if max_order is not None else 0

            item = ShowcaseItem(
                id=new_id(),
                conversation_id=conversation_id,
                user_id=user_id,
                sort_order=sort_order,
                block_type=block_type,
                title=title[:200] if title else "",
                content_snapshot=content_snapshot,
                source_message_id=source_message_id,
                source_block_index=source_block_index,
                data_source=data_source if data_source is not None else {},
                created_at=now,
                updated_at=now,
            )
            session.add(item)
            await session.flush()
            return ShowcaseItemRef(
                id=item.id,
                conversation_id=item.conversation_id,
                sort_order=item.sort_order,
                block_type=item.block_type,
                title=item.title,
                content_snapshot=item.content_snapshot,
                source_message_id=item.source_message_id,
                source_block_index=item.source_block_index,
                data_source=item.data_source if isinstance(item.data_source, dict) else {},
                created_at=item.created_at,
                updated_at=item.updated_at,
            )

    async def list_showcase_items(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> list[ShowcaseItemRef]:
        """列出对话橱窗的卡片（按 sort_order 正序）。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID（权限校验）。

        Returns:
            list[ShowcaseItemRef]: 卡片引用列表。

        Raises:
            AppError: code="not_found"，对话不存在或无权操作。
        """
        async with self._factory() as session:
            # 校验对话归属
            conv = await session.scalar(
                sa.select(AIConversation).where(
                    AIConversation.id == conversation_id,
                    AIConversation.user_id == user_id,
                )
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在或无权操作",
                    retryable=False,
                    fields={},
                )

            result = await session.execute(
                sa.select(ShowcaseItem)
                .where(ShowcaseItem.conversation_id == conversation_id)
                .order_by(sa.asc(ShowcaseItem.sort_order))
            )
            rows = result.scalars().all()
            return [
                ShowcaseItemRef(
                    id=r.id,
                    conversation_id=r.conversation_id,
                    sort_order=r.sort_order,
                    block_type=r.block_type,
                    title=r.title,
                    content_snapshot=r.content_snapshot,
                    source_message_id=r.source_message_id,
                    source_block_index=r.source_block_index,
                    data_source=r.data_source if isinstance(r.data_source, dict) else {},
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
                for r in rows
            ]

    async def update_showcase_item(
        self,
        item_id: UUID,
        user_id: UUID,
        title: str | None = None,
    ) -> ShowcaseItemRef:
        """更新橱窗卡片标题。

        Args:
            item_id: 卡片 ID。
            user_id: 用户 ID（权限校验）。
            title: 新标题（None 时不更新）。

        Returns:
            ShowcaseItemRef: 更新后的卡片引用。

        Raises:
            AppError: code="not_found"，卡片不存在或无权操作。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            item = await session.scalar(
                sa.select(ShowcaseItem).where(
                    ShowcaseItem.id == item_id,
                    ShowcaseItem.user_id == user_id,
                )
            )
            if item is None:
                raise AppError(
                    code="not_found",
                    message="橱窗卡片不存在或无权操作",
                    retryable=False,
                    fields={},
                )
            if title is not None:
                item.title = title[:200]
            item.updated_at = now
            return ShowcaseItemRef(
                id=item.id,
                conversation_id=item.conversation_id,
                sort_order=item.sort_order,
                block_type=item.block_type,
                title=item.title,
                content_snapshot=item.content_snapshot,
                source_message_id=item.source_message_id,
                source_block_index=item.source_block_index,
                data_source=item.data_source if isinstance(item.data_source, dict) else {},
                created_at=item.created_at,
                updated_at=item.updated_at,
            )

    async def delete_showcase_item(
        self,
        item_id: UUID,
        user_id: UUID,
    ) -> None:
        """删除橱窗卡片。

        Args:
            item_id: 卡片 ID。
            user_id: 用户 ID（权限校验）。

        Raises:
            AppError: code="not_found"，卡片不存在或无权操作。
        """
        async with session_scope(self._factory) as session:
            item = await session.scalar(
                sa.select(ShowcaseItem).where(
                    ShowcaseItem.id == item_id,
                    ShowcaseItem.user_id == user_id,
                )
            )
            if item is None:
                raise AppError(
                    code="not_found",
                    message="橱窗卡片不存在或无权操作",
                    retryable=False,
                    fields={},
                )
            await session.delete(item)

    async def reorder_showcase_items(
        self,
        conversation_id: UUID,
        user_id: UUID,
        item_ids: list[UUID],
    ) -> None:
        """批量更新橱窗卡片排序。

        按传入的 item_ids 顺序重新分配 sort_order（从 0 开始递增）。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID（权限校验）。
            item_ids: 按新顺序排列的卡片 ID 列表。

        Raises:
            AppError: code="not_found"，对话不存在或无权操作。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            # 校验对话归属
            conv = await session.scalar(
                sa.select(AIConversation).where(
                    AIConversation.id == conversation_id,
                    AIConversation.user_id == user_id,
                )
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在或无权操作",
                    retryable=False,
                    fields={},
                )

            for index, item_id in enumerate(item_ids):
                item = await session.scalar(
                    sa.select(ShowcaseItem).where(
                        ShowcaseItem.id == item_id,
                        ShowcaseItem.conversation_id == conversation_id,
                    )
                )
                if item is not None:
                    item.sort_order = index
                    item.updated_at = now

    async def generate_summary(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> tuple[str, int]:
        """基于橱窗卡片生成 Markdown 分析摘要。

        按卡片 sort_order 组织内容：标题 → 各卡片结论/图表引用/表格 → 数据来源。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID（权限校验）。

        Returns:
            tuple[str, int]: (Markdown 摘要文本, 卡片数量)。

        Raises:
            AppError: code="not_found"，对话不存在或无权操作。
        """
        async with self._factory() as session:
            # 校验对话归属
            conv = await session.scalar(
                sa.select(AIConversation).where(
                    AIConversation.id == conversation_id,
                    AIConversation.user_id == user_id,
                )
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在或无权操作",
                    retryable=False,
                    fields={},
                )
            conv_title = conv.title or "未命名对话"

            result = await session.execute(
                sa.select(ShowcaseItem)
                .where(ShowcaseItem.conversation_id == conversation_id)
                .order_by(sa.asc(ShowcaseItem.sort_order))
            )
            rows = result.scalars().all()

        if not rows:
            return ("", 0)

        # 拼装 Markdown 摘要
        lines: list[str] = []
        lines.append(f"# 分析摘要：{conv_title}")
        lines.append("")
        lines.append(f"> 生成时间：{self._clock.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        lines.append(f"> 来源对话：{conv_title}")
        lines.append(f"> 橱窗卡片数：{len(rows)}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 块类型中文标签映射
        type_labels: dict[str, str] = {
            "echarts": "ECharts 图表",
            "plotly": "Plotly 图表",
            "table": "数据表格",
            "conclusion": "分析结论",
            "formula": "计算公式",
            "text": "文本摘要",
        }

        for i, r in enumerate(rows, 1):
            type_label = type_labels.get(r.block_type, r.block_type)
            lines.append(f"## {i}. {r.title or type_label}")
            lines.append("")
            lines.append(f"**类型**：{type_label}")
            lines.append("")

            # 根据块类型输出不同内容
            if r.block_type in ("conclusion", "text", "formula"):
                # 文本类：直接输出内容
                lines.append(r.content_snapshot)
                lines.append("")
            elif r.block_type == "table":
                # 表格类：输出 Markdown 表格原文
                lines.append(r.content_snapshot)
                lines.append("")
            elif r.block_type in ("echarts", "plotly"):
                # 图表类：输出配置引用
                lines.append("> 📊 图表配置（JSON）：")
                lines.append("")
                lines.append("```json")
                # 截断过长的 JSON 配置
                snapshot = r.content_snapshot
                if len(snapshot) > 2000:
                    snapshot = snapshot[:2000] + "\n... (配置已截断)"
                lines.append(snapshot)
                lines.append("```")
                lines.append("")

            # 数据来源信息
            ds = r.data_source if isinstance(r.data_source, dict) else {}
            if ds:
                lines.append("**数据来源**：")
                if ds.get("sample_labels"):
                    lines.append(f"- 样品：{', '.join(ds['sample_labels'])}")
                if ds.get("task_name"):
                    lines.append(f"- 任务：{ds['task_name']}")
                if ds.get("fields"):
                    lines.append(f"- 检测指标：{', '.join(ds['fields'])}")
                if ds.get("source_tag"):
                    lines.append(f"- 数据来源标识：{ds['source_tag']}")
                if ds.get("data_range"):
                    lines.append(f"- 数据范围：{ds['data_range']}")
                lines.append("")

            lines.append(f"*创建时间：{r.created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC*")
            lines.append("")
            lines.append("---")
            lines.append("")

        return ("\n".join(lines), len(rows))

    # ---- 问答 ----

    async def ask(
        self,
        user: Any,
        question: str,
        conversation_id: UUID | None = None,
        provider_name: str = "offline",
        thinking_enabled: bool = False,
        system_context: str | None = None,
        mentions: list[str] | None = None,
    ) -> AIResponse:
        """处理用户问题，返回 AI 回答。

        流程：
        1. 验证用户拥有 assistant:use 权限（由路由层 require_permission 保证）；
        2. 构建对话上下文（加载历史消息或创建新对话）；
        3. 构建 AIRequest（消息、工具、用户上下文）；
        4. 调用 Provider.complete 获取回答；
        5. 对回答中的工具调用执行权限检查与工具执行；
        6. 持久化用户消息与 AI 消息；
        7. 返回 AIResponse。

        Args:
            user: 当前用户（需有 user_id, email, roles 属性）。
            question: 用户问题文本。
            conversation_id: 对话 ID（None 时自动创建新对话）。
            provider_name: Provider 名称（用于选择 provider，当前仅支持 offline）。

        Returns:
            AIResponse: AI 回答。

        Raises:
            AppError: code="forbidden"，当用户缺少工具所需权限时。
        """
        user_id: UUID = user.user_id
        org_id: UUID | None = getattr(user, "organization_id", None)
        if org_id is None:
            # CurrentUser 可能没有 organization_id，使用默认值
            org_id = new_id()

        # 热更新：每次 ask 从 DB 重新加载工具声明层（D-4）
        # 表预计 < 50 行，单行 SELECT 开销 < 1ms，可忽略。
        if self._factory is not None:
            async with session_scope(self._factory) as session:
                await self._tool_registry.reload_from_db(session)

        # 加载或创建对话
        if conversation_id is None:
            conv_ref = await self.create_conversation(
                user_id=user_id,
                organization_id=org_id,
                title=question[:60],
                provider_mode=provider_name,
            )
            conversation_id = conv_ref.id
            history_messages: list[dict[str, Any]] = []
        else:
            # 验证对话归属并加载历史
            msgs = await self.list_messages(conversation_id, user_id)
            history_messages = [{"role": m.role, "content": m.content} for m in msgs]

        # irip-ai-collab: 根据 conversation_participant 表的参与者数量判断对话类型
        # 私有对话（参与者 <= 1）：AI 自动回复（不管 mentions）
        # 协作对话（参与者 > 1）：mentions 中包含 "ai" 标识才调 AI，否则只保存用户消息
        mentions_list: list[str] = mentions or []
        participant_count: int | None = None
        async with self._factory() as session:
            participant_count = await session.scalar(
                sa.select(sa.func.count())
                .select_from(ConversationParticipant)
                .where(ConversationParticipant.conversation_id == conversation_id)
            )
        is_private: bool = participant_count is None or participant_count <= 1

        # 协作对话中：mentions 不含 "ai" 标识 → 仅持久化用户消息，不调 AI
        if not is_private and "ai" not in mentions_list:
            mention_only_response = AIResponse(
                answer="",
                tool_calls=(),
                citations=(),
                uncertainty=None,
                provider_mode=provider_name,
            )
            await self._persist_user_message_only(
                conversation_id=conversation_id,
                user_id=user_id,
                question=question,
                mentions=mentions_list,
                sender_display_name=getattr(user, "email", None),
                sender_avatar_url=None,
            )
            # 首次对话后自动生成标题
            if not history_messages:
                try:
                    await self._auto_generate_title(
                        conversation_id=conversation_id,
                        question=question,
                        answer=question[:60],
                    )
                except Exception:
                    pass
            return mention_only_response

        # 构建 user_context（不含凭据）
        user_context: dict[str, Any] = {
            "user_id": str(user_id),
            "organization_id": str(org_id),
            "roles": list(user.roles),
        }

        # 构建消息元组（历史 + 当前问题）
        # 如果有系统上下文（如实验数据），拼到默认 system 消息后面，不单独加 system role
        msg_list: list[dict[str, Any]] = []
        for m in history_messages:
            if m.get("role") == "system":
                continue  # 跳过历史中的 system 消息
            msg_list.append(m)
        msg_list.append({"role": "user", "content": question})
        messages: tuple[dict[str, Any], ...] = tuple(msg_list)

        # 把 system_context 存到 user_context 里，让 provider 拼到 system 消息
        if system_context:
            user_context["system_context"] = system_context
            # 同时存到对话记录里，切回对话时恢复
            async with self._factory() as session:
                conv_obj = await session.scalar(
                    sa.select(AIConversation).where(AIConversation.id == conversation_id)
                )
                if conv_obj:
                    conv_obj.system_context = system_context
                    await session.commit()

        # 构建工具名称元组（仅已启用工具，D-3 禁用工具不进 schema）
        tool_names: tuple[str, ...] = self._tool_registry.enabled_names()

        # 构建工具的 OpenAI JSON schema 定义
        tool_schemas: tuple[dict[str, Any], ...] = self._build_tool_schemas()

        # 构建 AIRequest
        ai_request = AIRequest(
            messages=messages,
            tools=tool_names,
            tool_schemas=tool_schemas,
            user_context=user_context,
            provider_mode=provider_name,
        )

        # 思考模式：由对话框开关控制（AI 配置页已移除全局 thinking 开关，
        # 改为以对话级 thinking_enabled 作为唯一控制源）
        if hasattr(self._provider, "_thinking_enabled"):
            self._provider._thinking_enabled = thinking_enabled

        # 创建取消事件并注册到模块级字典
        cancel_event = asyncio.Event()
        _active_requests[conversation_id] = cancel_event

        try:
            # 调用 Provider（支持取消）
            response: AIResponse = await self._provider.complete(
                ai_request, cancel_event=cancel_event
            )
        except AppError as exc:
            if exc.code == "ai_cancelled":
                # 用户取消，不持久化 AI 回答，但保留用户消息
                await self._persist_messages(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    question=question,
                    response=AIResponse(
                        answer="[已取消]",
                        tool_calls=(),
                        citations=(),
                        uncertainty=None,
                        provider_mode=provider_name,
                    ),
                    mentions=mentions or [],
                    sender_display_name=getattr(user, "email", None),
                    sender_avatar_url=None,
                )
            raise
        finally:
            _active_requests.pop(conversation_id, None)

        # 执行工具调用（权限检查 + 白名单工具真实执行 + 第二轮 completion）
        executed_tool_calls: list[dict[str, Any]] = []
        tool_result_messages: list[dict[str, Any]] = []
        all_citations: list[Any] = []

        for tc in response.tool_calls:
            tool_name = str(tc.get("tool", ""))
            tool_args = tc.get("args", {})
            if not isinstance(tool_args, dict):
                tool_args = {}
            tool_call_id = str(tc.get("id", "")) or f"call_{tool_name}_{len(executed_tool_calls)}"

            # 验证工具在白名单中
            try:
                spec = self._tool_registry.validate(tool_name)
            except AppError:
                executed_tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "summary": f"拒绝执行：未知工具 '{tool_name}'",
                        "status": "rejected",
                    }
                )
                tool_result_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(
                            {"error": f"未知工具: {tool_name}"}, ensure_ascii=False
                        ),
                    }
                )
                continue

            # 检查用户权限
            has_perm = self._check_role_permission(user, spec.required_permission)
            if not has_perm:
                executed_tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "summary": (f"拒绝执行：缺少权限 '{spec.required_permission}'"),
                        "status": "forbidden",
                    }
                )
                tool_result_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(
                            {"error": f"权限不足: 需要 {spec.required_permission}"},
                            ensure_ascii=False,
                        ),
                    }
                )
                continue

            # 白名单工具真实执行
            try:
                tool_result = await self._execute_tool(tool_name, tool_args, user, org_id)
                result_summary = str(tool_result.get("summary", ""))
                executed_tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "summary": result_summary or f"已执行 {spec.display_name}",
                        "status": "executed",
                        "result": tool_result.get("data"),
                    }
                )
                tool_result_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(
                            tool_result.get("data", tool_result),
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                )
                # 生成结构化 citation（服务端签名，不可伪造）
                citation_gen = CitationGenerator()
                signed_citation = citation_gen.generate(
                    tool_name=tool_name,
                    query_params=tool_args,
                    result_summary=result_summary or "工具执行完成",
                )
                all_citations.append(signed_citation)
            except Exception as exc:
                error_msg = f"工具执行失败: {exc}"
                executed_tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "summary": error_msg,
                        "status": "error",
                    }
                )
                tool_result_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps({"error": error_msg}, ensure_ascii=False),
                    }
                )

        # 如果有工具被执行，进行第二轮 completion 获取最终回答
        if tool_result_messages:
            # 构建 assistant 消息（含 tool_calls，OpenAI 格式）
            assistant_tool_calls = []
            for tc in response.tool_calls:
                tc_id = (
                    str(tc.get("id", ""))
                    or f"call_{tc.get('tool', 'unknown')}_{len(assistant_tool_calls)}"
                )  # noqa: E501
                assistant_tool_calls.append(
                    {
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": str(tc.get("tool", "")),
                            "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
                        },
                    }
                )

            # 构建第二轮消息：原始消息 + assistant tool_calls + tool 结果
            second_messages: list[dict[str, Any]] = list(msg_list)
            second_messages.append(
                {
                    "role": "assistant",
                    "content": response.answer,
                    "tool_calls": assistant_tool_calls,
                }
            )
            second_messages.extend(tool_result_messages)

            second_request = AIRequest(
                messages=tuple(second_messages),
                tools=tool_names,
                tool_schemas=tool_schemas,
                user_context=user_context,
                provider_mode=provider_name,
            )

            if hasattr(self._provider, "_thinking_enabled"):
                self._provider._thinking_enabled = thinking_enabled

            try:
                second_response: AIResponse = await self._provider.complete(
                    second_request, cancel_event=cancel_event
                )
                final_answer = self._redact_credentials(second_response.answer)
                final_uncertainty = second_response.uncertainty
            except Exception:
                # 第二轮失败时使用第一轮回答 + 工具结果摘要
                tool_summaries = "\n".join(
                    f"- {tc['tool']}: {tc.get('summary', '')}"
                    for tc in executed_tool_calls
                    if tc.get("status") == "executed"
                )
                final_answer = self._redact_credentials(
                    response.answer
                    + (f"\n\n工具执行结果：\n{tool_summaries}" if tool_summaries else "")
                )
                final_uncertainty = response.uncertainty
        else:
            final_answer = self._redact_credentials(response.answer)
            final_uncertainty = response.uncertainty

        # 构建最终响应
        final_response = AIResponse(
            answer=final_answer,
            tool_calls=tuple(executed_tool_calls),
            citations=tuple(all_citations),
            uncertainty=final_uncertainty,
            provider_mode=response.provider_mode,
        )

        # 持久化消息
        await self._persist_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            question=question,
            response=final_response,
            mentions=mentions or [],
            sender_display_name=getattr(user, "email", None),
            sender_avatar_url=None,
        )

        # 首次对话后自动生成标题
        if not history_messages:
            try:
                await self._auto_generate_title(
                    conversation_id=conversation_id,
                    question=question,
                    answer=final_response.answer,
                )
            except Exception:
                # 标题生成失败不影响主流程
                pass

        return final_response

    # ---- 内部方法 ----

    def _check_role_permission(self, user: Any, action: str) -> bool:
        """检查用户角色是否拥有指定权限（角色级，非对象级）。

        基于 BUILTIN_ROLES 权限矩阵，与 require_permission 依赖相同逻辑。

        Args:
            user: 当前用户（需有 roles 属性）。
            action: 权限字符串。

        Returns:
            bool: 有权返回 True。
        """
        from packages.auth.permissions import BUILTIN_ROLES

        for role_code in user.roles:
            role_def = BUILTIN_ROLES.get(role_code)
            if role_def is not None:
                permissions = role_def["permissions"]
                if isinstance(permissions, list) and action in permissions:
                    return True
        return False

    def _build_tool_schemas(self) -> tuple[dict[str, Any], ...]:
        """将 ToolRegistry 中的工具规格转为 OpenAI tools JSON schema 格式。

        Returns:
            tuple[dict, ...]: OpenAI tools 定义元组，每项为
            ``{"type": "function", "function": {"name", "description", "parameters"}}``。
        """
        schemas: list[dict[str, Any]] = []
        for spec in self._tool_registry.list_enabled_tools():
            if spec.category != "ai_tool":
                continue  # ingestion 类工具不暴露给 AI 对话
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters_schema
                        or {
                            "type": "object",
                            "properties": {},
                        },
                    },
                }
            )
        return tuple(schemas)

    async def _execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        user: Any,
        org_id: UUID,
    ) -> dict[str, Any]:
        """执行白名单工具的真实查询，返回结果数据。

        根据工具名称分派到对应的服务方法执行真实查询。
        工具执行均为只读操作，不修改平台数据。

        Args:
            tool_name: 工具名称。
            args: 工具参数。
            user: 当前用户。
            org_id: 组织 ID。

        Returns:
            dict: 包含 ``summary``（结果摘要）和 ``data``（结构化结果）的字典。
        """
        if tool_name == "search_facts":
            return await self._handle_search_facts(args, org_id)
        elif tool_name == "search_standards":
            return await self._handle_search_standards(args, org_id)
        elif tool_name == "search_parameters":
            return await self._handle_search_parameters(args, org_id)
        elif tool_name == "explain_provenance":
            return await self._handle_explain_provenance(args, org_id)
        elif tool_name == "compare_experiments":
            return await self._handle_compare_experiments(args, org_id)
        elif tool_name == "run_published_model":
            return await self._handle_run_model(args, user, org_id)
        elif tool_name == "draft_report":
            return await self._handle_draft_report(args, org_id)
        elif tool_name == "extract_data":
            return await self._handle_extract_data(args, org_id)
        else:
            return {
                "summary": f"未实现的工具: {tool_name}",
                "data": {"error": f"Tool not implemented: {tool_name}"},
            }

    async def _handle_search_facts(self, args: dict[str, Any], org_id: UUID) -> dict[str, Any]:
        """执行 search_facts 工具：搜索实验事实。"""
        query = str(args.get("query", ""))
        fact_type = str(args.get("fact_type", "")) or None

        if self._fact_service is not None:
            try:
                results = await self._fact_service.search(
                    query=query,
                    fact_type=fact_type,
                    organization_id=org_id,
                    limit=20,
                )
                items = []
                for r in (results or [])[:20]:
                    item = {
                        "id": str(r.get("id", "")),
                        "subject_id": str(r.get("subject_id", "")),
                        "fact_type": str(r.get("fact_type", "")),
                        "data_summary": str(r.get("data_summary", "")),
                    }
                    items.append(item)
                return {
                    "summary": f"搜索到 {len(items)} 条事实",
                    "data": {"count": len(items), "results": items},
                }
            except Exception:
                # fact_service.search 参数不匹配时走数据库 fallback
                pass

        # 直接查数据库（含 data_summary）
        async with self._factory() as session:
            stmt = sa.select(
                sa.text("f.id, f.subject_id, f.fact_type")
            ).select_from(sa.text("fact f"))
            conditions = [sa.text("f.organization_id = :org_id")]
            params: dict[str, Any] = {"org_id": org_id}
            if query:
                conditions.append(sa.text("subject_id ILIKE :query"))
                params["query"] = f"%{query}%"
            if fact_type:
                conditions.append(sa.text("fact_type = :fact_type"))
                params["fact_type"] = fact_type
            stmt = stmt.where(*conditions).limit(20)
            result = await session.execute(stmt, params)
            rows = result.fetchall()
            items = [
                {
                    "id": str(r[0]),
                    "subject_id": str(r[1]),
                    "fact_type": str(r[2]),
                    "data_summary": "",
                }
                for r in rows
            ]
            return {
                "summary": f"搜索到 {len(items)} 条事实",
                "data": {"count": len(items), "results": items},
            }

    async def _handle_search_standards(self, args: dict[str, Any], org_id: UUID) -> dict[str, Any]:
        """执行 search_standards 工具：搜索标准变量。"""
        query = str(args.get("query", ""))
        async with self._factory() as session:
            stmt = (
                sa.select(sa.text("vv.id, v.code, vv.display_name, vv.canonical_unit"))
                .select_from(sa.text("variable_version vv"))
                .join(sa.text("variable v"), sa.text("v.id = vv.variable_id"))
                .where(
                    sa.text("v.organization_id = :org_id"),
                    sa.text("vv.status = 'published'"),
                )
            )
            params: dict[str, Any] = {"org_id": org_id}
            if query:
                stmt = stmt.where(sa.text("(v.code ILIKE :q OR vv.display_name ILIKE :q)"))
                params["q"] = f"%{query}%"
            stmt = stmt.limit(20)
            result = await session.execute(stmt, params)
            rows = result.fetchall()
            items = [
                {
                    "id": str(r[0]),
                    "code": str(r[1]),
                    "display_name": str(r[2]),
                    "unit": str(r[3]) if r[3] else "",
                }
                for r in rows
            ]
            return {
                "summary": f"搜索到 {len(items)} 个标准变量",
                "data": {"count": len(items), "results": items},
            }

    async def _handle_search_parameters(self, args: dict[str, Any], org_id: UUID) -> dict[str, Any]:
        """执行 search_parameters 工具：搜索参数。"""
        variable_code = str(args.get("variable_code", ""))
        if self._parameter_service is not None:
            try:
                results = await self._parameter_service.search_by_variable(
                    variable_code=variable_code,
                    organization_id=org_id,
                )
                items = [
                    {
                        "id": str(r.get("id", "")),
                        "variable_code": str(r.get("variable_code", "")),
                        "value": str(r.get("value", "")),
                        "status": str(r.get("status", "")),
                    }
                    for r in (results or [])[:20]
                ]
                return {
                    "summary": f"搜索到 {len(items)} 个参数",
                    "data": {"count": len(items), "results": items},
                }
            except Exception as exc:
                return {
                    "summary": f"参数搜索失败: {exc}",
                    "data": {"error": str(exc)},
                }
        return {
            "summary": "参数服务不可用",
            "data": {"error": "parameter_service not configured"},
        }

    async def _handle_explain_provenance(
        self, args: dict[str, Any], org_id: UUID
    ) -> dict[str, Any]:
        """执行 explain_provenance 工具：解释溯源链路。"""
        parameter_id = str(args.get("parameter_id", ""))
        if self._provenance_service is not None:
            try:
                chain = await self._provenance_service.explain(
                    parameter_id=parameter_id,
                    organization_id=org_id,
                )
                return {
                    "summary": f"溯源链路包含 {len(chain.get('steps', []))} 个步骤",
                    "data": chain,
                }
            except Exception as exc:
                return {
                    "summary": f"溯源查询失败: {exc}",
                    "data": {"error": str(exc)},
                }
        return {
            "summary": "溯源服务不可用",
            "data": {"error": "provenance_service not configured"},
        }

    async def _handle_compare_experiments(
        self, args: dict[str, Any], org_id: UUID
    ) -> dict[str, Any]:
        """执行 compare_experiments 工具：对比实验事实。"""
        fact_ids = args.get("fact_ids", [])
        if not isinstance(fact_ids, list) or len(fact_ids) < 2:
            return {
                "summary": "需要至少 2 个事实 ID 进行对比",
                "data": {"error": "At least 2 fact_ids required"},
            }

        if self._fact_service is not None:
            try:
                facts = []
                for fid in fact_ids[:5]:
                    fact = await self._fact_service.get(
                        fact_id=UUID(str(fid)),
                        organization_id=org_id,
                    )
                    if fact:
                        facts.append(fact)
                return {
                    "summary": f"对比了 {len(facts)} 个实验事实",
                    "data": {
                        "count": len(facts),
                        "comparisons": [
                            {
                                "id": str(f.get("id", "")),
                                "subject_id": str(f.get("subject_id", "")),
                                "fact_type": str(f.get("fact_type", "")),
                                "data_summary": str(f.get("data_summary", "")),
                            }
                            for f in facts
                        ],
                    },
                }
            except Exception as exc:
                return {
                    "summary": f"实验对比失败: {exc}",
                    "data": {"error": str(exc)},
                }
        return {
            "summary": "事实服务不可用",
            "data": {"error": "fact_service not configured"},
        }

    async def _handle_run_model(
        self, args: dict[str, Any], user: Any, org_id: UUID
    ) -> dict[str, Any]:
        """执行 run_published_model 工具：运行已发布模型预测。"""
        model_id = str(args.get("model_id", ""))
        inputs = args.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}

        if self._model_service is not None:
            try:
                result = await self._model_service.predict(
                    model_id=UUID(model_id),
                    inputs=inputs,
                )
                return {
                    "summary": f"模型预测完成，版本 {result.version}",
                    "data": {
                        "model_id": str(result.model_id),
                        "model_version_id": str(result.model_version_id),
                        "version": result.version,
                        "predictions": dict(result.predictions),
                        "fact_id": str(result.fact_id) if result.fact_id else None,
                    },
                }
            except Exception as exc:
                return {
                    "summary": f"模型预测失败: {exc}",
                    "data": {"error": str(exc)},
                }
        return {
            "summary": "模型服务不可用",
            "data": {"error": "model_service not configured"},
        }

    async def _handle_draft_report(self, args: dict[str, Any], org_id: UUID) -> dict[str, Any]:
        """执行 draft_report 工具：生成报告草稿（只读，不落库）。"""
        title = str(args.get("title", "未命名报告"))
        fact_ids = args.get("fact_ids", [])
        if not isinstance(fact_ids, list):
            fact_ids = []

        # 查询引用的事实摘要
        fact_summaries: list[dict[str, str]] = []
        if fact_ids and self._factory is not None:
            async with self._factory() as session:
                for fid in fact_ids[:10]:
                    try:
                        result = await session.execute(
                            sa.select(sa.text("subject_id, fact_type"))
                            .select_from(sa.text("fact"))
                            .where(
                                sa.text("id = :fid"),
                                sa.text("organization_id = :org_id"),
                            ),
                            {"fid": UUID(str(fid)), "org_id": org_id},
                        )
                        row = result.fetchone()
                        if row:
                            fact_summaries.append(
                                {
                                    "fact_id": str(fid),
                                    "subject_id": str(row[0]),
                                    "fact_type": str(row[1]),
                                }
                            )
                    except Exception:
                        pass

        return {
            "summary": f"报告草稿已生成，引用 {len(fact_summaries)} 个事实",
            "data": {
                "title": title,
                "referenced_facts": fact_summaries,
                "note": "草稿不落库，需用户确认后保存",
            },
        }

    async def _handle_extract_data(self, args: dict[str, Any], org_id: UUID) -> dict[str, Any]:
        """执行 extract_data 工具：数据提取（标记为需要 ingestion:write 权限）。"""
        path = str(args.get("path", ""))
        prompt = str(args.get("prompt", ""))
        return {
            "summary": f"数据提取请求已记录（路径: {path[:100]}）",
            "data": {
                "path": path[:200],
                "prompt": prompt[:500],
                "note": "数据提取需要 ingestion 服务支持，当前返回元数据",
            },
        }

    def _redact_credentials(self, text: str) -> str:
        """凭据脱敏：移除回答中可能出现的密钥模式。

        扫描回答文本，将疑似 API 密钥、Bearer 令牌的模式替换为 [REDACTED]。
        确保 AI 回答不泄露凭据。

        Args:
            text: 原始回答文本。

        Returns:
            str: 脱敏后的回答文本。
        """
        import re

        # 替换 Bearer token 模式
        text = re.sub(r"[Bb]earer\s+[A-Za-z0-9\-_\.]{20,}", "[REDACTED]", text)
        # 替换疑似 API key 模式（sk- 开头或长 hex/base64 串）
        text = re.sub(r"sk-[A-Za-z0-9]{20,}", "[REDACTED]", text)
        return text

    async def _persist_user_message_only(
        self,
        conversation_id: UUID,
        user_id: UUID,
        question: str,
        mentions: list[str] | None = None,
        sender_display_name: str | None = None,
        sender_avatar_url: str | None = None,
    ) -> None:
        """仅持久化用户消息（不创建 AI 回复消息）。

        irip-ai-collab: 当消息仅 @人（不触发 AI）时调用此方法，
        只保存用户消息和 mentions，不生成 assistant 消息。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID。
            question: 用户问题文本。
            mentions: @ 人的 user_id 字符串数组。
            sender_display_name: 发送者显示名（从 app_user 快照）。
            sender_avatar_url: 发送者头像 URL（从 app_user 快照）。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            actual_display_name = sender_display_name
            actual_avatar_url = sender_avatar_url
            try:
                from packages.auth.entities import AppUser

                sender = await session.scalar(
                    sa.select(AppUser).where(AppUser.id == user_id)
                )
                if sender is not None:
                    actual_display_name = sender.display_name
                    actual_avatar_url = sender.avatar_url
            except Exception:
                pass

            user_msg = AIMessage(
                id=new_id(),
                conversation_id=conversation_id,
                role="user",
                content=question,
                tool_calls_json=[],
                citations_json=[],
                uncertainty=None,
                created_at=now,
                mentions=list(mentions) if mentions else [],
                sender_user_id=user_id,
                sender_display_name=actual_display_name,
                sender_avatar_url=actual_avatar_url,
            )
            session.add(user_msg)

            await session.execute(
                sa.update(AIConversation)
                .values(updated_at=now)
                .where(AIConversation.id == conversation_id)
            )

    async def _persist_messages(
        self,
        conversation_id: UUID,
        user_id: UUID,
        question: str,
        response: AIResponse,
        mentions: list[str] | None = None,
        sender_display_name: str | None = None,
        sender_avatar_url: str | None = None,
    ) -> None:
        """持久化用户消息与 AI 消息到数据库。

        irip-ai-collab: 用户消息填充 mentions + sender_user_id + sender_display_name
        + sender_avatar_url；AI 消息 sender 字段为 None。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID。
            question: 用户问题。
            response: AI 回答。
            mentions: @ 人的 user_id 字符串数组。
            sender_display_name: 发送者显示名（从 app_user 快照）。
            sender_avatar_url: 发送者头像 URL（从 app_user 快照）。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            # irip-ai-collab: 从数据库获取发送者 display_name 和 avatar_url 快照
            actual_display_name = sender_display_name
            actual_avatar_url = sender_avatar_url
            try:
                from packages.auth.entities import AppUser

                sender = await session.scalar(
                    sa.select(AppUser).where(AppUser.id == user_id)
                )
                if sender is not None:
                    actual_display_name = sender.display_name
                    actual_avatar_url = sender.avatar_url
            except Exception:
                pass

            # 用户消息（irip-ai-collab: 填充 mentions + sender 字段）
            user_msg = AIMessage(
                id=new_id(),
                conversation_id=conversation_id,
                role="user",
                content=question,
                tool_calls_json=[],
                citations_json=[],
                uncertainty=None,
                created_at=now,
                mentions=list(mentions) if mentions else [],
                sender_user_id=user_id,
                sender_display_name=actual_display_name,
                sender_avatar_url=actual_avatar_url,
            )
            session.add(user_msg)

            # AI 消息
            tool_calls_list: list[dict[str, Any]] = [
                {
                    "tool": tc.get("tool", ""),
                    "args": tc.get("args", {}),
                    "summary": tc.get("summary", ""),
                    "status": tc.get("status", ""),
                }
                for tc in response.tool_calls
            ]
            citations_list: list[dict[str, str]] = []
            for c in response.citations:
                if isinstance(c, Citation):
                    citations_list.append(c.to_dict())
                elif isinstance(c, SignedCitation):
                    citations_list.append(c.to_dict())
                elif isinstance(c, dict):
                    citations_list.append(c)

            ai_msg = AIMessage(
                id=new_id(),
                conversation_id=conversation_id,
                role="assistant",
                content=response.answer,
                tool_calls_json=tool_calls_list,
                citations_json=citations_list,
                uncertainty=response.uncertainty,
                created_at=now,
            )
            session.add(ai_msg)

            # 更新对话 updated_at
            await session.execute(
                sa.update(AIConversation)
                .values(updated_at=now)
                .where(AIConversation.id == conversation_id)
            )

    async def _auto_generate_title(
        self,
        conversation_id: UUID,
        question: str,
        answer: str,
    ) -> None:
        """首次对话后自动生成标题。

        直接用 httpx 调用 LLM API 生成标题（不走 thinking 模式），
        然后更新数据库中的对话标题。失败时静默跳过。

        Args:
            conversation_id: 对话 ID。
            question: 用户问题。
            answer: AI 回答。
        """
        # 从 provider 提取 API 配置
        api_key = getattr(self._provider, "_api_key", None)
        base_url = getattr(self._provider, "_base_url", None)
        model = getattr(self._provider, "_model", None)

        if not api_key or not base_url or not model:
            # 离线模式或其他无 API 配置的 provider，用问题前 30 字做标题
            title = question[:30].strip()
            if not title:
                return
        else:
            # H-05: 使用 SafeHTTPClient（SSRF 防护）
            from packages.common.safe_http import SafeHTTPClient

            try:
                async with SafeHTTPClient(timeout=15.0, max_size=1024 * 1024) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": [
                                {
                                    "role": "system",
                                    "content": (
                                        "请用一句话概括以下对话的主题，不超过15个字。"
                                        "直接返回标题文本，不要解释、不要引号。"
                                    ),
                                },
                                {
                                    "role": "user",
                                    "content": (
                                        f"用户问题：{question[:500]}\nAI回答：{answer[:500]}"
                                    ),
                                },
                            ],
                            "max_tokens": 200,
                            # 关闭思考模式，避免 token 浪费在思考过程
                            "chat_template_kwargs": {"enable_thinking": False},
                        },
                    )
                if resp.status_code != 200:
                    return
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    return
                msg = choices[0].get("message", {})
                # 优先取 content，回退到 reasoning_content（Qwen3 思考模式）
                title = (msg.get("content") or msg.get("reasoning_content") or "").strip()
            except Exception:
                return

        # 清理标题
        title = title.strip("\"'''「」『』 \n\r\t")  # noqa: B005
        title = title.split("\n")[0].strip()
        if len(title) > 60:
            title = title[:60]
        if not title:
            return

        # 更新数据库
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            await session.execute(
                sa.update(AIConversation)
                .values(title=title, updated_at=now)
                .where(AIConversation.id == conversation_id)
            )

    # ---- Provider 状态 ----

    def cancel_request(self, conversation_id: UUID) -> bool:
        """取消正在进行的 AI 请求。

        Args:
            conversation_id: 对话 ID。

        Returns:
            bool: 是否成功取消（False 表示没有正在进行的请求）。
        """
        event = _active_requests.get(conversation_id)
        if event is not None:
            event.set()
            return True
        return False

    async def reload_tools(self) -> None:
        """从 DB 重新加载工具注册表（供 provider-status 等端点调用）。

        确保管理页面的启用/禁用变更能立即反映到状态查询中，
        而不仅是在 ask 时才 reload。
        """
        if self._factory is not None:
            async with session_scope(self._factory) as session:
                await self._tool_registry.reload_from_db(session)

    def get_provider_status(self) -> dict[str, Any]:
        """返回当前 Provider 状态信息。

        Returns:
            dict: 包含 provider_mode、可用工具列表（仅已启用工具）。
        """
        # 仅展示已启用的 ai_tool 分类工具（ingestion 类不展示给 AI 对话）
        enabled = [s for s in self._tool_registry.list_enabled_tools() if s.category == "ai_tool"]
        return {
            "provider_mode": getattr(self._provider, "provider_mode", "unknown"),
            "whitelist_tools": [
                {
                    "name": s.name,
                    "display_name": s.display_name,
                    "description": s.description,
                    "required_permission": s.required_permission,
                }
                for s in enabled
            ],
            "candidate_tools": [],
        }
