"""Redis 分布式速率限制器（H-07: IP+账号双维限流）。

使用 Redis 滑动窗口算法实现多进程共享的速率限制。
在单进程和多进程部署中均提供精确的限流计数。

两种实现：
- RedisRateLimiter: 基于 Redis 的分布式限流（生产环境推荐）
- RateLimiter: 内存限流（单进程回退，用于测试/开发）

用法::

    limiter = get_rate_limiter()
    if not limiter.allow("login:ip:192.168.1.1", limit=20, window=60):
        raise AppError(code="rate_limited", message="请求过于频繁")
    if not limiter.allow("login:email:user@example.com", limit=5, window=60):
        raise AppError(code="rate_limited", message="账号登录尝试过多")
"""

import threading
import time
from collections import defaultdict
from typing import Any

from packages.common.redis_url import get_redis_url


class RedisRateLimiter:
    """Redis 滑动窗口速率限制器。

    使用 Redis Sorted Set 实现滑动窗口：
    - 每个 key 对应一个 Sorted Set
    - 成员为请求时间戳（纳秒），分数也为时间戳
    - 清除窗口外的过期成员后检查计数

    多进程共享：所有 Worker/API 进程通过同一个 Redis 实例计数。

    Attributes:
        _redis: Redis 客户端实例。
    """

    def __init__(self, redis_client: Any) -> None:
        """初始化 Redis 速率限制器。

        Args:
            redis_client: Redis 客户端实例。
        """
        self._redis = redis_client

    def allow(self, key: str, limit: int, window: int) -> bool:
        """检查是否允许请求通过。

        Args:
            key: 限流键（如 "login:ip:192.168.1.1"）。
            limit: 窗口内允许的最大请求数。
            window: 时间窗口（秒）。

        Returns:
            bool: True 表示允许，False 表示被限流。
        """
        import time as _time

        now: float = _time.time()
        cutoff: float = now - float(window)
        redis_key: str = f"ratelimit:{key}"

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(redis_key, 0, cutoff)
        pipe.zadd(redis_key, {str(now): now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, window + 10)
        results = pipe.execute()

        count: int = results[2]
        return count <= limit

    def reset(self, key: str | None = None) -> None:
        """重置限流计数器。

        Args:
            key: 指定 key 时只重置该 key；None 时重置全部。
        """
        if key is None:
            # 重置所有 ratelimit: 开头的 key
            for k in self._redis.scan_iter(match="ratelimit:*"):
                self._redis.delete(k)
        else:
            self._redis.delete(f"ratelimit:{key}")

    def get_count(self, key: str, window: int) -> int:
        """获取当前窗口内的请求计数。

        Args:
            key: 限流键。
            window: 时间窗口（秒）。

        Returns:
            int: 当前窗口内的请求数。
        """
        now: float = time.time()
        cutoff: float = now - float(window)
        return int(self._redis.zcount(f"ratelimit:{key}", cutoff, now))


class RateLimiter:
    """简单内存滑动窗口速率限制器（单进程回退）。

    使用滑动窗口算法：记录每个 key 的最近请求时间戳列表，
    清除窗口外的过期记录，检查当前窗口内的请求数是否超过限制。

    线程安全：使用 threading.Lock 保护内部状态。
    单进程内有效：多进程部署时每个进程有独立的计数器（保守限制）。

    Attributes:
        _buckets: key -> list of timestamps（最近请求时间戳）。
        _lock: 线程锁。
    """

    def __init__(self) -> None:
        """初始化速率限制器。"""
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock: threading.Lock = threading.Lock()

    def allow(self, key: str, limit: int, window: int) -> bool:
        """检查是否允许请求通过。

        如果当前滑动窗口内的请求数小于 limit，则记录此次请求并返回 True；
        否则返回 False（被限流）。

        Args:
            key: 限流键（如 "login:ip:192.168.1.1"）。
            limit: 窗口内允许的最大请求数。
            window: 时间窗口（秒）。

        Returns:
            bool: True 表示允许，False 表示被限流。
        """
        now: float = time.monotonic()
        cutoff: float = now - float(window)

        with self._lock:
            # 清除过期的时间戳
            timestamps: list[float] = self._buckets[key]
            # 使用列表推导过滤过期记录（保留窗口内的）
            timestamps[:] = [ts for ts in timestamps if ts > cutoff]

            if len(timestamps) >= limit:
                return False

            timestamps.append(now)
            return True

    def reset(self, key: str | None = None) -> None:
        """重置限流计数器。

        Args:
            key: 指定 key 时只重置该 key；None 时重置全部。
        """
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

    def get_count(self, key: str, window: int) -> int:
        """获取当前窗口内的请求计数（用于调试/监控）。

        Args:
            key: 限流键。
            window: 时间窗口（秒）。

        Returns:
            int: 当前窗口内的请求数。
        """
        now: float = time.monotonic()
        cutoff: float = now - float(window)

        with self._lock:
            timestamps: list[float] = self._buckets[key]
            return sum(1 for ts in timestamps if ts > cutoff)


#: 全局单例（惰性初始化：优先 Redis，回退内存）。
_global_limiter: RateLimiter | RedisRateLimiter | None = None


def get_rate_limiter() -> RateLimiter | RedisRateLimiter:
    """获取全局速率限制器单例。

    优先使用 Redis 分布式限流（多进程共享计数）。
    Redis 不可用时回退到内存限流（单进程保守限制）。

    Returns:
        RateLimiter | RedisRateLimiter: 全局速率限制器实例。
    """
    global _global_limiter
    if _global_limiter is not None:
        return _global_limiter

    redis_url: str = get_redis_url("")
    if redis_url:
        try:
            import redis as redis_lib

            client = redis_lib.from_url(redis_url)  # type: ignore[no-untyped-call]
            client.ping()
            _global_limiter = RedisRateLimiter(client)
            return _global_limiter
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Redis unavailable, falling back to in-memory rate limiter"
            )

    _global_limiter = RateLimiter()
    return _global_limiter
