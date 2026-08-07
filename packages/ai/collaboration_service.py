"""AI 协作管理服务。

从 ``service.py`` 提取的协作管理逻辑（irip-ai-collab）。
职责：按 tab 筛选对话、邀请/移除/退出参与者、列出参与者、列出可 @ 用户。

依赖注入：
- session_factory: 异步会话工厂
- clock: 时钟依赖

注意：``AppUser`` 的 import 保持为函数内延迟 import（4 处），
避免 ``packages.ai`` → ``packages.auth`` 的循环依赖。
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.ai.collaboration_entities import (
    ConversationParticipant,
    MentionableUserRef,
    ParticipantRef,
)
from packages.ai.entities import AIConversation, AIMessage, ConversationRef
from packages.common.clock import Clock
from packages.common.database import scoped_session
from packages.common.errors import AppError


class CollaborationService:
    """AI 协作管理服务。

    Attributes:
        _factory: 异步会话工厂。
        _clock: 时钟依赖。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None,
        clock: Clock,
    ) -> None:
        """初始化协作管理服务。

        Args:
            session_factory: 异步会话工厂。
            clock: 时钟依赖。
        """
        self._factory = session_factory
        self._clock = clock

    async def list_conversations_with_tab(
        self,
        user_id: UUID,
        department_id: UUID,
        tab: str = "private",
        limit: int = 50,
        include_archived: bool = False,
        archived_only: bool = False,
        keyword: str | None = None,
    ) -> list[ConversationRef]:
        """按 tab 筛选列出对话（irip-ai-collab 新增）。

        tab 分类逻辑（架构文档 §7.2）：
        - private: user_id == me AND 无其他参与者的对话
        - same_org: (user_id == me OR participant) — 同部门可见对话
          （org 命名为历史遗留，实际按 department_id 隔离）
        - cross_org: 返回空列表（已废弃，organization_id 已退役）

        结果附带参与者摘要（批量查询 conversation_participant JOIN app_user）。

        Args:
            user_id: 当前用户 ID。
            department_id: 当前用户部门 ID。
            tab: 筛选标签（private / same_org / cross_org）。
            limit: 最大返回数。
            include_archived: 是否包含已归档对话。
            archived_only: 是否只返回已归档对话。
            keyword: 搜索关键词（可选，复用 search 逻辑）。

        Returns:
            list[ConversationRef]: 对话引用列表（含参与者摘要）。
        """
        # cross_org 已废弃，不再使用
        if tab == "cross_org":
            return []

        from packages.auth.entities import AppUser

        async with scoped_session(self._factory, department_id, user_id) as session:  # type: ignore[arg-type]
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
            elif tab == "collaborative":
                # 我参与的 + 有其他参与者的对话
                other_participant_exists = (
                    sa.select(ConversationParticipant.conversation_id)
                    .where(
                        ConversationParticipant.conversation_id == AIConversation.id,
                        ConversationParticipant.user_id != user_id,
                    )
                    .exists()
                )
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
                conditions.append(other_participant_exists)
            elif tab == "same_org":
                # 同部门对话：owner 或参与者，不要求有其他参与者
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
        user_id = inviter_user_id
        now = self._clock.now()
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

            # 校验邀请者是 owner（或创建者）
            inviter_participant = await session.scalar(
                sa.select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conversation_id,
                    ConversationParticipant.user_id == inviter_user_id,
                )
            )
            is_owner = conv.user_id == inviter_user_id or (
                inviter_participant is not None and inviter_participant.role == "owner"
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

            # 权限校验：管理员角色可邀请任意人，其余只能邀请本部门
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
            # 查邀请者的角色和部门
            inviter_user = await session.scalar(
                sa.select(AppUser).where(AppUser.id == inviter_user_id)
            )
            inviter_roles = list(inviter_user.roles) if inviter_user and inviter_user.roles else []
            admin_roles = {"platform_administrator", "platform_auditor", "lab_director"}
            is_inviter_admin = len(admin_roles & set(inviter_roles)) > 0
            if not is_inviter_admin:
                # 非管理员：校验目标用户与邀请者属于同一部门
                inviter_dept = inviter_user.department_id if inviter_user else None
                if inviter_dept is not None and target_user.department_id != inviter_dept:
                    raise AppError(
                        code="validation_failed",
                        message="不能邀请跨部门用户加入对话",
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
        user_id = owner_user_id
        now = self._clock.now()
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

            # 校验操作者是 owner
            owner_participant = await session.scalar(
                sa.select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conversation_id,
                    ConversationParticipant.user_id == owner_user_id,
                )
            )
            is_owner = conv.user_id == owner_user_id or (
                owner_participant is not None and owner_participant.role == "owner"
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
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
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
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
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
                creator = await session.scalar(sa.select(AppUser).where(AppUser.id == conv.user_id))
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
        department_id: UUID,
        roles: list[str] | None = None,
    ) -> list[MentionableUserRef]:
        """列出可 @ 的用户（irip-ai-collab 新增）。

        查询同 organization 的 active 用户，返回 id / display_name / avatar_url / roles。
        irip-ai-collab: platform_administrator 不做 department 过滤（可见全租户），
        其他角色按 department_id 过滤（只可见同实验室）。

        Args:
            user_id: 当前用户 ID（排除自己）。
            department_id: 当前用户部门 ID。
            department_id: 当前用户部门 ID（非管理员时按此过滤）。
            roles: 当前用户角色列表（判断是否为管理员）。

        Returns:
            list[MentionableUserRef]: 可 @ 用户列表。
        """
        from packages.auth.entities import AppUser

        # 管理员角色可邀请任意人 → 列出全部 active 用户
        # 其余角色只能邀请本部门 → 只返回同 department 的用户
        admin_roles = {"platform_administrator", "platform_auditor", "lab_director"}
        is_admin = roles is not None and len(admin_roles & set(roles)) > 0

        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
            stmt = sa.select(
                AppUser.id,
                AppUser.display_name,
                AppUser.avatar_url,
                AppUser.roles,
            ).where(
                AppUser.status == "active",
                AppUser.id != user_id,
            )

            # 非管理员：只返回同 department 的用户
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
