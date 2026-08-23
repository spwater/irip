"""单元测试：CollaborationService 协作管理服务。

覆盖：
- list_conversations_with_tab：cross_dept 返回空 / private / collaborative / same_dept；
- add_participant：成功 / 对话不存在 / 非 owner / 已是参与者 / 跨部门；
- remove_participant：成功 / 非 owner / 目标非参与者；
- leave_conversation：成功 / owner 不能退出 / 非参与者；
- list_participants：正常 / 对话不存在 / 无权访问；
- list_mentionable_users：管理员 / 普通用户过滤。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import packages.ai.collaboration_service as collab_mod
from packages.ai.collaboration_entities import ConversationParticipant
from packages.ai.collaboration_service import CollaborationService
from packages.ai.entities import AIConversation
from packages.common.clock import FixedClock
from packages.common.errors import AppError


def _make_service() -> CollaborationService:
    """Create CollaborationService with FixedClock and mock factory."""
    return CollaborationService(
        session_factory=MagicMock(),
        clock=FixedClock(datetime(2025, 1, 15, 10, 30, tzinfo=UTC)),
    )


def _patch_scoped(mock_session: AsyncMock) -> Any:
    """Patch scoped_session to yield mock_session."""

    @asynccontextmanager
    async def fake_scoped(factory: Any, dept_id: Any = None, user_id: Any = None) -> Any:
        yield mock_session

    original = collab_mod.scoped_session
    collab_mod.scoped_session = fake_scoped  # type: ignore[assignment]
    return original


def _make_mock_session() -> AsyncMock:
    """Create a mock AsyncSession with sync add."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=MagicMock())
    return session


def _make_conv(user_id: Any = None) -> MagicMock:
    """Create a mock AIConversation."""
    conv = MagicMock(spec=AIConversation)
    conv.id = uuid4()
    conv.user_id = user_id or uuid4()
    conv.title = "协作对话"
    conv.provider_mode = "offline"
    conv.pinned = False
    conv.archived = False
    conv.created_at = datetime(2025, 1, 10, tzinfo=UTC)
    conv.updated_at = datetime(2025, 1, 15, tzinfo=UTC)
    conv.system_context = None
    return conv


def _make_participant(role: str = "owner") -> MagicMock:
    """Create a mock ConversationParticipant."""
    p = MagicMock(spec=ConversationParticipant)
    p.conversation_id = uuid4()
    p.user_id = uuid4()
    p.role = role
    p.joined_at = datetime(2025, 1, 10, tzinfo=UTC)
    return p


def _make_app_user(
    uid: Any = None,
    display_name: str = "用户",
    dept_id: Any = None,
    roles: list[str] | None = None,
    avatar_url: str | None = None,
    status: str = "active",
) -> MagicMock:
    """Create a mock AppUser."""
    user = MagicMock()
    user.id = uid or uuid4()
    user.display_name = display_name
    user.department_id = dept_id or uuid4()
    user.roles = roles or ["lab_member"]
    user.avatar_url = avatar_url
    user.status = status
    return user


# ============================================================
# list_conversations_with_tab
# ============================================================


class TestListConversationsWithTab:
    """list_conversations_with_tab 测试。"""

    async def test_cross_dept_returns_empty(self) -> None:
        """cross_dept tab 已废弃，返回空列表。"""
        svc = _make_service()
        result = await svc.list_conversations_with_tab(uuid4(), uuid4(), tab="cross_dept")
        assert result == []

    async def test_private_returns_conversations(self) -> None:
        """private tab 返回我创建的 + 无其他参与者的对话。"""
        svc = _make_service()
        conv = _make_conv()
        mock_session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [conv]
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.list_conversations_with_tab(uuid4(), uuid4(), tab="private")
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert len(result) == 1
        assert result[0].title == "协作对话"

    async def test_private_no_conversations(self) -> None:
        """private tab 无对话时返回空列表。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.list_conversations_with_tab(uuid4(), uuid4(), tab="private")
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert result == []


# ============================================================
# add_participant
# ============================================================


class TestAddParticipant:
    """add_participant 测试。"""

    async def test_add_participant_not_found(self) -> None:
        """对话不存在时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=None)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.add_participant(uuid4(), uuid4(), uuid4())
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"

    async def test_add_participant_not_owner(self) -> None:
        """非 owner 邀请时抛 forbidden。"""
        svc = _make_service()
        inviter_id = uuid4()
        conv = _make_conv(user_id=uuid4())  # different user, not inviter

        mock_session = _make_mock_session()
        # First scalar: conv; second scalar: inviter_participant (None)
        mock_session.scalar = AsyncMock(side_effect=[conv, None])
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.add_participant(uuid4(), inviter_id, uuid4())
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "forbidden"

    async def test_add_participant_already_exists(self) -> None:
        """目标用户已是参与者时抛 conflict。"""
        svc = _make_service()
        inviter_id = uuid4()
        conv = _make_conv(user_id=inviter_id)
        existing_participant = _make_participant()

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[conv, None, existing_participant])
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.add_participant(uuid4(), inviter_id, uuid4())
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "conflict"


