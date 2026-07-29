"""Worker 租约管理器 + 作业执行入口。

核心设计（docs/arch-v0.md §4.2 时序图 + §7.6 异步与事务约定）：
- 租约 TTL 30s，心跳间隔 10s；
- acquire: 条件 UPDATE 获取租约（失败则丢弃任务，Redis 重投）；
- heartbeat: 延长租约过期时间；
- release: 清除租约；
- reap_expired: 回收过期租约，重新入队。

作业执行入口 execute_job:
  1. acquire 租约；
  2. 执行作业处理器（kind → handler 映射）；
  3. 乐观锁提交结果（lock_version 匹配才更新）；
  4. release 租约。
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import Clock, SystemClock
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.jobs.entities import (
    TERMINAL_STATUSES,
    Job,
    JobResult,
    JobStatus,
)
from packages.jobs.repository import JobRepository

logger = logging.getLogger(__name__)

#: 租约 TTL（秒）。
LEASE_TTL_SECONDS: int = 30

#: 心跳间隔（秒）。
HEARTBEAT_INTERVAL_SECONDS: int = 10

#: 作业处理器类型：async (Job) -> dict[str, Any]
JobHandler = Callable[[Job], Awaitable[dict[str, Any]]]


class WorkerLeaseManager:
    """Worker 租约管理器。

    管理作业租约的获取、续租、释放和回收。
    每个操作在独立事务中执行，确保租约状态与作业执行解耦。

    Attributes:
        _factory: 异步会话工厂。
        _clock: 时钟实例。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock | None = None,
    ) -> None:
        """初始化租约管理器。

        Args:
            session_factory: 异步会话工厂。
            clock: 时钟（默认 SystemClock）。
        """
        self._factory = session_factory
        self._clock = clock or SystemClock()

    async def acquire(
        self,
        job_id: UUID,
        owner: str,
        ttl_seconds: int = LEASE_TTL_SECONDS,
    ) -> bool:
        """获取作业租约。

        条件 UPDATE：仅当作业可获取（非终态、租约可用）时才成功。

        Args:
            job_id: 作业 UUID。
            owner: worker ID。
            ttl_seconds: 租约 TTL（秒），默认 30。

        Returns:
            bool: 获取成功返回 True，否则 False。
        """
        expires_at = self._clock.now() + timedelta(seconds=ttl_seconds)

        async with session_scope(self._factory) as session:
            acquired = await JobRepository.acquire_lease(session, job_id, owner, expires_at)
            return acquired

    async def heartbeat(
        self,
        job_id: UUID,
        owner: str,
        ttl_seconds: int = LEASE_TTL_SECONDS,
    ) -> bool:
        """续租租约（心跳）。

        Args:
            job_id: 作业 UUID。
            owner: 持有租约的 worker ID。
            ttl_seconds: 新的 TTL（秒）。

        Returns:
            bool: 续租成功返回 True，否则 False（owner 不匹配）。
        """
        new_expires_at = self._clock.now() + timedelta(seconds=ttl_seconds)

        async with session_scope(self._factory) as session:
            renewed = await JobRepository.renew_lease(session, job_id, owner, new_expires_at)
            return renewed

    async def release(
        self,
        job_id: UUID,
        owner: str,
    ) -> None:
        """释放租约。

        Args:
            job_id: 作业 UUID。
            owner: 持有租约的 worker ID。
        """
        async with session_scope(self._factory) as session:
            await JobRepository.release_lease(session, job_id, owner)

    async def reap_expired(self) -> list[UUID]:
        """回收过期租约。

        将 running 状态且租约过期的作业重新入队（status→queued）。

        Returns:
            list[UUID]: 被回收的作业 ID 列表。
        """
        now = self._clock.now()

        async with session_scope(self._factory) as session:
            job_ids = await JobRepository.reap_expired_leases(session, now)
            return job_ids


