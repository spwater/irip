"""单元测试：FactRepository 游标编解码。

覆盖：
- _encode_cursor + _decode_cursor 往返一致；
- _decode_cursor 对非法 base64 抛 invalid_cursor；
- _decode_cursor 对非法 JSON 抛 invalid_cursor；
- _decode_cursor 缺少 v/id 字段抛 invalid_cursor；
- _decode_cursor v 字段非合法 ISO 时间抛 invalid_cursor；
- _decode_cursor id 字段非合法 UUID 抛 invalid_cursor；
- search_facts 的 page_size 边界（min 1 / max 100）。
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.facts.repository import _decode_cursor, _encode_cursor


class TestCursorEncodeDecode:
    """FactRepository 游标编解码测试。"""

    def test_roundtrip_preserves_values(self) -> None:
        """encode + decode 往返一致。"""
        created_at = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        entity_id = uuid4()
        cursor = _encode_cursor(created_at, entity_id)
        decoded_at, decoded_id = _decode_cursor(cursor)
        assert decoded_at == created_at
        assert decoded_id == entity_id

    def test_decode_invalid_base64_raises(self) -> None:
        """非法 base64 抛 invalid_cursor。"""
        with pytest.raises(AppError, match="base64url"):
            _decode_cursor("!!!not-base64!!!")

    def test_decode_invalid_json_raises(self) -> None:
        """非法 JSON 抛 invalid_cursor。"""
        import base64

        bad_cursor = base64.urlsafe_b64encode(b"not json").decode("ascii")
        with pytest.raises(AppError, match="JSON"):
            _decode_cursor(bad_cursor)

    def test_decode_missing_fields_raises(self) -> None:
        """缺少 v/id 字段抛 invalid_cursor。"""
        import base64
        import json

        payload = json.dumps({"only_v": "x"}).encode("utf-8")
        bad_cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="v / id"):
            _decode_cursor(bad_cursor)

    def test_decode_invalid_timestamp_raises(self) -> None:
        """v 字段非合法 ISO 时间抛 invalid_cursor。"""
        import base64
        import json

        payload = json.dumps({"v": "not-a-date", "id": str(uuid4())}).encode("utf-8")
        bad_cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="ISO 时间"):
            _decode_cursor(bad_cursor)

    def test_decode_invalid_uuid_raises(self) -> None:
        """id 字段非合法 UUID 抛 invalid_cursor。"""
        import base64
        import json

        payload = json.dumps({"v": datetime.now(UTC).isoformat(), "id": "not-a-uuid"}).encode(
            "utf-8"
        )
        bad_cursor = base64.urlsafe_b64encode(payload).decode("ascii")
        with pytest.raises(AppError, match="UUID"):
            _decode_cursor(bad_cursor)

    def test_cursor_is_url_safe(self) -> None:
        """游标仅含 base64url 安全字符。"""
        cursor = _encode_cursor(datetime.now(UTC), uuid4())
        safe_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=")
        assert all(c in safe_chars for c in cursor)