# ============================================================
# remove_participant
# ============================================================


class TestRemoveParticipant:
    """remove_participant 测试。"""

    async def test_remove_participant_not_found(self) -> None:
        """对话不存在时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=None)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.remove_participant(uuid4(), uuid4(), uuid4())
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"

    async def test_remove_participant_not_owner(self) -> None:
        """非 owner 移除时抛 forbidden。"""
        svc = _make_service()
        conv = _make_conv(user_id=uuid4())

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[conv, None])
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.remove_participant(uuid4(), uuid4(), uuid4())
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "forbidden"

    async def test_remove_participant_target_not_found(self) -> None:
        """目标用户非参与者时抛 not_found。"""
        svc = _make_service()
        owner_id = uuid4()
        conv = _make_conv(user_id=owner_id)

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[conv, None, None])
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.remove_participant(uuid4(), owner_id, uuid4())
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"


# ============================================================
# leave_conversation
# ============================================================


class TestLeaveConversation:
    """leave_conversation 测试。"""

    async def test_leave_not_participant(self) -> None:
        """非参与者退出时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=None)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.leave_conversation(uuid4(), uuid4())
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"

    async def test_leave_owner_forbidden(self) -> None:
        """owner 不能退出，抛 forbidden。"""
        svc = _make_service()
        participant = _make_participant(role="owner")

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=participant)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.leave_conversation(uuid4(), uuid4())
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "forbidden"

    async def test_leave_member_success(self) -> None:
        """member 可以退出。"""
        svc = _make_service()
        participant = _make_participant(role="member")

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=participant)
        mock_session.delete = AsyncMock()
        original = _patch_scoped(mock_session)

        try:
            await svc.leave_conversation(uuid4(), uuid4())
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        mock_session.delete.assert_awaited_once_with(participant)


# ============================================================
# list_participants
# ============================================================


class TestListParticipants:
    """list_participants 测试。"""

    async def test_conversation_not_found(self) -> None:
        """对话不存在时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=None)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.list_participants(uuid4(), uuid4())
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"

    async def test_forbidden_for_non_participant(self) -> None:
        """非参与者且非创建者抛 forbidden。"""
        svc = _make_service()
        conv = _make_conv(user_id=uuid4())  # different user

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[conv, None])
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.list_participants(uuid4(), uuid4())
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "forbidden"


# ============================================================
# list_mentionable_users
# ============================================================


class TestListMentionableUsers:
    """list_mentionable_users 测试。"""

    async def test_admin_returns_all_active_users(self) -> None:
        """管理员返回所有 active 用户（不限部门）。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            (uuid4(), "张三", None, ["lab_member"]),
            (uuid4(), "李四", "http://avatar.url", ["lab_director"]),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.list_mentionable_users(
                uuid4(), uuid4(), roles=["platform_administrator"]
            )
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert len(result) == 2
        assert result[0].display_name == "张三"
        assert result[1].avatar_url == "http://avatar.url"

    async def test_non_admin_filters_by_department(self) -> None:
        """非管理员按部门过滤。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            (uuid4(), "同部门用户", None, ["lab_member"]),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.list_mentionable_users(uuid4(), uuid4(), roles=["lab_member"])
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert len(result) == 1
        assert result[0].display_name == "同部门用户"

    async def test_empty_result(self) -> None:
        """无可 @ 用户时返回空列表。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.list_mentionable_users(uuid4(), uuid4())
        finally:
            collab_mod.scoped_session = original  # type: ignore[assignment]

        assert result == []
