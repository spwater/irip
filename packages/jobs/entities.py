"""作业实体：JobStatus 枚举 + Job ORM + JobRef / WorkerLease / JobResult 值对象。

对应 job 表（docs/arch-v0.md §3.1 第 300-319 行）。
T01（迁移 0001）已创建 job 根表骨架（含全部字段），本模块提供 ORM 映射与值对象。

状态机（docs/arch-v0.md §4.2）：
    accepted → queued → running → succeeded / failed / retry_wait
                                    ↑                ↓
                                    └─── reaper ──────┘
    cancel_requested → cancelled
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class JobStatus(StrEnum):
    """作业状态枚举。"""

    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


#: 终态集合（不可再转移）。
TERMINAL_STATUSES: frozenset[JobStatus] = frozenset({
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
})

#: 可被 worker 获取租约的状态集合（非终态且可执行）。
LEASEABLE_STATUSES: frozenset[JobStatus] = frozenset({
    JobStatus.ACCEPTED,
    JobStatus.QUEUED,
    JobStatus.RETRY_WAIT,
})


class Job(Base):
    """异步作业 ORM 模型（对应 job 表）。

    Attributes:
        id: 作业 UUID（PK）。
        organization_id: 所属组织 ID。
        kind: 作业类型（如 ``echo``、``parse_excel``）。
        status: 作业状态（JobStatus 枚举值）。
        payload: 输入快照（JSONB）。
        idempotency_key: 幂等键（与 organization_id 组成 UNIQUE）。
        attempt: 当前尝试次数。
        max_attempts: 最大重试次数。
        run_after: 重试退避时间（到该时间后才可被获取）。
        lease_owner: 当前持有租约的 worker ID。
        lease_expires_at: 租约过期时间。
        result: 终态结果（JSONB，成功时填充）。
        last_error: AppError 序列化（JSONB，失败时填充）。
        created_by: 创建者用户 ID（FK→app_user.id）。
        created_at: 创建时间。
        updated_at: 更新时间。
        lock_version: 乐观锁版本号。
    """

    __tablename__ = "job"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    attempt: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0"), nullable=False
    )
    max_attempts: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("3"), nullable=False
    )
    run_after: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    last_error: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    created_by: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0"), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"Job(id={self.id!r}, kind={self.kind!r}, "
            f"status={self.status!r})"
        )


@dataclass(frozen=True)
class JobRef:
    """作业引用（不可变值对象）。

    作为 JobService.accept() 等方法的返回值。

    Attributes:
        job_id: 作业 UUID。
        status: 当前状态。
        kind: 作业类型。
        stage: 当前阶段描述（V0 默认空字符串，后续任务可填充）。
        progress: 进度百分比 0-100（V0 默认 0）。
        retryable: 是否可重试（attempt < max_attempts 且非终态）。
    """

    job_id: UUID
    status: JobStatus
    kind: str
    stage: str = ""
    progress: int = 0
    retryable: bool = False


@dataclass(frozen=True)
class WorkerLease:
    """Worker 租约（不可变值对象）。

    表示一个 worker 对某作业持有的租约。

    Attributes:
        job_id: 作业 UUID。
        owner: 持有租约的 worker ID。
        expires_at: 租约过期时间。
        ttl_seconds: 租约 TTL（秒）。
    """

    job_id: UUID
    owner: str
    expires_at: datetime
    ttl_seconds: int


@dataclass(frozen=True)
class JobResult:
    """作业结果（不可变值对象）。

    表示作业的最终执行结果。

    Attributes:
        job_id: 作业 UUID。
        status: 终态状态。
        payload: 结果数据（成功时为作业输出）。
        last_error: 错误信息（失败时为 AppError 序列化）。
    """

    job_id: UUID
    status: JobStatus
    payload: dict[str, Any] | None = None
    last_error: dict[str, Any] | None = None
