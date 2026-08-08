"""单元测试：CancellationRegistry 取消注册表。

覆盖：
- register 返回新 asyncio.Event 并存入注册表；
- cancel 触发已注册事件并返回 True；
- cancel 对未注册的 conversation_id 返回 False；
- unregister 移除事件（后续 cancel 返回 False）；
- get 返回已注册事件 / None；
- 多个 conversation_id 互不干扰。
"""

import asyncio
from uuid import uuid4

from packages.ai.cancellation import CancellationRegistry


class TestCancellationRegistry:
    """CancellationRegistry 纯内存逻辑测试。"""

    def test_register_returns_event(self) -> None:
        """register 返回 asyncio.Event 实例。"""
        reg = CancellationRegistry()
        conv_id = uuid4()
        event = reg.register(conv_id)
        assert isinstance(event, asyncio.Event)
        assert event.is_set() is False

    def test_cancel_triggers_registered_event(self) -> None:
        """cancel 触发已注册的取消事件。"""
        reg = CancellationRegistry()
        conv_id = uuid4()
        event = reg.register(conv_id)
        result = reg.cancel(conv_id)
        assert result is True
        assert event.is_set() is True

    def test_cancel_unregistered_returns_false(self) -> None:
        """cancel 对未注册的 conversation_id 返回 False。"""
        reg = CancellationRegistry()
        result = reg.cancel(uuid4())
        assert result is False

    def test_unregister_removes_event(self) -> None:
        """unregister 后再 cancel 返回 False。"""
        reg = CancellationRegistry()
        conv_id = uuid4()
        reg.register(conv_id)
        reg.unregister(conv_id)
        assert reg.cancel(conv_id) is False

    def test_get_returns_registered_event(self) -> None:
        """get 返回已注册事件。"""
        reg = CancellationRegistry()
        conv_id = uuid4()
        event = reg.register(conv_id)
        assert reg.get(conv_id) is event

    def test_get_unregistered_returns_none(self) -> None:
        """get 对未注册的 conversation_id 返回 None。"""
        reg = CancellationRegistry()
        assert reg.get(uuid4()) is None

    def test_multiple_conversations_isolated(self) -> None:
        """多个 conversation_id 互不干扰。"""
        reg = CancellationRegistry()
        conv_a = uuid4()
        conv_b = uuid4()
        event_a = reg.register(conv_a)
        reg.register(conv_b)

        reg.cancel(conv_a)
        assert event_a.is_set() is True
        assert reg.get(conv_b) is not None
        assert reg.get(conv_b).is_set() is False  # type: ignore[union-attr]

    def test_re_register_replaces_previous_event(self) -> None:
        """对同一 conversation_id 重新 register 替换旧事件。"""
        reg = CancellationRegistry()
        conv_id = uuid4()
        first = reg.register(conv_id)
        second = reg.register(conv_id)
        assert first is not second
        assert reg.get(conv_id) is second
