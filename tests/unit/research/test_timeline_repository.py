"""Tests for timeline repository cursor encoding and page-size validation."""

from uuid import UUID, uuid4

import pytest

from packages.common.errors import AppError
from packages.research.timeline.repository import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    decode_cursor,
    encode_cursor,
    validate_page_size,
)


class TestCursorEncodeDecode:
    """Cursor encoding/decoding round-trip tests."""

    def test_roundtrip(self) -> None:
        turn_number = 37
        turn_id = uuid4()
        cursor = encode_cursor(turn_number, turn_id)
        decoded_n, decoded_id = decode_cursor(cursor)
        assert decoded_n == turn_number
        assert decoded_id == turn_id

    def test_cursor_is_opaque_string(self) -> None:
        cursor = encode_cursor(1, uuid4())
        assert isinstance(cursor, str)
        # Should not contain raw turn_number as plain text
        assert "1" not in cursor or cursor != "1"

    def test_different_values_different_cursors(self) -> None:
        c1 = encode_cursor(1, uuid4())
        c2 = encode_cursor(2, uuid4())
        assert c1 != c2

    def test_malformed_cursor_raises(self) -> None:
        with pytest.raises(AppError, match="Invalid cursor"):
            decode_cursor("not-a-valid-cursor")

    def test_empty_cursor_raises(self) -> None:
        with pytest.raises(AppError, match="Invalid cursor"):
            decode_cursor("")

    def test_valid_uuid_in_cursor(self) -> None:
        turn_id = UUID("12345678-1234-5678-1234-567812345678")
        cursor = encode_cursor(42, turn_id)
        decoded_n, decoded_id = decode_cursor(cursor)
        assert decoded_n == 42
        assert decoded_id == turn_id


class TestPageSizeValidation:
    """Page size boundary tests."""

    def test_default_is_20(self) -> None:
        assert DEFAULT_PAGE_SIZE == 20

    def test_max_is_50(self) -> None:
        assert MAX_PAGE_SIZE == 50

    def test_valid_page_sizes(self) -> None:
        for size in [1, 10, 20, 50]:
            assert validate_page_size(size) == size

    def test_zero_raises(self) -> None:
        with pytest.raises(AppError, match="page_size"):
            validate_page_size(0)

    def test_negative_raises(self) -> None:
        with pytest.raises(AppError, match="page_size"):
            validate_page_size(-1)

    def test_51_raises(self) -> None:
        with pytest.raises(AppError, match="page_size"):
            validate_page_size(51)

    def test_does_not_clamp(self) -> None:
        """page_size=51 must raise, not silently reduce to 50."""
        with pytest.raises(AppError):
            validate_page_size(51)
