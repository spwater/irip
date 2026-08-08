"""单元测试：DeptConcurrencyLimiter 部门并发上限管理器。

覆盖：
- _key：Redis key 构建；
- acquire：未超限成功 / 超限回退失败 / 设置 TTL；
- release：正常释放 / 防止负数（set 0）；
- get_count：有值 / 无值（None）返回 0；
- DEFAULT_MAX_CONCURRENT_PER_DEPT 常量。

使用 mock Redis 客户端。
"""

from unittest.mock import MagicMock

from packages.jobs.dept_concurrency import (
    DEFAULT_MAX_CONCURRENT_PER_DEPT,
    DEPT_CONCURRENCY_PREFIX,
    DeptConcurrencyLimiter,
)


def _make_redis() -> MagicMock:
    """构造 mock Redis 客户端。"""
    redis = MagicMock()
    redis.incr.return_value = 1
    redis.decr.return_value = 0
    redis.get.return_value = None
    redis.expire = MagicMock()
    redis.set = MagicMock()
    return redis


class TestKey:
    """_key 测试。"""

    def test_key_format(self) -> None:
        """Redis key 格式正确。"""
        limiter = DeptConcurrencyLimiter(_make_redis())
        key = limiter._key("dept-123")
        assert key == f"{DEPT_CONCURRENCY_PREFIX}dept-123"


class TestAcquire:
    """acquire 测试。"""

    def test_acquire_success(self) -> None:
        """未超限时成功获取。"""
        redis = _make_redis()
        redis.incr.return_value = 1
        limiter = DeptConcurrencyLimiter(redis, max_per_dept=3)

        assert limiter.acquire("dept-1") is True
        redis.incr.assert_called_once()
        redis.expire.assert_called_once()

    def test_acquire_at_limit_success(self) -> None:
        """刚好达到上限时成功获取。"""
        redis = _make_redis()
        redis.incr.return_value = 3  # == max_per_dept
        limiter = DeptConcurrencyLimiter(redis, max_per_dept=3)

        assert limiter.acquire("dept-1") is True

    def test_acquire_exceeds_limit_fail(self) -> None:
        """超过上限时回退并返回 False。"""
        redis = _make_redis()
        redis.incr.return_value = 4  # > max_per_dept=3
        limiter = DeptConcurrencyLimiter(redis, max_per_dept=3)

        assert limiter.acquire("dept-1") is False
        redis.decr.assert_called_once()
        redis.expire.assert_not_called()

    def test_acquire_sets_ttl(self) -> None:
        """成功获取时设置 TTL。"""
        redis = _make_redis()
        redis.incr.return_value = 1
        limiter = DeptConcurrencyLimiter(redis, max_per_dept=3)

        limiter.acquire("dept-1")
        redis.expire.assert_called_once()
        args = redis.expire.call_args
        assert args[0][1] == 7200


class TestRelease:
    """release 测试。"""

    def test_release_normal(self) -> None:
        """正常释放。"""
        redis = _make_redis()
        redis.decr.return_value = 1
        limiter = DeptConcurrencyLimiter(redis)

        limiter.release("dept-1")
        redis.decr.assert_called_once()

    def test_release_prevents_negative(self) -> None:
        """DECR 结果为负数时 set 0。"""
        redis = _make_redis()
        redis.decr.return_value = -1
        limiter = DeptConcurrencyLimiter(redis)

        limiter.release("dept-1")
        redis.set.assert_called_once()
        args = redis.set.call_args
        assert args[0][1] == 0


class TestGetCount:
    """get_count 测试。"""

    def test_returns_value(self) -> None:
        """有值时返回计数。"""
        redis = _make_redis()
        redis.get.return_value = b"5"
        limiter = DeptConcurrencyLimiter(redis)

        assert limiter.get_count("dept-1") == 5

    def test_returns_zero_when_none(self) -> None:
        """无值时返回 0。"""
        redis = _make_redis()
        redis.get.return_value = None
        limiter = DeptConcurrencyLimiter(redis)

        assert limiter.get_count("dept-1") == 0

    def test_returns_int_from_string(self) -> None:
        """字符串值转为 int。"""
        redis = _make_redis()
        redis.get.return_value = "3"
        limiter = DeptConcurrencyLimiter(redis)

        assert limiter.get_count("dept-1") == 3


class TestConstants:
    """常量测试。"""

    def test_prefix(self) -> None:
        """Redis key 前缀正确。"""
        assert DEPT_CONCURRENCY_PREFIX == "irip:dept:concurrency:"

    def test_default_max_concurrent(self) -> None:
        """默认并发上限为 3（或环境变量值）。"""
        assert DEFAULT_MAX_CONCURRENT_PER_DEPT >= 1
