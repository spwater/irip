"""单元测试：ToolRepository ai_tool 表 CRUD 仓库。

覆盖：
- _to_row：ORM 实体 → AIToolRow 转换；
- list_all：按 name 排序列出全部工具；
- get_by_name：按名称查询工具（存在/不存在）；
- create：新建工具（成功/名称冲突）；
- update：更新工具字段（成功/不存在/乐观锁冲突）；
- set_enabled：启用/禁用工具（成功/不存在/乐观锁冲突）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.ai.tool_repository import AITool, AIToolRow, ToolRepository, _to_row
from packages.common.errors import AppError


def _make_tool(
    name: str = "test_tool",
    display_name: str = "测试工具",
    description: str = "测试工具描述",
    required_permission: str = "fact:read",
    enabled: bool = True,
    lock_version: int = 0,
    parameters_schema: dict[str, Any] | None = None,
    category: str = "ai_tool",
) -> MagicMock:
    """Create a mock AITool ORM entity."""
    tool = MagicMock(spec=AITool)
    tool.id = uuid4()
    tool.name = name
    tool.display_name = display_name
    tool.description = description
    tool.required_permission = required_permission
    tool.parameters_schema = parameters_schema or {"type": "object"}
    tool.enabled = enabled
    tool.lock_version = lock_version
    tool.created_at = datetime(2025, 1, 10, tzinfo=UTC)
    tool.updated_at = datetime(2025, 1, 15, tzinfo=UTC)
    tool.updated_by = None
    tool.category = category
    return tool


def _make_mock_session() -> AsyncMock:
    """Create a mock AsyncSession."""
    session = AsyncMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


# ============================================================
# _to_row
# ============================================================


class TestToRow:
    """_to_row ORM → AIToolRow 转换测试。"""

    def test_to_row_basic(self) -> None:
        """基本字段转换。"""
        tool = _make_tool(name="my_tool", display_name="我的工具")
        row = _to_row(tool)

        assert row.name == "my_tool"
        assert row.display_name == "我的工具"
        assert row.description == "测试工具描述"
        assert row.required_permission == "fact:read"
        assert row.enabled is True
        assert row.lock_version == 0
        assert row.category == "ai_tool"

    def test_to_row_none_schema(self) -> None:
        """parameters_schema 为 None 时转为空 dict。"""
        tool = _make_tool()
        tool.parameters_schema = None
        row = _to_row(tool)

        assert row.parameters_schema == {}

    def test_to_row_copies_schema(self) -> None:
        """parameters_schema 被复制（解耦 ORM）。"""
        schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        tool = _make_tool(parameters_schema=schema)
        row = _to_row(tool)

        assert row.parameters_schema == schema
        # Verify it's a copy, not the same object
        row.parameters_schema["new_key"] = "val"
        assert "new_key" not in schema


# ============================================================
# list_all
# ============================================================


class TestListAll:
    """ToolRepository.list_all 测试。"""

    async def test_list_all_returns_rows(self) -> None:
        """返回全部工具行（按 name 排序）。"""
        tool1 = _make_tool(name="alpha")
        tool2 = _make_tool(name="beta")
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [tool1, tool2]
        session.execute = AsyncMock(return_value=mock_result)

        rows = await ToolRepository.list_all(session)

        assert len(rows) == 2
        assert all(isinstance(r, AIToolRow) for r in rows)
        assert rows[0].name == "alpha"
        assert rows[1].name == "beta"

    async def test_list_all_empty(self) -> None:
        """无工具时返回空列表。"""
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        rows = await ToolRepository.list_all(session)

        assert rows == []


# ============================================================
# get_by_name
# ============================================================


class TestGetByName:
    """ToolRepository.get_by_name 测试。"""

    async def test_get_by_name_found(self) -> None:
        """工具存在时返回 AIToolRow。"""
        tool = _make_tool(name="search_facts")
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = tool
        session.execute = AsyncMock(return_value=mock_result)

        row = await ToolRepository.get_by_name(session, "search_facts")

        assert row is not None
        assert row.name == "search_facts"

    async def test_get_by_name_not_found(self) -> None:
        """工具不存在时返回 None。"""
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        row = await ToolRepository.get_by_name(session, "nonexistent")

        assert row is None


# ============================================================
# create
# ============================================================


class TestCreate:
    """ToolRepository.create 测试。"""

    async def test_create_success(self) -> None:
        """成功创建工具。"""
        session = _make_mock_session()
        # get_by_name returns None (no existing)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        data = {
            "name": "new_tool",
            "display_name": "新工具",
            "description": "描述",
            "required_permission": "fact:read",
            "parameters_schema": {"type": "object"},
        }
        updated_by = uuid4()

        row = await ToolRepository.create(session, data, updated_by)

        assert row.name == "new_tool"
        assert row.display_name == "新工具"
        assert row.enabled is True
        assert row.lock_version == 0
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_create_duplicate_raises_conflict(self) -> None:
        """工具名已存在时抛 conflict。"""
        existing_tool = _make_tool(name="existing")
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_tool
        session.execute = AsyncMock(return_value=mock_result)

        data = {
            "name": "existing",
            "display_name": "重复",
            "description": "desc",
            "required_permission": "fact:read",
        }

        with pytest.raises(AppError) as exc_info:
            await ToolRepository.create(session, data, uuid4())

        assert exc_info.value.code == "conflict"

    async def test_create_default_schema(self) -> None:
        """未提供 parameters_schema 时默认空 dict。"""
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        data = {
            "name": "tool_no_schema",
            "display_name": "无 Schema",
            "description": "desc",
            "required_permission": "fact:read",
        }

        row = await ToolRepository.create(session, data, uuid4())

        assert row.parameters_schema == {}


# ============================================================
# update
# ============================================================


class TestUpdate:
    """ToolRepository.update 测试。"""

    async def test_update_success(self) -> None:
        """成功更新工具字段。"""
        tool = _make_tool(name="update_me", lock_version=2)
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = tool
        session.execute = AsyncMock(return_value=mock_result)

        data = {
            "display_name": "更新后名称",
            "description": "新描述",
            "required_permission": "model:publish",
            "parameters_schema": {"type": "object", "properties": {}},
        }

        row = await ToolRepository.update(session, "update_me", data, 2, uuid4())

        assert row.display_name == "更新后名称"
        assert row.description == "新描述"
        assert row.required_permission == "model:publish"
        assert tool.lock_version == 3  # incremented
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once()

    async def test_update_not_found(self) -> None:
        """工具不存在时抛 not_found。"""
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        data = {
            "display_name": "x",
            "description": "x",
            "required_permission": "x",
            "parameters_schema": {},
        }

        with pytest.raises(AppError) as exc_info:
            await ToolRepository.update(session, "missing", data, 0, uuid4())

        assert exc_info.value.code == "not_found"

    async def test_update_lock_version_conflict(self) -> None:
        """乐观锁版本不匹配时抛 conflict。"""
        tool = _make_tool(name="conflict_tool", lock_version=5)
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = tool
        session.execute = AsyncMock(return_value=mock_result)

        data = {
            "display_name": "x",
            "description": "x",
            "required_permission": "x",
            "parameters_schema": {},
        }

        with pytest.raises(AppError) as exc_info:
            await ToolRepository.update(session, "conflict_tool", data, 3, uuid4())

        assert exc_info.value.code == "conflict"


# ============================================================
# set_enabled
# ============================================================


class TestSetEnabled:
    """ToolRepository.set_enabled 测试。"""

    async def test_enable_tool(self) -> None:
        """启用工具。"""
        tool = _make_tool(name="toggle", enabled=False, lock_version=1)
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = tool
        session.execute = AsyncMock(return_value=mock_result)

        row = await ToolRepository.set_enabled(session, "toggle", True, 1, uuid4())

        assert row.enabled is True
        assert tool.enabled is True
        assert tool.lock_version == 2

    async def test_disable_tool(self) -> None:
        """禁用工具。"""
        tool = _make_tool(name="toggle", enabled=True, lock_version=1)
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = tool
        session.execute = AsyncMock(return_value=mock_result)

        row = await ToolRepository.set_enabled(session, "toggle", False, 1, uuid4())

        assert row.enabled is False
        assert tool.enabled is False

    async def test_set_enabled_not_found(self) -> None:
        """工具不存在时抛 not_found。"""
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(AppError) as exc_info:
            await ToolRepository.set_enabled(session, "missing", True, 0, uuid4())

        assert exc_info.value.code == "not_found"

    async def test_set_enabled_lock_version_conflict(self) -> None:
        """乐观锁版本不匹配时抛 conflict。"""
        tool = _make_tool(name="conflict", enabled=True, lock_version=3)
        session = _make_mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = tool
        session.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(AppError) as exc_info:
            await ToolRepository.set_enabled(session, "conflict", False, 1, uuid4())

        assert exc_info.value.code == "conflict"
