"""单元测试：OutboxDispatcherService Outbox 事件周期调度服务。

覆盖：
- dispatch：成功投递 + 无 task_sender 跳过 + 发送失败不标记投递；
- _send_to_celery：task_sender=None 跳过 + kind 路由 + 发送异常返回 False；
- get_undelivered_count：查询未投递事件数。

使用 mock session_scope + RecordingTaskSender。
"""

from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from packages.common.clock import FixedClock
from packages.jobs.dispatcher import OutboxDispatcherService

# ============================================================
# Helpers
# ============================================================


class RecordingTaskSender:
    """测试用 TaskSender 替身。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, list, str]] = []

    def send_task(self, name: str, args: list, queue: str) -> None:
        self.sent.append((name, list(args), queue))


class FailingTaskSender:
    """发送时抛异常的 TaskSender。"""

    def send_task(self, name: str, args: list, queue: str) -> None:
        raise RuntimeError("broker down")


@contextmanager
def _patch_session_scope(mock_session: AsyncMock) -> Any:
    """临时替换 dispatcher.session_scope 为返回 mock_session 的上下文。"""
    import packages.jobs.dispatcher as dispatcher_mod

    original = dispatcher_mod.session_scope

    @asynccontextmanager
    async def fake_session_scope(factory: Any, **kwargs: Any) -> Any:
        yield mock_session

    dispatcher_mod.session_scope = fake_session_scope  # type: ignore[assignment]
    try:
        yield
    finally:
        dispatcher_mod.session_scope = original  # type: ignore[assignment]


def _make_event(
    event_id: Any = None,
    event_type: str = "job.accepted",
    payload: dict[str, Any] | None = None,
) -> MagicMock:
    e = MagicMock()
    e.id = event_id or uuid4()
    e.aggregate_type = "job"
    e.aggregate_id = uuid4()
    e.event_type = event_type
    e.payload = payload
    e.occurred_at = datetime.now(UTC)
    e.delivered_at = None
    return e


def _make_execute_result(events: list[Any] | None = None, scalar: Any = None) -> MagicMock:
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = events or []
    result.scalars.return_value = scalars_mock
    result.scalar.return_value = scalar or 0
    return result


# ============================================================
# dispatch
# ============================================================


class TestDispatch:
    """dispatch 测试。"""

    async def test_dispatch_success(self) -> None:
        """成功投递事件。"""
        event = _make_event(payload={"kind": "flow_execute"})
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[
                _make_execute_result(events=[event]),  # select
                MagicMock(),  # update delivered_at
            ]
        )
        sender = RecordingTaskSender()
        svc = OutboxDispatcherService(
            MagicMock(),
            clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
            task_sender=sender,
        )

        with _patch_session_scope(session):
            count = await svc.dispatch()

        assert count == 1
        assert len(sender.sent) == 1
        assert sender.sent[0][0] == "jobs.execute"

    async def test_dispatch_no_task_sender(self) -> None:
        """无 task_sender 时跳过投递。"""
        event = _make_event()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_execute_result(events=[event]))
        svc = OutboxDispatcherService(MagicMock(), task_sender=None)

        with _patch_session_scope(session):
            count = await svc.dispatch()

        assert count == 0

    async def test_dispatch_send_failure_not_counted(self) -> None:
        """发送失败时不计数。"""
        event = _make_event(payload={"kind": "flow_execute"})
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_execute_result(events=[event]))
        svc = OutboxDispatcherService(
            MagicMock(),
            task_sender=FailingTaskSender(),
        )

        with _patch_session_scope(session):
            count = await svc.dispatch()

        assert count == 0

    async def test_dispatch_empty_events(self) -> None:
        """无事件时返回 0。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_execute_result(events=[]))
        sender = RecordingTaskSender()
        svc = OutboxDispatcherService(MagicMock(), task_sender=sender)

        with _patch_session_scope(session):
            count = await svc.dispatch()

        assert count == 0
        assert len(sender.sent) == 0

    async def test_dispatch_multiple_events(self) -> None:
        """多个事件全部投递。"""
        events = [
            _make_event(payload={"kind": "flow_execute"}),
            _make_event(payload={"kind": "ingestion"}),
        ]
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[
                _make_execute_result(events=events),
                MagicMock(),
                MagicMock(),
            ]
        )
        sender = RecordingTaskSender()
        svc = OutboxDispatcherService(MagicMock(), task_sender=sender)

        with _patch_session_scope(session):
            count = await svc.dispatch()

        assert count == 2
        assert len(sender.sent) == 2


# ============================================================
# _send_to_celery
# ============================================================


class TestSendToCelery:
    """_send_to_celery 测试。"""

    async def test_no_task_sender_returns_false(self) -> None:
        """无 task_sender 返回 False。"""
        svc = OutboxDispatcherService(MagicMock(), task_sender=None)
        result = await svc._send_to_celery(_make_event())
        assert result is False

    async def test_routes_by_kind(self) -> None:
        """按 kind 路由到对应队列。"""
        event = _make_event(payload={"kind": "flow_execute"})
        sender = RecordingTaskSender()
        svc = OutboxDispatcherService(MagicMock(), task_sender=sender)

        await svc._send_to_celery(event)

        assert sender.sent[0][2] == "irip-normal"

    async def test_unknown_kind_uses_default_queue(self) -> None:
        """未知 kind 使用默认队列。"""
        event = _make_event(payload={"kind": "unknown_kind"})
        sender = RecordingTaskSender()
        svc = OutboxDispatcherService(MagicMock(), task_sender=sender)

        await svc._send_to_celery(event)

        assert sender.sent[0][2] == "irip-normal"

    async def test_no_payload_uses_default_queue(self) -> None:
        """无 payload 使用默认队列。"""
        event = _make_event(payload=None)
        sender = RecordingTaskSender()
        svc = OutboxDispatcherService(MagicMock(), task_sender=sender)

        await svc._send_to_celery(event)

        assert sender.sent[0][2] == "irip-normal"

    async def test_fast_queue_for_ingestion(self) -> None:
        """ingestion kind 路由到 irip-fast 队列。"""
        event = _make_event(payload={"kind": "ingestion"})
        sender = RecordingTaskSender()
        svc = OutboxDispatcherService(MagicMock(), task_sender=sender)

        await svc._send_to_celery(event)

        assert sender.sent[0][2] == "irip-fast"

    async def test_send_exception_returns_false(self) -> None:
        """发送异常时返回 False。"""
        event = _make_event(payload={"kind": "flow_execute"})
        svc = OutboxDispatcherService(MagicMock(), task_sender=FailingTaskSender())

        result = await svc._send_to_celery(event)
        assert result is False


# ============================================================
# get_undelivered_count
# ============================================================


class TestGetUndeliveredCount:
    """get_undelivered_count 测试。"""

    async def test_returns_count(self) -> None:
        """返回未投递事件数。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=5))
        svc = OutboxDispatcherService(MagicMock())

        with _patch_session_scope(session):
            count = await svc.get_undelivered_count()

        assert count == 5

    async def test_returns_zero(self) -> None:
        """无未投递事件返回 0。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=0))
        svc = OutboxDispatcherService(MagicMock())

        with _patch_session_scope(session):
            count = await svc.get_undelivered_count()

        assert count == 0

    async def test_returns_zero_when_none(self) -> None:
        """scalar 为 None 时返回 0。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalar=None))
        svc = OutboxDispatcherService(MagicMock())

        with _patch_session_scope(session):
            count = await svc.get_undelivered_count()

        assert count == 0
