"""单元测试：标准层 — 工业对象图枚举、游标与可见性。

覆盖：
- ObjectType 枚举包含全部 7 种对象类型；
- RelationType 枚举包含 7 种关系类型；
- HIERARCHICAL_RELATIONS 仅含 contains / upstream_of / downstream_of；
- _encode_list_cursor + _decode_list_cursor 往返一致；
- _decode_list_cursor 非法 base64 抛 invalid_cursor；
- _decode_list_cursor 缺少 v/id 字段抛 invalid_cursor；
- IndustrialObject __repr__ 包含 code 和 object_type；
- ObjectGraphService CRUD 方法（add/get/update/status/delete/count/by_code/list）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.standards.objects.object_graph import (
    ObjectGraphService,
    _decode_list_cursor,
    _encode_list_cursor,
)
from packages.standards.objects.objects import (
    HIERARCHICAL_RELATIONS,
    IndustrialObject,
    ObjectType,
    RelationType,
)


class TestObjectTypeEnum:
    """ObjectType 枚举测试。"""

    def test_object_type_has_seven_values(self) -> None:
        """ObjectType 包含 7 种对象类型。"""
        assert ObjectType.LAB == "lab"
        assert ObjectType.PRODUCTION_LINE == "production_line"
        assert ObjectType.EQUIPMENT_GROUP == "equipment_group"
        assert ObjectType.INSTRUMENT == "instrument"
        assert ObjectType.MEASUREMENT_POINT == "measurement_point"
        assert ObjectType.MATERIAL == "material"
        assert ObjectType.SIGNAL == "signal"
        assert len(ObjectType) == 7

    def test_object_type_values_are_unique(self) -> None:
        """枚举值互不相同。"""
        values = [t.value for t in ObjectType]
        assert len(values) == len(set(values))


class TestRelationTypeEnum:
    """RelationType 枚举测试。"""

    def test_relation_type_has_seven_values(self) -> None:
        """RelationType 包含 7 种关系类型。"""
        assert RelationType.CONTAINS == "contains"
        assert RelationType.CONNECTED_TO == "connected_to"
        assert RelationType.UPSTREAM_OF == "upstream_of"
        assert RelationType.DOWNSTREAM_OF == "downstream_of"
        assert RelationType.MEASURES == "measures"
        assert RelationType.SIMULATES == "simulates"
        assert RelationType.EQUIVALENT_TO == "equivalent_to"
        assert len(RelationType) == 7

    def test_hierarchical_relations_subset(self) -> None:
        """HIERARCHICAL_RELATIONS 仅含层次型关系。"""
        assert HIERARCHICAL_RELATIONS == frozenset({"contains", "upstream_of", "downstream_of"})
        # 非层次型关系不在集合中
        assert "connected_to" not in HIERARCHICAL_RELATIONS
        assert "measures" not in HIERARCHICAL_RELATIONS


class TestListCursorEncodeDecode:
    """ObjectGraphService 列表游标编解码测试。"""

    def test_roundtrip_preserves_values(self) -> None:
        """encode + decode 往返一致。"""
        created_at = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        obj_id = uuid4()
        cursor = _encode_list_cursor(created_at, obj_id)
        decoded_at, decoded_id = _decode_list_cursor(cursor)
        assert decoded_at == created_at
        assert decoded_id == obj_id

    def test_decode_invalid_base64_raises(self) -> None:
        """非法 base64 抛 invalid_cursor。"""
        with pytest.raises(AppError, match="base64url"):
            _decode_list_cursor("@@@invalid@@@")

    def test_decode_missing_fields_raises(self) -> None:
        """缺少 v/id 字段抛 invalid_cursor。"""
        import base64
        import json

        payload = json.dumps({"no_v": "x"}).encode("utf-8")
        bad_cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="v / id"):
            _decode_list_cursor(bad_cursor)

    def test_decode_invalid_iso_time_raises(self) -> None:
        """v 字段非合法 ISO 时间抛 invalid_cursor。"""
        import base64
        import json

        payload = json.dumps({"v": "bad-time", "id": str(uuid4())}).encode("utf-8")
        bad_cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="ISO 时间"):
            _decode_list_cursor(bad_cursor)


class TestIndustrialObjectRepr:
    """IndustrialObject __repr__ 测试。"""

    def test_repr_contains_code_and_type(self) -> None:
        """__repr__ 包含 code 和 object_type。"""
        obj = IndustrialObject()
        obj.code = "LAB-001"
        obj.object_type = "lab"
        obj.status = "active"
        repr_str = repr(obj)
        assert "LAB-001" in repr_str
        assert "lab" in repr_str
        assert "IndustrialObject" in repr_str


# ============================================================
# ObjectGraphService — mock session 测试
# ============================================================


def _make_session() -> MagicMock:
    """创建一个支持 async context 的 mock session。"""
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.delete = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock())
    return session


def _make_service(
    monkeypatch: pytest.MonkeyPatch,
    session: MagicMock,
) -> ObjectGraphService:
    """创建 ObjectGraphService 并 patch _scoped_session。"""
    dept_id = uuid4()
    actor_id = uuid4()
    service = ObjectGraphService(
        session_factory=MagicMock(),
        department_id=dept_id,
        actor_id=actor_id,
    )

    @asynccontextmanager
    async def _scoped(self: ObjectGraphService):  # type: ignore[no-untyped-def]
        yield session

    monkeypatch.setattr(ObjectGraphService, "_scoped_session", _scoped)
    return service


def _obj(**overrides: object) -> SimpleNamespace:
    """创建一个简单的对象替身。"""
    defaults: dict[str, object] = {
        "id": uuid4(),
        "object_type": "lab",
        "code": "LAB-001",
        "display_name": "实验室",
        "description": None,
        "component_id": None,
        "department_id": uuid4(),
        "visible_departments": [],
        "visibility_scope": "tree",
        "owner_user_id": None,
        "status": "active",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
        "lock_version": 0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestAddObject:
    """ObjectGraphService.add_object 测试。"""

    async def test_add_object_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        # existing query returns None (no conflict)
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = None
        # uniqueness check (no parent → only 1 execute call)
        session.execute = AsyncMock(return_value=existing_result)

        service = _make_service(monkeypatch, session)

        result = await service.add_object(
            object_type="lab",
            code="LAB-001",
            display_name="实验室A",
        )
        assert result.code == "LAB-001"
        assert result.object_type == "lab"
        assert result.status == "active"
        assert result.visibility_scope == "tree"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_add_object_conflict_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = _obj()
        session.execute = AsyncMock(return_value=existing_result)

        service = _make_service(monkeypatch, session)

        with pytest.raises(AppError) as exc_info:
            await service.add_object(
                object_type="lab",
                code="LAB-001",
                display_name="实验室A",
            )
        assert exc_info.value.code == "conflict"

    async def test_add_object_with_parent_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dept_id = uuid4()
        session = _make_session()
        parent_obj = _obj(department_id=dept_id)

        # First execute → uniqueness check (None); second → parent check (parent_obj)
        uniqueness_result = MagicMock()
        uniqueness_result.scalar_one_or_none.return_value = None
        parent_result = MagicMock()
        parent_result.scalar_one_or_none.return_value = parent_obj
        session.execute = AsyncMock(side_effect=[uniqueness_result, parent_result])

        service = _make_service(monkeypatch, session)
        service._dept_id = dept_id  # type: ignore[assignment]

        result = await service.add_object(
            object_type="production_line",
            code="PL-001",
            display_name="产线A",
            parent_id=parent_obj.id,
        )
        assert result.code == "PL-001"

    async def test_add_object_parent_not_found_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _make_session()
        # First execute → uniqueness None; second → parent None
        uniqueness_result = MagicMock()
        uniqueness_result.scalar_one_or_none.return_value = None
        parent_result = MagicMock()
        parent_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(side_effect=[uniqueness_result, parent_result])

        service = _make_service(monkeypatch, session)

        with pytest.raises(AppError) as exc_info:
            await service.add_object(
                object_type="production_line",
                code="PL-001",
                display_name="产线A",
                parent_id=uuid4(),
            )
        assert exc_info.value.code == "not_found"

    async def test_add_object_parent_wrong_dept_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _make_session()
        parent_obj = _obj(department_id=uuid4())  # different dept
        uniqueness_result = MagicMock()
        uniqueness_result.scalar_one_or_none.return_value = None
        parent_result = MagicMock()
        parent_result.scalar_one_or_none.return_value = parent_obj
        session.execute = AsyncMock(side_effect=[uniqueness_result, parent_result])

        service = _make_service(monkeypatch, session)

        with pytest.raises(AppError) as exc_info:
            await service.add_object(
                object_type="production_line",
                code="PL-001",
                display_name="产线A",
                parent_id=uuid4(),
            )
        assert exc_info.value.code == "not_found"

    async def test_add_object_with_all_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=existing_result)

        service = _make_service(monkeypatch, session)
        comp_id = uuid4()
        dept_id = uuid4()
        result = await service.add_object(
            object_type="instrument",
            code="INST-001",
            display_name="仪器A",
            description="描述",
            component_id=comp_id,
            department_id=dept_id,
            visible_departments=["dept1", "dept2"],
        )
        assert result.description == "描述"
        assert result.component_id == comp_id
        assert result.department_id == dept_id
        assert result.visible_departments == ["dept1", "dept2"]


class TestGetObject:
    """ObjectGraphService.get_object 测试。"""

    async def test_get_object_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        obj = _obj()
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = obj
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        result = await service.get_object(obj.id)
        assert result.code == "LAB-001"

    async def test_get_object_not_found_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        with pytest.raises(AppError) as exc_info:
            await service.get_object(uuid4())
        assert exc_info.value.code == "not_found"


class TestUpdateObject:
    """ObjectGraphService.update_object 测试。"""

    async def test_update_object_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        obj = _obj()
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = obj
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        result = await service.update_object(
            obj.id,
            display_name="新名称",
            description="新描述",
        )
        assert result.display_name == "新名称"
        assert result.description == "新描述"
        assert result.lock_version == 1
        session.flush.assert_awaited_once()

    async def test_update_object_with_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        obj = _obj()
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = obj
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        result = await service.update_object(
            obj.id,
            display_name="新名称",
            object_type="production_line",
        )
        assert result.object_type == "production_line"

    async def test_update_object_with_dept_and_visible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _make_session()
        obj = _obj()
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = obj
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        new_dept = uuid4()
        result = await service.update_object(
            obj.id,
            display_name="新名称",
            department_id=new_dept,
            visible_departments=["d1"],
        )
        assert result.department_id == new_dept
        assert result.visible_departments == ["d1"]

    async def test_update_object_not_found_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        with pytest.raises(AppError) as exc_info:
            await service.update_object(uuid4(), "新名称")
        assert exc_info.value.code == "not_found"


class TestSetObjectStatus:
    """ObjectGraphService.set_object_status 测试。"""

    async def test_set_status_inactive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        obj = _obj()
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = obj
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        result = await service.set_object_status(obj.id, "inactive")
        assert result.status == "inactive"
        assert result.lock_version == 1

    async def test_set_status_not_found_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        with pytest.raises(AppError) as exc_info:
            await service.set_object_status(uuid4(), "inactive")
        assert exc_info.value.code == "not_found"


class TestDeleteObject:
    """ObjectGraphService.delete_object 测试。"""

    async def test_delete_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        obj = _obj()
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = obj
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        await service.delete_object(obj.id)
        session.delete.assert_awaited_once_with(obj)
        session.flush.assert_awaited_once()

    async def test_delete_not_found_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        with pytest.raises(AppError) as exc_info:
            await service.delete_object(uuid4())
        assert exc_info.value.code == "not_found"


class TestCountFactsByObject:
    """ObjectGraphService.count_facts_by_object 测试。"""

    async def test_count_returns_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        count_result = MagicMock()
        count_result.scalar.return_value = 5
        session.execute = AsyncMock(return_value=count_result)

        service = _make_service(monkeypatch, session)
        count = await service.count_facts_by_object(uuid4())
        assert count == 5

    async def test_count_returns_zero_when_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        count_result = MagicMock()
        count_result.scalar.return_value = None
        session.execute = AsyncMock(return_value=count_result)

        service = _make_service(monkeypatch, session)
        count = await service.count_facts_by_object(uuid4())
        assert count == 0


class TestGetObjectByCode:
    """ObjectGraphService.get_object_by_code 测试。"""

    async def test_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        obj = _obj(code="LAB-001", object_type="lab")
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = obj
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        result = await service.get_object_by_code("LAB-001", "lab")
        assert result is not None
        assert result.code == "LAB-001"

    async def test_not_found_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        result = await service.get_object_by_code("NOPE", "lab")
        assert result is None


class TestListObjects:
    """ObjectGraphService.list_objects 测试。"""

    async def test_empty_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        query_result = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        query_result.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        items, cursor = await service.list_objects()
        assert items == []
        assert cursor is None

    async def test_with_items_no_next_cursor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        obj1 = _obj(id=uuid4(), created_at=datetime(2026, 1, 1, tzinfo=UTC))
        obj2 = _obj(id=uuid4(), created_at=datetime(2026, 1, 2, tzinfo=UTC))
        query_result = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [obj1, obj2]
        query_result.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        items, cursor = await service.list_objects(page_size=20)
        assert len(items) == 2
        # fewer than page_size → no next cursor
        assert cursor is None

    async def test_with_items_has_next_cursor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        # page_size=1, fetch_limit=2 → 2 objects returned → has_more=True
        obj1 = _obj(id=uuid4(), created_at=datetime(2026, 1, 1, tzinfo=UTC))
        obj2 = _obj(id=uuid4(), created_at=datetime(2026, 1, 2, tzinfo=UTC))
        query_result = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [obj1, obj2]
        query_result.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        items, cursor = await service.list_objects(page_size=1)
        assert len(items) == 1
        assert cursor is not None

    async def test_list_with_type_filter_str(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        query_result = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        query_result.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        items, cursor = await service.list_objects(object_type="lab")
        assert items == []

    async def test_list_with_type_filter_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        query_result = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        query_result.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        items, cursor = await service.list_objects(object_type=["lab", "instrument"])
        assert items == []

    async def test_list_with_cursor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        query_result = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        query_result.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        cursor = _encode_list_cursor(datetime(2026, 1, 1, tzinfo=UTC), uuid4())
        items, _ = await service.list_objects(cursor=cursor)
        assert items == []

    async def test_list_with_invalid_cursor_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        service = _make_service(monkeypatch, session)
        with pytest.raises(AppError) as exc_info:
            await service.list_objects(cursor="@@@bad@@@")
        assert exc_info.value.code == "invalid_cursor"

    async def test_list_with_department_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        query_result = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        query_result.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        items, _ = await service.list_objects(department_id=uuid4())
        assert items == []

    async def test_list_page_size_clamped_to_max(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _make_session()
        query_result = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        query_result.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=query_result)

        service = _make_service(monkeypatch, session)
        # page_size=200 → clamped to MAX_PAGE_SIZE
        items, _ = await service.list_objects(page_size=200)
        assert items == []


class TestDecodeListCursorEdgeCases:
    """Additional cursor decode edge cases for coverage."""

    def test_decode_invalid_json_raises(self) -> None:
        """base64 decodes but JSON parse fails."""
        import base64

        bad_cursor = base64.urlsafe_b64encode(b"not-json").decode("ascii")
        with pytest.raises(AppError, match="JSON"):
            _decode_list_cursor(bad_cursor)

    def test_decode_invalid_uuid_raises(self) -> None:
        """id field is not a valid UUID."""
        import base64
        import json

        payload = json.dumps({"v": "2026-01-01T00:00:00+00:00", "id": "not-a-uuid"}).encode()
        bad_cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="UUID"):
            _decode_list_cursor(bad_cursor)


class TestObjectGraphServiceProperties:
    """Test the session_factory property."""

    def test_session_factory_property(self) -> None:
        factory = MagicMock()
        service = ObjectGraphService(
            session_factory=factory,
            department_id=uuid4(),
            actor_id=uuid4(),
        )
        assert service.session_factory is factory
