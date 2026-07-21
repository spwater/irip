"""IRIP 时钟抽象。

所有需要"当前时间"的组件必须依赖注入 Clock，禁止直接调用
datetime.now()，从而保证：
- 生产环境使用 SystemClock（真实 UTC）；
- 测试环境使用 FixedClock（可重复、确定性）；
- 全平台时间语义统一为带 UTC 时区的 aware datetime。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """时钟协议：返回带 UTC 时区的当前时刻。"""

    def now(self) -> datetime:
        """返回当前 UTC 时刻（必须 timezone-aware）。"""
        ...


@dataclass(frozen=True)
class SystemClock:
    """生产时钟：真实系统 UTC 时间。"""

    def now(self) -> datetime:
        """返回当前 UTC 时刻。"""
        return datetime.now(UTC)


@dataclass(frozen=True)
class FixedClock:
    """测试时钟：固定于注入的瞬间，可重复。

    Attributes:
        instant: 固定时刻，必须 timezone-aware，否则 now() 抛 ValueError。
    """

    instant: datetime

    def now(self) -> datetime:
        """返回固定时刻（统一转换为 UTC）。

        Raises:
            ValueError: instant 为 naive datetime 时抛出。
        """
        if self.instant.tzinfo is None:
            raise ValueError("clock instant must be timezone-aware")
        return self.instant.astimezone(UTC)
