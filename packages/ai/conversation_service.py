"""AI 对话管理服务。

从 ``service.py`` 提取的对话 CRUD + 搜索逻辑。
职责：创建对话、列出对话、置顶/归档切换、删除对话、列出消息、搜索对话。

依赖注入：
- session_factory: 异步会话工厂
- clock: 时钟依赖
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.ai.collaboration_entities import ConversationParticipant
from packages.ai.entities import AIConversation, AIMessage, ConversationRef, MessageRef
from packages.common.clock import Clock
from packages.common.database import scoped_session
from packages.common.errors import AppError
from packages.common.ids import new_id


class ConversationService:
    """AI 对话管理服务。

    Attributes:
        _factory: 异步会话工厂。
        _clock: 时钟依赖。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None,
        clock: Clock,
    ) -> None:
        """初始化对话管理服务。

        Args:
            session_factory: 异步会话工厂。
            clock: 时钟依赖。
        """
        self._factory = session_factory
        self._clock = clock

    async def create_conversation(
        self,
        user_id: UUID,
        department_id: UUID,
        title: str = "",
        provider_mode: str = "offline",
    ) -> ConversationRef:
        """创建新对话。

        Args:
            user_id: 用户 ID。
            department_id: 部门 ID。
            title: 对话标题（空时自动生成）。
            provider_mode: Provider 模式。

        Returns:
            ConversationRef: 新对话引用。
        """
        conv_id = new_id()
        now = self._clock.now()
        if not title:
            title = f"对话 {now.strftime('%Y-%m-%d %H:%M')}"

        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
            conv = AIConversation(
                id=conv_id,
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
        department_id: UUID,
        limit: int = 50,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> list[ConversationRef]:
        """列出用户的对话（置顶优先，然后按更新时间倒序）。

        Args:
            user_id: 用户 ID（仅返回该用户的对话）。
            department_id: 部门 ID。
            limit: 最大返回数。
            include_archived: 是否包含已归档对话（默认不含）。
            archived_only: 是否只返回已归档对话（优先于 include_archived）。

        Returns:
            list[ConversationRef]: 对话引用列表。
        """
        conditions = [
            AIConversation.user_id == user_id,
        ]
        if archived_only:
            conditions.append(AIConversation.archived == sa.true())
        elif not include_archived:
            conditions.append(AIConversation.archived == sa.false())

        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
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

    async def get_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> ConversationRef | None:
        """查询单个对话（按 conversation_id + user_id）。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID（权限检查）。

        Returns:
            ConversationRef | None: 对话引用，不存在时返回 None。
        """
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
            r = await session.scalar(
                sa.select(AIConversation).where(
                    AIConversation.id == conversation_id,
                    AIConversation.user_id == user_id,
                )
            )
            if r is None:
                return None
            return ConversationRef(
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
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
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
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
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
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
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
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
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

    async def search_conversations(
        self,
        user_id: UUID,
        department_id: UUID,
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
            department_id: 部门 ID。
            keyword: 搜索关键词。
            include_archived: 是否包含已归档对话。
            archived_only: 是否只返回已归档对话。
            limit: 最大返回数。

        Returns:
            list[ConversationRef]: 匹配的对话引用列表。
        """
        conditions = [
            AIConversation.user_id == user_id,
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

        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
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
