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
