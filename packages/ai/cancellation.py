"""AI 请求取消注册表。

封装原 ``service.py`` 模块级可变全局状态 ``_active_requests``，
通过实例化注入消除模块级可变状态，提升并发安全性。

``CancellationRegistry`` 在 ``AIService.__init__`` 中创建一次，
注入到 ``AskService``，不使用模块级单例。
"""

from __future__ import annotations

import asyncio
from uuid import UUID


class CancellationRegistry:
    """AI 请求取消注册表。

    管理 ``conversation_id → asyncio.Event`` 映射，用于取消正在进行的 AI 请求。

    Attributes:
        _active: 当前活跃的取消事件字典（conversation_id → Event）。
    """

    def __init__(self) -> None:
        """初始化取消注册表。"""
        self._active: dict[UUID, asyncio.Event] = {}

    def register(self, conversation_id: UUID) -> asyncio.Event:
        """注册一个取消事件并返回。

        Args:
            conversation_id: 对话 ID。

        Returns:
            asyncio.Event: 新创建的取消事件。
        """
        event = asyncio.Event()
        self._active[conversation_id] = event
        return event

    def cancel(self, conversation_id: UUID) -> bool:
        """触发指定对话的取消事件。

        Args:
            conversation_id: 对话 ID。

        Returns:
            bool: True 如果找到并触发了取消事件，False 如果没有正在进行的请求。
        """
        event = self._active.get(conversation_id)
        if event is not None:
            event.set()
            return True
        return False

    def unregister(self, conversation_id: UUID) -> None:
        """注销取消事件（请求完成后调用）。

        Args:
            conversation_id: 对话 ID。
        """
        self._active.pop(conversation_id, None)

    def get(self, conversation_id: UUID) -> asyncio.Event | None:
        """获取指定对话的取消事件（不触发）。

        Args:
            conversation_id: 对话 ID。

        Returns:
            asyncio.Event | None: 取消事件，不存在时返回 None。
        """
        return self._active.get(conversation_id)
