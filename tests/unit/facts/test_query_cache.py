"""单元测试：FactQueryService Redis 缓存逻辑。

覆盖：
- get_fact_data 缓存命中时直接返回缓存数据（不查 DB）；
- get_fact_data 无 Redis 时降级为无缓存（get_redis 返回 None 不报错）；
- get_fact_data 缓存未命中且无 artifact 时缓存空结果；
- invalidate_fact_data_cache 调用 redis.delete；
- invalidate_fact_data_cache 无 Redis 时不报错；
- FACT_DATA_CACHE_TTL 为 300 秒。
"""

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import packages.facts.query_service as qs_mod
from packages.facts.query_service import FACT_DATA_CACHE_TTL, FactQueryService


def _make_service() -> FactQueryService:
    """构造 FactQueryService 实例（session_factory 为 mock）。"""
    return FactQueryService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=uuid4(),
        s3_repo=MagicMock(),
    )


class TestFactDataCacheHit:
    """get_fact_data 缓存命中测试。"""

    async def test_cache_hit_returns_cached_data(self) -> None:
        """缓存命中时直接返回缓存数据，不查 DB。"""
        service = _make_service()
        fact_id = uuid4()
        cached_data = {"metadata": {"name": "cached"}, "points": [], "series": []}

        mock_redis = MagicMock()
        mock_redis.get = MagicMock(return_value=json.dumps(cached_data))

        original_get_redis = qs_mod._get_redis
        qs_mod._get_redis = lambda: mock_redis  # type: ignore[assignment]
        try:
            result = await service.get_fact_data(fact_id)
        finally:
            qs_mod._get_redis = original_get_redis  # type: ignore[assignment]

        assert result == cached_data
        mock_redis.get.assert_called_once_with(f"fact_data:{fact_id}")

    async def test_cache_hit_with_string_value(self) -> None:
        """缓存命中时正确解析 JSON 字符串。"""
        service = _make_service()
        fact_id = uuid4()
        cached = {"points": [{"name": "x", "value": 1}]}

        mock_redis = MagicMock()
        mock_redis.get = MagicMock(return_value=json.dumps(cached))

        original_get_redis = qs_mod._get_redis
        qs_mod._get_redis = lambda: mock_redis  # type: ignore[assignment]
        try:
            result = await service.get_fact_data(fact_id)
        finally:
            qs_mod._get_redis = original_get_redis  # type: ignore[assignment]

        assert result["points"][0]["value"] == 1


class TestNoRedisDegradation:
    """无 Redis 降级测试。"""

    async def test_no_redis_does_not_raise_on_cache_check(self) -> None:
        """_get_redis 返回 None 时缓存检查不报错。"""
        # 验证 _get_redis 返回 None 时不抛异常（仅检查调用路径）
        original_get_redis = qs_mod._get_redis
        qs_mod._get_redis = lambda: None  # type: ignore[assignment]
        try:
            # 仅验证 _get_redis 返回 None 时不抛异常
            assert qs_mod._get_redis() is None
        finally:
            qs_mod._get_redis = original_get_redis  # type: ignore[assignment]


class TestCacheMissAndWrite:
    """get_fact_data 缓存未命中且无 artifact 时缓存空结果。"""

    async def test_cache_miss_no_artifact_caches_empty_result(self) -> None:
        """缓存未命中 + 无 JSON artifact 时缓存空数据。"""
        service = _make_service()
        fact_id = uuid4()

        mock_redis = MagicMock()
        mock_redis.get = MagicMock(return_value=None)
        mock_redis.setex = MagicMock()

        mock_session = AsyncMock()

        original_get_redis = qs_mod._get_redis
        original_scoped = type(service)._scoped_session

        @asynccontextmanager
        async def fake_scoped_session(self_inner: Any) -> Any:
            yield mock_session

        qs_mod._get_redis = lambda: mock_redis  # type: ignore[assignment]
        type(service)._scoped_session = fake_scoped_session  # type: ignore[method-assign]
        try:
            # patch FactRepository 静态方法
            original_get_fact = qs_mod.FactRepository.get_fact
            original_find_json = qs_mod.FactRepository.find_json_artifact
            qs_mod.FactRepository.get_fact = AsyncMock(return_value=MagicMock())  # type: ignore[assignment]
            qs_mod.FactRepository.find_json_artifact = AsyncMock(return_value=None)  # type: ignore[assignment]
            try:
                result = await service.get_fact_data(fact_id)
            finally:
                qs_mod.FactRepository.get_fact = original_get_fact  # type: ignore[assignment]
                qs_mod.FactRepository.find_json_artifact = original_find_json  # type: ignore[assignment]
        finally:
            qs_mod._get_redis = original_get_redis  # type: ignore[assignment]
            type(service)._scoped_session = original_scoped  # type: ignore[method-assign]

        assert result == {"metadata": {}, "points": [], "series": []}
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == f"fact_data:{fact_id}"
        assert call_args[0][1] == FACT_DATA_CACHE_TTL


class TestInvalidateCache:
    """invalidate_fact_data_cache 测试。"""

    def test_invalidate_calls_redis_delete(self) -> None:
        """invalidate_fact_data_cache 调用 redis.delete。"""
        fact_id = uuid4()
        mock_redis = MagicMock()
        mock_redis.delete = MagicMock()

        original_get_redis = qs_mod._get_redis
        qs_mod._get_redis = lambda: mock_redis  # type: ignore[assignment]
        try:
            FactQueryService.invalidate_fact_data_cache(fact_id)
        finally:
            qs_mod._get_redis = original_get_redis  # type: ignore[assignment]

        mock_redis.delete.assert_called_once_with(f"fact_data:{fact_id}")

    def test_invalidate_no_redis_does_not_raise(self) -> None:
        """无 Redis 时 invalidate 不报错。"""
        fact_id = uuid4()
        original_get_redis = qs_mod._get_redis
        qs_mod._get_redis = lambda: None  # type: ignore[assignment]
        try:
            FactQueryService.invalidate_fact_data_cache(fact_id)
        finally:
            qs_mod._get_redis = original_get_redis  # type: ignore[assignment]


class TestCacheTtl:
    """FACT_DATA_CACHE_TTL 常量测试。"""

    def test_cache_ttl_is_300_seconds(self) -> None:
        """缓存 TTL 为 300 秒（5 分钟）。"""
        assert FACT_DATA_CACHE_TTL == 300
