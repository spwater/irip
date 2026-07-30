"""备份/恢复 API 路由：触发备份/恢复异步作业 + 查询作业状态。

端点（IRIP V3-T03）：
  POST   /api/v1/backups                  — 创建备份作业（异步）
  GET    /api/v1/backups                  — 列出备份作业
  GET    /api/v1/backups/{id}              — 备份/恢复作业详情
  POST   /api/v1/backups/{id}/restore      — 创建恢复作业（异步）

安全约定：
- 全部端点需 Authorization: Bearer <jwt> + require_permission("system:manage")；
- 错误响应统一格式：{"error": {"code", "message", "retryable", "fields"}}。

异步作业模式：
- 备份/恢复通过 JobService 提交异步作业（kind="backup" / "restore"）；
- Worker 在后台执行 backup.py / restore.py 脚本；
- 作业状态通过 GET 端点轮询。
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.jobs.entities import Job, JobStatus
from packages.jobs.outbox import OutboxDispatcher

#: 路由实例。
backups_router = APIRouter(prefix="/api/v1/backups", tags=["backups"])

#: 需 system:manage 权限的当前用户依赖。
SystemManageDep = Annotated[CurrentUser, Depends(require_permission("system:manage"))]

#: 备份作业 kind 常量。
BACKUP_JOB_KIND: str = "backup"

#: 恢复作业 kind 常量。
RESTORE_JOB_KIND: str = "restore"

#: 备份/恢复作业 kind 集合。
BACKUP_RESTORE_KINDS: frozenset[str] = frozenset({BACKUP_JOB_KIND, RESTORE_JOB_KIND})


# ---- 依赖占位（由应用启动或测试覆盖）----


def get_backups_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取数据库会话工厂（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_backups_session_factory must be overridden via dependency_overrides"
    )


BackupsSessionFactoryDep = Annotated[
    async_sessionmaker[AsyncSession], Depends(get_backups_session_factory)
]


# ---- 请求/响应模型 ----


class CreateBackupRequest(BaseModel):
    """创建备份作业请求体。"""

    output_dir: str | None = Field(None, description="备份输出目录（留空则使用默认路径）")
    encrypt: bool = Field(False, description="是否加密备份包（需配置 age recipient）")


class CreateRestoreRequest(BaseModel):
    """创建恢复作业请求体。"""

    backup_dir: str = Field(..., description="备份目录路径（含 manifest.json）")
    skip_migrations: bool = Field(False, description="是否跳过迁移步骤")


class BackupJobResponse(BaseModel):
    """备份/恢复作业响应体。"""

    job_id: str
    status: str
    kind: str
    created_at: datetime
    created_by: str | None


class BackupJobListResponse(BaseModel):
    """备份/恢复作业分页列表响应。"""

    items: list[BackupJobResponse]
    next_cursor: str | None
    has_more: bool


class BackupJobDetailResponse(BackupJobResponse):
    """备份/恢复作业详情响应体。"""

    payload: dict[str, object] | None
    result: dict[str, object] | None
    last_error: dict[str, object] | None
    attempt: int
    max_attempts: int


# ---- 辅助函数 ----


def _to_job_response(job: Job) -> BackupJobResponse:
    """将 Job ORM 实体转换为响应模型。"""
    return BackupJobResponse(
        job_id=str(job.id),
        status=job.status,
        kind=job.kind,
        created_at=job.created_at,
        created_by=str(job.created_by) if job.created_by is not None else None,
    )


def _to_job_detail_response(job: Job) -> BackupJobDetailResponse:
    """将 Job ORM 实体转换为详情响应模型。"""
    return BackupJobDetailResponse(
        job_id=str(job.id),
        status=job.status,
        kind=job.kind,
        created_at=job.created_at,
        created_by=str(job.created_by) if job.created_by is not None else None,
        payload=job.payload,
        result=job.result,
        last_error=job.last_error,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
    )


# ---- 端点 ----


@backups_router.post("/", response_model=BackupJobResponse, status_code=202)
async def create_backup(
    body: CreateBackupRequest,
    current_user: SystemManageDep,
    session_factory: BackupsSessionFactoryDep,
) -> BackupJobResponse:
    """创建备份作业（异步）。

    将备份请求提交为异步作业，返回 202 Accepted + 作业 ID。
    Worker 在后台执行 backup.py 脚本。

    Args:
        body: 备份请求体。
        current_user: 当前认证用户（需 system:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        BackupJobResponse: 备份作业信息（202 Accepted）。
    """
    job_id: UUID = new_id()
    now: datetime = datetime.now(UTC)

    async with session_scope(session_factory) as session:
        job = Job(
            id=job_id,
            organization_id=current_user.organization_id
            if current_user.organization_id is not None
            else current_user.user_id,
            kind=BACKUP_JOB_KIND,
            status=JobStatus.ACCEPTED.value,
            payload={
                "output_dir": body.output_dir,
                "encrypt": body.encrypt,
                "triggered_by": str(current_user.user_id),
            },
            idempotency_key=f"backup:{job_id}",
            attempt=0,
            max_attempts=1,
            created_by=current_user.user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        await session.flush()

        await OutboxDispatcher.enqueue(
            session,
            aggregate_type="job",
            aggregate_id=job_id,
            event_type="job.accepted",
            payload={
                "job_id": str(job_id),
                "kind": BACKUP_JOB_KIND,
            },
        )

    return BackupJobResponse(
        job_id=str(job_id),
        status=JobStatus.ACCEPTED.value,
        kind=BACKUP_JOB_KIND,
        created_at=now,
        created_by=str(current_user.user_id),
    )


@backups_router.get("/", response_model=BackupJobListResponse)
async def list_backups(
    current_user: SystemManageDep,
    session_factory: BackupsSessionFactoryDep,
    kind: str | None = Query(None, description="按作业类型筛选（backup / restore）"),
    cursor: str | None = Query(None, description="分页游标（上一页最后一条的 job_id）"),
    limit: int = Query(20, ge=1, le=100, description="每页数量（最大 100）"),
) -> BackupJobListResponse:
    """列出备份/恢复作业（分页）。

    按创建时间倒序排列，每页最多 100 条。支持按 kind 过滤。

    Args:
        current_user: 当前认证用户（需 system:manage 权限）。
        session_factory: 数据库会话工厂。
        kind: 作业类型筛选（backup / restore）。
        cursor: 分页游标（上一页最后一条记录的 job_id UUID 字符串）。
        limit: 每页数量。

    Returns:
        BackupJobListResponse: 分页备份/恢复作业列表。
    """
    async with session_factory() as session:
        stmt = (
            sa.select(Job).where(Job.kind.in_(BACKUP_RESTORE_KINDS)).order_by(Job.created_at.desc())
        )

        if kind is not None:
            if kind not in BACKUP_RESTORE_KINDS:
                raise AppError(
                    code="validation_failed",
                    message=f"无效的作业类型: {kind}",
                    retryable=False,
                    fields={"kind": kind},
                )
            stmt = stmt.where(Job.kind == kind)

        if cursor is not None:
            try:
                cursor_uuid: UUID = UUID(cursor)
            except ValueError as exc:
                raise AppError(
                    code="invalid_cursor",
                    message="无效的分页游标",
                    retryable=False,
                    fields={"cursor": cursor},
                ) from exc
            stmt = stmt.where(Job.id < cursor_uuid)

        stmt = stmt.limit(limit + 1)
        result = await session.execute(stmt)
        rows: list[Job] = list(result.scalars().all())

    has_more: bool = len(rows) > limit
    page_items: list[Job] = rows[:limit]
    next_cursor: str | None = None
    if has_more and page_items:
        next_cursor = str(page_items[-1].id)

    return BackupJobListResponse(
        items=[_to_job_response(j) for j in page_items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@backups_router.get("/{job_id}", response_model=BackupJobDetailResponse)
async def get_backup_detail(
    job_id: UUID,
    current_user: SystemManageDep,
    session_factory: BackupsSessionFactoryDep,
) -> BackupJobDetailResponse:
    """获取备份/恢复作业详情。

    Args:
        job_id: 作业 UUID。
        current_user: 当前认证用户（需 system:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        BackupJobDetailResponse: 作业详情。

    Raises:
        AppError: code="not_found"，当作业不存在或非备份/恢复类型时。
    """
    async with session_factory() as session:
        job: Job | None = await session.scalar(sa.select(Job).where(Job.id == job_id))
        if job is None:
            raise AppError(
                code="not_found",
                message=f"作业不存在: {job_id}",
                retryable=False,
                fields={"job_id": str(job_id)},
            )

        if job.kind not in BACKUP_RESTORE_KINDS:
            raise AppError(
                code="not_found",
                message=f"作业 {job_id} 不是备份/恢复作业（kind={job.kind}）",
                retryable=False,
                fields={"job_id": str(job_id), "kind": job.kind},
            )

        return _to_job_detail_response(job)


@backups_router.post("/{job_id}/restore", response_model=BackupJobResponse, status_code=202)
async def create_restore(
    job_id: UUID,
    body: CreateRestoreRequest,
    current_user: SystemManageDep,
    session_factory: BackupsSessionFactoryDep,
) -> BackupJobResponse:
    """基于已有备份创建恢复作业（异步）。

    根据备份作业 ID 查找对应的备份输出目录，提交恢复作业。
    也可通过 ``backup_dir`` 直接指定备份目录。

    Args:
        job_id: 备份作业 UUID（用于查找备份输出目录）。
        body: 恢复请求体。
        current_user: 当前认证用户（需 system:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        BackupJobResponse: 恢复作业信息（202 Accepted）。

    Raises:
        AppError: code="not_found"，当备份作业不存在时。
        AppError: code="validation_failed"，当备份作业未成功完成时。
    """
    # 查找备份作业以获取输出目录（若 body.backup_dir 未指定）
    backup_dir: str = body.backup_dir
    async with session_factory() as session:
        backup_job: Job | None = await session.scalar(
            sa.select(Job).where(
                Job.id == job_id,
                Job.kind == BACKUP_JOB_KIND,
            )
        )
        if backup_job is None:
            raise AppError(
                code="not_found",
                message=f"备份作业不存在: {job_id}",
                retryable=False,
                fields={"job_id": str(job_id)},
            )

        # 若 backup_dir 未显式指定，从备份作业 payload 中推断
        if not backup_dir and backup_job.payload is not None:
            inferred: object | None = backup_job.payload.get("output_dir")
            if isinstance(inferred, str) and inferred:
                backup_dir = inferred

    if not backup_dir:
        raise AppError(
            code="validation_failed",
            message="无法确定备份目录：backup_dir 未指定且备份作业 payload 中无 output_dir",
            retryable=False,
            fields={"backup_dir": "required"},
        )

    restore_job_id: UUID = new_id()
    now: datetime = datetime.now(UTC)

    async with session_scope(session_factory) as session:
        job = Job(
            id=restore_job_id,
            organization_id=current_user.organization_id
            if current_user.organization_id is not None
            else current_user.user_id,
            kind=RESTORE_JOB_KIND,
            status=JobStatus.ACCEPTED.value,
            payload={
                "backup_job_id": str(job_id),
                "backup_dir": backup_dir,
                "skip_migrations": body.skip_migrations,
                "triggered_by": str(current_user.user_id),
            },
            idempotency_key=f"restore:{restore_job_id}",
            attempt=0,
            max_attempts=1,
            created_by=current_user.user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        await session.flush()

        await OutboxDispatcher.enqueue(
            session,
            aggregate_type="job",
            aggregate_id=restore_job_id,
            event_type="job.accepted",
            payload={
                "job_id": str(restore_job_id),
                "kind": RESTORE_JOB_KIND,
            },
        )

    return BackupJobResponse(
        job_id=str(restore_job_id),
        status=JobStatus.ACCEPTED.value,
        kind=RESTORE_JOB_KIND,
        created_at=now,
        created_by=str(current_user.user_id),
    )
