"""ResearchScheduler 单元测试。

覆盖 ``packages.research.execution.scheduler`` 的全部方法：
- acquire_slot: 空位获取 / 同用户重复拒绝 / 满员入队
- release_slot: 释放许可 + 清理
- get_queue_position: 排队位置与等待估算
- register_heartbeat / check_heartbeats: 心跳注册与超时回收
- check_and_promote: 空闲槽位提升等待 Run
- remove_from_queue: 取消排队

使用内存 FakeRedis 模拟 Redis 客户端（同步接口），不依赖真实 Redis。
"""

from __future__ import annotations

import time

from packages.research.execution.models_trusted import QueuePosition
from packages.research.execution.scheduler import (
    ACTIVE_USER_KEY,
    ACTIVE_USERS_SET,
    HEARTBEAT_KEY,
    QUEUE_KEY,
    ResearchScheduler,
)

# ============================================================
# FakeRedis
# ============================================================


class FakeRedis:
    """内存版 Redis 客户端，模拟 scheduler 使用的同步接口。

    返回值与真实 redis-py 一致：bytes 输入 → bytes 输出（set/sadd），
    zpopmin 返回 [(member_bytes, score), ...]。
    """

    def __init__(self) -> None:
        self._sets: dict[str, set[str]] = {}
        self._strings: dict[str, str] = {}
        self._zsets: dict[str, dict[str, float]] = {}

    # ---- string ops ----
    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._strings[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self._strings.get(key)

    def delete(self, key: str) -> int:
        existed = key in self._strings
        self._strings.pop(key, None)
        return 1 if existed else 0

    # ---- set ops ----
    def sadd(self, key: str, *members: str) -> int:
        s = self._sets.setdefault(key, set())
        added = 0
        for m in members:
            if m not in s:
                s.add(m)
                added += 1
        return added

    def srem(self, key: str, *members: str) -> int:
        s = self._sets.get(key, set())
        removed = 0
        for m in members:
            if m in s:
                s.discard(m)
                removed += 1
        return removed

    def scard(self, key: str) -> int:
        return len(self._sets.get(key, set()))

    def smembers(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))

    # ---- sorted set ops ----
    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        z = self._zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in z:
                added += 1
            z[member] = float(score)
        return added

    def zrank(self, key: str, member: str) -> int | None:
        z = self._zsets.get(key, {})
        if member not in z:
            return None
        sorted_members = sorted(z.items(), key=lambda kv: (kv[1], kv[0]))
        for idx, (m, _) in enumerate(sorted_members):
            if m == member:
                return idx
        return None

    def zpopmin(self, key: str, count: int = 1) -> list[tuple[str, float]]:
        z = self._zsets.get(key, {})
        if not z:
            return []
        sorted_members = sorted(z.items(), key=lambda kv: (kv[1], kv[0]))
        result = []
        for _ in range(min(count, len(sorted_members))):
            m, s = sorted_members.pop(0)
            del z[m]
            result.append((m.encode(), s))
        return result

    def zrem(self, key: str, member: str) -> int:
        z = self._zsets.get(key, {})
        if member in z:
            del z[member]
            return 1
        return 0


# ============================================================
# acquire_slot
# ============================================================


class TestAcquireSlot:
    """获取用户许可槽位。"""

    async def test_acquire_slot_success(self) -> None:
        """有空位时获取成功，位置为 0。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=20)
        ok, pos = await sched.acquire_slot("user-1", "run-1")
        assert ok is True
        assert pos == 0
        assert "user-1" in r._sets[ACTIVE_USERS_SET]

    async def test_acquire_slot_rejects_duplicate_user(self) -> None:
        """同一用户已有活跃 Run 时拒绝。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=20)
        await sched.acquire_slot("user-1", "run-1")
        ok, pos = await sched.acquire_slot("user-1", "run-2")
        assert ok is False
        assert pos == -1

    async def test_acquire_slot_queues_when_full(self) -> None:
        """满员时入等待队列并返回排队位置。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=2)
        await sched.acquire_slot("user-1", "run-1")
        await sched.acquire_slot("user-2", "run-2")
        # 第三人应入队
        ok, pos = await sched.acquire_slot("user-3", "run-3")
        assert ok is False
        assert pos == 1
        assert "run-3" in r._zsets[QUEUE_KEY]


# ============================================================
# release_slot
# ============================================================


class TestReleaseSlot:
    """释放许可槽位。"""

    async def test_release_slot_clears_all_keys(self) -> None:
        """释放后从活跃集合移除并清理用户/心跳记录。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=20)
        await sched.acquire_slot("user-1", "run-1")
        await sched.register_heartbeat("run-1")
        await sched.release_slot("user-1", "run-1")
        assert "user-1" not in r._sets.get(ACTIVE_USERS_SET, set())
        assert r.get(ACTIVE_USER_KEY.format(user_id="user-1")) is None
        assert r.get(HEARTBEAT_KEY.format(run_id="run-1")) is None


