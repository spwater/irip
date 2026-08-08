"""单元测试：FactRepository 事实数据访问层 + 游标编解码。

覆盖：
- _encode_cursor / _decode_cursor 往返 + 非法游标各分支；
- insert_fact / get_fact（成功 + not_found）/ search_facts / list_facts /
  find_by_idempotency_key。

使用 mock AsyncSession。
"""

import base64
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.facts.repository import FactRepository, _decode_cursor, _encode_cursor


def _make_result(scalar: Any = None, scalars_all: list[Any] | None = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = scalars_all or []
    result.scalars.return_value = scalars_mock
    return result


# ============================================================
# Cursor encode/decode
# ============================================================


class TestCursorEncodeDecode:
    """_encode_cursor / _decode_cursor 测试。"""

    def test_roundtrip(self) -> None:
        """编解码往返一致。"""
        created_at = datetime(2026, 6, 15, 8, 0, 0, tzinfo=UTC)
        entity_id = uuid4()
        cursor = _encode_cursor(created_at, entity_id)
        ct, eid = _decode_cursor(cursor)
        assert ct == created_at
        assert eid == entity_id

    def test_decode_invalid_base64(self) -> None:
        """非法 base64 抛 invalid_cursor。"""
        with pytest.raises(AppError, match="base64url"):
            _decode_cursor("@@@bad@@@")

    def test_decode_invalid_json(self) -> None:
        """JSON 解析失败抛 invalid_cursor。"""
        payload = b"\x00\x01\x02"
        bad = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="JSON"):
            _decode_cursor(bad)

    def test_decode_missing_fields(self) -> None:
        """缺少 v / id 字段抛 invalid_cursor。"""
        import json

        payload = json.dumps({"no_v": 1}).encode("utf-8")
        bad = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="v / id"):
            _decode_cursor(bad)

    def test_decode_invalid_iso_time(self) -> None:
        """v 字段非合法 ISO 时间抛 invalid_cursor。"""
        import json

        payload = json.dumps({"v": "not-a-time", "id": str(uuid4())}).encode("utf-8")
        bad = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="ISO"):
            _decode_cursor(bad)

    def test_decode_invalid_uuid(self) -> None:
        """id 字段非合法 UUID 抛 invalid_cursor。"""
        import json

        payload = json.dumps({"v": datetime.now(UTC).isoformat(), "id": "not-uuid"}).encode("utf-8")
        bad = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="UUID"):
            _decode_cursor(bad)

    def test_cursor_url_safe(self) -> None:
        """游标仅含 base64url 安全字符。"""
        cursor = _encode_cursor(datetime.now(UTC), uuid4())
        safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=")
        assert all(c in safe for c in cursor)


# ============================================================
# FactRepository
# ============================================================


class TestInsertFact:
    """insert_fact 测试。"""

    async def test_insert_fact_success(self) -> None:
        session = AsyncMock()
        fact = await FactRepository.insert_fact(
            session,
            department_id=uuid4(),
            fact_type="experiment_run",
            object_id=uuid4(),
            owner_user_id=uuid4(),
        )
        session.add.assert_called_once()
        session.flush.assert_awaited_once()
        assert fact.fact_type == "experiment_run"
        assert fact.status == "active"

    async def test_insert_fact_with_all_params(self) -> None:
        session = AsyncMock()
        fact = await FactRepository.insert_fact(
            session,
            department_id=uuid4(),
            fact_type="simulation_run",
            object_id=uuid4(),
            owner_user_id=uuid4(),
            subject_id="SUBJ-001",
            task_name="任务A",
            department_name="研发一部",
        )
        assert fact.subject_id == "SUBJ-001"
        assert fact.task_name == "任务A"


class TestGetFact:
    """get_fact 测试。"""

    async def test_get_fact_success(self) -> None:
        fact = MagicMock()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=fact))
        result = await FactRepository.get_fact(session, uuid4(), uuid4())
        assert result is fact

    async def test_get_fact_not_found(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_result(scalar=None))
        with pytest.raises(AppError, match="不存在"):
            await FactRepository.get_fact(session, uuid4(), uuid4())


class TestSearchFacts:
    """search_facts 测试。"""

    async def test_search_facts_empty(self) -> None:
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = []
        session.execute = AsyncMock(return_value=result_mock)
        results, cursor = await FactRepository.search_facts(session, "nothing", uuid4())
        assert results == []
        assert cursor is None

    async def test_search_facts_with_cursor(self) -> None:
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = []
        session.execute = AsyncMock(return_value=result_mock)
        cursor = _encode_cursor(datetime.now(UTC), uuid4())
        results, next_cursor = await FactRepository.search_facts(
            session, "test", uuid4(), cursor=cursor
        )
        assert results == []

    async def test_search_facts_with_filters(self) -> None:
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = []
        session.execute = AsyncMock(return_value=result_mock)
        results, _ = await FactRepository.search_facts(
            session, "test", uuid4(), filters={"fact_type": "experiment_run"}
        )
        assert results == []


class TestListFacts:
    """list_facts 测试。"""

    async def test_list_facts_success(self) -> None:
        rows = [
            MagicMock(
                fact_id=uuid4(),
                fact_type="experiment_run",
                subject_id="SUBJ-1",
                status="active",
                created_at=datetime.now(UTC),
            )
        ]
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = rows
        session.execute = AsyncMock(return_value=result_mock)
        results, cursor = await FactRepository.list_facts(session, uuid4())
        assert len(results) == 1

    async def test_list_facts_empty(self) -> None:
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.all.return_value = []
        session.execute = AsyncMock(return_value=result_mock)
        results, cursor = await FactRepository.list_facts(session, uuid4())
        assert results == []
        assert cursor is None


class TestFindByIdempotencyKey:
    """find_by_idempotency_key 测试。"""
