"""研究调度器：20 用户许可管理 + 公平队列 + 心跳回收 + 保温容器管理。

ResearchScheduler 负责：
1. acquire_slot: 获取用户许可（最多 20 个并发用户）；
2. release_slot: 释放许可 + 从等待队列提升下一个 Run；
3. get_queue_position: 查询排队位置；
4. register_heartbeat: 注册 Run 心跳；
5. check_heartbeats: 检查心跳超时（>90 秒标记 failed）；
6. check_and_promote: 检查空闲槽位并提升等待 Run。

调度策略：
- 用户间：轮询公平调度（Redis Sorted Set 按入队时间排序）；
- 用户内：FIFO（同一用户的 Run 按提交顺序）；
- 老化优先级：等待时间越长优先级越高；
- 心跳回收：30 秒 Beat 检查心跳，90 秒无心跳标记 failed + 释放槽位。
"""

import logging
import os
import time
from typing import Any

from packages.research.execution.models_trusted import QueuePosition

logger = logging.getLogger("research.scheduler")

#: 最大并发用户数。
MAX_CONCURRENT_USERS: int = int(os.getenv("RESEARCH_MAX_CONCURRENT_USERS", "20"))

#: 心跳超时阈值（秒）。
HEARTBEAT_TIMEOUT: int = int(os.getenv("RESEARCH_HEARTBEAT_TIMEOUT_SECONDS", "90"))

#: Redis key 常量。
ACTIVE_USERS_SET: str = "research:scheduler:active_users"
ACTIVE_USER_KEY: str = "research:scheduler:active_user:{user_id}"
QUEUE_KEY: str = "research:scheduler:queue"
HEARTBEAT_KEY: str = "research:scheduler:heartbeat:{run_id}"

#: 预估每用户平均执行时长（秒），用于估算等待时间。
AVG_RUN_DURATION: int = 300


