"""H-07 内存滑动窗口限流器单元测试。

覆盖 ``packages/common/rate_limiter.py``：
- IP 维度限流（超阈值返回 False）；
- 账号维度限流；
- 滑动窗口过期后恢复；
- 不同 IP/账号独立计数；
- get_count 辅助方法；
- reset 重置方法。

本测试为纯单元测试，不依赖数据库或外部服务。
通过 mock ``time.monotonic`` 实现确定性时间控制。
"""

from unittest.mock import patch

import pytest

from packages.common.rate_limiter import RateLimiter, get_rate_limiter


class TestRateLimiterBasic:
    """限流器基础行为。"""

    def test_allow_under_limit(self) -> None:
        """未达阈值时全部允许。"""
        limiter = RateLimiter()
        for _ in range(5):
            assert limiter.allow("key", limit=5, window=60) is True

    def test_allow_at_limit_returns_false(self) -> None:
        """达到阈值后拒绝。"""
        limiter = RateLimiter()
        for _ in range(3):
            assert limiter.allow("key", limit=3, window=60) is True
        # 第 4 次应被拒绝
        assert limiter.allow("key", limit=3, window=60) is False

    def test_allow_limit_one(self) -> None:
        """limit=1 时第二次请求即被拒绝。"""
        limiter = RateLimiter()
        assert limiter.allow("key", limit=1, window=60) is True
        assert limiter.allow("key", limit=1, window=60) is False


class TestIPDimensionRateLimit:
    """IP 维度限流。"""

    def test_ip_rate_limit_blocks_after_threshold(self) -> None:
        """同一 IP 超过阈值后返回 False。"""
        limiter = RateLimiter()
        ip_key = "login:ip:192.168.1.100"
        # 阈值 3，前 3 次允许
        for i in range(3):
            assert limiter.allow(ip_key, limit=3, window=60) is True, f"第{i + 1}次应允许"
        # 第 4 次被限流
        assert limiter.allow(ip_key, limit=3, window=60) is False

    def test_different_ips_independent(self) -> None:
        """不同 IP 独立计数。"""
        limiter = RateLimiter()
        key_a = "login:ip:10.0.0.1"
        key_b = "login:ip:10.0.0.2"
        # 耗尽 key_a 的配额
        for _ in range(3):
            limiter.allow(key_a, limit=3, window=60)
        assert limiter.allow(key_a, limit=3, window=60) is False
        # key_b 仍可正常使用
        assert limiter.allow(key_b, limit=3, window=60) is True


class TestAccountDimensionRateLimit:
    """账号维度限流。"""

    def test_account_rate_limit_blocks_after_threshold(self) -> None:
        """同一账号超过阈值后返回 False。"""
        limiter = RateLimiter()
        email_key = "login:email:user@example.com"
        for i in range(5):
            assert limiter.allow(email_key, limit=5, window=60) is True, f"第{i + 1}次应允许"
        assert limiter.allow(email_key, limit=5, window=60) is False

    def test_different_accounts_independent(self) -> None:
        """不同账号独立计数。"""
        limiter = RateLimiter()
        key_a = "login:email:alice@example.com"
        key_b = "login:email:bob@example.com"
        # 耗尽 key_a
        for _ in range(2):
            limiter.allow(key_a, limit=2, window=60)
        assert limiter.allow(key_a, limit=2, window=60) is False
        # key_b 不受影响
        assert limiter.allow(key_b, limit=2, window=60) is True

    def test_ip_and_account_dimensions_independent(self) -> None:
        """IP 维与账号维独立计数（双维限流场景）。"""
        limiter = RateLimiter()
        ip_key = "login:ip:192.168.1.1"
        email_key = "login:email:user@example.com"
        # 耗尽 IP 维配额
        for _ in range(3):
            limiter.allow(ip_key, limit=3, window=60)
        assert limiter.allow(ip_key, limit=3, window=60) is False
        # 账号维不受 IP 维影响
        assert limiter.allow(email_key, limit=5, window=60) is True


