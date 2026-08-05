"""Worker 租约管理器 + 作业执行入口。

核心设计（docs/arch-v0.md §4.2 时序图 + §7.6 异步与事务约定）：
- 租约 TTL 30s，心跳间隔 10s；
- acquire: 条件 UPDATE 获取租约（失败则丢弃任务，Redis 重投）；
- acquire_with_fencing: 条件 UPDATE + RETURNING lock_version（H-03 fencing token）；
- heartbeat: 延长租约过期时间；
- release: 清除租约；
- reap_expired: 回收过期租约，重新入队。

作业执行入口 execute_job（H-03 增强）:
  1. acquire_with_fencing 获取租约 + fencing token；
  2. 启动独立心跳任务（asyncio.create_task）；
  3. 执行作业处理器（kind -> handler 映射）；
  4. 乐观锁提交结果（fencing token 匹配才更新）；
  5. 取消心跳任务 + release 租约。
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import Clock, SystemClock
from packages.common.errors import AppError
from packages.common.tenant_guc import set_dept_guc, set_user_guc
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


@asynccontextmanager
async def _session_scope_with_dept(
    factory: async_sessionmaker[AsyncSession],
    dept_id: UUID | None = None,
    user_id: UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    """带 GUC 的 session_scope（阶段2 worker 专用）。

    Worker 不持有 Principal，但需要为 RLS 设置 dept GUC。
    从 job 记录中读取 department_id / created_by 后传入。

    Args:
        factory: 异步会话工厂。
        dept_id: 作业所属部门 ID（None 时 fail-closed）。
        user_id: 作业创建者 ID（可选）。

    Yields:
        AsyncSession: 已设置 GUC 的异步会话。
    """
    async with factory() as session:
        async with session.begin():
            await set_dept_guc(session, dept_id)
            await set_user_guc(session, user_id)
            yield session


class WorkerLeaseManager:
    """Worker 租约管理器。

    管理作业租约的获取、续租、释放和回收。
    每个操作在独立事务中执行，确保租约状态与作业执行解耦。

    阶段2 RLS 通电：所有 session 操作使用 _session_scope_with_dept 设置 GUC，
    确保 RLS 策略不拦截 job 表操作。default_dept_id / default_user_id
    在 Worker 启动时从环境变量注入（system 哨兵部门 + system_service 用户，
    后者挂 root 部门以获得全部门可见性）。

    Attributes:
        _factory: 异步会话工厂。
        _clock: 时钟实例。
        _default_dept_id: 默认部门 ID（RLS GUC）。
        _default_user_id: 默认用户 ID（RLS GUC）。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock | None = None,
        default_dept_id: UUID | None = None,
        default_user_id: UUID | None = None,
    ) -> None:
        """初始化租约管理器。

        Args:
            session_factory: 异步会话工厂。
            clock: 时钟（默认 SystemClock）。
            default_dept_id: 默认部门 ID（用于 RLS GUC，通常为 system 哨兵部门）。
            default_user_id: 默认用户 ID（用于 RLS GUC，通常为 system_service 用户）。
        """
        self._factory = session_factory
        self._clock = clock or SystemClock()
        self._default_dept_id = default_dept_id
        self._default_user_id = default_user_id

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

        async with _session_scope_with_dept(
            self._factory, self._default_dept_id, self._default_user_id
        ) as session:
            acquired = await JobRepository.acquire_lease(session, job_id, owner, expires_at)
            return acquired

    async def acquire_with_fencing(
        self,
        job_id: UUID,
        owner: str,
        ttl_seconds: int = LEASE_TTL_SECONDS,
    ) -> tuple[bool, int]:
        """获取作业租约并返回 fencing token（H-03）。

        与 acquire 相同，但通过 RETURNING 子句返回获取后的 lock_version
        作为 fencing token。fencing token 用于提交结果时的乐观锁校验。

        Args:
            job_id: 作业 UUID。
            owner: worker ID。
            ttl_seconds: 租约 TTL（秒），默认 30。

        Returns:
            tuple[bool, int]: (是否获取成功, fencing token)。
        """
        expires_at = self._clock.now() + timedelta(seconds=ttl_seconds)

        async with _session_scope_with_dept(
            self._factory, self._default_dept_id, self._default_user_id
        ) as session:
            acquired, fencing_token = await JobRepository.acquire_lease_with_fencing(
                session, job_id, owner, expires_at
            )
            return acquired, fencing_token

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

        async with _session_scope_with_dept(
            self._factory, self._default_dept_id, self._default_user_id
        ) as session:
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
        async with _session_scope_with_dept(
            self._factory, self._default_dept_id, self._default_user_id
        ) as session:
            await JobRepository.release_lease(session, job_id, owner)

    async def reap_expired(self) -> list[UUID]:
        """回收过期租约并重新投递（H-03）。

        将 running 状态且租约过期的作业重新入队（status->queued），
        并同事务创建 outbox 事件确保 Dispatcher 重新投递。

        使用 default GUC（system_service 用户挂 root 部门 → 全部门可见），
        确保 RLS 不拦截跨部门作业的回收。

        Returns:
            list[UUID]: 被回收的作业 ID 列表。
        """
        now = self._clock.now()

        async with _session_scope_with_dept(
            self._factory, self._default_dept_id, self._default_user_id
        ) as session:
            job_ids = await JobRepository.reap_and_redeliver(session, now)
            return job_ids


