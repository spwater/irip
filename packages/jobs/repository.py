"""作业数据仓库：Job 的数据库操作。

所有方法接受 AsyncSession 参数，由调用方（JobService / WorkerLeaseManager）
管理事务边界。查询使用乐观锁（lock_version）和条件 UPDATE 保证并发安全。

关键操作（docs/arch-v0.md §4.2 时序图）：
- accept: INSERT job（状态=accepted）；
- get: SELECT job by id；
- update_status: UPDATE with lock_version（乐观锁，幂等提交）；
- acquire_lease: 条件 UPDATE（仅当租约可用时获取）；
- renew_lease: 延长租约过期时间；
- release_lease: 清除租约；
- reap_expired_leases: 回收过期租约，重新入队。
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from packages.jobs.entities import (
    LEASEABLE_STATUSES,
    TERMINAL_STATUSES,
    Job,
    JobStatus,
)


class JobRepository:
    """作业持久化仓库。

    所有方法为纯数据访问，不含业务逻辑——业务编排由 JobService 负责。
    """

    @staticmethod
    async def insert(session: AsyncSession, job: Job) -> Job:
        """INSERT 作业记录。"""
        session.add(job)
        await session.flush()
        return job

    @staticmethod
    async def get(session: AsyncSession, job_id: UUID) -> Job | None:
        """按 ID 查询作业。"""
        result = await session.execute(sa.select(Job).where(Job.id == job_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_idempotency(
        session: AsyncSession,
        organization_id: UUID,
        idempotency_key: str,
    ) -> Job | None:
        """按幂等键查询作业。"""
        result = await session.execute(
            sa.select(Job).where(
                Job.organization_id == organization_id,
                Job.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_status(
        session: AsyncSession,
        job_id: UUID,
        status: JobStatus,
        result: dict[str, Any] | None = None,
        last_error: dict[str, Any] | None = None,
        expected_lock_version: int | None = None,
    ) -> bool:
        """更新作业状态（乐观锁）。

        使用 lock_version 进行乐观锁控制：仅当 expected_lock_version
        匹配时才更新，防止重复提交覆盖结果。
        """
        values: dict[str, Any] = {
            "status": status.value,
            "updated_at": sa.func.now(),
            "lock_version": Job.lock_version + 1,
        }
        if result is not None:
            values["result"] = result
        if last_error is not None:
            values["last_error"] = last_error

        conditions: list[Any] = [Job.id == job_id]
        if expected_lock_version is not None:
            conditions.append(Job.lock_version == expected_lock_version)

        # 终态不可再更新（幂等保护）
        if status in TERMINAL_STATUSES:
            conditions.append(~Job.status.in_([s.value for s in TERMINAL_STATUSES]))

        stmt = sa.update(Job).values(**values).where(*conditions)
        exec_result: CursorResult[Any] = await session.execute(stmt)  # type: ignore[assignment]
        return exec_result.rowcount > 0

    @staticmethod
    async def acquire_lease(
        session: AsyncSession,
        job_id: UUID,
        owner: str,
        expires_at: datetime,
    ) -> bool:
        """获取作业租约（条件 UPDATE）。

        仅当以下条件全部满足时才获取成功：
        1. 作业状态在可获取集合中（accepted/queued/retry_wait）；
        2. 租约可用（lease_owner IS NULL 或 lease_expires_at < now）。

        获取成功后状态变为 running。
        """
        now = sa.func.now()
        leaseable_values = [s.value for s in LEASEABLE_STATUSES]

        stmt = (
            sa.update(Job)
            .values(
                lease_owner=owner,
                lease_expires_at=expires_at,
                status=JobStatus.RUNNING.value,
                updated_at=now,
                lock_version=Job.lock_version + 1,
            )
            .where(
                Job.id == job_id,
                Job.status.in_(leaseable_values),
                sa.or_(
                    Job.lease_owner.is_(None),
                    Job.lease_expires_at < now,
                ),
            )
        )
        exec_result: CursorResult[Any] = await session.execute(stmt)  # type: ignore[assignment]
        return exec_result.rowcount > 0

    @staticmethod
    async def renew_lease(
        session: AsyncSession,
        job_id: UUID,
        owner: str,
        new_expires_at: datetime,
    ) -> bool:
        """续租租约。"""
        stmt = (
            sa.update(Job)
            .values(
                lease_expires_at=new_expires_at,
                updated_at=sa.func.now(),
            )
            .where(
                Job.id == job_id,
                Job.lease_owner == owner,
            )
        )
        exec_result: CursorResult[Any] = await session.execute(stmt)  # type: ignore[assignment]
        return exec_result.rowcount > 0

    @staticmethod
    async def release_lease(
        session: AsyncSession,
        job_id: UUID,
        owner: str,
    ) -> None:
        """释放租约。"""
        await session.execute(
            sa.update(Job)
            .values(
                lease_owner=None,
                lease_expires_at=None,
                updated_at=sa.func.now(),
            )
            .where(
                Job.id == job_id,
                Job.lease_owner == owner,
            )
        )

    @staticmethod
    async def reap_expired_leases(
        session: AsyncSession,
        now: datetime,
    ) -> list[UUID]:
        """回收过期租约，将 running 状态的过期作业重新入队。"""
        result = await session.execute(
            sa.select(Job.id).where(
                Job.status == JobStatus.RUNNING.value,
                Job.lease_expires_at < now,
            )
        )
        job_ids: list[UUID] = [row[0] for row in result.all()]

        if job_ids:
            await session.execute(
                sa.update(Job)
                .values(
                    status=JobStatus.QUEUED.value,
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=sa.func.now(),
                    lock_version=Job.lock_version + 1,
                )
                .where(
                    Job.id.in_(job_ids),
                    Job.status == JobStatus.RUNNING.value,
                )
            )

        return job_ids

    @staticmethod
    async def increment_attempt(
        session: AsyncSession,
        job_id: UUID,
    ) -> None:
        """递增作业尝试次数。"""
        await session.execute(
            sa.update(Job)
            .values(
                attempt=Job.attempt + 1,
                updated_at=sa.func.now(),
            )
            .where(Job.id == job_id)
        )

    @staticmethod
    async def set_run_after(
        session: AsyncSession,
        job_id: UUID,
        run_after: datetime,
    ) -> None:
        """设置重试退避时间。"""
        await session.execute(
            sa.update(Job)
            .values(
                run_after=run_after,
                status=JobStatus.RETRY_WAIT.value,
                updated_at=sa.func.now(),
            )
            .where(Job.id == job_id)
        )
