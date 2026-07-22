"""作业业务服务：accept + request_cancel。

核心流程（docs/arch-v0.md §4.2 时序图）：

accept(kind, payload, idempotency_key):
  1. 检查幂等键是否已存在 → 若存在则返回已有作业（幂等）；
  2. 同事务 INSERT job(status=accepted) + INSERT outbox_event(job.accepted)；
  3. 返回 JobRef。

request_cancel(job_id, actor_id):
  1. 检查作业存在且非终态；
  2. 同事务 UPDATE job(status=cancel_requested) + INSERT outbox_event(job.cancel_requested)；
  3. 返回 JobRef。

关键约束：
- Job + Outbox 同事务插入（架构文档 §7.6）；
- 幂等键 UNIQUE(organization_id, idempotency_key)。
"""

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.clock import Clock, SystemClock
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.jobs.entities import TERMINAL_STATUSES, Job, JobRef, JobStatus
from packages.jobs.outbox import OutboxDispatcher
from packages.jobs.repository import JobRepository


class JobService:
    """作业业务编排服务。

    依赖注入 session_factory（事务管理）、organization_id（当前组织）、
    created_by（当前用户）、clock（时钟）。

    Attributes:
        _factory: 异步会话工厂。
        _org_id: 当前组织 ID。
        _created_by: 当前用户 ID。
        _clock: 时钟实例。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        organization_id: UUID,
        created_by: UUID,
        clock: Clock | None = None,
    ) -> None:
        """初始化作业服务。

        Args:
            session_factory: 异步会话工厂。
            organization_id: 当前组织 ID。
            created_by: 当前用户 ID。
            clock: 时钟（默认 SystemClock）。
        """
        self._factory = session_factory
        self._org_id = organization_id
        self._created_by = created_by
        self._clock = clock or SystemClock()

    async def accept(
        self,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> JobRef:
        """接受作业（幂等）。

        流程：
        1. 查询幂等键是否已存在 → 若存在则返回已有作业；
        2. 同事务 INSERT job + INSERT outbox_event。

        Args:
            kind: 作业类型（如 ``echo``）。
            payload: 输入载荷。
            idempotency_key: 幂等键。

        Returns:
            JobRef: 作业引用。

        Raises:
            AppError: code="validation_failed"，当 kind 为空时。
        """
        if not kind or not kind.strip():
            raise AppError(
                code="validation_failed",
                message="作业类型不能为空",
                retryable=False,
                fields={"kind": "required"},
            )

        async with session_scope(self._factory) as session:
            # 幂等检查：查询已有作业
            existing: Job | None = await JobRepository.get_by_idempotency(
                session, self._org_id, idempotency_key
            )
            if existing is not None:
                return JobRef(
                    job_id=existing.id,
                    status=JobStatus(existing.status),
                    kind=existing.kind,
                )

            # 创建新作业
            job = Job(
                id=new_id(),
                organization_id=self._org_id,
                kind=kind,
                status=JobStatus.ACCEPTED.value,
                payload=payload,
                idempotency_key=idempotency_key,
                attempt=0,
                max_attempts=3,
                created_by=self._created_by,
            )
            await JobRepository.insert(session, job)

            # 同事务插入 outbox 事件
            await OutboxDispatcher.enqueue(
                session,
                aggregate_type="job",
                aggregate_id=job.id,
                event_type="job.accepted",
                payload={
                    "job_id": str(job.id),
                    "kind": kind,
                },
            )

            return JobRef(
                job_id=job.id,
                status=JobStatus.ACCEPTED,
                kind=kind,
            )

    async def request_cancel(
        self,
        job_id: UUID,
        actor_id: UUID,
    ) -> JobRef:
        """请求取消作业。

        流程：
        1. 查询作业 → 不存在抛 not_found；
        2. 若已终态 → 抛 conflict；
        3. 同事务 UPDATE status=cancel_requested + INSERT outbox_event。

        Args:
            job_id: 作业 UUID。
            actor_id: 操作者用户 ID。

        Returns:
            JobRef: 作业引用。

        Raises:
            AppError: code="not_found"，当作业不存在时。
            AppError: code="conflict"，当作业已终态时。
        """
        async with session_scope(self._factory) as session:
            job: Job | None = await JobRepository.get(session, job_id)
            if job is None:
                raise AppError(
                    code="not_found",
                    message=f"作业不存在: {job_id}",
                    retryable=False,
                    fields={"job_id": str(job_id)},
                )

            current_status = JobStatus(job.status)
            if current_status in TERMINAL_STATUSES:
                raise AppError(
                    code="conflict",
                    message=f"作业已处于终态: {current_status.value}",
                    retryable=False,
                    fields={"status": current_status.value},
                )

            await session.execute(
                sa.update(Job)
                .values(
                    status=JobStatus.CANCEL_REQUESTED.value,
                    updated_at=sa.func.now(),
                    lock_version=Job.lock_version + 1,
                )
                .where(
                    Job.id == job_id,
                    Job.lock_version == job.lock_version,
                )
            )

            await OutboxDispatcher.enqueue(
                session,
                aggregate_type="job",
                aggregate_id=job_id,
                event_type="job.cancel_requested",
                payload={
                    "job_id": str(job_id),
                    "actor_id": str(actor_id),
                },
            )

            return JobRef(
                job_id=job_id,
                status=JobStatus.CANCEL_REQUESTED,
                kind=job.kind,
            )

    async def get(self, job_id: UUID) -> JobRef:
        """获取作业引用。

        Args:
            job_id: 作业 UUID。

        Returns:
            JobRef: 作业引用（含 stage/progress/retryable）。

        Raises:
            AppError: code="not_found"，当作业不存在时。
        """
        async with session_scope(self._factory) as session:
            job: Job | None = await JobRepository.get(session, job_id)
            if job is None:
                raise AppError(
                    code="not_found",
                    message=f"作业不存在: {job_id}",
                    retryable=False,
                    fields={"job_id": str(job_id)},
                )
            status = JobStatus(job.status)
            retryable = (
                job.attempt < job.max_attempts
                and status not in TERMINAL_STATUSES
            )
            stage = job.last_error.get("stage", "") if job.last_error else ""
            progress = 100 if status in TERMINAL_STATUSES else (50 if status == JobStatus.RUNNING else 0)
            return JobRef(
                job_id=job.id,
                status=status,
                kind=job.kind,
                stage=stage,
                progress=progress,
                retryable=retryable,
            )

    async def list(
        self,
        status: str | None = None,
        kind: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[tuple[Job, str, int, bool]], str | None, bool]:
        """分页查询作业列表。

        按创建时间倒序排列，支持按状态和类型过滤。

        Args:
            status: 状态过滤（如 ``running``、``succeeded``）。
            kind: 类型过滤（如 ``echo``、``audit_export``）。
            cursor: 分页游标（上一页最后一条的 created_at ISO 字符串）。
            limit: 每页数量。

        Returns:
            tuple: (items, next_cursor, has_more)
              - items: [(job, stage, progress, retryable), ...] 元组列表
              - next_cursor: 下一页游标（无更多数据时为 None）
              - has_more: 是否还有更多数据
        """
        from datetime import datetime

        conditions: list[Any] = [Job.organization_id == self._org_id]

        if status is not None:
            conditions.append(Job.status == status)

        if kind is not None:
            conditions.append(Job.kind == kind)

        if cursor is not None:
            try:
                cursor_dt = datetime.fromisoformat(cursor)
            except ValueError as exc:
                raise AppError(
                    code="invalid_cursor",
                    message="无效的分页游标",
                    retryable=False,
                    fields={"cursor": cursor},
                ) from exc
            conditions.append(Job.created_at < cursor_dt)

        async with self._factory() as session:
            stmt = (
                sa.select(Job)
                .where(*conditions)
                .order_by(Job.created_at.desc())
                .limit(limit + 1)
            )
            result = await session.execute(stmt)
            rows: list[Job] = list(result.scalars().all())

        has_more: bool = len(rows) > limit
        page_rows: list[Job] = rows[:limit]
        next_cursor: str | None = None
        if has_more and page_rows:
            next_cursor = page_rows[-1].created_at.isoformat()

        items: list[tuple[Job, str, int, bool]] = []
        for job in page_rows:
            job_status = JobStatus(job.status)
            retryable = (
                job.attempt < job.max_attempts
                and job_status not in TERMINAL_STATUSES
            )
            stage = job.last_error.get("stage", "") if job.last_error else ""
            progress = (
                100
                if job_status in TERMINAL_STATUSES
                else (50 if job_status == JobStatus.RUNNING else 0)
            )
            items.append((job, stage, progress, retryable))

        return items, next_cursor, has_more

    async def get_raw(self, job_id: UUID) -> Job:
        """获取作业原始 ORM 实体（含 payload、result、last_error 等全字段）。

        与 ``get()`` 不同，此方法返回完整的 Job ORM 对象，
        用于作业详情页展示输入载荷、执行结果和错误日志。

        Args:
            job_id: 作业 UUID。

        Returns:
            Job: 作业 ORM 实体。

        Raises:
            AppError: code="not_found"，当作业不存在时。
        """
        async with self._factory() as session:
            job: Job | None = await JobRepository.get(session, job_id)
            if job is None:
                raise AppError(
                    code="not_found",
                    message=f"作业不存在: {job_id}",
                    retryable=False,
                    fields={"job_id": str(job_id)},
                )
            return job
