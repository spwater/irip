"""恢复测试：Redis 丢失后从 Outbox 重建队列。

验证（docs/arch-v0.md §4.2 时序图 + §7.6 异步与事务约定 + §8.2 风险）：

核心场景：
- Redis 丢失（队列清空）后，Outbox 中未投递/已投递的事件可重新构建队列；
- 重建后作业执行不产生重复结果（幂等保证）；
- 所有作业最终全部完成（无丢失）。

Outbox 模式保证：
- 业务写操作 + outbox_event INSERT 在同一事务中（原子性）；
- Redis 丢失不影响数据库中的 outbox_event 记录；
- ``reset_delivered()`` 可将已投递事件重置为未投递，重新发送。
"""

import pytest
import sqlalchemy as sa

from packages.jobs.entities import JobStatus

# ============================================================
# 辅助函数
# ============================================================


async def _submit_and_dispatch(harness, count: int, prefix: str) -> list:
    """提交多个作业并调度 outbox 事件。"""
    refs = []
    for i in range(count):
        ref = await harness.accept(
            "echo",
            {"batch": prefix, "index": i},
            f"idem-redis-{prefix}-{i}",
        )
        refs.append(ref)
    # 调度 outbox 事件到 Redis
    await harness.dispatch_outbox()
    return refs


# ============================================================
# 1. Redis 丢失后从 Outbox 重建队列
# ============================================================


@pytest.mark.integration
async def test_redis_loss_rebuild_from_outbox(job_harness) -> None:
    """Redis 丢失后，重置 outbox 已投递事件，重新构建队列。"""
    # 1. 提交 5 个作业
    _refs = await _submit_and_dispatch(job_harness, 5, "loss")

    # 2. 验证 outbox 事件已投递
    undelivered = await job_harness.undelivered_count()
    assert undelivered == 0, "All outbox events should be delivered"

    # 3. 模拟 Redis 丢失：重置已投递事件为未投递
    reset_count = await job_harness.reset_outbox()
    assert reset_count > 0, "Should have events to reset"

    # 4. 重新调度（从 outbox 重建队列）
    delivered = await harness_dispatch_all(job_harness)
    assert delivered > 0, "Should re-deliver events after rebuild"

    # 5. 验证所有事件已重新投递
    undelivered = await job_harness.undelivered_count()
    assert undelivered == 0, "All events should be re-delivered"


@pytest.mark.integration
async def test_redis_loss_no_duplicate_results(job_harness) -> None:
    """Redis 丢失重建后，重复投递不产生重复结果。"""
    # 1. 提交 3 个作业
    refs = await _submit_and_dispatch(job_harness, 3, "dup")

    # 2. 执行所有作业（第一次）
    for ref in refs:
        await job_harness.deliver(ref.job_id)

    # 3. 验证全部成功
    for ref in refs:
        result_ref = await job_harness.get(ref.job_id)
        assert result_ref.status == JobStatus.SUCCEEDED

    # 4. 模拟 Redis 丢失 + 重建
    await job_harness.reset_outbox()
    await harness_dispatch_all(job_harness)

    # 5. 重复执行（模拟重投）
    for ref in refs:
        await job_harness.deliver(ref.job_id)

    # 6. 验证无重复结果
    for ref in refs:
        results = await job_harness.authoritative_results(ref.job_id)
        assert len(results) == 1, (
            f"Job {ref.job_id} should have exactly 1 result, got {len(results)}"
        )


@pytest.mark.integration
async def test_redis_loss_all_jobs_complete(job_harness) -> None:
    """Redis 丢失后所有作业最终全部完成。"""
    # 1. 提交 10 个作业
    refs = await _submit_and_dispatch(job_harness, 10, "complete")

    # 2. 模拟 Redis 丢失 + 重建
    await job_harness.reset_outbox()
    await harness_dispatch_all(job_harness)

    # 3. 执行所有作业
    for ref in refs:
        await job_harness.deliver(ref.job_id)

    # 4. 验证全部完成
    for ref in refs:
        result_ref = await job_harness.get(ref.job_id)
        assert result_ref.status == JobStatus.SUCCEEDED, (
            f"Job {ref.job_id} should be SUCCEEDED, got {result_ref.status}"
        )


@pytest.mark.integration
async def test_outbox_preserves_events_through_redis_loss(
    job_harness,
    async_session_factory,
) -> None:
    """Redis 丢失不影响 outbox_event 表中的事件记录。"""
    from packages.jobs.outbox import OutboxEvent

    # 1. 提交作业（自动创建 outbox 事件）
    ref = await job_harness.accept("echo", {"value": 1}, "idem-outbox-preserve")

    # 2. 调度
    await job_harness.dispatch_outbox()

    # 3. 验证 outbox 事件存在
    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(OutboxEvent).where(
                OutboxEvent.aggregate_id == ref.job_id
            )
        )
        events = result.scalars().all()
        assert len(events) >= 1, "Outbox event should exist in DB"

    # 4. 模拟 Redis 丢失（不影响 DB）
    await job_harness.reset_outbox()

    # 5. 验证事件仍在 DB 中
    async with async_session_factory() as session:
        result = await session.execute(
            sa.select(OutboxEvent).where(
                OutboxEvent.aggregate_id == ref.job_id
            )
        )
        events = result.scalars().all()
        assert len(events) >= 1, "Outbox events should survive Redis loss"

    # 6. 重新调度
    delivered = await harness_dispatch_all(job_harness)
    assert delivered >= 1


@pytest.mark.integration
async def test_partial_redis_loss_recovery(job_harness) -> None:
    """部分作业已执行、部分未执行时 Redis 丢失，重建后全部完成。"""
    # 1. 提交 4 个作业
    refs = await _submit_and_dispatch(job_harness, 4, "partial")

    # 2. 只执行前 2 个
    await job_harness.deliver(refs[0].job_id)
    await job_harness.deliver(refs[1].job_id)

    # 3. 验证前 2 个成功
    assert (await job_harness.get(refs[0].job_id)).status == JobStatus.SUCCEEDED
    assert (await job_harness.get(refs[1].job_id)).status == JobStatus.SUCCEEDED

    # 4. 模拟 Redis 丢失 + 重建
    await job_harness.reset_outbox()
    await harness_dispatch_all(job_harness)

    # 5. 执行剩余 2 个
    await job_harness.deliver(refs[2].job_id)
    await job_harness.deliver(refs[3].job_id)

    # 6. 验证全部完成
    for ref in refs:
        result_ref = await job_harness.get(ref.job_id)
        assert result_ref.status == JobStatus.SUCCEEDED


# ============================================================
# 辅助函数
# ============================================================


async def harness_dispatch_all(harness, max_rounds: int = 10) -> int:
    """重复调度直到所有事件投递或达到最大轮次。"""
    total = 0
    for _ in range(max_rounds):
        undelivered = await harness.undelivered_count()
        if undelivered == 0:
            break
        delivered = await harness.dispatch_outbox()
        total += delivered
    return total