# ============================================================
# get_queue_position
# ============================================================


class TestQueuePosition:
    """排队位置查询。"""

    async def test_position_zero_when_not_queued(self) -> None:
        """Run 不在队列中时返回 0 位置。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=20)
        pos = await sched.get_queue_position("run-x")
        assert isinstance(pos, QueuePosition)
        assert pos.position == 0
        assert pos.ahead_count == 0
        assert pos.estimated_wait_seconds == 0

    async def test_position_reflects_queue_order(self) -> None:
        """排队位置反映入队顺序。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=1)
        await sched.acquire_slot("user-1", "run-1")  # 占位
        await sched.acquire_slot("user-2", "run-2")  # 入队 #1
        await sched.acquire_slot("user-3", "run-3")  # 入队 #2
        pos = await sched.get_queue_position("run-3")
        assert pos.position == 2
        assert pos.ahead_count == 1
        assert pos.estimated_wait_seconds > 0


# ============================================================
# register_heartbeat / check_heartbeats
# ============================================================


class TestHeartbeat:
    """心跳注册与超时回收。"""

    async def test_register_heartbeat_stores_timestamp(self) -> None:
        """register_heartbeat 写入心跳时间戳。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=20)
        await sched.register_heartbeat("run-1")
        assert r.get(HEARTBEAT_KEY.format(run_id="run-1")) is not None

    async def test_check_heartbeats_no_active_users(self) -> None:
        """无活跃用户时返回空列表。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=20)
        assert await sched.check_heartbeats() == []

    async def test_check_heartbeats_missing_heartbeat_expired(self) -> None:
        """活跃 Run 无心跳记录 → 判定超时并释放槽位。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=20)
        await sched.acquire_slot("user-1", "run-1")
        # 不注册心跳
        expired = await sched.check_heartbeats()
        assert "run-1" in expired
        # 槽位已释放
        assert "user-1" not in r._sets.get(ACTIVE_USERS_SET, set())

    async def test_check_heartbeats_fresh_heartbeat_not_expired(self) -> None:
        """近期心跳不超时。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=20)
        await sched.acquire_slot("user-1", "run-1")
        await sched.register_heartbeat("run-1")
        expired = await sched.check_heartbeats()
        assert expired == []

    async def test_check_heartbeats_stale_heartbeat_expired(self) -> None:
        """过期心跳（>90s）判定超时。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=20)
        await sched.acquire_slot("user-1", "run-1")
        # 手动写入过期心跳
        r._strings[HEARTBEAT_KEY.format(run_id="run-1")] = str(time.time() - 200)
        expired = await sched.check_heartbeats()
        assert "run-1" in expired

    async def test_check_heartbeats_invalid_heartbeat_expired(self) -> None:
        """心跳值非数字时判定超时。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=20)
        await sched.acquire_slot("user-1", "run-1")
        r._strings[HEARTBEAT_KEY.format(run_id="run-1")] = "not-a-number"
        expired = await sched.check_heartbeats()
        assert "run-1" in expired


# ============================================================
# check_and_promote
# ============================================================


class TestCheckAndPromote:
    """空闲槽位提升。"""

    async def test_promote_when_slots_available(self) -> None:
        """有空位时从队列提升等待 Run。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=2)
        await sched.acquire_slot("user-1", "run-1")
        await sched.acquire_slot("user-2", "run-2")
        await sched.acquire_slot("user-3", "run-3")  # 入队
        # 释放一个槽位
        await sched.release_slot("user-1", "run-1")
        promoted = await sched.check_and_promote()
        assert "run-3" in promoted

    async def test_no_promote_when_full(self) -> None:
        """满员时不提升。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=1)
        await sched.acquire_slot("user-1", "run-1")
        await sched.acquire_slot("user-2", "run-2")  # 入队
        promoted = await sched.check_and_promote()
        assert promoted == []

    async def test_no_promote_when_queue_empty(self) -> None:
        """队列为空时返回空列表。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=20)
        assert await sched.check_and_promote() == []


# ============================================================
# remove_from_queue
# ============================================================


class TestRemoveFromQueue:
    """取消排队。"""

    async def test_remove_from_queue_deletes_member(self) -> None:
        """从队列移除指定 Run。"""
        r = FakeRedis()
        sched = ResearchScheduler(r, max_concurrent_users=1)
        await sched.acquire_slot("user-1", "run-1")
        await sched.acquire_slot("user-2", "run-2")  # 入队
        await sched.remove_from_queue("run-2")
        assert "run-2" not in r._zsets.get(QUEUE_KEY, {})
