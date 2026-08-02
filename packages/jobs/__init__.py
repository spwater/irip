"""IRIP 可靠作业运行时包。

Phase V0 T07: 可靠异步作业 + PostgreSQL Outbox + Worker 租约 + 幂等。

提供：
- JobStatus / Job / JobRef / WorkerLease / JobResult: 作业实体与值对象；
- JobRepository: 作业持久化（accept / get / lease / reap）；
- OutboxEvent / OutboxDispatcher: 事务性 Outbox 模式；
- JobService: 作业业务编排（accept / request_cancel）；
- WorkerLeaseManager: 租约管理（acquire / heartbeat / release / reap）。

核心设计（docs/arch-v0.md §4.2 时序图 + §7.6 异步与事务约定）：
- Job + Outbox 同事务插入；
- Worker 租约 30s TTL + 10s 心跳；
- 幂等键 UNIQUE(department_id, idempotency_key)。
"""

from packages.jobs.entities import (
    Job,
    JobRef,
    JobResult,
    JobStatus,
    WorkerLease,
)
from packages.jobs.outbox import OutboxDispatcher, OutboxEvent
from packages.jobs.repository import JobRepository
from packages.jobs.service import JobService
from packages.jobs.worker import WorkerLeaseManager

__all__ = [
    "Job",
    "JobRef",
    "JobRepository",
    "JobResult",
    "JobService",
    "JobStatus",
    "OutboxDispatcher",
    "OutboxEvent",
    "WorkerLease",
    "WorkerLeaseManager",
]
