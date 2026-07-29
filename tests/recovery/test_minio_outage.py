"""恢复测试：MinIO 临时中断时作业重试与最终完成。

验证（docs/arch-v0.md §4.2 时序图 + §7.6 异步与事务约定 + §8.2 风险）：

核心场景：
- MinIO 临时中断时作业处理失败并进入重试等待；
- 中断期间不提交任何事实/结果（结果一致性）；
- MinIO 恢复后作业重试成功完成。

测试策略：
- 注册一个 flaky handler，前 N 次调用抛异常（模拟 MinIO 中断），
  第 N+1 次调用成功（模拟 MinIO 恢复）；
- 验证作业在 MinIO 中断期间处于 RETRY_WAIT，无结果提交；
- 验证 MinIO 恢复后作业最终 SUCCEEDED。
"""

from uuid import UUID

import pytest
import sqlalchemy as sa

from packages.common.database import session_scope
from packages.jobs.entities import TERMINAL_STATUSES, Job, JobStatus

# ============================================================
# 辅助函数
# ============================================================


async def _clear_retry_backoff(factory, job_id: UUID) -> None:
    """清除作业的重试退避时间，使其可立即重新执行。

    将 RETRY_WAIT 状态的作业重置为 QUEUED 并清除 run_after。
    """
    async with session_scope(factory) as session:
        await session.execute(
            sa.update(Job)
            .values(status=JobStatus.QUEUED.value, run_after=None)
            .where(Job.id == job_id)
        )


# ============================================================
# 1. MinIO 临时中断时作业重试
# ============================================================


@pytest.mark.integration
async def test_minio_outage_job_retries(job_harness) -> None:
    """MinIO 中断时作业进入 RETRY_WAIT，恢复后重试成功。"""
    fail_count = [0]
    max_fails = 2

    async def minio_flaky_handler(job) -> dict:
        if fail_count[0] < max_fails:
            fail_count[0] += 1
            raise RuntimeError(f"MinIO connection refused (attempt #{fail_count[0]})")
        return {"result": "minio_recovered", **(job.payload or {})}

    job_harness._executor.register_handler("minio_flaky", minio_flaky_handler)

    ref = await job_harness.accept("minio_flaky", {"data": "test"}, "idem-minio-outage-retry")

    # 第一次执行：MinIO 中断 → 失败 → RETRY_WAIT
    await job_harness.deliver(ref.job_id)
    result_ref = await job_harness.get(ref.job_id)
    assert result_ref.status in (JobStatus.RETRY_WAIT, JobStatus.FAILED), (
        f"Job should be in RETRY_WAIT or FAILED during outage, got {result_ref.status}"
    )

    # 如果进入 RETRY_WAIT，清除退避并重试直到成功
    for _ in range(max_fails + 1):
        result_ref = await job_harness.get(ref.job_id)
        if result_ref.status == JobStatus.SUCCEEDED:
            break
        if result_ref.status == JobStatus.RETRY_WAIT:
            await _clear_retry_backoff(job_harness._factory, ref.job_id)
        await job_harness.deliver(ref.job_id)

    # 验证最终成功
    result_ref = await job_harness.get(ref.job_id)
    assert result_ref.status == JobStatus.SUCCEEDED, (
        f"Job should eventually SUCCEEDED after recovery, got {result_ref.status}"
    )


# ============================================================
# 2. 中断期间不提交结果
# ============================================================


