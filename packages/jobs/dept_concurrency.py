"""部门并发上限管理器：基于 Redis 的每部门并发任务计数。

确保单个部门不会占满全部 Worker 槽位，实现部门间资源隔离。
当某部门的并发任务数达到上限时，新任务被拒绝（留在队列等待下次调度）。

机制：
- acquire(dept_id): 原子 INCR + 比较，超限返回 False
- release(dept_id): 原子 DECR，释放槽位
- get_count(dept_id): 查询当前并发数

配合 OutboxDispatcher 使用：dispatcher 在投递前检查部门并发，
若超限则跳过该事件（不标记 delivered_at），下次调度自动重试。
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

#: 默认每部门最大并发任务数。
DEFAULT_MAX_CONCURRENT_PER_DEPT: int = int(os.getenv("IRIP_MAX_CONCURRENT_TASKS_PER_DEPT", "3"))

#: Redis key 前缀。
DEPT_CONCURRENCY_PREFIX: str = "irip:dept:concurrency:"


class DeptConcurrencyLimiter:
    """部门并发上限管理器。

    基于 Redis 原子操作实现每部门并发任务计数，
    确保单个部门不会占满全部 Worker 槽位。

    Attributes:
        _redis: Redis 客户端实例。
        _max_per_dept: 每部门最大并发任务数。
    """

    def __init__(
        self,
        redis_client: Any,
        max_per_dept: int = DEFAULT_MAX_CONCURRENT_PER_DEPT,
    ) -> None:
        """初始化部门并发上限管理器。

        Args:
            redis_client: Redis 客户端实例。
            max_per_dept: 每部门最大并发任务数（默认从环境变量读取）。
        """
        self._redis = redis_client
        self._max_per_dept = max_per_dept

    def _key(self, dept_id: str) -> str:
        """构建 Redis key。

        Args:
            dept_id: 部门 ID 字符串。

        Returns:
            str: Redis key。
        """
        return f"{DEPT_CONCURRENCY_PREFIX}{dept_id}"

    def acquire(self, dept_id: str) -> bool:
        """尝试获取部门并发槽位。

        原子 INCR 后比较：若超过上限则 DECR 回退并返回 False。

        Args:
            dept_id: 部门 ID 字符串。

        Returns:
            bool: 成功获取返回 True，超限返回 False。
        """
        key = self._key(dept_id)
        current = self._redis.incr(key)
        if current > self._max_per_dept:
            self._redis.decr(key)
            logger.info(
                "Dept %s concurrency limit reached (%d/%d), task will retry",
                dept_id,
                current - 1,
                self._max_per_dept,
            )
            return False
        # 设置 TTL 防止计数泄漏（Worker 崩溃时未 release）
        self._redis.expire(key, 7200)
        logger.debug(
            "Dept %s acquired slot (%d/%d)",
            dept_id,
            current,
            self._max_per_dept,
        )
        return True

    def release(self, dept_id: str) -> None:
        """释放部门并发槽位。

        原子 DECR，但不低于 0。

        Args:
            dept_id: 部门 ID 字符串。
        """
        key = self._key(dept_id)
        current = self._redis.decr(key)
        if current < 0:
            # 防止负数（release 多于 acquire 的情况）
            self._redis.set(key, 0)
            current = 0
        logger.debug(
            "Dept %s released slot (%d/%d)",
            dept_id,
            current,
            self._max_per_dept,
        )

    def get_count(self, dept_id: str) -> int:
        """查询部门当前并发任务数。

        Args:
            dept_id: 部门 ID 字符串。

        Returns:
            int: 当前并发任务数。
        """
        val = self._redis.get(self._key(dept_id))
        if val is None:
            return 0
        return int(val)
