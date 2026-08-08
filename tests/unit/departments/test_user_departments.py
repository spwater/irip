"""单元测试：UserDepartmentService 用户-实验室关联管理。

覆盖：
- set_user_departments：有/无 department_ids + 有/无 primary_department_id；
- get_user_departments：查询用户实验室列表（JOIN department）；
- get_department_users：查询实验室下用户列表（JOIN app_user）。

使用 patched _scoped_session + mock session.execute。
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from packages.common.database import ScopedSessionMixin
from packages.departments.user_departments import (
    DepartmentUserItem,
    UserDepartmentItem,
    UserDepartmentService,
)

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


def _make_service() -> UserDepartmentService:
    return UserDepartmentService(session_factory=MagicMock(), department_id=uuid4())


def _make_result(rows: list[Any] | None = None) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows or []
    return result


# ============================================================
# set_user_departments
# ============================================================


class TestSetUserDepartments:
    """set_user_departments 测试。"""

    async def test_with_departments_and_primary(self) -> None:
        """设置实验室列表 + primary。"""
        session = AsyncMock()
        svc = _make_service()
        dept_ids = [uuid4(), uuid4()]
        primary = dept_ids[0]

        async with _patch_scoped_session(session):
            await svc.set_user_departments(uuid4(), dept_ids, primary)

        # delete + insert * 2 + update = 4 execute calls
        assert session.execute.await_count == 4

    async def test_with_departments_no_primary(self) -> None:
        """设置实验室列表但无 primary。"""
        session = AsyncMock()
        svc = _make_service()

        async with _patch_scoped_session(session):
            await svc.set_user_departments(uuid4(), [uuid4()], None)

        # delete + insert * 1 + update = 3
        assert session.execute.await_count == 3

    async def test_empty_departments(self) -> None:
        """空实验室列表：删除全部关联。"""
        session = AsyncMock()
        svc = _make_service()

        async with _patch_scoped_session(session):
            await svc.set_user_departments(uuid4(), [], None)

        # delete (all) + update = 2
        assert session.execute.await_count == 2

    async def test_empty_departments_with_primary(self) -> None:
        """空列表 + primary：删除全部 + 全部设 false。"""
        session = AsyncMock()
        svc = _make_service()

        async with _patch_scoped_session(session):
            await svc.set_user_departments(uuid4(), [], uuid4())

        assert session.execute.await_count == 2


# ============================================================
# get_user_departments
# ============================================================


class TestGetUserDepartments:
    """get_user_departments 测试。"""

    async def test_returns_items(self) -> None:
        """返回用户-实验室关联列表。"""
        user_id = uuid4()
        dept_id = uuid4()
        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=_make_result(rows=[(user_id, dept_id, "lab-001", "实验室A", True)])
        )
        svc = _make_service()

        async with _patch_scoped_session(session):
            result = await svc.get_user_departments(user_id)

        assert len(result) == 1
        assert isinstance(result[0], UserDepartmentItem)
        assert result[0].user_id == user_id
        assert result[0].department_id == dept_id
        assert result[0].department_code == "lab-001"
        assert result[0].department_display_name == "实验室A"
        assert result[0].is_primary is True

    async def test_returns_empty(self) -> None:
        """无关联时返回空列表。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(rows=[]))
        svc = _make_service()

        async with _patch_scoped_session(session):
            result = await svc.get_user_departments(uuid4())

        assert result == []


# ============================================================
# get_department_users
# ============================================================


class TestGetDepartmentUsers:
    """get_department_users 测试。"""

    async def test_returns_items(self) -> None:
        """返回实验室下用户列表。"""
        user_id = uuid4()
        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=_make_result(rows=[(user_id, "user@irip.local", "张三", True)])
        )
        svc = _make_service()

        async with _patch_scoped_session(session):
            result = await svc.get_department_users(uuid4())

        assert len(result) == 1
        assert isinstance(result[0], DepartmentUserItem)
        assert result[0].user_id == user_id
        assert result[0].email == "user@irip.local"
        assert result[0].display_name == "张三"
        assert result[0].is_primary is True

    async def test_returns_empty(self) -> None:
        """无用户时返回空列表。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(rows=[]))
        svc = _make_service()

        async with _patch_scoped_session(session):
            result = await svc.get_department_users(uuid4())

        assert result == []
