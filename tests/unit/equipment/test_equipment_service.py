"""单元测试：EquipmentService 设备仪器业务编排服务。

覆盖：
- create：成功 + 冲突（code 已存在）；
- get：成功 + 不存在；
- update：成功 + lock_version 不匹配（conflict）+ 不存在（not_found）；
- set_status：成功 + 不存在 + conflict；
- delete：成功 + 不存在；
- list：分页（含 has_more / next_cursor）+ 游标解码；
- _encode_cursor / _decode_cursor 往返 + 非法游标。

使用 patched _scoped_session + mock EquipmentRepository。
"""

import base64
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from packages.common.clock import FixedClock
from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.equipment.service import (
    EquipmentListResult,
    EquipmentService,
    _decode_cursor,
    _encode_cursor,
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


def _make_service() -> EquipmentService:
    return EquipmentService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        actor_id=uuid4(),
    )


def _make_equipment(equip_id: UUID | None = None) -> MagicMock:
    e = MagicMock()
    e.id = equip_id or uuid4()
    e.code = "EQ-001"
    e.display_name = "光谱仪"
    e.description = "描述"
    e.department_id = uuid4()
    e.status = "active"
    e.sort_order = 1
    e.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    e.lock_version = 0
    return e


# ============================================================
# properties
# ============================================================


class TestProperties:
    """公开只读属性测试。"""

    def test_department_id_property(self) -> None:
        """department_id 属性返回构造值。"""
        dept = uuid4()
        svc = EquipmentService(MagicMock(), dept, actor_id=uuid4())
        assert svc.department_id == dept

    def test_actor_id_property(self) -> None:
        """actor_id 属性返回构造值。"""
        actor = uuid4()
        svc = EquipmentService(MagicMock(), uuid4(), actor_id=actor)
        assert svc.actor_id == actor

    def test_actor_id_none(self) -> None:
        """未传 actor_id 时为 None。"""
        svc = EquipmentService(MagicMock(), uuid4())
        assert svc.actor_id is None

    def test_session_factory_property(self) -> None:
        """session_factory 属性返回构造值。"""
        factory = MagicMock()
        svc = EquipmentService(factory, uuid4())
        assert svc.session_factory is factory


# ============================================================
# create
# ============================================================


class TestCreate:
    """create 测试。"""

    async def test_create_success(self) -> None:
        """成功创建设备。"""
        session = AsyncMock()
        equipment = _make_equipment()
        service = _make_service()

        with (
            patch(
                "packages.equipment.service.EquipmentRepository.select_by_org_and_code",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "packages.equipment.service.EquipmentRepository.insert",
                new_callable=AsyncMock,
                return_value=equipment,
            ),
        ):
            async with _patch_scoped_session(session):
                result = await service.create(
                    department_id=uuid4(),
                    code="EQ-001",
                    display_name="光谱仪",
                    description="desc",
                    sort_order=0,
                )

        assert result is equipment

    async def test_create_conflict(self) -> None:
        """code 已存在抛 conflict。"""
        session = AsyncMock()
        service = _make_service()

        with patch(
            "packages.equipment.service.EquipmentRepository.select_by_org_and_code",
            new_callable=AsyncMock,
            return_value=_make_equipment(),
        ):
            async with _patch_scoped_session(session):
                with pytest.raises(AppError, match="已存在"):
                    await service.create(
                        department_id=uuid4(),
                        code="EQ-001",
                        display_name="x",
                        description=None,
                        sort_order=0,
                    )


# ============================================================
# get
# ============================================================


