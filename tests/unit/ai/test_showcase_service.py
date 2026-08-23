"""单元测试：ShowcaseService 橱窗卡片管理服务。

覆盖：
- _check_conversation_access：创建者/参与者/无权访问；
- add_showcase_item：成功/无权/重复添加返回已有卡片；
- list_showcase_items：返回列表/无权访问；
- update_showcase_item：更新标题/不存在/无权；
- delete_showcase_item：删除/不存在/无权；
- reorder_showcase_items：重排序/无权；
- generate_summary：有卡片/无卡片/无权。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import packages.ai.showcase_service as showcase_mod
from packages.ai.entities import AIConversation
from packages.ai.showcase_entities import ShowcaseItem
from packages.ai.showcase_service import ShowcaseService
from packages.common.clock import FixedClock
from packages.common.errors import AppError


def _make_service() -> ShowcaseService:
    """Create ShowcaseService with FixedClock and mock factory."""
    return ShowcaseService(
        session_factory=MagicMock(),
        clock=FixedClock(datetime(2025, 1, 15, 10, 30, tzinfo=UTC)),
    )


def _patch_scoped(mock_session: AsyncMock) -> Any:
    """Patch scoped_session to yield mock_session."""

    @asynccontextmanager
    async def fake_scoped(factory: Any, dept_id: Any = None, user_id: Any = None) -> Any:
        yield mock_session

    original = showcase_mod.scoped_session
    showcase_mod.scoped_session = fake_scoped  # type: ignore[assignment]
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


def _make_item(
    item_id: Any = None,
    block_type: str = "echarts",
    title: str = "测试图表",
    sort_order: int = 0,
    content_snapshot: str = '{"x": 1}',
    data_source: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a mock ShowcaseItem."""
    item = MagicMock(spec=ShowcaseItem)
    item.id = item_id or uuid4()
    item.conversation_id = uuid4()
    item.user_id = uuid4()
    item.sort_order = sort_order
    item.block_type = block_type
    item.title = title
    item.content_snapshot = content_snapshot
    item.source_message_id = uuid4()
    item.source_block_index = 0
    item.data_source = data_source or {}
    item.created_at = datetime(2025, 1, 10, tzinfo=UTC)
    item.updated_at = datetime(2025, 1, 15, tzinfo=UTC)
    return item


def _make_conv(user_id: Any = None) -> MagicMock:
    """Create a mock AIConversation."""
    conv = MagicMock(spec=AIConversation)
    conv.id = uuid4()
    conv.user_id = user_id or uuid4()
    conv.title = "测试对话"
    conv.provider_mode = "offline"
    conv.pinned = False
    conv.archived = False
    conv.created_at = datetime(2025, 1, 10, tzinfo=UTC)
    conv.updated_at = datetime(2025, 1, 15, tzinfo=UTC)
    conv.system_context = None
    return conv


# ============================================================
# _check_conversation_access
# ============================================================


class TestCheckConversationAccess:
    """_check_conversation_access 测试。"""

    async def test_owner_has_access(self) -> None:
        """创建者有访问权。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        # No need to patch scoped_session for this method (takes session as arg)

        result = await svc._check_conversation_access(mock_session, uuid4(), uid)

        assert result is True

    async def test_non_owner_no_participant_no_access(self) -> None:
        """非创建者且非参与者无访问权。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[None, None])

        result = await svc._check_conversation_access(mock_session, uuid4(), uuid4())

        assert result is False

    async def test_participant_has_access(self) -> None:
        """参与者有访问权。"""
        svc = _make_service()
        participant = MagicMock()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[None, participant])

        result = await svc._check_conversation_access(mock_session, uuid4(), uuid4())

        assert result is True


# ============================================================
# add_showcase_item
# ============================================================