@pytest.mark.integration
async def test_no_results_committed_during_outage(job_harness) -> None:
    """MinIO 中断期间不提交任何结果。"""
    fail_count = [0]
    max_fails = 3

    async def always_fail_handler(job) -> dict:
        if fail_count[0] < max_fails:
            fail_count[0] += 1
            raise ConnectionError(f"MinIO unavailable (attempt #{fail_count[0]})")
        return {"result": "ok"}

    job_harness._executor.register_handler("minio_always_fail", always_fail_handler)

    ref = await job_harness.accept("minio_always_fail", {"data": "test"}, "idem-minio-no-commit")

    # 多次执行（均失败）
    for _ in range(max_fails):
        await job_harness.deliver(ref.job_id)
        ref_status = await job_harness.get(ref.job_id)
        if ref_status.status == JobStatus.RETRY_WAIT:
            await _clear_retry_backoff(job_harness._factory, ref.job_id)

    # 验证无成功结果提交
    results = await job_harness.authoritative_results(ref.job_id)
    successful = [r for r in results if r.status == JobStatus.SUCCEEDED]
    assert len(successful) == 0, "No results should be committed during outage"

    # 最终恢复后执行成功
    await job_harness.deliver(ref.job_id)
    result_ref = await job_harness.get(ref.job_id)
    assert result_ref.status == JobStatus.SUCCEEDED


# ============================================================
# 3. 恢复后作业成功完成
# ============================================================


@pytest.mark.integration
async def test_minio_recovery_job_succeeds(job_harness) -> None:
    """MinIO 恢复后作业成功完成，结果正确。"""
    fail_count = [0]

    async def recover_handler(job) -> dict:
        if fail_count[0] < 1:
            fail_count[0] += 1
            raise OSError("MinIO: connection reset by peer")
        return {"status": "recovered", "payload": job.payload}

    job_harness._executor.register_handler("minio_recover", recover_handler)

    ref = await job_harness.accept(
        "minio_recover",
        {"experiment": "particle_size", "d50": 12.5},
        "idem-minio-recover",
    )

    # 第一次执行：MinIO 中断
    await job_harness.deliver(ref.job_id)
    status_ref = await job_harness.get(ref.job_id)

    # 清除退避并重试
    if status_ref.status == JobStatus.RETRY_WAIT:
        await _clear_retry_backoff(job_harness._factory, ref.job_id)

    # 第二次执行：MinIO 恢复 → 成功
    await job_harness.deliver(ref.job_id)

    # 验证最终状态
    result_ref = await job_harness.get(ref.job_id)
    assert result_ref.status == JobStatus.SUCCEEDED

    # 验证结果正确
    results = await job_harness.authoritative_results(ref.job_id)
    assert len(results) == 1
    assert results[0].status == JobStatus.SUCCEEDED
    assert results[0].payload is not None
    assert results[0].payload["status"] == "recovered"
    assert results[0].payload["payload"]["d50"] == 12.5


# ============================================================
# 4. 多作业在中断后全部恢复
# ============================================================


@pytest.mark.integration
async def test_multiple_jobs_survive_minio_outage(job_harness) -> None:
    """多个作业在 MinIO 中断后全部恢复完成。"""
    fail_count = [0]

    async def batch_handler(job) -> dict:
        if fail_count[0] < 2:
            fail_count[0] += 1
            raise RuntimeError("MinIO batch upload failed")
        return {"batch_id": job.payload.get("batch_id", 0)}

    job_harness._executor.register_handler("minio_batch", batch_handler)

    refs = []
    for i in range(3):
        ref = await job_harness.accept(
            "minio_batch",
            {"batch_id": i},
            f"idem-minio-batch-{i}",
        )
        refs.append(ref)

    # 执行所有作业（前两次失败，第三次成功）
    for ref in refs:
        await job_harness.deliver(ref.job_id)
        status = await job_harness.get(ref.job_id)
        if status.status == JobStatus.RETRY_WAIT:
            await _clear_retry_backoff(job_harness._factory, ref.job_id)

    # 继续重试直到全部成功
    for _ in range(5):
        all_done = True
        for ref in refs:
            status = await job_harness.get(ref.job_id)
            if status.status not in TERMINAL_STATUSES:
                all_done = False
                if status.status == JobStatus.RETRY_WAIT:
                    await _clear_retry_backoff(job_harness._factory, ref.job_id)
                await job_harness.deliver(ref.job_id)
        if all_done:
            break

    # 验证全部成功
    for i, ref in enumerate(refs):
        status = await job_harness.get(ref.job_id)
        assert status.status == JobStatus.SUCCEEDED, (
            f"Job {i} should be SUCCEEDED, got {status.status}"
        )