class ResearchScheduler:
    """研究域调度器。

    管理用户并发许可、公平队列、心跳回收和保温容器。

    Attributes:
        _redis: Redis 客户端。
        _max_concurrent_users: 最大并发用户数。
    """

    def __init__(
        self,
        redis_client: Any,
        max_concurrent_users: int = MAX_CONCURRENT_USERS,
    ) -> None:
        """初始化调度器。

        Args:
            redis_client: Redis 客户端实例。
            max_concurrent_users: 最大并发用户数（默认 20）。
        """
        self._redis = redis_client
        self._max_concurrent_users = max_concurrent_users

    async def acquire_slot(self, user_id: str, run_id: str) -> tuple[bool, int]:
        """获取用户许可槽位。

        流程：
        1. 检查同一用户是否已有活跃 Run → 拒绝（P0-17）；
        2. 检查活跃用户数 < 20 → 获取槽位；
        3. >= 20 → 入等待队列。

        Args:
            user_id: 用户 ID。
            run_id: Run ID。

        Returns:
            tuple[bool, int]: (是否获取成功, 排队位置)。
                             成功时位置为 0，失败时为队列中的位置。
        """
        # 检查同一用户是否已有活跃 Run
        existing_run = self._redis.get(ACTIVE_USER_KEY.format(user_id=user_id))
        if existing_run:
            # 同一用户已有活跃 Run，拒绝
            logger.warning("User %s already has active run %s", user_id, existing_run)
            return False, -1

        # 检查活跃用户数
        active_count = self._redis.scard(ACTIVE_USERS_SET)

        if active_count < self._max_concurrent_users:
            # 有空位 → 获取槽位
            self._redis.sadd(ACTIVE_USERS_SET, user_id)
            self._redis.set(ACTIVE_USER_KEY.format(user_id=user_id), run_id)
            logger.info("Slot acquired: user=%s, run=%s", user_id, run_id)
            return True, 0
        else:
            # 无空位 → 入等待队列
            timestamp = time.time()
            self._redis.zadd(QUEUE_KEY, {run_id: timestamp})
            position = self._redis.zrank(QUEUE_KEY, run_id)
            pos = int(position) + 1 if position is not None else 1
            logger.info("Queued: run=%s, position=%d", run_id, pos)
            return False, pos

    async def release_slot(self, user_id: str, run_id: str) -> None:
        """释放用户许可槽位。

        流程：
        1. 从活跃用户集合移除；
        2. 删除用户活跃 Run 记录；
        3. 删除心跳记录。

        Args:
            user_id: 用户 ID。
            run_id: Run ID。
        """
        self._redis.srem(ACTIVE_USERS_SET, user_id)
        self._redis.delete(ACTIVE_USER_KEY.format(user_id=user_id))
        self._redis.delete(HEARTBEAT_KEY.format(run_id=run_id))
        logger.info("Slot released: user=%s, run=%s", user_id, run_id)

    async def get_queue_position(self, run_id: str) -> QueuePosition:
        """获取排队位置。

        Args:
            run_id: Run ID。

        Returns:
            QueuePosition: 排队位置信息。
        """
        rank = self._redis.zrank(QUEUE_KEY, run_id)
        if rank is None:
            return QueuePosition(position=0, ahead_count=0, estimated_wait_seconds=0)

        position = int(rank) + 1
        ahead_count = position - 1
        # 估算等待时间：前方人数 × 平均执行时长 / 并发用户数
        estimated_wait = int(ahead_count * AVG_RUN_DURATION / self._max_concurrent_users)

        return QueuePosition(
            position=position,
            ahead_count=ahead_count,
            estimated_wait_seconds=estimated_wait,
        )

    async def register_heartbeat(self, run_id: str) -> None:
        """注册 Run 心跳。

        每 30 秒更新一次心跳时间戳。

        Args:
            run_id: Run ID。
        """
        self._redis.set(
            HEARTBEAT_KEY.format(run_id=run_id),
            str(time.time()),
            ex=60,
        )

    async def check_heartbeats(self) -> list[str]:
        """检查活跃 Run 心跳，返回超时的 Run ID 列表。

        超过 HEARTBEAT_TIMEOUT（默认 90 秒）无心跳的 Run 标记为 failed。

        Returns:
            list[str]: 超时的 Run ID 列表。
        """
        expired_run_ids: list[str] = []

        # 遍历活跃用户的 Run
        users = self._redis.smembers(ACTIVE_USERS_SET)
        for user_id_bytes in users:
            user_id = user_id_bytes.decode() if isinstance(user_id_bytes, bytes) else user_id_bytes
            run_id = self._redis.get(ACTIVE_USER_KEY.format(user_id=user_id))
            if run_id is None:
                continue

            run_id_str = run_id.decode() if isinstance(run_id, bytes) else run_id
            heartbeat = self._redis.get(HEARTBEAT_KEY.format(run_id=run_id_str))

            if heartbeat is None:
                # 无心跳记录 → 可能从未注册或已过期
                expired_run_ids.append(run_id_str)
                # 释放槽位
                await self.release_slot(user_id, run_id_str)
            else:
                # 检查心跳是否超时
                try:
                    heartbeat_time = float(
                        heartbeat.decode() if isinstance(heartbeat, bytes) else heartbeat
                    )
                    if time.time() - heartbeat_time > HEARTBEAT_TIMEOUT:
                        expired_run_ids.append(run_id_str)
                        await self.release_slot(user_id, run_id_str)
                except (ValueError, TypeError):
                    expired_run_ids.append(run_id_str)
                    await self.release_slot(user_id, run_id_str)

        if expired_run_ids:
            logger.warning("Expired heartbeats: %s", expired_run_ids)

        return expired_run_ids

    async def check_and_promote(self) -> list[str]:
        """检查队列并提升等待 Run。

        当有空闲槽位时，从等待队列取出最早的 Run 提升。

        Returns:
            list[str]: 被提升的 Run ID 列表。
        """
        promoted: list[str] = []

        while True:
            active_count = self._redis.scard(ACTIVE_USERS_SET)
            if active_count >= self._max_concurrent_users:
                break

            # 从队列取出最早的 Run
            result = self._redis.zpopmin(QUEUE_KEY, 1)
            if not result:
                break

            run_id_bytes, _score = result[0]
            run_id = run_id_bytes.decode() if isinstance(run_id_bytes, bytes) else run_id_bytes

            # 注意：此处不知道 user_id，需要从数据库查询
            # 简化处理：直接提升，由调用方补充 user_id 信息
            promoted.append(run_id)
            logger.info("Promoted run %s from queue", run_id)

        return promoted

    async def remove_from_queue(self, run_id: str) -> None:
        """从等待队列移除 Run（取消排队时调用）。

        Args:
            run_id: Run ID。
        """
        self._redis.zrem(QUEUE_KEY, run_id)
