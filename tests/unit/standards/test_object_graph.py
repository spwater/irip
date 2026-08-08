"""单元测试：标准层 — 工业对象图枚举、游标与可见性。

覆盖：
- ObjectType 枚举包含全部 7 种对象类型；
- RelationType 枚举包含 7 种关系类型；
- HIERARCHICAL_RELATIONS 仅含 contains / upstream_of / downstream_of；
- _encode_list_cursor + _decode_list_cursor 往返一致；
- _decode_list_cursor 非法 base64 抛 invalid_cursor；
- _decode_list_cursor 缺少 v/id 字段抛 invalid_cursor；
- IndustrialObject __repr__ 包含 code 和 object_type。
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.standards.objects.object_graph import _decode_list_cursor, _encode_list_cursor
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
