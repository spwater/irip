"""摄入 worker 重启恢复测试（Task 20 Step 3）。

验证 worker 在摄入过程中崩溃后能正确恢复：
- Worker 在 parse 阶段后被终止
- 启动替换 worker，等待租约过期
- 断言只有一个权威事实集
- 保留首次尝试日志
- 第二次尝试成功
"""

import asyncio
from uuid import uuid4

import pytest
import sqlalchemy as sa

from packages.jobs.entities import JobStatus


@pytest.mark.integration
async def test_ingestion_worker_restart_completes(
    job_harness,
) -> None:
    """摄入 worker 在 parse 阶段崩溃后，替换 worker 能成功完成。"""
    # 提交一个摄入作业
    job = await job_harness.accept(
        "ingest.file",
        {"file_id": "test-batch-01.xlsx", "source_type": "file"},
        f"idem-restart-{uuid4().hex[:8]}",
    )

    # 模拟 worker 在 parse 阶段崩溃（获取租约但不执行）
    await job_harness.simulate_worker_crash(job.job_id)

    # 验证作业仍在 running 状态（租约未释放）
    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.RUNNING

    # 回收过期租约
    reaped = await job_harness.reap_expired_leases()
    assert job.job_id in reaped

    # 验证作业回到 queued 状态
    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.QUEUED

    # 替换 worker 重新投递执行
    await job_harness.deliver(job.job_id, owner="replacement-worker")

    # 验证最终成功
    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.SUCCEEDED

    # 断言只有一个权威结果
    results = await job_harness.authoritative_results(job.job_id)
    assert len(results) == 1


@pytest.mark.integration
async def test_ingestion_retains_first_attempt_logs(
    job_harness,
) -> None:
    """摄入 worker 崩溃后，首次尝试的日志被保留。"""
    from packages.common.database import session_scope
    from packages.jobs.entities import Job

    job = await job_harness.accept(
        "ingest.file",
        {"file_id": "test-batch-02.xlsx", "source_type": "file"},
        f"idem-logs-{uuid4().hex[:8]}",
    )

    # 首次尝试：worker 崩溃
    await job_harness.simulate_worker_crash(job.job_id)

    # 记录首次尝试的 lease_owner
    async with session_scope(job_harness._factory) as session:
        job_record = await session.scalar(
            sa.select(Job).where(Job.id == job.job_id)
        )
        assert job_record is not None
        first_lease_owner = job_record.lease_owner
        assert first_lease_owner == "crashed-worker"

    # 回收并重试
    await job_harness.reap_expired_leases()
    await job_harness.deliver(job.job_id, owner="replacement-worker-2")

    # 验证最终成功
    ref = await job_harness.get(job.job_id)
    assert ref.status == JobStatus.SUCCEEDED

    # 验证有结果
    results = await job_harness.authoritative_results(job.job_id)
    assert len(results) == 1
    assert results[0].status == JobStatus.SUCCEEDED
