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
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import Clock, SystemClock
from packages.common.database import session_scope
from packages.jobs.outbox import OutboxEvent
from packages.jobs.task_sender import TaskSender

logger = logging.getLogger(__name__)

#: 默认批量拉取大小。
DEFAULT_BATCH_SIZE: int = 100

#: Research timeline 事件的显式路由白名单。
#: 不允许由 payload 注入任意 task 名。
RESEARCH_EVENT_ROUTES: dict[str, tuple[str, str]] = {
    "research.recommendation.requested": (
        "research.recommendations.generate",
        "irip-research",
    ),
    "research.run.requested": (
        "research.run.execute",
        "irip-research",
    ),
    "research.candidate_extraction.requested": (
        "research.candidates.extract",
        "irip-research",
    ),
}


class OutboxDispatcherService:
    """Outbox 事件周期调度服务。

    由 Celery Beat 定时调用 ``dispatch()`` 方法，拉取未投递的 outbox 事件
    并通过依赖注入的 ``TaskSender.send_task`` 发送到 Celery broker。

    Attributes:
        _factory: 异步会话工厂。
        _clock: 时钟实例。
        _task_sender: 任务发送者（依赖注入）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock | None = None,
        task_sender: TaskSender | None = None,
    ) -> None:
        """初始化 Outbox 调度服务。

        Phase 3 架构收敛（T3-3）：通过 ``task_sender`` 依赖注入接收任务投递通道，
        不再在 ``_send_to_celery`` 中 lazy import ``apps.worker.celery_app``。
        由 ``apps/`` 组装层注入真实 Celery 实例，测试中注入测试替身。

        Args:
            session_factory: 异步会话工厂。
            clock: 时钟（默认 SystemClock）。
            task_sender: 任务发送者（满足 ``TaskSender`` 协议）。``None`` 时
                ``_send_to_celery`` 将跳过投递并返回 False，等待下次调度重试；
                生产环境必须由组装层注入。
        """
        self._factory = session_factory
        self._clock = clock or SystemClock()
        self._task_sender = task_sender

    async def dispatch(self, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
        """拉取未投递事件并发送到 Celery，返回已投递事件数。

        使用 ``FOR UPDATE SKIP LOCKED`` 拉取未投递事件（``delivered_at IS NULL``），
        逐条通过依赖注入的 ``TaskSender.send_task`` 发送，发送成功后标记
        ``delivered_at``。

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
                        "Failed to dispatch event %s (kind=%s); will retry on next cycle",
                        event.id,
                        event.event_type,
                    )

        return delivered_count

    async def _send_to_celery(self, event: OutboxEvent) -> bool:
        """发送事件到 Celery broker。

        通过依赖注入的 ``self._task_sender.send_task`` 将作业 ID 发送到
        按 job kind 路由的 Celery 队列；未配置 ``TaskSender`` 时跳过并返回 False，
        等待下一调度周期重试。

        队列路由：
        - research.* 事件走显式白名单路由表（RESEARCH_EVENT_ROUTES）
        - 其他事件从 outbox event payload 中读取 job kind
        - 无法确定 kind 时使用默认队列

        Args:
            event: 待发送的 outbox 事件。

        Returns:
            bool: 发送成功返回 True。
        """
        if self._task_sender is None:
            logger.warning(
                "TaskSender not configured; skip dispatch for event %s (will retry on next cycle)",
                event.id,
            )
            return False
        try:
            # Check research event routes first (explicit whitelist)
            if event.event_type in RESEARCH_EVENT_ROUTES:
                task_name, queue = RESEARCH_EVENT_ROUTES[event.event_type]
                self._task_sender.send_task(
                    task_name,
                    args=[str(event.aggregate_id)],
                    queue=queue,
                )
                logger.info(
                    "Dispatched research event %s (type=%s, aggregate_id=%s, queue=%s)",
                    event.id,
                    event.event_type,
                    event.aggregate_id,
                    queue,
                )
                return True

            # Default routing via job kind
            default_queue: str = "irip-normal"
            payload: dict[str, Any] | None = event.payload
            if payload and "kind" in payload:
                kind: str = str(payload["kind"])
                from packages.common.job_policy import JobKindPolicy

                policy = JobKindPolicy.get_policy(kind)
                if policy is not None:
                    default_queue = policy.queue
            self._task_sender.send_task(
                "jobs.execute",
                args=[str(event.aggregate_id)],
                queue=default_queue,
            )
            logger.info(
                "Dispatched event %s (type=%s, aggregate_id=%s, queue=%s)",
                event.id,
                event.event_type,
                event.aggregate_id,
                default_queue,
            )
            return True
        except Exception:
            logger.exception("Failed to send task for event %s", event.id)
            return False

    async def get_undelivered_count(self) -> int:
        """获取未投递事件数（用于监控/测试）。

        Returns:
            int: 未投递事件数。
        """
        async with session_scope(self._factory) as session:
            result = await session.execute(
                sa.select(sa.func.count(OutboxEvent.id)).where(OutboxEvent.delivered_at.is_(None))
            )
            count: int = result.scalar() or 0
            return count


def run_dispatch(task_sender: TaskSender | None = None) -> int:
    """Celery Beat 调度入口：同步包装的 dispatch 调用。

    由 Celery Beat 定时调用此函数，触发 Outbox 事件投递。
    ``apps/`` 组装层（``dispatch_outbox`` Beat 任务）在调用时注入真实的
    ``celery_app`` 作为 ``task_sender``，从而避免 ``packages`` 层直接依赖
    ``apps.worker.celery_app``。

    Args:
        task_sender: 任务发送者（满足 ``TaskSender`` 协议）。生产环境由
            Celery Beat 任务入口注入 ``celery_app``；为 ``None`` 时投递被跳过。

    Returns:
        int: 已投递事件数。
    """
    import asyncio
    import os

    from packages.common.database import build_session_factory

    db_url = os.getenv("IRIP_DATABASE_URL", "")
    if db_url.startswith("postgresql+psycopg://"):
        async_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    else:
        async_url = db_url

    factory = build_session_factory(async_url)
    dispatcher = OutboxDispatcherService(factory, task_sender=task_sender)

    return asyncio.run(dispatcher.dispatch())
