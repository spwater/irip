"""OutboxDispatcherService：周期调度入口，使用 FOR UPDATE SKIP LOCKED 拉取 pending 事件。

技术设计文档 F-04 §8.5：所有异步任务**只**通过 Outbox→Dispatcher→Celery 一条通道。
Dispatcher 由 Celery Beat 定时触发，使用 ``FOR UPDATE SKIP LOCKED`` 拉取未投递事件，
支持多 Dispatcher 并发，然后通过 ``celery_app.send_task`` 发送到 Celery broker。

设计要点：
- ``FOR UPDATE SKIP LOCKED`` 确保多个 Dispatcher 不会拉取到相同事件；
- 拉取后立即标记 ``delivered_at``，防止重复投递；
- 发送失败时不标记 ``delivered_at``，下次调度会重新拉取。
"""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import Clock, SystemClock
from packages.common.database import session_scope
from packages.jobs.outbox import OutboxEvent

logger = logging.getLogger(__name__)

#: 默认批量拉取大小。
DEFAULT_BATCH_SIZE: int = 100


class OutboxDispatcherService:
    """Outbox 事件周期调度服务。

    由 Celery Beat 定时调用 ``dispatch()`` 方法，拉取未投递的 outbox 事件
    并通过 ``celery_app.send_task`` 发送到 Celery broker。

    Attributes:
        _factory: 异步会话工厂。
        _clock: 时钟实例。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock | None = None,
    ) -> None:
        """初始化 Outbox 调度服务。

        Args:
            session_factory: 异步会话工厂。
            clock: 时钟（默认 SystemClock）。
        """
        self._factory = session_factory
        self._clock = clock or SystemClock()

    async def dispatch(self, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
        """拉取未投递事件并发送到 Celery，返回已投递事件数。

        使用 ``FOR UPDATE SKIP LOCKED`` 拉取未投递事件（``delivered_at IS NULL``），
        逐条通过 ``celery_app.send_task`` 发送，发送成功后标记 ``delivered_at``。

        Args:
            batch_size: 单次拉取数量上限。

        Returns:
            int: 已投递事件数。
        """
        delivered_count: int = 0

        async with session_scope(self._factory) as session:
            # 使用 FOR UPDATE SKIP LOCKED 拉取未投递事件
            # 支持多 Dispatcher 并发，不会拉取到相同事件
            result = await session.execute(
                sa.select(OutboxEvent)
                .where(OutboxEvent.delivered_at.is_(None))
                .order_by(OutboxEvent.occurred_at)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            events: list[OutboxEvent] = list(result.scalars().all())

            for event in events:
                sent = await self._send_to_celery(event)
                if sent:
                    await session.execute(
                        sa.update(OutboxEvent)
                        .values(delivered_at=self._clock.now())
                        .where(OutboxEvent.id == event.id)
                    )
                    delivered_count += 1
                else:
                    logger.warning(
                        "Failed to dispatch event %s (kind=%s); "
                        "will retry on next cycle",
                        event.id,
                        event.event_type,
                    )

        return delivered_count

    async def _send_to_celery(self, event: OutboxEvent) -> bool:
        """发送事件到 Celery broker。

        通过 ``celery_app.send_task`` 将作业 ID 发送到 ``irip-jobs`` 队列。

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
                "Failed to send task for event %s", event.id
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


def run_dispatch() -> int:
    """Celery Beat 调度入口：同步包装的 dispatch 调用。

    由 Celery Beat 定时调用此函数，触发 Outbox 事件投递。

    Returns:
        int: 已投递事件数。
    """
    import asyncio
    import os

    from packages.common.database import build_session_factory

    db_url = os.getenv(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip",
    )
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
    else:
        async_url = db_url

    factory = build_session_factory(async_url)
    dispatcher = OutboxDispatcherService(factory)

    return asyncio.run(dispatcher.dispatch())
