"""Tests for TimelineQueryService: cursor pagination and page size validation."""

from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.research.timeline.repository import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    decode_cursor,
    encode_cursor,
    validate_page_size,
)


class TestTimelineQueryServiceExists:
    """Verify the service class exists and has expected methods."""

    def test_import_service(self) -> None:
        from packages.research.timeline.timeline_query_service import (
            TimelineQueryService,
        )

        assert TimelineQueryService is not None

    def test_has_list_timeline_method(self) -> None:
        from packages.research.timeline.timeline_query_service import (
            TimelineQueryService,
        )

        assert hasattr(TimelineQueryService, "list_timeline")

    def test_has_get_turn_detail_method(self) -> None:
        from packages.research.timeline.timeline_query_service import (
            TimelineQueryService,
        )

        assert hasattr(TimelineQueryService, "get_turn_detail")

    def test_constructor_takes_session_factory(self) -> None:
        import inspect

        from packages.research.timeline.timeline_query_service import (
            TimelineQueryService,
        )

        sig = inspect.signature(TimelineQueryService.__init__)
        assert "session_factory" in sig.parameters


class TestTimelinePaginationDefaults:
    """Verify pagination defaults and limits."""

    def test_default_page_size_is_20(self) -> None:
        assert DEFAULT_PAGE_SIZE == 20

    def test_max_page_size_is_50(self) -> None:
        assert MAX_PAGE_SIZE == 50

    def test_page_size_51_rejected(self) -> None:
        with pytest.raises(AppError):
            validate_page_size(51)

    def test_page_size_0_rejected(self) -> None:
        with pytest.raises(AppError):
            validate_page_size(0)

    def test_page_size_50_accepted(self) -> None:
        assert validate_page_size(50) == 50

    def test_page_size_1_accepted(self) -> None:
        assert validate_page_size(1) == 1


class TestCursorRoundtrip:
    """Verify cursor encoding/decoding works correctly for timeline pagination."""

    def test_encode_decode_roundtrip(self) -> None:
        turn_number = 42
        turn_id = uuid4()
        cursor = encode_cursor(turn_number, turn_id)
        decoded_n, decoded_id = decode_cursor(cursor)
        assert decoded_n == turn_number
        assert decoded_id == turn_id

    def test_malformed_cursor_raises(self) -> None:
        with pytest.raises(AppError, match="Invalid cursor"):
            decode_cursor("invalid")
