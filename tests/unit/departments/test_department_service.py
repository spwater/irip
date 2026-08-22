"""单元测试：DepartmentService 实验室服务。

覆盖：
- DepartmentStatus 枚举值；
- _encode_cursor + _decode_cursor 往返一致；
- _decode_cursor 非法 base64 / 缺少 so/ct / 非整数 sort_order 抛 invalid_cursor；
- update 哨兵部门（root/system）禁止 re-parent 抛 forbidden；
- delete 哨兵部门禁止删除抛 forbidden；
- delete 存在子部门抛 conflict；
- list_all 非法游标抛 invalid_cursor。
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import packages.departments.service as dept_svc_mod
from packages.common.clock import FixedClock
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.departments.entities import DepartmentStatus
from packages.departments.service import DepartmentService, _decode_cursor, _encode_cursor


def _make_service() -> DepartmentService:
    """构造 DepartmentService 实例。"""
    return DepartmentService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        clock=FixedClock(datetime.now(UTC)),
    )


@asynccontextmanager
async def _patch_scoped_session(mock_session: AsyncMock) -> Any:
    """临时替换 ScopedSessionMixin._scoped_session。"""
    original = ScopedSessionMixin._scoped_session

    @asynccontextmanager
    async def fake_scoped_session(self: Any) -> Any:
        yield mock_session

    ScopedSessionMixin._scoped_session = fake_scoped_session  # type: ignore[method-assign]
    try:
        yield
    finally:
        ScopedSessionMixin._scoped_session = original  # type: ignore[method-assign]


class TestDepartmentStatus:
    """DepartmentStatus 枚举测试。"""

    def test_status_values(self) -> None:
        """枚举值为 active / disabled。"""
        assert DepartmentStatus.ACTIVE == "active"
        assert DepartmentStatus.DISABLED == "disabled"
        assert len(DepartmentStatus) == 2


class TestCursorEncodeDecode:
    """DepartmentService 游标编解码测试。"""

    def test_roundtrip_preserves_values(self) -> None:
        """encode + decode 往返一致。"""
        sort_order = 5
        created_at = datetime(2026, 6, 15, 8, 0, 0, tzinfo=UTC)
        dept_id = uuid4()
        cursor = _encode_cursor(sort_order, created_at, dept_id)
        so, ct, did = _decode_cursor(cursor)
        assert so == sort_order
        assert ct == created_at
        assert did == dept_id

    def test_decode_invalid_base64_raises(self) -> None:
        """非法 base64 抛 invalid_cursor。"""
        with pytest.raises(AppError, match="base64url"):
            _decode_cursor("@@@bad@@@")

    def test_decode_missing_so_ct_raises(self) -> None:
        """缺少 so/ct 字段抛 invalid_cursor。"""
        import base64
        import json

        payload = json.dumps({"v": {"no_so": 1}, "id": str(uuid4())}).encode("utf-8")
        bad_cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="so / ct"):
            _decode_cursor(bad_cursor)

    def test_decode_non_integer_sort_order_raises(self) -> None:
        """so 字段非整数抛 invalid_cursor。"""
        import base64
        import json

        payload = json.dumps(
            {"v": {"so": "not-int", "ct": datetime.now(UTC).isoformat()}, "id": str(uuid4())}
        ).encode("utf-8")
        bad_cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="整数"):
            _decode_cursor(bad_cursor)

    def test_cursor_is_url_safe(self) -> None:
        """游标仅含 base64url 安全字符。"""
        cursor = _encode_cursor(1, datetime.now(UTC), uuid4())
        safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=")
        assert all(c in safe for c in cursor)


class TestUpdateSentinelProtection:
    """update 哨兵部门保护测试。"""

    async def test_root_department_reparent_forbidden(self) -> None:
        """root 部门禁止调整父子关系。"""
        service = _make_service()
        mock_session = AsyncMock()

        root_dept = MagicMock()
        root_dept.code = "root"
        root_dept.parent_id = None
        root_dept.id = uuid4()

        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=root_dept)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError, match="哨兵部门"):
                    await service.update(
                        department_id=root_dept.id,
                        display_name="root",
                        description=None,
                        sort_order=0,
                        lock_version=0,
                        parent_id=uuid4(),  # 试图改 parent
                    )
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]

    async def test_system_department_reparent_forbidden(self) -> None:
        """system 部门禁止调整父子关系。"""
        service = _make_service()
        mock_session = AsyncMock()

        system_dept = MagicMock()
        system_dept.code = "system"
        system_dept.parent_id = None
        system_dept.id = uuid4()

        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=system_dept)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError, match="哨兵部门"):
                    await service.update(
                        department_id=system_dept.id,
                        display_name="system",
                        description=None,
                        sort_order=0,
                        lock_version=0,
                        parent_id=uuid4(),
                    )
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]


class TestDeleteSentinelProtection:
    """delete 哨兵部门保护测试。"""

    async def test_delete_root_forbidden(self) -> None:
        """删除 root 哨兵部门抛 forbidden。"""
        service = _make_service()
        mock_session = AsyncMock()

        root_dept = MagicMock()
        root_dept.code = "root"
        root_dept.id = uuid4()

        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=root_dept)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError, match="哨兵部门"):
                    await service.delete(root_dept.id)
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]

    async def test_delete_with_children_raises_conflict(self) -> None:
        """存在子部门时删除抛 conflict。"""
        service = _make_service()
        mock_session = AsyncMock()

        normal_dept = MagicMock()
        normal_dept.code = "lab-001"
        normal_dept.id = uuid4()

        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        original_children_count = dept_svc_mod.DepartmentRepository.select_children_count
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=normal_dept)  # type: ignore[assignment]
        dept_svc_mod.DepartmentRepository.select_children_count = AsyncMock(return_value=3)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError, match="子部门"):
                    await service.delete(normal_dept.id)
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]
            dept_svc_mod.DepartmentRepository.select_children_count = original_children_count  # type: ignore[assignment]


class TestListAllInvalidCursor:
    """list_all 非法游标测试。"""

    async def test_invalid_cursor_raises(self) -> None:
        """非法游标抛 invalid_cursor。"""
        service = _make_service()
        with pytest.raises(AppError, match="base64url"):
            await service.list_all(cursor="@@@invalid@@@")


# ============================================================
# Additional coverage tests for DepartmentService
# ============================================================


class TestCreateDepartment:
    """DepartmentService.create — create department tests."""

    async def test_create_conflict_raises(self) -> None:
        """Creating with existing code raises conflict."""
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=MagicMock())  # existing dept found

        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError) as exc_info:
                await service.create(
                    code="LAB-001",
                    display_name="实验室A",
                    description=None,
                    sort_order=1,
                )
            assert exc_info.value.code == "conflict"

    async def test_create_success(self) -> None:
        """Creating with no conflict succeeds."""
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=None)  # no existing

        new_dept = MagicMock()
        new_dept.code = "LAB-001"
        original_insert = dept_svc_mod.DepartmentRepository.insert
        dept_svc_mod.DepartmentRepository.insert = AsyncMock(return_value=new_dept)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                result = await service.create(
                    code="LAB-001",
                    display_name="实验室A",
                    description="描述",
                    sort_order=5,
                )
                assert result.code == "LAB-001"
        finally:
            dept_svc_mod.DepartmentRepository.insert = original_insert  # type: ignore[assignment]

    async def test_create_with_parent(self) -> None:
        """Creating with parent_id succeeds."""
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=None)

        new_dept = MagicMock()
        new_dept.code = "LAB-002"
        original_insert = dept_svc_mod.DepartmentRepository.insert
        dept_svc_mod.DepartmentRepository.insert = AsyncMock(return_value=new_dept)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                result = await service.create(
                    code="LAB-002",
                    display_name="子实验室",
                    description=None,
                    sort_order=1,
                    parent_id=uuid4(),
                )
                assert result.code == "LAB-002"
        finally:
            dept_svc_mod.DepartmentRepository.insert = original_insert  # type: ignore[assignment]


class TestGetDepartment:
    """DepartmentService.get — get department tests."""

    async def test_get_not_found_raises(self) -> None:
        """Get non-existent department raises not_found."""
        service = _make_service()
        mock_session = AsyncMock()

        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=None)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError) as exc_info:
                    await service.get(uuid4())
                assert exc_info.value.code == "not_found"
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]

    async def test_get_success(self) -> None:
        """Get existing department succeeds."""
        service = _make_service()
        mock_session = AsyncMock()

        dept = MagicMock()
        dept.code = "LAB-001"
        dept.display_name = "实验室A"
        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=dept)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                result = await service.get(uuid4())
                assert result.code == "LAB-001"
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]


class TestUpdateDepartment:
    """DepartmentService.update — update department tests."""

    async def test_update_success(self) -> None:
        """Update succeeds when lock_version matches."""
        service = _make_service()
        mock_session = AsyncMock()

        updated_dept = MagicMock()
        updated_dept.code = "LAB-001"
        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        original_update = dept_svc_mod.DepartmentRepository.update
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=None)  # type: ignore[assignment]
        dept_svc_mod.DepartmentRepository.update = AsyncMock(return_value=updated_dept)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                result = await service.update(
                    department_id=uuid4(),
                    display_name="新名称",
                    description="新描述",
                    sort_order=2,
                    lock_version=0,
                )
                assert result.code == "LAB-001"
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]
            dept_svc_mod.DepartmentRepository.update = original_update  # type: ignore[assignment]

    async def test_update_not_found_raises(self) -> None:
        """Update non-existent department raises not_found."""
        service = _make_service()
        mock_session = AsyncMock()

        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        original_update = dept_svc_mod.DepartmentRepository.update
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=None)  # type: ignore[assignment]
        dept_svc_mod.DepartmentRepository.update = AsyncMock(return_value=None)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError) as exc_info:
                    await service.update(
                        department_id=uuid4(),
                        display_name="新名称",
                        description=None,
                        sort_order=1,
                        lock_version=0,
                    )
                assert exc_info.value.code == "not_found"
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]
            dept_svc_mod.DepartmentRepository.update = original_update  # type: ignore[assignment]

    async def test_update_lock_version_mismatch_raises(self) -> None:
        """Update with wrong lock_version raises conflict."""
        service = _make_service()
        mock_session = AsyncMock()

        existing_dept = MagicMock()
        existing_dept.code = "LAB-001"
        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        original_update = dept_svc_mod.DepartmentRepository.update
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=existing_dept)  # type: ignore[assignment]
        dept_svc_mod.DepartmentRepository.update = AsyncMock(return_value=None)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError) as exc_info:
                    await service.update(
                        department_id=uuid4(),
                        display_name="新名称",
                        description=None,
                        sort_order=1,
                        lock_version=5,
                    )
                assert exc_info.value.code == "conflict"
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]
            dept_svc_mod.DepartmentRepository.update = original_update  # type: ignore[assignment]


class TestSetStatus:
    """DepartmentService.set_status tests."""

    async def test_set_status_success(self) -> None:
        service = _make_service()
        mock_session = AsyncMock()

        updated = MagicMock()
        updated.status = "disabled"
        original_update_status = dept_svc_mod.DepartmentRepository.update_status
        dept_svc_mod.DepartmentRepository.update_status = AsyncMock(return_value=updated)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                result = await service.set_status(uuid4(), "disabled", 0)
                assert result.status == "disabled"
        finally:
            dept_svc_mod.DepartmentRepository.update_status = original_update_status  # type: ignore[assignment]

    async def test_set_status_not_found(self) -> None:
        service = _make_service()
        mock_session = AsyncMock()

        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        original_update_status = dept_svc_mod.DepartmentRepository.update_status
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=None)  # type: ignore[assignment]
        dept_svc_mod.DepartmentRepository.update_status = AsyncMock(return_value=None)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError) as exc_info:
                    await service.set_status(uuid4(), "disabled", 0)
                assert exc_info.value.code == "not_found"
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]
            dept_svc_mod.DepartmentRepository.update_status = original_update_status  # type: ignore[assignment]

    async def test_set_status_lock_mismatch(self) -> None:
        service = _make_service()
        mock_session = AsyncMock()

        existing = MagicMock()
        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        original_update_status = dept_svc_mod.DepartmentRepository.update_status
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=existing)  # type: ignore[assignment]
        dept_svc_mod.DepartmentRepository.update_status = AsyncMock(return_value=None)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError) as exc_info:
                    await service.set_status(uuid4(), "disabled", 5)
                assert exc_info.value.code == "conflict"
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]
            dept_svc_mod.DepartmentRepository.update_status = original_update_status  # type: ignore[assignment]


class TestListAll:
    """DepartmentService.list_all — pagination tests."""

    async def test_list_all_empty(self) -> None:
        service = _make_service()
        mock_session = AsyncMock()

        original_select_list = dept_svc_mod.DepartmentRepository.select_list
        dept_svc_mod.DepartmentRepository.select_list = AsyncMock(return_value=[])  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                result = await service.list_all()
                assert result.items == []
                assert result.next_cursor is None
                assert result.has_more is False
        finally:
            dept_svc_mod.DepartmentRepository.select_list = original_select_list  # type: ignore[assignment]

    async def test_list_all_with_items_no_next(self) -> None:
        from datetime import UTC, datetime

        service = _make_service()
        mock_session = AsyncMock()

        dept = MagicMock()
        dept.sort_order = 1
        dept.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        dept.id = uuid4()
        rows = [(dept, 5, 2, 3)]
        original_select_list = dept_svc_mod.DepartmentRepository.select_list
        dept_svc_mod.DepartmentRepository.select_list = AsyncMock(return_value=rows)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                result = await service.list_all()
                assert len(result.items) == 1
                assert result.has_more is False
        finally:
            dept_svc_mod.DepartmentRepository.select_list = original_select_list  # type: ignore[assignment]

    async def test_list_all_with_next_cursor(self) -> None:
        from datetime import UTC, datetime

        service = _make_service()
        mock_session = AsyncMock()

        dept1 = MagicMock()
        dept1.sort_order = 1
        dept1.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        dept1.id = uuid4()
        dept2 = MagicMock()
        dept2.sort_order = 2
        dept2.created_at = datetime(2026, 1, 2, tzinfo=UTC)
        dept2.id = uuid4()
        rows = [(dept1, 5, 2, 3), (dept2, 3, 1, 2)]
        original_select_list = dept_svc_mod.DepartmentRepository.select_list
        dept_svc_mod.DepartmentRepository.select_list = AsyncMock(return_value=rows)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                result = await service.list_all(limit=1)
                assert len(result.items) == 1
                assert result.has_more is True
                assert result.next_cursor is not None
        finally:
            dept_svc_mod.DepartmentRepository.select_list = original_select_list  # type: ignore[assignment]

    async def test_list_all_with_status_filter(self) -> None:
        service = _make_service()
        mock_session = AsyncMock()

        original_select_list = dept_svc_mod.DepartmentRepository.select_list
        dept_svc_mod.DepartmentRepository.select_list = AsyncMock(return_value=[])  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                result = await service.list_all(status="active")
                assert result.items == []
        finally:
            dept_svc_mod.DepartmentRepository.select_list = original_select_list  # type: ignore[assignment]

    async def test_list_all_with_valid_cursor(self) -> None:
        service = _make_service()
        mock_session = AsyncMock()

        original_select_list = dept_svc_mod.DepartmentRepository.select_list
        dept_svc_mod.DepartmentRepository.select_list = AsyncMock(return_value=[])  # type: ignore[assignment]
        cursor = _encode_cursor(5, datetime.now(UTC), uuid4())
        try:
            async with _patch_scoped_session(mock_session):
                result = await service.list_all(cursor=cursor)
                assert result.items == []
        finally:
            dept_svc_mod.DepartmentRepository.select_list = original_select_list  # type: ignore[assignment]


class TestGetNameMap:
    """DepartmentService.get_name_map tests."""

    async def test_get_name_map(self) -> None:
        service = _make_service()
        mock_session = AsyncMock()
        id1, id2 = uuid4(), uuid4()
        result_mock = MagicMock()
        result_mock.all.return_value = [(id1, "实验室A"), (id2, "实验室B")]
        mock_session.execute = AsyncMock(return_value=result_mock)

        async with _patch_scoped_session(mock_session):
            result = await service.get_name_map()
            assert len(result) == 2
            assert result[0] == (id1, "实验室A")


class TestDeleteDepartment:
    """DepartmentService.delete — additional delete tests."""

    async def test_delete_with_equipment_raises_conflict(self) -> None:
        """Delete with equipment raises conflict."""
        service = _make_service()
        mock_session = AsyncMock()

        normal_dept = MagicMock()
        normal_dept.code = "lab-001"
        normal_dept.id = uuid4()
        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        original_children_count = dept_svc_mod.DepartmentRepository.select_children_count
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=normal_dept)  # type: ignore[assignment]
        dept_svc_mod.DepartmentRepository.select_children_count = AsyncMock(return_value=0)  # type: ignore[assignment]

        equip_result = MagicMock()
        equip_result.scalar.return_value = 5
        mock_session.execute = AsyncMock(return_value=equip_result)
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError, match="仪器"):
                    await service.delete(normal_dept.id)
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]
            dept_svc_mod.DepartmentRepository.select_children_count = original_children_count  # type: ignore[assignment]

    async def test_delete_success(self) -> None:
        """Delete succeeds when no children and no equipment."""
        service = _make_service()
        mock_session = AsyncMock()

        normal_dept = MagicMock()
        normal_dept.code = "lab-001"
        normal_dept.id = uuid4()
        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        original_children_count = dept_svc_mod.DepartmentRepository.select_children_count
        original_delete_by_id = dept_svc_mod.DepartmentRepository.delete_by_id
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=normal_dept)  # type: ignore[assignment]
        dept_svc_mod.DepartmentRepository.select_children_count = AsyncMock(return_value=0)  # type: ignore[assignment]
        dept_svc_mod.DepartmentRepository.delete_by_id = AsyncMock(return_value=True)  # type: ignore[assignment]

        equip_result = MagicMock()
        equip_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=equip_result)
        try:
            async with _patch_scoped_session(mock_session):
                await service.delete(normal_dept.id)
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]
            dept_svc_mod.DepartmentRepository.select_children_count = original_children_count  # type: ignore[assignment]
            dept_svc_mod.DepartmentRepository.delete_by_id = original_delete_by_id  # type: ignore[assignment]

    async def test_delete_not_found_raises(self) -> None:
        """Delete non-existent department raises not_found."""
        service = _make_service()
        mock_session = AsyncMock()

        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=None)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError, match="实验室"):
                    await service.delete(uuid4())
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]

    async def test_delete_delete_fails_raises_not_found(self) -> None:
        """Delete when delete_by_id returns False raises not_found."""
        service = _make_service()
        mock_session = AsyncMock()

        normal_dept = MagicMock()
        normal_dept.code = "lab-001"
        normal_dept.id = uuid4()
        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        original_children_count = dept_svc_mod.DepartmentRepository.select_children_count
        original_delete_by_id = dept_svc_mod.DepartmentRepository.delete_by_id
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=normal_dept)  # type: ignore[assignment]
        dept_svc_mod.DepartmentRepository.select_children_count = AsyncMock(return_value=0)  # type: ignore[assignment]
        dept_svc_mod.DepartmentRepository.delete_by_id = AsyncMock(return_value=False)  # type: ignore[assignment]

        equip_result = MagicMock()
        equip_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=equip_result)
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError, match="实验室不存在"):
                    await service.delete(normal_dept.id)
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]
            dept_svc_mod.DepartmentRepository.select_children_count = original_children_count  # type: ignore[assignment]
            dept_svc_mod.DepartmentRepository.delete_by_id = original_delete_by_id  # type: ignore[assignment]


class TestReparentImpactPreview:
    """DepartmentService.reparent_impact_preview tests."""

    async def test_sentinel_dept_raises_forbidden(self) -> None:
        service = _make_service()
        mock_session = AsyncMock()

        root_dept = MagicMock()
        root_dept.code = "root"
        root_dept.display_name = "Root"
        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=root_dept)  # type: ignore[assignment]
        try:
            async with _patch_scoped_session(mock_session):
                with pytest.raises(AppError, match="哨兵部门"):
                    await service.reparent_impact_preview(uuid4(), uuid4())
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]

    async def test_preview_success(self) -> None:
        service = _make_service()
        mock_session = AsyncMock()

        normal_dept = MagicMock()
        normal_dept.code = "lab-001"
        normal_dept.display_name = "实验室A"
        normal_dept.id = uuid4()
        original_select_by_id = dept_svc_mod.DepartmentRepository.select_by_id
        dept_svc_mod.DepartmentRepository.select_by_id = AsyncMock(return_value=normal_dept)  # type: ignore[assignment]

        children_result = MagicMock()
        children_result.__iter__ = MagicMock(return_value=iter([]))
        equip_result = MagicMock()
        equip_result.scalar.return_value = 3
        mock_session.execute = AsyncMock(side_effect=[children_result, equip_result])
        try:
            async with _patch_scoped_session(mock_session):
                result = await service.reparent_impact_preview(normal_dept.id, uuid4())
                assert result["department_name"] == "实验室A"
                assert result["subtree_count"] == 1
                assert result["equipment_count"] == 3
        finally:
            dept_svc_mod.DepartmentRepository.select_by_id = original_select_by_id  # type: ignore[assignment]


class TestCursorDecodeEdgeCases:
    """Additional cursor decode edge cases."""

    def test_decode_invalid_json_raises(self) -> None:
        import base64

        bad_cursor = base64.urlsafe_b64encode(b"not-json").decode("ascii")
        with pytest.raises(AppError, match="JSON"):
            _decode_cursor(bad_cursor)

    def test_decode_missing_v_id_raises(self) -> None:
        import base64
        import json

        payload = json.dumps({"no_v": 1}).encode("utf-8")
        bad_cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="v / id"):
            _decode_cursor(bad_cursor)

    def test_decode_invalid_ct_raises(self) -> None:
        import base64
        import json

        payload = json.dumps({"v": {"so": 1, "ct": "bad-time"}, "id": str(uuid4())}).encode("utf-8")
        bad_cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="ISO 时间"):
            _decode_cursor(bad_cursor)

    def test_decode_invalid_uuid_raises(self) -> None:
        import base64
        import json

        payload = json.dumps(
            {"v": {"so": 1, "ct": "2026-01-01T00:00:00+00:00"}, "id": "not-a-uuid"}
        ).encode("utf-8")
        bad_cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="UUID"):
            _decode_cursor(bad_cursor)


class TestSessionFactoryProperty:
    """Test session_factory property."""

    def test_session_factory_returns_factory(self) -> None:
        factory = MagicMock()
        service = DepartmentService(
            session_factory=factory,
            department_id=uuid4(),
        )
        assert service.session_factory is factory