class TestGet:
    """get 测试。"""

    async def test_get_success(self) -> None:
        """成功获取设备。"""
        session = AsyncMock()
        equip = _make_equipment()
        service = _make_service()

        with patch(
            "packages.equipment.service.EquipmentRepository.select_by_id",
            new_callable=AsyncMock,
            return_value=equip,
        ):
            async with _patch_scoped_session(session):
                result = await service.get(equip.id)

        assert result is equip

    async def test_get_not_found(self) -> None:
        """设备不存在抛 not_found。"""
        session = AsyncMock()
        service = _make_service()

        with patch(
            "packages.equipment.service.EquipmentRepository.select_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with _patch_scoped_session(session):
                with pytest.raises(AppError, match="不存在"):
                    await service.get(uuid4())


# ============================================================
# update
# ============================================================


class TestUpdate:
    """update 测试。"""

    async def test_update_success(self) -> None:
        """成功更新设备。"""
        session = AsyncMock()
        equip = _make_equipment()
        service = _make_service()

        with patch(
            "packages.equipment.service.EquipmentRepository.update",
            new_callable=AsyncMock,
            return_value=equip,
        ):
            async with _patch_scoped_session(session):
                result = await service.update(
                    equipment_id=equip.id,
                    display_name="新名",
                    description=None,
                    department_id=uuid4(),
                    sort_order=2,
                    lock_version=0,
                )

        assert result is equip

    async def test_update_conflict_lock_version(self) -> None:
        """lock_version 不匹配抛 conflict。"""
        session = AsyncMock()
        existing = _make_equipment()
        existing.department_id = service_dept = uuid4()
        service = EquipmentService(MagicMock(), service_dept, actor_id=uuid4())

        with (
            patch(
                "packages.equipment.service.EquipmentRepository.update",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "packages.equipment.service.EquipmentRepository.select_by_id",
                new_callable=AsyncMock,
                return_value=existing,
            ),
        ):
            async with _patch_scoped_session(session):
                with pytest.raises(AppError, match="已被修改"):
                    await service.update(
                        equipment_id=uuid4(),
                        display_name="x",
                        description=None,
                        department_id=service_dept,
                        sort_order=0,
                        lock_version=5,
                    )

    async def test_update_not_found(self) -> None:
        """设备不存在抛 not_found。"""
        session = AsyncMock()
        service = _make_service()

        with (
            patch(
                "packages.equipment.service.EquipmentRepository.update",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "packages.equipment.service.EquipmentRepository.select_by_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            async with _patch_scoped_session(session):
                with pytest.raises(AppError, match="不存在"):
                    await service.update(
                        equipment_id=uuid4(),
                        display_name="x",
                        description=None,
                        department_id=uuid4(),
                        sort_order=0,
                        lock_version=0,
                    )


# ============================================================
# set_status
# ============================================================


class TestSetStatus:
    """set_status 测试。"""

    async def test_set_status_success(self) -> None:
        """成功更新状态。"""
        session = AsyncMock()
        equip = _make_equipment()
        service = _make_service()

        with patch(
            "packages.equipment.service.EquipmentRepository.update_status",
            new_callable=AsyncMock,
            return_value=equip,
        ):
            async with _patch_scoped_session(session):
                result = await service.set_status(uuid4(), "disabled", 0)

        assert result is equip

    async def test_set_status_not_found(self) -> None:
        """设备不存在抛 not_found。"""
        session = AsyncMock()
        service = _make_service()

        with (
            patch(
                "packages.equipment.service.EquipmentRepository.update_status",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "packages.equipment.service.EquipmentRepository.select_by_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            async with _patch_scoped_session(session):
                with pytest.raises(AppError, match="不存在"):
                    await service.set_status(uuid4(), "disabled", 0)

    async def test_set_status_conflict(self) -> None:
        """lock_version 不匹配抛 conflict。"""
        session = AsyncMock()
        service = _make_service()

        with (
            patch(
                "packages.equipment.service.EquipmentRepository.update_status",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "packages.equipment.service.EquipmentRepository.select_by_id",
                new_callable=AsyncMock,
                return_value=_make_equipment(),
            ),
        ):
            async with _patch_scoped_session(session):
                with pytest.raises(AppError, match="已被修改"):
                    await service.set_status(uuid4(), "disabled", 5)


# ============================================================
# delete
# ============================================================


class TestDelete:
    """delete 测试。"""

    async def test_delete_success(self) -> None:
        """成功删除设备。"""
        session = AsyncMock()
        equip = _make_equipment()
        service = _make_service()

        with patch(
            "packages.equipment.service.EquipmentRepository.select_by_id",
            new_callable=AsyncMock,
            return_value=equip,
        ):
            async with _patch_scoped_session(session):
                await service.delete(equip.id)

        session.execute.assert_awaited_once()

    async def test_delete_not_found(self) -> None:
        """设备不存在抛 not_found。"""
        session = AsyncMock()
        service = _make_service()

        with patch(
            "packages.equipment.service.EquipmentRepository.select_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            async with _patch_scoped_session(session):
                with pytest.raises(AppError, match="不存在"):
                    await service.delete(uuid4())


# ============================================================
# list
# ============================================================


class TestList:
    """list 分页测试。"""

    async def test_list_single_page(self) -> None:
        """单页列表无 has_more。"""
        session = AsyncMock()
        equip = _make_equipment()
        rows = [(equip, "研发一部")]
        service = _make_service()

        with patch(
            "packages.equipment.service.EquipmentRepository.select_list",
            new_callable=AsyncMock,
            return_value=rows,
        ):
            async with _patch_scoped_session(session):
                result = await service.list(limit=20)

        assert isinstance(result, EquipmentListResult)
        assert len(result.items) == 1
        assert result.has_more is False
        assert result.next_cursor is None

    async def test_list_has_more_with_cursor(self) -> None:
        """超过 limit 时 has_more=True 且生成 next_cursor。"""
        session = AsyncMock()
        equips = [_make_equipment() for _ in range(3)]
        rows = [(e, f"dept-{i}") for i, e in enumerate(equips)]
        service = _make_service()

        with patch(
            "packages.equipment.service.EquipmentRepository.select_list",
            new_callable=AsyncMock,
            return_value=rows,
        ):
            async with _patch_scoped_session(session):
                result = await service.list(limit=2)

        assert result.has_more is True
        assert result.next_cursor is not None
        assert len(result.items) == 2

    async def test_list_with_cursor(self) -> None:
        """带游标查询。"""
        session = AsyncMock()
        equip = _make_equipment()
        cursor = _encode_cursor(equip.sort_order, equip.created_at, equip.id)
        service = _make_service()

        with patch(
            "packages.equipment.service.EquipmentRepository.select_list",
            new_callable=AsyncMock,
            return_value=[(equip, "dept")],
        ):
            async with _patch_scoped_session(session):
                result = await service.list(cursor=cursor, limit=10)

        assert len(result.items) == 1

    async def test_list_limit_clamped(self) -> None:
        """limit 被 clamp 到 [1, MAX_PAGE_SIZE]。"""
        session = AsyncMock()
        service = _make_service()

        with patch(
            "packages.equipment.service.EquipmentRepository.select_list",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_select:
            async with _patch_scoped_session(session):
                await service.list(limit=0)
        # fetch_limit = effective_limit + 1, effective_limit = max(0, 1) = 1
        call_kwargs = mock_select.call_args.kwargs
        assert call_kwargs["limit"] == 2  # max(0,1)+1=2


# ============================================================
# cursor encode/decode
# ============================================================


class TestCursor:
    """_encode_cursor / _decode_cursor 测试。"""

    def test_roundtrip(self) -> None:
        """编解码往返一致。"""
        sort_order = 5
        created_at = datetime(2026, 6, 15, 8, 0, 0, tzinfo=UTC)
        equip_id = uuid4()
        cursor = _encode_cursor(sort_order, created_at, equip_id)
        so, ct, eid = _decode_cursor(cursor)
        assert so == sort_order
        assert ct == created_at
        assert eid == equip_id

    def test_decode_invalid_base64(self) -> None:
        """非法 base64 抛 invalid_cursor。"""
        with pytest.raises(AppError, match="base64url"):
            _decode_cursor("@@@bad@@@")

    def test_decode_missing_fields(self) -> None:
        """缺少 v / id 字段抛 invalid_cursor。"""
        payload = json.dumps({"no_v": 1}).encode("utf-8")
        bad = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="v / id"):
            _decode_cursor(bad)

    def test_decode_missing_so_ct(self) -> None:
        """缺少 so / ct 字段抛 invalid_cursor。"""
        payload = json.dumps({"v": {"x": 1}, "id": str(uuid4())}).encode("utf-8")
        bad = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="so / ct"):
            _decode_cursor(bad)

    def test_decode_non_integer_so(self) -> None:
        """so 字段非整数抛 invalid_cursor。"""
        payload = json.dumps(
            {"v": {"so": "abc", "ct": datetime.now(UTC).isoformat()}, "id": str(uuid4())}
        ).encode("utf-8")
        bad = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="整数"):
            _decode_cursor(bad)

    def test_decode_invalid_iso_time(self) -> None:
        """ct 字段非合法 ISO 时间抛 invalid_cursor。"""
        payload = json.dumps({"v": {"so": 1, "ct": "not-a-time"}, "id": str(uuid4())}).encode(
            "utf-8"
        )
        bad = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="ISO"):
            _decode_cursor(bad)

    def test_decode_invalid_uuid(self) -> None:
        """id 字段非合法 UUID 抛 invalid_cursor。"""
        payload = json.dumps(
            {"v": {"so": 1, "ct": datetime.now(UTC).isoformat()}, "id": "not-uuid"}
        ).encode("utf-8")
        bad = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="UUID"):
            _decode_cursor(bad)

    def test_decode_invalid_json(self) -> None:
        """JSON 解析失败抛 invalid_cursor。"""
        payload = b"\x00\x01\x02"
        bad = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="JSON"):
            _decode_cursor(bad)

    def test_cursor_url_safe(self) -> None:
        """游标仅含 base64url 安全字符。"""
        cursor = _encode_cursor(1, datetime.now(UTC), uuid4())
        safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=")
        assert all(c in safe for c in cursor)
