"""AI 橱窗卡片管理服务。

从 ``service.py`` 提取的橱窗卡片管理逻辑。
职责：添加/列出/更新/删除/排序橱窗卡片，生成分析摘要。

依赖注入：
- session_factory: 异步会话工厂
- clock: 时钟依赖
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.ai.collaboration_entities import ConversationParticipant
from packages.ai.entities import AIConversation
from packages.ai.showcase_entities import ShowcaseItem, ShowcaseItemRef
from packages.common.clock import Clock
from packages.common.database import scoped_session
from packages.common.errors import AppError
from packages.common.ids import new_id


class ShowcaseService:
    """AI 橱窗卡片管理服务。

    Attributes:
        _factory: 异步会话工厂。
        _clock: 时钟依赖。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None,
        clock: Clock,
    ) -> None:
        """初始化橱窗卡片服务。

        Args:
            session_factory: 异步会话工厂。
            clock: 时钟依赖。
        """
        self._factory = session_factory
        self._clock = clock

    async def _check_conversation_access(
        self,
        session: AsyncSession,
        conversation_id: UUID,
        user_id: UUID,
    ) -> bool:
        """校验用户是否有权访问对话（创建者或参与者）。

        Args:
            session: 异步会话。
            conversation_id: 对话 ID。
            user_id: 用户 ID。

        Returns:
            bool: True 如果有权访问。
        """
        # 创建者
        conv = await session.scalar(
            sa.select(AIConversation).where(
                AIConversation.id == conversation_id,
                AIConversation.user_id == user_id,
            )
        )
        if conv is not None:
            return True
        # 参与者
        participant = await session.scalar(
            sa.select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id == user_id,
            )
        )
        return participant is not None

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
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
            # 校验对话归属（创建者或参与者均可）
            has_access = await self._check_conversation_access(session, conversation_id, user_id)
            if not has_access:
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
                    data_source=existing.data_source
                    if isinstance(existing.data_source, dict)
                    else {},
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
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
            # 校验对话归属（创建者或参与者均可）
            has_access = await self._check_conversation_access(session, conversation_id, user_id)
            if not has_access:
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
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
            item = await session.scalar(sa.select(ShowcaseItem).where(ShowcaseItem.id == item_id))
            if item is None:
                raise AppError(
                    code="not_found",
                    message="橱窗卡片不存在或无权操作",
                    retryable=False,
                    fields={},
                )
            # 校验对话归属（创建者或参与者均可）
            has_access = await self._check_conversation_access(
                session, item.conversation_id, user_id
            )
            if not has_access:
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
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
            item = await session.scalar(sa.select(ShowcaseItem).where(ShowcaseItem.id == item_id))
            if item is None:
                raise AppError(
                    code="not_found",
                    message="橱窗卡片不存在或无权操作",
                    retryable=False,
                    fields={},
                )
            # 校验对话归属（创建者或参与者均可）
            has_access = await self._check_conversation_access(
                session, item.conversation_id, user_id
            )
            if not has_access:
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
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
            # 校验对话归属（创建者或参与者均可）
            has_access = await self._check_conversation_access(session, conversation_id, user_id)
            if not has_access:
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
        async with scoped_session(self._factory, None, user_id) as session:  # type: ignore[arg-type]
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
