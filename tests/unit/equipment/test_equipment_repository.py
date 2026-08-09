"""单元测试：EquipmentRepository 设备仓库 CRUD。

覆盖：
- insert：add + flush；
- select_by_id：查询返回 / None；
- select_by_org_and_code：按部门+编码查询；
- select_list：分页查询（含部门名 JOIN）；
- update：乐观锁 UPDATE（含/不含 visible_departments）；
- update_status：乐观锁状态更新。

使用 mock AsyncSession，不依赖真实数据库。
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from packages.equipment.entities import Equipment
from packages.equipment.repository import (
    EquipmentRepository,
)

# ============================================================
# Helpers
# ============================================================


def _make_equipment() -> Equipment:
    return Equipment(
        id=uuid4(),
        code="EQ-001",
        display_name="光谱仪",
        description=None,
        department_id=uuid4(),
        visible_departments=[],
        visibility_scope="tree",
        owner_user_id=uuid4(),
        status="active",
        sort_order=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        lock_version=0,
    )


def _make_result(scalar: Any = None, rows: list[Any] | None = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    result.all.return_value = rows or []
    return result


# ============================================================
# insert
# ============================================================


class TestInsert:
    """insert 测试。"""

    async def test_insert_calls_add_and_flush(self) -> None:
        """insert 调用 session.add + session.flush。"""
        session = AsyncMock()
        equip = _make_equipment()
        result = await EquipmentRepository.insert(session, equip)
        session.add.assert_called_once_with(equip)
        session.flush.assert_awaited_once()
        assert result is equip


# ============================================================
# select_by_id
# ============================================================


class TestSelectById:
    """select_by_id 测试。"""

    async def test_returns_equipment(self) -> None:
        """查询到设备返回。"""
        equip = _make_equipment()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=equip))
        result = await EquipmentRepository.select_by_id(session, equip.id)
        assert result is equip

    async def test_returns_none_when_not_found(self) -> None:
        """未查询到返回 None。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=None))
        result = await EquipmentRepository.select_by_id(session, uuid4())
        assert result is None


# ============================================================
# select_by_org_and_code
# ============================================================


class TestSelectByOrgAndCode:
    """select_by_org_and_code 测试。"""

    async def test_returns_equipment(self) -> None:
        """按部门+编码查询到设备。"""
        equip = _make_equipment()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=equip))
        result = await EquipmentRepository.select_by_org_and_code(
            session, equip.department_id, "EQ-001"
        )
        assert result is equip

    async def test_returns_none(self) -> None:
        """未查询到返回 None。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=None))
        result = await EquipmentRepository.select_by_org_and_code(session, uuid4(), "X")
        assert result is None


# ============================================================
# select_list
# ============================================================


class TestSelectList:
    """select_list 测试。"""

    async def test_basic_list(self) -> None:
        """基本列表查询（带部门筛选）。"""
        equip = _make_equipment()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(rows=[(equip, "dept")]))
        result = await EquipmentRepository.select_list(session, department_id=uuid4(), limit=10)
        assert len(result) == 1
        assert result[0][0] is equip
        assert result[0][1] == "dept"

    async def test_list_no_dept_filter(self) -> None:
        """不传 department_id 时不做部门筛选（RLS 处理可见性）。"""
        equip = _make_equipment()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(rows=[(equip, "")]))
        result = await EquipmentRepository.select_list(session, department_id=None, limit=10)
        assert len(result) == 1

    async def test_list_with_status_filter(self) -> None:
        """带 status 过滤。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(rows=[]))
        result = await EquipmentRepository.select_list(
            session, department_id=None, status="active", limit=10
        )
        assert result == []

    async def test_list_with_cursor(self) -> None:
        """带游标查询。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(rows=[]))
        result = await EquipmentRepository.select_list(
            session,
            department_id=None,
            cursor_sort_order=1,
            cursor_created_at=datetime.now(UTC),
            cursor_id=uuid4(),
            limit=10,
        )
        assert result == []

    async def test_list_no_filters(self) -> None:
        """无任何过滤条件。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(rows=[]))
        result = await EquipmentRepository.select_list(session, department_id=None, limit=10)
        assert result == []


# ============================================================
# update
# ============================================================


class TestUpdate:
    """update 测试。"""

    async def test_update_returns_equipment(self) -> None:
        """乐观锁更新成功返回设备。"""
        equip = _make_equipment()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=equip))
        result = await EquipmentRepository.update(
            session,
            equipment_id=equip.id,
            display_name="新名",
            description="desc",
            department_id=uuid4(),
            sort_order=2,
            lock_version=0,
        )
        assert result is equip

    async def test_update_with_visible_departments(self) -> None:
        """带 visible_departments 更新。"""
        equip = _make_equipment()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=equip))
        result = await EquipmentRepository.update(
            session,
            equipment_id=equip.id,
            display_name="x",
            description=None,
            department_id=uuid4(),
            sort_order=0,
            lock_version=0,
            visible_departments=["dept-1"],
        )
        assert result is equip

    async def test_update_returns_none_when_lock_mismatch(self) -> None:
        """lock_version 不匹配返回 None。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=None))
        result = await EquipmentRepository.update(
            session,
            equipment_id=uuid4(),
            display_name="x",
            description=None,
            department_id=uuid4(),
            sort_order=0,
            lock_version=99,
        )
        assert result is None


# ============================================================
# update_status
# ============================================================


class TestUpdateStatus:
    """update_status 测试。"""

    async def test_update_status_returns_equipment(self) -> None:
        """状态更新成功返回设备。"""
        equip = _make_equipment()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=equip))
        result = await EquipmentRepository.update_status(session, equip.id, "disabled", 0)
        assert result is equip

    async def test_update_status_returns_none(self) -> None:
        """lock_version 不匹配返回 None。"""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=None))
        result = await EquipmentRepository.update_status(session, uuid4(), "disabled", 99)
        assert result is None
