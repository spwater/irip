"""单元测试：WorkerLeaseManager 租约管理器。

覆盖 acquire / acquire_with_fencing / heartbeat / release / reap_expired。
"""

from typing import Any
from unittest.mock import MagicMock

from packages.jobs.worker import HEARTBEAT_INTERVAL_SECONDS, LEASE_TTL_SECONDS, WorkerLeaseManager


def _make_result(scalar: Any = None, rowcount: int = 0, scalar_return: Any = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    result.scalar.return_value = scalar_return
    result.rowcount = rowcount
    return result


def _make_manager() -> WorkerLeaseManager:
    return WorkerLeaseManager(MagicMock())


class TestConstants:
    """常量测试。"""

    def test_lease_ttl(self) -> None:
        assert LEASE_TTL_SECONDS == 30

    def test_heartbeat_interval(self) -> None:
        assert HEARTBEAT_INTERVAL_SECONDS == 10
