"""AI 助手协作实体：对话参与者 ORM 模型 + 值对象。

定义：
- ConversationParticipant(Base)：对话参与者表（联合主键 conversation_id + user_id）；
- ParticipantRef：参与者引用（不可变值对象）；
- MentionableUserRef：可 @ 的用户引用（不可变值对象）。

设计依据：docs/arch-ai-collab.md §3.2 SQLAlchemy 模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime


class ConversationParticipant(Base):
    """对话参与者实体（对应 conversation_participant 表）。

    联合主键 (conversation_id, user_id)，role 区分 owner / member。
    创建对话时自动插入 owner 记录；邀请成员时插入 member 记录。

    Attributes:
        conversation_id: 对话 ID（FK→ai_conversation.id, ON DELETE CASCADE）。
        user_id: 用户 ID（FK→app_user.id, ON DELETE CASCADE）。
        role: 参与者角色（owner / member），默认 member。
        joined_at: 加入时间。
    """

    __tablename__ = "conversation_participant"

    conversation_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("ai_conversation.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("app_user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(
        sa.String(20),
        nullable=False,
        default="member",
        server_default=sa.text("'member'"),
    )
    joined_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        server_default=sa.func.now(),
    )


@dataclass(frozen=True)
class ParticipantRef:
    """参与者引用（不可变值对象）。

    用于服务层返回参与者信息，避免直接暴露 ORM 实体。

    Attributes:
        conversation_id: 对话 ID。
        user_id: 用户 ID。
        role: 参与者角色（owner / member）。
        joined_at: 加入时间。
        display_name: 用户显示名（JOIN app_user 获取，可选）。
        avatar_url: 用户头像 URL（JOIN app_user 获取，可选）。
    """

    conversation_id: UUID
    user_id: UUID
    role: str
    joined_at: datetime
    display_name: str = ""
    avatar_url: str | None = None


@dataclass(frozen=True)
class MentionableUserRef:
    """可 @ 的用户引用（不可变值对象）。

    用于 @ 人输入组件的成员列表，包含显示名、头像和角色信息。

    Attributes:
        id: 用户 ID。
        display_name: 用户显示名。
        avatar_url: 用户头像 URL（可为 None）。
        roles: 用户角色列表（如 ["lab_member"]）。
    """

    id: UUID
    display_name: str
    avatar_url: str | None = None
    roles: list[str] = field(default_factory=list)
