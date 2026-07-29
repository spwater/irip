"""通用内核（packages.common）单元测试。

覆盖实施计划 Task 2 Step 1 的契约测试，并补充：
- SystemClock 时区语义
- FixedClock naive instant 拒绝
- AppError.to_dict() 精确键集
- PageCursor 编码往返 + 畸形游标拒绝
- new_id 唯一性

注意：sha256(b"irip") 的真实摘要为
a5ebfab3d0dcea62678ab31148b1a308155b1d200426a832f5d22206459e1d54
（实施计划 L187 中写的 c8728c47... 与实际不符，已按真实值修正）。
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from packages.common.clock import FixedClock, SystemClock
from packages.common.errors import AppError
from packages.common.hashing import sha256_bytes
from packages.common.ids import new_id
from packages.common.pagination import PageCursor

# ---- 计划 Task 2 Step 1 的契约测试（sha256 期望值已按真实值修正）----


def test_fixed_clock_and_error_contract() -> None:
    instant = datetime(2026, 7, 15, tzinfo=UTC)
    assert FixedClock(instant).now() == instant
    error = AppError(code="conflict", message="版本冲突", retryable=False, fields={})
    assert error.to_dict()["code"] == "conflict"
    assert (
        sha256_bytes(b"irip") == "a5ebfab3d0dcea62678ab31148b1a308155b1d200426a832f5d22206459e1d54"
    )


# ---- 时钟 ----


def test_system_clock_returns_utc_aware() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == datetime.now(UTC).utcoffset()


def test_fixed_clock_converts_to_utc() -> None:
    # UTC+8 的 16:00 == UTC 08:00
    from datetime import timedelta, timezone

    tz_cn = timezone(timedelta(hours=8))
    instant = datetime(2026, 7, 15, 16, 0, 0, tzinfo=tz_cn)
    assert FixedClock(instant).now() == datetime(2026, 7, 15, 8, 0, 0, tzinfo=UTC)


def test_fixed_clock_rejects_naive_instant() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 7, 15)).now()


# ---- 错误契约 ----


def test_app_error_to_dict_exact_keys() -> None:
    error = AppError(
        code="validation_failed",
        message="参数校验失败",
        retryable=False,
        fields={"email": "格式错误"},
    )
    payload = error.to_dict()
    assert set(payload.keys()) == {"code", "message", "retryable", "fields"}
    assert payload["fields"] == {"email": "格式错误"}


def test_app_error_defaults() -> None:
    error = AppError(code="internal_error", message="内部错误")
    assert error.retryable is False
    assert error.fields == {}
    assert str(error) == "内部错误"


# ---- 哈希 ----


def test_sha256_bytes_empty_input() -> None:
    assert sha256_bytes(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ---- ID 生成 ----


def test_new_id_returns_unique_uuid4() -> None:
    ids = {new_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(isinstance(i, UUID) for i in ids)
    assert all(i.version == 4 for i in ids)


# ---- 分页游标 ----


def test_page_cursor_roundtrip() -> None:
    cursor = PageCursor(sort_value="2026-07-15T08:30:00Z", id=new_id())
    decoded = PageCursor.decode(cursor.encode())
    assert decoded == cursor


def test_page_cursor_encode_is_urlsafe_base64() -> None:
    encoded = PageCursor(sort_value=1, id=new_id()).encode()
    # base64url 字符集不含 + / 空格
    assert all(c.isalnum() or c in "-_=" for c in encoded)


def test_page_cursor_rejects_garbage() -> None:
    with pytest.raises(AppError) as exc_info:
        PageCursor.decode("!!!not-base64!!!")
    assert exc_info.value.code == "invalid_cursor"


def test_page_cursor_rejects_non_json() -> None:
    import base64

    raw = base64.urlsafe_b64encode(b"not json").decode("ascii")
    with pytest.raises(AppError) as exc_info:
        PageCursor.decode(raw)
    assert exc_info.value.code == "invalid_cursor"


def test_page_cursor_rejects_missing_fields() -> None:
    import base64
    import json

    raw = base64.urlsafe_b64encode(json.dumps({"v": 1}).encode()).decode()
    with pytest.raises(AppError) as exc_info:
        PageCursor.decode(raw)
    assert exc_info.value.code == "invalid_cursor"


def test_page_cursor_rejects_bad_uuid() -> None:
    import base64
    import json

    raw = base64.urlsafe_b64encode(json.dumps({"v": 1, "id": "not-a-uuid"}).encode()).decode()
    with pytest.raises(AppError) as exc_info:
        PageCursor.decode(raw)
    assert exc_info.value.code == "invalid_cursor"
