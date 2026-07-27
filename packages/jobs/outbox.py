"""事务性 Outbox：OutboxEvent ORM + OutboxDispatcher。

对应 outbox_event 表（docs/arch-v0.md §3.1 第 321-331 行 + §7.6 异步与事务约定）。

核心模式（docs/arch-v0.md §4.2 时序图）：
- enqueue: 同事务 INSERT outbox_event（与业务写操作在同一 DB 事务中）；
- dispatch: 轮询未投递事件 → 发送 Redis/Celery → 标记已投递。

关键约束：
- "写业务表 + 触发异步事件" 必须同事务插入 outbox_event（架构文档 §7.6）；
- delivered_at NULLS FIRST + occurred_at 索引高效拉取未投递事件。
"""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.clock import Clock, SystemClock
from packages.common.database import Base, session_scope
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id

logger = logging.getLogger(__name__)


class OutboxEvent(Base):
    """Outbox 事件 ORM 模型（对应 outbox_event 表）。

    Attributes:
        id: 事件 UUID（PK）。
        aggregate_type: 聚合类型（如 ``job``）。
        aggregate_id: 聚合 ID（如 job_id）。
        event_type: 事件类型（如 ``job.accepted``）。
        payload: 事件载荷（JSONB）。
        occurred_at: 发生时间。
        delivered_at: 投递时间（NULL 表示未投递）。
    """

    __tablename__ = "outbox_event"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    aggregate_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"OutboxEvent(id={self.id!r}, event_type={self.event_type!r}, "
            f"delivered_at={self.delivered_at!r})"
        )


class OutboxDispatcher:
    """Outbox 事件调度器。

    负责将未投递的 outbox 事件发送到消息代理（Redis/Celery），
    并标记为已投递。

    设计：
    - enqueue: 在调用方事务中 INSERT（静态方法，直接操作 session）；
    - dispatch: 独立事务中读取未投递事件 → 发送 → 标记已投递。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock | None = None,
        redis_url: str | None = None,
    ) -> None:
        """初始化 Outbox 调度器。

        Args:
            session_factory: 异步会话工厂。
            clock: 时钟（默认 SystemClock）。
            redis_url: Redis 连接 URL（用于实际发送，None 时模拟发送）。
        """
        self._factory = session_factory
        self._clock = clock or SystemClock()
        self._redis_url = redis_url

    @staticmethod
    async def enqueue(
        session: AsyncSession,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> OutboxEvent:
        """在当前事务中插入 outbox 事件。

        必须在写业务表的同一事务中调用（架构文档 §7.6）。

        Args:
            session: 数据库异步会话（由调用方管理事务）。
            aggregate_type: 聚合类型（如 ``job``）。
            aggregate_id: 聚合 ID（如 job_id）。
            event_type: 事件类型（如 ``job.accepted``）。
            payload: 事件载荷。

        Returns:
            OutboxEvent: 已插入的事件 ORM 实例。
        """
        event = OutboxEvent(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
        )
        session.add(event)
        await session.flush()
        return event

    async def dispatch(self, batch_size: int = 100) -> int:
        """拉取未投递事件并发送，返回已投递事件数。

        流程：
        1. SELECT 未投递事件（delivered_at IS NULL），按 occurred_at 升序；
        2. 逐条发送到 Redis/Celery；
        3. 标记 delivered_at = now。

        Args:
            batch_size: 单次拉取数量上限。

        Returns:
            int: 已投递事件数。
        """
        delivered_count = 0

        async with session_scope(self._factory) as session:
            result = await session.execute(
                sa.select(OutboxEvent)
                .where(OutboxEvent.delivered_at.is_(None))
                .order_by(OutboxEvent.occurred_at)
                .limit(batch_size)
            )
            events: list[OutboxEvent] = list(result.scalars().all())

            for event in events:
                # 发送到 Redis/Celery（此处模拟发送）
                sent = await self._send_to_broker(event)
                if sent:
                    await session.execute(
                        sa.update(OutboxEvent)
                        .values(delivered_at=self._clock.now())
                        .where(OutboxEvent.id == event.id)
                    )
                    delivered_count += 1

        return delivered_count

    async def _send_to_broker(self, event: OutboxEvent) -> bool:
        """发送事件到 Celery broker。

        技术设计文档 F-04 §8.5：统一通过 ``celery_app.send_task`` 发送，
        不再使用 Redis LPUSH。所有异步任务只通过 Outbox→Dispatcher→Celery
        一条通道。

        Args:
            event: 待发送的 outbox 事件。

        Returns:
            bool: 发送成功返回 True。
        """
        try:
            from apps.worker.celery_app import celery_app

            celery_app.send_task(
                "jobs.execute",
                args=[str(event.aggregate_id)],
                queue="irip-jobs",
            )
            logger.info(
                "Dispatched event %s (type=%s, aggregate_id=%s)",
                event.id,
                event.event_type,
                event.aggregate_id,
            )
            return True
        except Exception:
            logger.exception(
                "Failed to dispatch event %s", event.id
            )
            return False

    async def get_undelivered_count(self) -> int:
        """获取未投递事件数（用于监控/测试）。

        Returns:
            int: 未投递事件数。
        """
        async with session_scope(self._factory) as session:
            result = await session.execute(
                sa.select(sa.func.count(OutboxEvent.id)).where(
                    OutboxEvent.delivered_at.is_(None)
                )
            )
            count: int = result.scalar() or 0
            return count

    async def reset_delivered(
        self,
        aggregate_id: UUID | None = None,
    ) -> int:
        """重置已投递事件为未投递（用于恢复测试 / Redis 重建）。

        Args:
            aggregate_id: 仅重置指定聚合的事件（None 时重置全部）。

        Returns:
            int: 重置的事件数。
        """
        async with session_scope(self._factory) as session:
            conditions: list[Any] = [OutboxEvent.delivered_at.is_not(None)]
            if aggregate_id is not None:
                conditions.append(OutboxEvent.aggregate_id == aggregate_id)

            from sqlalchemy.engine import CursorResult

            result = await session.execute(
                sa.update(OutboxEvent)
                .values(delivered_at=None)
                .where(*conditions)
            )
            typed_result: CursorResult[Any] = result  # type: ignore[assignment]
            return typed_result.rowcount