class TestSlidingWindow:
    """滑动窗口过期恢复。"""

    def test_window_expiry_recovers(self) -> None:
        """窗口过期后限流恢复（mock time）。"""
        limiter = RateLimiter()
        key = "login:ip:1.2.3.4"
        # t=0: 耗尽配额
        with patch("packages.common.rate_limiter.time.monotonic", return_value=100.0):
            for _ in range(3):
                assert limiter.allow(key, limit=3, window=60) is True
            assert limiter.allow(key, limit=3, window=60) is False

        # t=59: 仍在窗口内，仍被限流
        with patch("packages.common.rate_limiter.time.monotonic", return_value=159.0):
            assert limiter.allow(key, limit=3, window=60) is False

        # t=61: 窗口已过期，恢复允许
        with patch("packages.common.rate_limiter.time.monotonic", return_value=161.0):
            assert limiter.allow(key, limit=3, window=60) is True

    def test_partial_window_expiry(self) -> None:
        """部分时间戳过期后窗口内剩余计数减少。"""
        limiter = RateLimiter()
        key = "test:partial"
        # t=0: 3 次请求
        with patch("packages.common.rate_limiter.time.monotonic", return_value=0.0):
            for _ in range(3):
                limiter.allow(key, limit=5, window=100)
        # t=50: 2 次请求（共 5 次，耗尽配额）
        with patch("packages.common.rate_limiter.time.monotonic", return_value=50.0):
            for _ in range(2):
                assert limiter.allow(key, limit=5, window=100) is True
            assert limiter.allow(key, limit=5, window=100) is False
        # t=101: t=0 的 3 次已过期，窗口内仅剩 t=50 的 2 次，可再 3 次
        with patch("packages.common.rate_limiter.time.monotonic", return_value=101.0):
            assert limiter.allow(key, limit=5, window=100) is True


class TestGetCount:
    """get_count 辅助方法。"""

    def test_get_count_within_window(self) -> None:
        """获取窗口内请求计数。"""
        limiter = RateLimiter()
        key = "count:test"
        with patch("packages.common.rate_limiter.time.monotonic", return_value=0.0):
            limiter.allow(key, limit=10, window=60)
            limiter.allow(key, limit=10, window=60)
            limiter.allow(key, limit=10, window=60)
        with patch("packages.common.rate_limiter.time.monotonic", return_value=10.0):
            assert limiter.get_count(key, window=60) == 3

    def test_get_count_excludes_expired(self) -> None:
        """过期时间戳不计入。"""
        limiter = RateLimiter()
        key = "count:expired"
        with patch("packages.common.rate_limiter.time.monotonic", return_value=0.0):
            limiter.allow(key, limit=10, window=60)
        with patch("packages.common.rate_limiter.time.monotonic", return_value=61.0):
            assert limiter.get_count(key, window=60) == 0

    def test_get_count_unknown_key(self) -> None:
        """未知 key 计数为 0。"""
        limiter = RateLimiter()
        with patch("packages.common.rate_limiter.time.monotonic", return_value=0.0):
            assert limiter.get_count("nonexistent", window=60) == 0


class TestReset:
    """reset 重置方法。"""

    def test_reset_single_key(self) -> None:
        """重置单个 key 后恢复允许。"""
        limiter = RateLimiter()
        key = "reset:single"
        for _ in range(3):
            limiter.allow(key, limit=3, window=60)
        assert limiter.allow(key, limit=3, window=60) is False
        limiter.reset(key)
        assert limiter.allow(key, limit=3, window=60) is True

    def test_reset_all(self) -> None:
        """重置全部 key。"""
        limiter = RateLimiter()
        key_a = "reset:a"
        key_b = "reset:b"
        for _ in range(2):
            limiter.allow(key_a, limit=2, window=60)
            limiter.allow(key_b, limit=2, window=60)
        assert limiter.allow(key_a, limit=2, window=60) is False
        assert limiter.allow(key_b, limit=2, window=60) is False
        limiter.reset()
        assert limiter.allow(key_a, limit=2, window=60) is True
        assert limiter.allow(key_b, limit=2, window=60) is True

    def test_reset_nonexistent_key_silent(self) -> None:
        """重置不存在的 key 不报错。"""
        limiter = RateLimiter()
        limiter.reset("does-not-exist")  # 不应抛异常


class TestGlobalLimiter:
    """全局单例。"""

    def test_get_rate_limiter_returns_same_instance(self) -> None:
        """get_rate_limiter 返回同一实例。"""
        a = get_rate_limiter()
        b = get_rate_limiter()
        assert a is b
