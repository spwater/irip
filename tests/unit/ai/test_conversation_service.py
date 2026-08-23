"""单元测试：ConversationService 对话管理服务。

覆盖：
- create_conversation：创建对话 + 创建者自动成为 owner 参与者；
- list_conversations：按 user_id 过滤，排除已归档；
- get_conversation：返回对话引用或 None；
- toggle_pin：切换置顶状态；
- toggle_archive：切换归档状态；
- delete_conversation：仅删除已归档对话，未归档抛 forbidden；
- list_messages：权限检查 + 返回消息列表；
- search_conversations：按关键词搜索标题 + 消息内容。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import packages.ai.conversation_service as conv_mod
from packages.ai.conversation_service import ConversationService
from packages.ai.entities import AIConversation, AIMessage
from packages.common.clock import FixedClock
from packages.common.errors import AppError


def _make_service() -> ConversationService:
    """Create ConversationService with FixedClock and mock factory."""
    return ConversationService(
        session_factory=MagicMock(),
        clock=FixedClock(datetime(2025, 1, 15, 10, 30, tzinfo=UTC)),
    )


def _patch_scoped(mock_session: AsyncMock) -> Any:
    """Patch scoped_session to yield mock_session."""

    @asynccontextmanager
    async def fake_scoped(factory: Any, dept_id: Any = None, user_id: Any = None) -> Any:
        yield mock_session

    original = conv_mod.scoped_session
    conv_mod.scoped_session = fake_scoped  # type: ignore[assignment]
    return original


def _make_mock_session() -> AsyncMock:
    """Create a mock AsyncSession with sync add."""
    session = AsyncMock()
    session.add = MagicMock()  # session.add is sync
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=MagicMock())
    return session


def _make_conv(
    conv_id: Any = None,
    user_id: Any = None,
    title: str = "测试对话",
    pinned: bool = False,
    archived: bool = False,
) -> MagicMock:
    """Create a mock AIConversation."""
    conv = MagicMock(spec=AIConversation)
    conv.id = conv_id or uuid4()
    conv.user_id = user_id or uuid4()
    conv.title = title
    conv.provider_mode = "offline"
    conv.pinned = pinned
    conv.archived = archived
    conv.created_at = datetime(2025, 1, 10, tzinfo=UTC)
    conv.updated_at = datetime(2025, 1, 15, tzinfo=UTC)
    conv.system_context = None
    return conv


def _make_msg(
    msg_id: Any = None,
    role: str = "user",
    content: str = "hello",
) -> MagicMock:
    """Create a mock AIMessage."""
    msg = MagicMock(spec=AIMessage)
    msg.id = msg_id or uuid4()
    msg.conversation_id = uuid4()
    msg.role = role
    msg.content = content
    msg.tool_calls_json = []
    msg.citations_json = []
    msg.uncertainty = None
    msg.created_at = datetime(2025, 1, 15, tzinfo=UTC)
    msg.mentions = []
    msg.sender_user_id = None
    msg.sender_display_name = None
    msg.sender_avatar_url = None
    return msg


# ============================================================
# create_conversation
# ============================================================


class TestCreateConversation:
    """create_conversation 测试。"""

    async def test_create_conversation_with_title(self) -> None:
        """指定标题时直接使用。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.flush = AsyncMock()
        original = _patch_scoped(mock_session)

        uid = uuid4()
        try:
            result = await svc.create_conversation(uid, uuid4(), title="自定义标题")
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert result.title == "自定义标题"
        assert result.pinned is False
        assert result.archived is False
        # session.add called twice (conversation + participant)
        assert mock_session.add.call_count == 2

    async def test_create_conversation_default_title(self) -> None:
        """无标题时生成默认标题。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        original = _patch_scoped(mock_session)

        try:
            result = await svc.create_conversation(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert "对话" in result.title
        assert "2025-01-15" in result.title


# ============================================================
# list_conversations
# ============================================================


class TestListConversations:
    """list_conversations 测试。"""

    async def test_list_returns_conversations(self) -> None:
        """返回对话列表。"""
        svc = _make_service()
        conv = _make_conv(title="对话1")
        mock_session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [conv]
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.list_conversations(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert len(result) == 1
        assert result[0].title == "对话1"

    async def test_list_empty(self) -> None:
        """无对话时返回空列表。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.list_conversations(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert result == []


# ============================================================
# get_conversation
# ============================================================


class TestGetConversation:
    """get_conversation 测试。"""

    async def test_get_returns_ref(self) -> None:
        """对话存在时返回 ConversationRef。"""
        svc = _make_service()
        conv = _make_conv(title="找到的对话")
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.get_conversation(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert result is not None
        assert result.title == "找到的对话"

    async def test_get_returns_none_when_not_found(self) -> None:
        """对话不存在时返回 None。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=None)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.get_conversation(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert result is None


# ============================================================
# toggle_pin
# ============================================================


class TestTogglePin:
    """toggle_pin 测试。"""

    async def test_toggle_pin_from_false_to_true(self) -> None:
        """pinned=False 切换为 True。"""
        svc = _make_service()
        conv = _make_conv(pinned=False)
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.toggle_pin(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert result is True
        assert conv.pinned is True

    async def test_toggle_pin_from_true_to_false(self) -> None:
        """pinned=True 切换为 False。"""
        svc = _make_service()
        conv = _make_conv(pinned=True)
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.toggle_pin(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert result is False
        assert conv.pinned is False

    async def test_toggle_pin_explicit_value(self) -> None:
        """指定 pinned=True 时直接设置。"""
        svc = _make_service()
        conv = _make_conv(pinned=False)
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.toggle_pin(uuid4(), uuid4(), pinned=True)
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert result is True

    async def test_toggle_pin_not_found_raises(self) -> None:
        """对话不存在时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=None)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.toggle_pin(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"


# ============================================================
# toggle_archive
# ============================================================


class TestToggleArchive:
    """toggle_archive 测试。"""

    async def test_toggle_archive_from_false_to_true(self) -> None:
        """archived=False 切换为 True。"""
        svc = _make_service()
        conv = _make_conv(archived=False)
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.toggle_archive(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert result is True
        assert conv.archived is True

    async def test_toggle_archive_explicit_value(self) -> None:
        """指定 archived=True 时直接设置。"""
        svc = _make_service()
        conv = _make_conv(archived=False)
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.toggle_archive(uuid4(), uuid4(), archived=True)
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert result is True

    async def test_toggle_archive_not_found_raises(self) -> None:
        """对话不存在时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=None)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.toggle_archive(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"


# ============================================================
# delete_conversation
# ============================================================


class TestDeleteConversation:
    """delete_conversation 测试。"""

    async def test_delete_archived_conversation(self) -> None:
        """已归档对话可以删除。"""
        svc = _make_service()
        conv = _make_conv(archived=True)
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        mock_session.delete = AsyncMock()
        original = _patch_scoped(mock_session)

        try:
            await svc.delete_conversation(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        mock_session.delete.assert_awaited_once_with(conv)

    async def test_delete_non_archived_raises_forbidden(self) -> None:
        """未归档对话不允许删除。"""
        svc = _make_service()
        conv = _make_conv(archived=False)
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.delete_conversation(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "forbidden"

    async def test_delete_not_found_raises(self) -> None:
        """对话不存在时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=None)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.delete_conversation(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"


# ============================================================
# list_messages
# ============================================================


class TestListMessages:
    """list_messages 测试。"""

    async def test_list_messages_as_owner(self) -> None:
        """对话创建者可以列出消息。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)
        msg1 = _make_msg(role="user", content="question")
        msg2 = _make_msg(role="assistant", content="answer")

        mock_session = _make_mock_session()
        # First scalar call returns conv
        mock_session.scalar = AsyncMock(return_value=conv)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [msg1, msg2]
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.list_messages(uuid4(), uid)
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert len(result) == 2
        assert result[0].content == "question"
        assert result[1].content == "answer"

    async def test_list_messages_not_found(self) -> None:
        """对话不存在时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=None)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.list_messages(uuid4(), uuid4())
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"


# ============================================================
# search_conversations
# ============================================================


class TestSearchConversations:
    """search_conversations 测试。"""

    async def test_search_returns_matching_conversations(self) -> None:
        """关键词搜索返回匹配的对话。"""
        svc = _make_service()
        conv = _make_conv(title="温度分析")
        mock_session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [conv]
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.search_conversations(uuid4(), uuid4(), "温度")
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert len(result) == 1
        assert result[0].title == "温度分析"

    async def test_search_no_matches_returns_empty(self) -> None:
        """无匹配时返回空列表。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.search_conversations(uuid4(), uuid4(), "不存在")
        finally:
            conv_mod.scoped_session = original  # type: ignore[assignment]

        assert result == []