class JobExecutor:
    """作业执行器。

    负责执行单个作业：获取租约 → 执行处理器 → 提交结果 → 释放租约。
    支持重试和取消。

    Attributes:
        _lease_manager: 租约管理器。
        _factory: 异步会话工厂。
        _handlers: 作业类型 → 处理器映射。
    """

    def __init__(
        self,
        lease_manager: WorkerLeaseManager,
        session_factory: async_sessionmaker[AsyncSession],
        handlers: dict[str, JobHandler] | None = None,
    ) -> None:
        """初始化作业执行器。

        Args:
            lease_manager: 租约管理器。
            session_factory: 异步会话工厂。
            handlers: 作业类型 → 处理器映射（None 时使用空映射，未知 kind 将失败）。
        """
        self._lease_manager = lease_manager
        self._factory = session_factory
        self._handlers: dict[str, JobHandler] = handlers or {}

    def register_handler(self, kind: str, handler: JobHandler) -> None:
        """注册作业处理器。

        Args:
            kind: 作业类型。
            handler: 异步处理函数 (Job) -> dict。
        """
        self._handlers[kind] = handler

    async def execute(
        self,
        job_id: UUID,
        owner: str,
    ) -> JobResult | None:
        """执行作业（幂等）。

        流程：
        1. 获取租约（失败则返回 None，表示他人在跑或终态）；
        2. 读取作业（检查是否已终态 → 跳过）；
        3. 执行处理器；
        4. 乐观锁提交结果（lock_version 不匹配 → 重复提交，no-op）；
        5. 释放租约。

        Args:
            job_id: 作业 UUID。
            owner: worker ID。

        Returns:
            JobResult | None: 执行结果（None 表示未获取租约或已终态）。
        """
        # Step 1: 获取租约
        acquired = await self._lease_manager.acquire(job_id, owner)
        if not acquired:
            return None

        try:
            # Step 2: 读取作业
            async with session_scope(self._factory) as session:
                job: Job | None = await JobRepository.get(session, job_id)
                if job is None:
                    return None

                current_status = JobStatus(job.status)
                if current_status in TERMINAL_STATUSES:
                    # 已终态，无需执行（幂等保护）
                    return JobResult(
                        job_id=job_id,
                        status=current_status,
                        payload=job.result,
                        last_error=job.last_error,
                    )

                lock_version: int = job.lock_version
                kind: str = job.kind
                attempt: int = job.attempt
                max_attempts: int = job.max_attempts

            # Step 3: 执行处理器
            handler = self._handlers.get(kind)
            if handler is None:
                # 未知作业类型直接失败（F-04 §8.5：禁止 echo fallback）
                error = AppError(
                    code="unknown_job_kind",
                    message=f"未注册的作业类型: {kind}",
                    retryable=False,
                    fields={"kind": kind},
                )
                await self._commit_failure(job_id, lock_version, error, attempt, max_attempts)
                return JobResult(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    last_error=error.to_dict(),
                )

            try:
                result_data = await handler(job)
            except AppError as exc:
                # 不可重试的错误
                await self._commit_failure(job_id, lock_version, exc, attempt, max_attempts)
                return JobResult(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    last_error=exc.to_dict(),
                )
            except Exception as exc:
                # 可重试的错误
                should_retry = attempt + 1 < max_attempts
                if should_retry:
                    await self._commit_retry(job_id, lock_version, exc, attempt, max_attempts)
                    return JobResult(
                        job_id=job_id,
                        status=JobStatus.RETRY_WAIT,
                        last_error={
                            "code": "transient_error",
                            "message": str(exc),
                        },
                    )
                else:
                    await self._commit_failure(
                        job_id,
                        lock_version,
                        AppError(
                            code="max_retries_exceeded",
                            message=f"已达最大重试次数: {max_attempts}",
                            retryable=False,
                        ),
                        attempt,
                        max_attempts,
                    )
                    return JobResult(
                        job_id=job_id,
                        status=JobStatus.FAILED,
                        last_error={
                            "code": "max_retries_exceeded",
                            "message": str(exc),
                        },
                    )

            # Step 4: 乐观锁提交结果
            committed = await self._commit_success(job_id, lock_version, result_data)

            if committed:
                return JobResult(
                    job_id=job_id,
                    status=JobStatus.SUCCEEDED,
                    payload=result_data,
                )
            else:
                # lock_version 不匹配 → 重复提交，读取当前结果
                async with session_scope(self._factory) as session:
                    existing: Job | None = await JobRepository.get(session, job_id)
                    if existing is not None:
                        return JobResult(
                            job_id=job_id,
                            status=JobStatus(existing.status),
                            payload=existing.result,
                            last_error=existing.last_error,
                        )
                return None

        finally:
            # Step 5: 释放租约
            await self._lease_manager.release(job_id, owner)

    async def _commit_success(
        self,
        job_id: UUID,
        lock_version: int,
        result: dict[str, Any],
    ) -> bool:
        """乐观锁提交成功结果。

        Args:
            job_id: 作业 UUID。
            lock_version: 期望的锁版本。
            result: 结果数据。

        Returns:
            bool: 提交成功返回 True，lock_version 不匹配返回 False。
        """
        async with session_scope(self._factory) as session:
            committed = await JobRepository.update_status(
                session,
                job_id,
                JobStatus.SUCCEEDED,
                result=result,
                expected_lock_version=lock_version,
            )
            return committed

    async def _commit_failure(
        self,
        job_id: UUID,
        lock_version: int,
        error: AppError,
        attempt: int,
        max_attempts: int,
    ) -> None:
        """提交失败结果（不可重试）。

        Args:
            job_id: 作业 UUID。
            lock_version: 期望的锁版本。
            error: 应用错误。
            attempt: 当前尝试次数。
            max_attempts: 最大尝试次数。
        """
        async with session_scope(self._factory) as session:
            await JobRepository.update_status(
                session,
                job_id,
                JobStatus.FAILED,
                last_error=error.to_dict(),
                expected_lock_version=lock_version,
            )

    async def _commit_retry(
        self,
        job_id: UUID,
        lock_version: int,
        error: Exception,
        attempt: int,
        max_attempts: int,
    ) -> None:
        """提交重试状态。

        Args:
            job_id: 作业 UUID。
            lock_version: 期望的锁版本。
            error: 异常。
            attempt: 当前尝试次数。
            max_attempts: 最大尝试次数。
        """
        clock = SystemClock()
        backoff = timedelta(seconds=2**attempt)

        async with session_scope(self._factory) as session:
            await session.execute(
                sa.update(Job)
                .values(
                    status=JobStatus.RETRY_WAIT.value,
                    attempt=attempt + 1,
                    run_after=clock.now() + backoff,
                    last_error={
                        "code": "transient_error",
                        "message": str(error),
                    },
                    updated_at=sa.func.now(),
                    lock_version=Job.lock_version + 1,
                )
                .where(
                    Job.id == job_id,
                    Job.lock_version == lock_version,
                )
            )