class JobExecutor:
    """作业执行器。

    负责执行单个作业：获取租约 → 执行处理器 → 提交结果 → 释放租约。
    支持重试和取消。

    阶段2 RLS 通电：default_dept_id / default_user_id 在 Worker 启动时注入，
    用于 step 3（读取作业）和 lease 操作的 GUC 设置。读取作业后，
    后续操作使用作业自身的 department_id / created_by 作为 GUC。

    Attributes:
        _lease_manager: 租约管理器。
        _factory: 异步会话工厂。
        _handlers: 作业类型 → 处理器映射。
        _default_dept_id: 默认部门 ID（RLS GUC，system 哨兵部门）。
        _default_user_id: 默认用户 ID（RLS GUC，system_service 用户）。
    """

    def __init__(
        self,
        lease_manager: WorkerLeaseManager,
        session_factory: async_sessionmaker[AsyncSession],
        handlers: dict[str, JobHandler] | None = None,
        default_dept_id: UUID | None = None,
        default_user_id: UUID | None = None,
    ) -> None:
        """初始化作业执行器。

        Args:
            lease_manager: 租约管理器。
            session_factory: 异步会话工厂。
            handlers: 作业类型 → 处理器映射（None 时使用空映射，未知 kind 将失败）。
            default_dept_id: 默认部门 ID（RLS GUC，system 哨兵部门）。
            default_user_id: 默认用户 ID（RLS GUC，system_service 用户）。
        """
        self._lease_manager = lease_manager
        self._factory = session_factory
        self._handlers: dict[str, JobHandler] = handlers or {}
        self._default_dept_id = default_dept_id
        self._default_user_id = default_user_id

    def register_handler(self, kind: str, handler: JobHandler) -> None:
        """注册作业处理器。

        Args:
            kind: 作业类型。
            handler: 异步处理函数 (Job) -> dict。
        """
        self._handlers[kind] = handler

    async def _heartbeat_loop(
        self,
        job_id: UUID,
        owner: str,
        fencing_token: int,
    ) -> None:
        """独立心跳任务（H-03）。

        以 HEARTBEAT_INTERVAL_SECONDS 为间隔持续续租租约。
        如果续租失败（owner 不匹配，表示租约已被其他 worker 获取），
        则停止心跳。

        Args:
            job_id: 作业 UUID。
            owner: 持有租约的 worker ID。
            fencing_token: 获取租约时的 fencing token（用于日志追踪）。
        """
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            renewed = await self._lease_manager.heartbeat(job_id, owner)
            if not renewed:
                logger.warning(
                    "Heartbeat lost for job %s (owner=%s, fencing=%d)",
                    job_id,
                    owner,
                    fencing_token,
                )
                break

    async def execute(
        self,
        job_id: UUID,
        owner: str,
    ) -> JobResult | None:
        """执行作业（幂等）。

        H-03 增强流程：
        1. acquire_with_fencing 获取租约 + fencing token（失败则返回 None）；
        2. 启动独立心跳任务（asyncio.create_task）；
        3. 读取作业（检查是否已终态 -> 跳过）；
        4. 执行处理器；
        5. 乐观锁提交结果（fencing token 匹配才更新）；
        6. 取消心跳任务 + 释放租约。

        Args:
            job_id: 作业 UUID。
            owner: worker ID。

        Returns:
            JobResult | None: 执行结果（None 表示未获取租约或已终态）。
        """
        # Step 1: 获取租约 + fencing token（H-03）
        acquired, fencing_token = await self._lease_manager.acquire_with_fencing(job_id, owner)
        if not acquired:
            return None

        # Step 2: 启动独立心跳任务（H-03）
        heartbeat_task: asyncio.Task[None] = asyncio.create_task(
            self._heartbeat_loop(job_id, owner, fencing_token)
        )

        try:
            # Step 3: 读取作业（使用 default GUC，system_service 用户挂 root → 全部门可见）
            async with _session_scope_with_dept(
                self._factory, self._default_dept_id, self._default_user_id
            ) as session:
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

                lock_version: int = fencing_token
                kind: str = job.kind
                attempt: int = job.attempt
                max_attempts: int = job.max_attempts
                # 阶段2: 从作业记录读取 department_id 和 created_by 用于 GUC
                dept_id: UUID | None = job.department_id
                job_user_id: UUID | None = job.created_by

            # Step 4: 执行处理器
            handler = self._handlers.get(kind)
            if handler is None:
                # 未知作业类型直接失败（F-04 §8.5：禁止 echo fallback）
                error = AppError(
                    code="unknown_job_kind",
                    message=f"未注册的作业类型: {kind}",
                    retryable=False,
                    fields={"kind": kind},
                )
                await self._commit_failure(
                    job_id, lock_version, error, attempt, max_attempts, dept_id, job_user_id
                )
                return JobResult(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    last_error=error.to_dict(),
                )

            try:
                result_data = await handler(job)
            except AppError as exc:
                # 不可重试的错误
                await self._commit_failure(
                    job_id, lock_version, exc, attempt, max_attempts, dept_id, job_user_id
                )
                return JobResult(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    last_error=exc.to_dict(),
                )
            except Exception as exc:
                # 可重试的错误
                should_retry = attempt + 1 < max_attempts
                if should_retry:
                    await self._commit_retry(
                        job_id, lock_version, exc, attempt, max_attempts, dept_id, job_user_id
                    )
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
                        dept_id,
                        job_user_id,
                    )
                    return JobResult(
                        job_id=job_id,
                        status=JobStatus.FAILED,
                        last_error={
                            "code": "max_retries_exceeded",
                            "message": str(exc),
                        },
                    )

            # Step 5: 乐观锁提交结果（使用 fencing token）
            committed = await self._commit_success(
                job_id, lock_version, result_data, dept_id, job_user_id
            )

            if committed:
                return JobResult(
                    job_id=job_id,
                    status=JobStatus.SUCCEEDED,
                    payload=result_data,
                )
            else:
                # lock_version 不匹配 -> 重复提交，读取当前结果
                async with _session_scope_with_dept(self._factory, dept_id, job_user_id) as session:
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
            # Step 6: 取消心跳任务 + 释放租约
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await self._lease_manager.release(job_id, owner)

    async def _commit_success(
        self,
        job_id: UUID,
        lock_version: int,
        result: dict[str, Any],
        dept_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> bool:
        """乐观锁提交成功结果。

        Args:
            job_id: 作业 UUID。
            lock_version: 期望的锁版本。
            result: 结果数据。
            dept_id: 作业所属部门 ID（用于设置 GUC）。
            user_id: 作业创建者 ID（用于设置 GUC）。

        Returns:
            bool: 提交成功返回 True，lock_version 不匹配返回 False。
        """
        async with _session_scope_with_dept(self._factory, dept_id, user_id) as session:
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
        dept_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> None:
        """提交失败结果（不可重试）。

        Args:
            job_id: 作业 UUID。
            lock_version: 期望的锁版本。
            error: 应用错误。
            attempt: 当前尝试次数。
            max_attempts: 最大尝试次数。
            dept_id: 作业所属部门 ID（用于设置 GUC）。
            user_id: 作业创建者 ID（用于设置 GUC）。
        """
        async with _session_scope_with_dept(self._factory, dept_id, user_id) as session:
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
        dept_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> None:
        """提交重试状态（H-03: 同事务创建 outbox 事件重新投递）。

        Args:
            job_id: 作业 UUID。
            lock_version: 期望的锁版本（fencing token）。
            error: 异常。
            attempt: 当前尝试次数。
            max_attempts: 最大尝试次数。
            dept_id: 作业所属部门 ID（用于设置 GUC）。
            user_id: 作业创建者 ID（用于设置 GUC）。
        """
        clock = SystemClock()
        backoff = timedelta(seconds=2**attempt)

        async with _session_scope_with_dept(self._factory, dept_id, user_id) as session:
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
            # H-03: 同事务创建 outbox 事件，确保 Dispatcher 在 run_after 后重新投递
            from packages.jobs.outbox import OutboxEvent

            event = OutboxEvent(
                aggregate_type="job",
                aggregate_id=job_id,
                event_type="job.retry_wait",
                payload={"attempt": attempt + 1, "reason": str(error)[:500]},
            )
            session.add(event)
            await session.flush()
