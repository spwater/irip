"""AI 橱窗卡片实体 + 值对象（对应 ai_showcase_item 表）。

ShowcaseItem 是 AI 助手分析橱窗的持久化实体，与对话（ai_conversation）一对一绑定，
用于留存用户从对话消息中精选的内容块（图表 / 表格 / 结论 / 公式等）。

核心不变量：
- conversation_belonging: 每张卡片必须属于某个对话，删除对话时级联删除；
- block_snapshot: content_snapshot 保存块内容快照，源数据更新不影响已存卡片；
- unique_block: 同一对话内 (source_message_id, source_block_index) 唯一，防重复加入。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class ShowcaseItem(Base):
    """橱窗卡片实体（对应 ai_showcase_item 表）。

    每条记录是用户从某段对话中精选留存的一个内容块快照。

    Attributes:
        id: 卡片 UUID。
        conversation_id: 所属对话 ID（FK→ai_conversation.id，CASCADE）。
        user_id: 创建用户 ID。
        sort_order: 拖拽排序序号（从 0 递增）。
        block_type: 块类型（echarts / plotly / table / conclusion / formula / text）。
        title: 卡片标题（可编辑）。
        content_snapshot: 块内容完整快照（JSON 配置 / Markdown 原文）。
        source_message_id: 来源消息 UUID。
        source_block_index: 来源块在消息内的序号（从 0 开始）。
        data_source: 数据来源信息 JSONB（样品标签、任务名、字段等）。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    __tablename__ = "ai_showcase_item"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    conversation_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("ai_conversation.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    sort_order: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    block_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(200), nullable=False, default="")
    content_snapshot: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_message_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    source_block_index: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0
    )
    data_source: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


@dataclass(frozen=True)
class ShowcaseItemRef:
    """橱窗卡片引用（不可变值对象）。

    用于服务层向路由层传递卡片数据，隔离 ORM 实体与 API 响应。

    Attributes:
        id: 卡片 UUID。
        conversation_id: 所属对话 UUID。
        sort_order: 排序序号。
        block_type: 块类型。
        title: 卡片标题。
        content_snapshot: 块内容快照。
        source_message_id: 来源消息 UUID。
        source_block_index: 来源块序号。
        data_source: 数据来源信息字典。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    id: UUID
    conversation_id: UUID
    sort_order: int
    block_type: str
    title: str
    content_snapshot: str
    source_message_id: UUID
    source_block_index: int
    data_source: dict[str, Any]
    created_at: datetime
    updated_at: datetime
