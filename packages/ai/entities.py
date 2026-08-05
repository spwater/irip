"""AI 模块 ORM 实体与值对象定义。

包含 AI 对话表、消息表的 ORM 映射，以及对应的不可变值对象（Ref）。

实体：
- AIConversation: AI 对话表 ORM 映射（ai_conversation）。
- AIMessage: AI 消息表 ORM 映射（ai_message）。

值对象：
- ConversationRef: 对话引用（不可变快照）。
- MessageRef: 消息引用（不可变快照）。

迁移兼容：migrations/env.py 和 tests/conftest.py 通过
``import packages.ai.service`` 间接导入此模块完成 ORM 模型注册。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.clock import SystemClock
from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class AIConversation(Base):
    """AI 对话实体（对应 ai_conversation 表）。

    Attributes:
        id: 对话 UUID。
        department_id: 部门 ID。
        user_id: 创建用户 ID。
        title: 对话标题。
        provider_mode: Provider 模式（offline / openai_compatible）。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    __tablename__ = "ai_conversation"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
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
