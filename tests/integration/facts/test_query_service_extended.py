"""Integration tests for packages.facts.query_service — FactQueryService.

Uses the real test database. Tests list_facts_detail, search_facts_detail,
search_by_data, get_fact_detail, and get_fact_data with empty/minimal data.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.facts.query_service import FactQueryService


@pytest.fixture
def s3_repo() -> object:
    """Mock S3 repo — not used for read-only queries on empty data."""
    from unittest.mock import MagicMock

    return MagicMock()


@pytest.fixture
def dept_id() -> str:
    """Department ID as UUID string."""
    return str(uuid4())


@pytest.fixture
def actor_id() -> str:
    """Actor user ID as UUID string."""
    return str(uuid4())


@pytest.fixture
def query_service(
    async_session_factory: async_sessionmaker[AsyncSession],
    s3_repo: object,
    dept_id: str,
    actor_id: str,
) -> FactQueryService:
    """FactQueryService instance."""
    return FactQueryService(
        session_factory=async_session_factory,
        department_id=dept_id,  # type: ignore[arg-type]
        actor_id=actor_id,  # type: ignore[arg-type]
        s3_repo=s3_repo,
    )


class TestListFactsDetail:
    """Tests for FactQueryService.list_facts_detail."""

    @pytest.mark.integration
    async def test_empty_result(self, query_service: FactQueryService) -> None:
        rows, cursor, counts = await query_service.list_facts_detail()
        assert rows == []
        assert counts == {}

    @pytest.mark.integration
    async def test_with_filters_empty(self, query_service: FactQueryService) -> None:
        rows, cursor, counts = await query_service.list_facts_detail(
            filters={"fact_type": "measurement"},
            page_size=5,
        )
        assert rows == []


class TestSearchFactsDetail:
    """Tests for FactQueryService.search_facts_detail."""

    @pytest.mark.integration
    async def test_empty_search(self, query_service: FactQueryService) -> None:
        rows, cursor, counts = await query_service.search_facts_detail(query="nonexistent")
        assert rows == []
        assert counts == {}

    @pytest.mark.integration
    async def test_search_with_filters(self, query_service: FactQueryService) -> None:
        rows, cursor, counts = await query_service.search_facts_detail(
            query="test",
            filters={"status": "active"},
            page_size=10,
        )
        assert rows == []


class TestSearchByData:
    """Tests for FactQueryService.search_by_data."""

    @pytest.mark.integration
    async def test_empty_result(self, query_service: FactQueryService) -> None:
        rows, counts = await query_service.search_by_data(q="nonexistent")
        assert rows == []
        assert counts == {}

    @pytest.mark.integration
    async def test_search_by_key(self, query_service: FactQueryService) -> None:
        rows, counts = await query_service.search_by_data(key="temperature")
        assert rows == []
        assert counts == {}

    @pytest.mark.integration
    async def test_search_by_value_range(self, query_service: FactQueryService) -> None:
        rows, counts = await query_service.search_by_data(min_value=0.0, max_value=100.0)
        assert rows == []
        assert counts == {}


class TestGetFactDetail:
    """Tests for FactQueryService.get_fact_detail."""

    @pytest.mark.integration
    async def test_not_found_raises(self, query_service: FactQueryService) -> None:
        from packages.common.errors import AppError

        with pytest.raises(AppError, match="事实不存在"):
            await query_service.get_fact_detail(uuid4())


class TestGetFactData:
    """Tests for FactQueryService.get_fact_data."""

    @pytest.mark.integration
    async def test_not_found_raises(self, query_service: FactQueryService) -> None:
        from packages.common.errors import AppError

        with pytest.raises(AppError, match="事实不存在"):
            await query_service.get_fact_data(uuid4())


class TestFactQueryServiceProperties:
    """Tests for FactQueryService read-only properties."""

    @pytest.mark.integration
    def test_department_id_property(self, query_service: FactQueryService, dept_id: str) -> None:
        assert str(query_service.department_id) == dept_id

    @pytest.mark.integration
    def test_actor_id_property(self, query_service: FactQueryService, actor_id: str) -> None:
        assert str(query_service.actor_id) == actor_id

    @pytest.mark.integration
    def test_session_factory_property(self, query_service: FactQueryService) -> None:
        assert query_service.session_factory is not None


class TestInvalidateCache:
    """Tests for FactQueryService.invalidate_fact_data_cache (static method)."""

    @pytest.mark.integration
    def test_invalidate_no_redis(self) -> None:
        """Should not raise even if Redis is not available."""
        from packages.facts import query_service as qs_mod

        # Force no Redis
        original_redis = qs_mod._redis_client
        qs_mod._redis_client = None
        try:
            FactQueryService.invalidate_fact_data_cache(uuid4())
        finally:
            qs_mod._redis_client = original_redis
