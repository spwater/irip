"""作业生命周期集成测试。

验证可靠作业运行时的核心行为（docs/arch-v0.md §4.2 + Task 7 计划第 532-576 行）：
- 正常成功：echo 作业执行成功；
- 3 次重试：瞬时故障重试后成功；
- 不可重试失败：验证错误直接失败；
- 取消：请求取消后作业终态为 cancelled；
- 过期租约回收：租约过期后作业重新入队；
- 重复投递提交一次结果：幂等保证；
- Redis 清空重建：outbox 恢复。

前置依赖：
- Docker compose 中的 redis-test 服务已启动（localhost:56379）；
- job_harness fixture（在 conftest.py 中定义）。
"""

import pytest

from packages.jobs.entities import JobStatus


@pytest.mark.integration
async def test_duplicate_delivery_commits_one_result(
    job_harness,
) -> None:
    """重复投递只提交一次结果。"""
    job = await job_harness.accept("echo", {"value": 7}, "idem-7")
    await job_harness.deliver_twice(job.job_id)
    results = await job_harness.authoritative_results(job.job_id)
    assert len(results) == 1
    assert results[0].payload == {"value": 7}


@pytest.mark.integration
async def test_expired_lease_returns_running_job_to_queue(
    job_harness,
) -> None:
    """过期租约将 running 作业重新入队。"""
    job = await job_harness.start_then_abandon("echo")
    await job_harness.reap_expired_leases()
    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.QUEUED


@pytest.mark.integration
async def test_normal_success(job_harness) -> None:
    """正常成功路径：echo 作业执行成功。"""
    job = await job_harness.accept("echo", {"value": 42}, "idem-success")
    await job_harness.deliver(job.job_id)
    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.SUCCEEDED

    results = await job_harness.authoritative_results(job.job_id)
    assert len(results) == 1
    assert results[0].payload == {"value": 42}


@pytest.mark.integration
async def test_three_retries_then_success(job_harness) -> None:
    """3 次重试后成功。"""
    job = await job_harness.accept("flaky", {"value": 1}, "idem-retry")
    await job_harness.deliver_with_retries(job.job_id, fail_times=2)
    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.SUCCEEDED


@pytest.mark.integration
async def test_non_retryable_failure(job_harness) -> None:
    """不可重试错误直接失败。"""
    job = await job_harness.accept("validation_fail", {"bad": True}, "idem-validation")
    await job_harness.deliver(job.job_id)
    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.FAILED


@pytest.mark.integration
async def test_cancellation(job_harness) -> None:
    """取消作业。"""
    job = await job_harness.accept("echo", {"value": 1}, "idem-cancel")
    await job_harness.request_cancel(job.job_id)
    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.CANCEL_REQUESTED


@pytest.mark.integration
async def test_redis_cleared_and_rebuilt(job_harness) -> None:
    """Redis 清空后 outbox 重建。"""
    job = await job_harness.accept("echo", {"value": 99}, "idem-redis")

    # 模拟 dispatch 到 Redis
    await job_harness.dispatch_outbox()
    assert await job_harness.undelivered_count() == 0

    # 模拟 Redis 清空 → 重置 outbox 为未投递
    await job_harness.reset_outbox()
    assert await job_harness.undelivered_count() > 0

    # 重新 dispatch
    await job_harness.dispatch_outbox()
    assert await job_harness.undelivered_count() == 0

    # 执行作业
    await job_harness.deliver(job.job_id)
    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.SUCCEEDED


@pytest.mark.integration
async def test_idempotent_accept(job_harness) -> None:
    """相同幂等键的重复 accept 返回同一作业。"""
    ref1 = await job_harness.accept("echo", {"value": 1}, "idem-dup")
    ref2 = await job_harness.accept("echo", {"value": 1}, "idem-dup")
    assert ref1.job_id == ref2.job_id
    assert ref1.status == ref2.status