class TestAddShowcaseItem:
    """add_showcase_item 测试。"""

    async def test_add_item_success(self) -> None:
        """成功添加橱窗卡片。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)

        mock_session = _make_mock_session()
        # _check_conversation_access: scalar returns conv (owner)
        # existing check: scalar returns None (not duplicate)
        # max_order: execute returns None
        mock_session.scalar = AsyncMock(side_effect=[conv, None])
        mock_max_result = MagicMock()
        mock_max_result.scalar.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_max_result)
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()
        original = _patch_scoped(mock_session)

        try:
            result = await svc.add_showcase_item(
                user_id=uid,
                conversation_id=uuid4(),
                block_type="echarts",
                title="图表",
                content_snapshot="{}",
                source_message_id=uuid4(),
                source_block_index=0,
            )
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert result.block_type == "echarts"
        assert result.title == "图表"
        mock_session.add.assert_called_once()

    async def test_add_item_no_access(self) -> None:
        """无权访问时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[None, None])
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.add_showcase_item(
                    user_id=uuid4(),
                    conversation_id=uuid4(),
                    block_type="echarts",
                    title="x",
                    content_snapshot="{}",
                    source_message_id=uuid4(),
                    source_block_index=0,
                )
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"

    async def test_add_item_duplicate_returns_existing(self) -> None:
        """重复添加返回已有卡片。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)
        existing_item = _make_item(title="已有卡片")

        mock_session = _make_mock_session()
        # scalar: conv (access), existing_item (duplicate found)
        mock_session.scalar = AsyncMock(side_effect=[conv, existing_item])
        original = _patch_scoped(mock_session)

        try:
            result = await svc.add_showcase_item(
                user_id=uid,
                conversation_id=uuid4(),
                block_type="echarts",
                title="新标题",
                content_snapshot="{}",
                source_message_id=uuid4(),
                source_block_index=0,
            )
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        # Should return the existing item, not add a new one
        assert result.title == "已有卡片"
        mock_session.add.assert_not_called()

    async def test_add_item_long_title_truncated(self) -> None:
        """超过 200 字符的标题被截断。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)
        long_title = "T" * 250

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[conv, None])
        mock_max_result = MagicMock()
        mock_max_result.scalar.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_max_result)
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()
        original = _patch_scoped(mock_session)

        try:
            result = await svc.add_showcase_item(
                user_id=uid,
                conversation_id=uuid4(),
                block_type="text",
                title=long_title,
                content_snapshot="content",
                source_message_id=uuid4(),
                source_block_index=0,
            )
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert len(result.title) == 200


# ============================================================
# list_showcase_items
# ============================================================


class TestListShowcaseItems:
    """list_showcase_items 测试。"""

    async def test_list_returns_items(self) -> None:
        """返回卡片列表。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)
        item1 = _make_item(title="卡片1", sort_order=0)
        item2 = _make_item(title="卡片2", sort_order=1)

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [item1, item2]
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.list_showcase_items(uuid4(), uid)
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert len(result) == 2
        assert result[0].title == "卡片1"
        assert result[1].title == "卡片2"

    async def test_list_no_access(self) -> None:
        """无权访问时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[None, None])
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.list_showcase_items(uuid4(), uuid4())
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"

    async def test_list_empty(self) -> None:
        """无卡片时返回空列表。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            result = await svc.list_showcase_items(uuid4(), uid)
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert result == []


# ============================================================
# update_showcase_item
# ============================================================


class TestUpdateShowcaseItem:
    """update_showcase_item 测试。"""

    async def test_update_title_success(self) -> None:
        """更新标题成功。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)
        item = _make_item(title="旧标题")

        mock_session = _make_mock_session()
        # scalar: item (found), conv (access check)
        mock_session.scalar = AsyncMock(side_effect=[item, conv])
        original = _patch_scoped(mock_session)

        try:
            result = await svc.update_showcase_item(uuid4(), uid, title="新标题")
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert result.title == "新标题"
        assert item.title == "新标题"

    async def test_update_item_not_found(self) -> None:
        """卡片不存在时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=None)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.update_showcase_item(uuid4(), uuid4(), title="x")
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"

    async def test_update_no_access(self) -> None:
        """有卡片但无权访问对话时抛 not_found。"""
        svc = _make_service()
        item = _make_item()

        mock_session = _make_mock_session()
        # scalar: item (found), None (no access)
        mock_session.scalar = AsyncMock(side_effect=[item, None, None])
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.update_showcase_item(uuid4(), uuid4(), title="x")
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"

    async def test_update_title_none_keeps_old(self) -> None:
        """title=None 时不更新标题。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)
        item = _make_item(title="保留标题")

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[item, conv])
        original = _patch_scoped(mock_session)

        try:
            result = await svc.update_showcase_item(uuid4(), uid, title=None)
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert result.title == "保留标题"


