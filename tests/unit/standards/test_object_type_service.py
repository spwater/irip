"""单元测试：ObjectTypeService 实验对象类型字典管理服务。

覆盖：
- list_object_types：查询全部类型；
- create_object_type：成功 + 重名冲突 + sort_order 计算；
- update_object_type：成功 + 不存在 + 部分字段更新；
- delete_object_type：成功 + 不存在 + 引用冲突。

使用 patched _scoped_session + mock session.execute。
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.standards.object_type_service import ObjectTypeService

# ============================================================
# Helpers
# ============================================================


@asynccontextmanager
async def _patch_scoped_session(mock_session: AsyncMock) -> Any:
    original = ScopedSessionMixin._scoped_session

    @asynccontextmanager
    async def fake_scoped_session(self: Any) -> Any:
        yield mock_session

    ScopedSessionMixin._scoped_session = fake_scoped_session  # type: ignore[method-assign]
    try:
        yield
    finally:
        ScopedSessionMixin._scoped_session = original  # type: ignore[method-assign]


def _make_service() -> ObjectTypeService:
    return ObjectTypeService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=uuid4(),
    )


def _make_type_obj(
    type_id: Any = None,
    display_name: str = "类型A",
    code: str = "obtype_x1",
    sort_order: int = 1,
) -> MagicMock:
    obj = MagicMock()
    obj.id = type_id or uuid4()
    obj.display_name = display_name
    obj.code = code
    obj.description = None
    obj.sort_order = sort_order
    obj.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    return obj


def _make_execute_result(
    scalar: Any = None,
    scalars_all: list[Any] | None = None,
    one_or_none: Any = None,
) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = scalar
    result.scalar_one_or_none.return_value = one_or_none
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = scalars_all or []
    result.scalars.return_value = scalars_mock
    return result


# ============================================================
# list_object_types
# ============================================================


class TestListObjectTypes:
    """list_object_types 测试。"""

    async def test_returns_sorted_list(self) -> None:
        """返回按 sort_order 排序的类型列表。"""
        session = AsyncMock()
        types = [_make_type_obj(sort_order=1), _make_type_obj(sort_order=2)]
        session.execute = AsyncMock(return_value=_make_execute_result(scalars_all=types))
        svc = _make_service()

        async with _patch_scoped_session(session):
            result = await svc.list_object_types()

        assert len(result) == 2

    async def test_returns_empty_list(self) -> None:
        """无类型时返回空列表。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_execute_result(scalars_all=[]))
        svc = _make_service()

        async with _patch_scoped_session(session):
            result = await svc.list_object_types()

        assert result == []


# ============================================================
# create_object_type
# ============================================================


class TestCreateObjectType:
    """create_object_type 测试。"""

    async def test_create_success(self) -> None:
        """成功创建类型。"""
        session = AsyncMock()
        # First execute: existing check (None). Second: max sort_order (0).
        session.execute = AsyncMock(
            side_effect=[
                _make_execute_result(one_or_none=None),
                _make_execute_result(scalar=None),
            ]
        )
        svc = _make_service()

        async with _patch_scoped_session(session):
            result = await svc.create_object_type("新类型", "描述")

        assert result.display_name == "新类型"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_create_conflict(self) -> None:
        """重名类型抛 conflict。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_execute_result(one_or_none=_make_type_obj()))
        svc = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="已存在"):
                await svc.create_object_type("类型A")

    async def test_create_with_existing_sort_order(self) -> None:
        """已有 sort_order 时新类型递增。"""
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[
                _make_execute_result(one_or_none=None),
                _make_execute_result(scalar=5),
            ]
        )
        svc = _make_service()

        async with _patch_scoped_session(session):
            result = await svc.create_object_type("类型B")

        assert result.sort_order == 6


# ============================================================
# update_object_type
# ============================================================


class TestUpdateObjectType:
    """update_object_type 测试。"""

    async def test_update_success(self) -> None:
        """成功更新类型。"""
        obj = _make_type_obj()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_execute_result(one_or_none=obj))
        svc = _make_service()

        async with _patch_scoped_session(session):
            result = await svc.update_object_type(obj.id, display_name="新名", description="新描述")

        assert result.display_name == "新名"
        assert result.description == "新描述"
        session.flush.assert_awaited_once()

    async def test_update_display_name_only(self) -> None:
        """仅更新 display_name。"""
        obj = _make_type_obj()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_execute_result(one_or_none=obj))
        svc = _make_service()

        async with _patch_scoped_session(session):
            result = await svc.update_object_type(obj.id, display_name="仅名")

        assert result.display_name == "仅名"

    async def test_update_not_found(self) -> None:
        """类型不存在抛 not_found。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_execute_result(one_or_none=None))
        svc = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await svc.update_object_type(uuid4(), display_name="x")


# ============================================================
# delete_object_type
# ============================================================


class TestDeleteObjectType:
    """delete_object_type 测试。"""

    async def test_delete_success(self) -> None:
        """成功删除类型。"""
        obj = _make_type_obj()
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[
                _make_execute_result(one_or_none=obj),
                _make_execute_result(scalar=0),  # 引用计数为 0
            ]
        )
        svc = _make_service()

        async with _patch_scoped_session(session):
            await svc.delete_object_type(obj.id)

        session.delete.assert_called_once_with(obj)

    async def test_delete_not_found(self) -> None:
        """类型不存在抛 not_found。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_execute_result(one_or_none=None))
        svc = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="不存在"):
                await svc.delete_object_type(uuid4())

    async def test_delete_conflict_in_use(self) -> None:
        """类型正在被使用抛 conflict。"""
        obj = _make_type_obj()
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[
                _make_execute_result(one_or_none=obj),
                _make_execute_result(scalar=3),  # 3 个引用
            ]
        )
        svc = _make_service()

        async with _patch_scoped_session(session):
            with pytest.raises(AppError, match="使用中"):
                await svc.delete_object_type(obj.id)
