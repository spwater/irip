"""重复投递恢复测试。

验证作业在消息队列重复投递时的幂等保证
（docs/arch-v0.md §4.2 时序图 + Task 7 计划第 532-538 行）。

核心场景：
- Worker 收到同一作业的两次投递（Redis 重投 / 网络重试）；
- 第一次投递执行成功并提交结果；
- 第二次投递尝试获取租约失败或发现终态 → no-op；
- 数据库中只有一个结果。
"""

import pytest

from packages.jobs.entities import JobStatus


@pytest.mark.integration
async def test_duplicate_delivery_commits_one_result(
    job_harness,
) -> None:
    """重复投递只提交一次结果（recovery 测试目录）。"""
    job = await job_harness.accept("echo", {"value": 7}, "idem-recovery-7")
    await job_harness.deliver_twice(job.job_id)
    results = await job_harness.authoritative_results(job.job_id)
    assert len(results) == 1
    assert results[0].payload == {"value": 7}


@pytest.mark.integration
async def test_duplicate_delivery_with_concurrent_workers(
    job_harness,
) -> None:
    """两个 worker 同时投递同一作业，只提交一次结果。"""
    job = await job_harness.accept("echo", {"value": 42}, "idem-concurrent")

    # 第一个 worker 执行（成功）
    await job_harness.deliver(job.job_id, owner="worker-1")

    # 第二个 worker 尝试执行（应发现终态 → no-op）
    await job_harness.deliver(job.job_id, owner="worker-2")

    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.SUCCEEDED

    results = await job_harness.authoritative_results(job.job_id)
    assert len(results) == 1


@pytest.mark.integration
async def test_redis_redelivery_after_crash(
    job_harness,
) -> None:
    """Worker 崩溃后 Redis 重投，作业仍可正确执行。"""
    job = await job_harness.accept("echo", {"value": 100}, "idem-crash")

    # 模拟 worker 崩溃：获取租约但不执行
    await job_harness.simulate_worker_crash(job.job_id)

    # 租约过期后回收
    await job_harness.reap_expired_leases()
    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.QUEUED

    # 重新投递执行
    await job_harness.deliver(job.job_id)
    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.SUCCEEDED

    results = await job_harness.authoritative_results(job.job_id)
    assert len(results) == 1
    assert results[0].payload == {"value": 100}