# ============================================================
# delete_showcase_item
# ============================================================


class TestDeleteShowcaseItem:
    """delete_showcase_item 测试。"""

    async def test_delete_success(self) -> None:
        """删除卡片成功。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)
        item = _make_item()

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[item, conv])
        mock_session.delete = AsyncMock()
        original = _patch_scoped(mock_session)

        try:
            await svc.delete_showcase_item(uuid4(), uid)
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        mock_session.delete.assert_awaited_once_with(item)

    async def test_delete_not_found(self) -> None:
        """卡片不存在时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=None)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.delete_showcase_item(uuid4(), uuid4())
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"


# ============================================================
# reorder_showcase_items
# ============================================================


class TestReorderShowcaseItems:
    """reorder_showcase_items 测试。"""

    async def test_reorder_success(self) -> None:
        """重排序成功。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)
        item1 = _make_item(sort_order=0)
        item2 = _make_item(sort_order=1)

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[conv, item1, item2])
        original = _patch_scoped(mock_session)

        try:
            await svc.reorder_showcase_items(uuid4(), uid, [item1.id, item2.id])
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert item1.sort_order == 0
        assert item2.sort_order == 1

    async def test_reorder_no_access(self) -> None:
        """无权访问时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(side_effect=[None, None])
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.reorder_showcase_items(uuid4(), uuid4(), [])
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"


# ============================================================
# generate_summary
# ============================================================


class TestGenerateSummary:
    """generate_summary 测试。"""

    async def test_summary_with_items(self) -> None:
        """有卡片时生成 Markdown 摘要。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)
        item1 = _make_item(
            block_type="conclusion",
            title="结论1",
            content_snapshot="温度呈上升趋势",
            sort_order=0,
        )
        item2 = _make_item(
            block_type="echarts",
            title="图表1",
            content_snapshot='{"series": [1, 2, 3]}',
            sort_order=1,
            data_source={"sample_labels": ["S1", "S2"], "task_name": "T1"},
        )

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [item1, item2]
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            summary, count = await svc.generate_summary(uuid4(), uid)
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert count == 2
        assert "分析摘要" in summary
        assert "结论1" in summary
        assert "图表1" in summary
        assert "温度呈上升趋势" in summary
        assert "S1" in summary

    async def test_summary_no_items(self) -> None:
        """无卡片时返回空摘要。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            summary, count = await svc.generate_summary(uuid4(), uid)
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert count == 0
        assert summary == ""

    async def test_summary_no_access(self) -> None:
        """无权访问时抛 not_found。"""
        svc = _make_service()
        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=None)
        original = _patch_scoped(mock_session)

        try:
            with pytest.raises(AppError) as exc_info:
                await svc.generate_summary(uuid4(), uuid4())
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert exc_info.value.code == "not_found"

    async def test_summary_truncates_long_echarts_config(self) -> None:
        """过长的 echarts JSON 配置被截断。"""
        svc = _make_service()
        uid = uuid4()
        conv = _make_conv(user_id=uid)
        long_config = '{"data": "' + "x" * 3000 + '"}'
        item = _make_item(
            block_type="echarts",
            title="大图表",
            content_snapshot=long_config,
            sort_order=0,
        )

        mock_session = _make_mock_session()
        mock_session.scalar = AsyncMock(return_value=conv)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [item]
        mock_session.execute = AsyncMock(return_value=mock_result)
        original = _patch_scoped(mock_session)

        try:
            summary, count = await svc.generate_summary(uuid4(), uid)
        finally:
            showcase_mod.scoped_session = original  # type: ignore[assignment]

        assert count == 1
        assert "配置已截断" in summary
