"""备份/恢复 API 路由：触发备份/恢复异步作业 + 查询备份记录。

端点（IRIP 数据库备份功能增强）：
  POST   /api/v1/backups                  — 创建备份作业（daily 自动 / milestone 手动）
  GET    /api/v1/backups                  — 列出备份记录（按 type/status 过滤）
  GET    /api/v1/backups/{id}             — 备份记录详情
  POST   /api/v1/backups/{id}/restore     — 从备份恢复（先创建 pre_restore 备份）
  DELETE /api/v1/backups/{id}             — 删除里程碑备份
  GET    /api/v1/backups/stats            — 汇总统计

安全约定：
- 全部端点需 Authorization: Bearer <jwt> + require_permission("system:manage")；
- 错误响应统一格式：{"error": {"code", "message", "retryable", "fields"}}。

异步作业模式：
- 备份/恢复通过 JobService 提交异步作业（kind="backup" / "restore"）；
- Worker 在后台执行 backup.py / restore.py 脚本；
- 备份记录持久化到 backup_record 表，通过 BackupRecordService 管理。
"""

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.backups.entities import BackupRecord, BackupStatus, BackupType
from packages.backups.service import BackupRecordService
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
    """创建备份作业请求体。

    Attributes:
        type: 备份类型（daily / milestone）。
        name: 里程碑名称（type=milestone 时必填，≤100 字符）。
        description: 里程碑描述（≤500 字符）。
    """

    type: str = Field(..., description="备份类型: daily | milestone")
    name: str | None = Field(None, description="里程碑名称 (type=milestone 时必填)")
    description: str | None = Field(None, description="里程碑描述")


class CreateRestoreRequest(BaseModel):
    """创建恢复作业请求体。

    Attributes:
        skip_migrations: 是否跳过迁移步骤。
        recovery_target_time: PITR 恢复目标时间（ISO 8601），不传时恢复到备份时间点。
    """

    skip_migrations: bool = Field(False, description="是否跳过迁移步骤")
    recovery_target_time: str | None = Field(
        None, description="PITR 恢复目标时间（ISO 8601），不传时恢复到备份时间点"
    )


class BackupRecordResponse(BaseModel):
    """备份记录响应体。

    Attributes:
        id: 备份记录 UUID。
        job_id: 关联作业 UUID。
        backup_type: 备份类型（daily / milestone / pre_restore）。
        name: 里程碑名称。
        description: 里程碑描述。
        backup_date: 快照日期。
        file_path: 备份文件路径。
        file_size: 备份文件大小（字节）。
        sha256: 数据库 dump SHA-256 校验和。
        status: 备份状态（pending / succeeded / failed）。
        migration_version: Alembic 迁移版本。
        application_version: IRIP 应用版本。
        created_by: 创建者用户 ID。
        created_at: 创建时间。
        completed_at: 完成时间。
        expires_at: 过期时间。
    """

    id: str
    job_id: str | None
    backup_type: str
    name: str | None
    description: str | None
    backup_date: str | None
    file_path: str
    file_size: int | None
    sha256: str | None
    status: str
    migration_version: str | None
    application_version: str | None
    created_by: str | None
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime | None
    error_message: str | None
    backup_method: str | None = Field(None, description="备份方法: pitr | pg_dump")
    backup_timestamp: datetime | None = Field(None, description="联合时间戳")


class BackupRecordListResponse(BaseModel):
    """备份记录分页列表响应。"""

    items: list[BackupRecordResponse]
    next_cursor: str | None
    has_more: bool


class CreateBackupResponse(BaseModel):
    """创建备份作业响应体。

    Attributes:
        job_id: 作业 UUID。
        backup_record_id: 备份记录 UUID。
        status: 作业状态。
        kind: 作业类型。
        created_at: 创建时间。
    """

    job_id: str
    backup_record_id: str
    status: str
    kind: str
    created_at: datetime


class RestoreJobResponse(BaseModel):
    """恢复作业响应体。

    Attributes:
        job_id: 恢复作业 UUID。
        backup_id: 要恢复的备份记录 UUID。
        status: 作业状态。
        kind: 作业类型。
        created_at: 创建时间。
    """

    job_id: str
    backup_id: str
    status: str
    kind: str
    created_at: datetime


class BackupStatsResponse(BaseModel):
    """备份汇总统计响应。

    Attributes:
        total_count: 备份记录总数。
        total_size_bytes: 备份文件总大小（字节）。
        daily_count: 每日快照数量。
        milestone_count: 里程碑备份数量。
        succeeded_count: 成功备份数量。
        failed_count: 失败备份数量。
    """

    total_count: int
    total_size_bytes: int
    daily_count: int
    milestone_count: int
    succeeded_count: int
    failed_count: int


# ---- 辅助函数 ----


def _to_record_response(record: BackupRecord) -> BackupRecordResponse:
    """将 BackupRecord ORM 实体转换为响应模型。"""
    return BackupRecordResponse(
        id=str(record.id),
        job_id=str(record.job_id) if record.job_id is not None else None,
        backup_type=record.backup_type,
        name=record.name,
        description=record.description,
        backup_date=record.backup_date.isoformat() if record.backup_date else None,
        file_path=record.file_path,
        file_size=record.file_size,
        sha256=record.sha256,
        status=record.status,
        migration_version=record.migration_version,
        application_version=record.application_version,
        created_by=str(record.created_by) if record.created_by is not None else None,
        created_at=record.created_at,
        completed_at=record.completed_at,
        expires_at=record.expires_at,
        error_message=record.error_message,
        backup_method=record.backup_method,
        backup_timestamp=record.backup_timestamp,
    )


def _build_backup_output_dir(backup_id: UUID) -> str:
    """构建备份文件输出目录路径。

    路径格式：{IRIP_BACKUP_OUTPUT_DIR}/{backup_id}/

    Args:
        backup_id: 备份记录 UUID。

    Returns:
        str: 备份目录绝对路径。
    """
    output_dir: str = os.getenv("IRIP_BACKUP_OUTPUT_DIR", "/backups")
    return str(Path(output_dir) / str(backup_id))


# ---- 端点 ----


@backups_router.post("/", response_model=CreateBackupResponse, status_code=202)
async def create_backup(
    body: CreateBackupRequest,
    current_user: SystemManageDep,
    session_factory: BackupsSessionFactoryDep,
) -> CreateBackupResponse:
    """创建备份作业（异步）。

    支持两种备份类型：
    - daily: 每日自动快照（通常由 Celery beat 触发，也可手动创建）；
    - milestone: 里程碑手动备份（需提供 name + description）。

    将备份请求提交为异步作业，返回 202 Accepted + 作业 ID + 备份记录 ID。
    Worker 在后台执行 backup.py 脚本。

    Args:
        body: 备份请求体。
        current_user: 当前认证用户（需 system:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        CreateBackupResponse: 备份作业 + 备份记录信息（202 Accepted）。

    Raises:
        AppError: code="validation_failed"，当 type 非法或 milestone 未提供 name 时。
    """
    # 校验备份类型
    valid_types: set[str] = {BackupType.DAILY.value, BackupType.MILESTONE.value}
    if body.type not in valid_types:
        raise AppError(
            code="validation_failed",
            message=f"无效的备份类型: {body.type}，仅支持 daily / milestone",
            retryable=False,
            fields={"type": body.type},
        )

    # milestone 必须提供 name
    if body.type == BackupType.MILESTONE.value and not body.name:
        raise AppError(
            code="validation_failed",
            message="里程碑备份必须提供名称",
            retryable=False,
            fields={"name": "required"},
        )

    org_id: UUID = (
        current_user.organization_id
        if current_user.organization_id is not None
        else current_user.user_id
    )
    backup_service: BackupRecordService = BackupRecordService(session_factory)

    job_id: UUID = new_id()
    now: datetime = datetime.now(UTC)
    backup_dir: str = _build_backup_output_dir(job_id)

    async with session_scope(session_factory) as session:
        job = Job(
            id=job_id,
            organization_id=org_id,
            kind=BACKUP_JOB_KIND,
            status=JobStatus.ACCEPTED.value,
            payload={
                "type": body.type,
                "name": body.name,
                "description": body.description,
                "backup_record_id": str(job_id),
                "backup_method": "pitr",
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

        # 创建备份记录（id 与 job_id 一致，便于关联）
        record = BackupRecord(
            id=job_id,
            job_id=job_id,
            backup_type=body.type,
            name=body.name,
            description=body.description,
            backup_date=now.date() if body.type == BackupType.DAILY.value else None,
            file_path=backup_dir,
            status=BackupStatus.PENDING.value,
            created_by=current_user.user_id if body.type == BackupType.MILESTONE.value else None,
            created_at=now,
            expires_at=None,  # service.create 会按类型计算，此处直接构造
            organization_id=org_id,
            backup_method="pitr",
        )
        # 按类型设置过期时间
        from datetime import timedelta

        if body.type == BackupType.DAILY.value:
            record.expires_at = now + timedelta(days=14)
        # milestone: expires_at = None（永久保留）
        session.add(record)
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

    return CreateBackupResponse(
        job_id=str(job_id),
        backup_record_id=str(job_id),
        status=JobStatus.ACCEPTED.value,
        kind=BACKUP_JOB_KIND,
        created_at=now,
    )


@backups_router.get("/stats", response_model=BackupStatsResponse)
async def get_backup_stats(
    current_user: SystemManageDep,
    session_factory: BackupsSessionFactoryDep,
) -> BackupStatsResponse:
    """获取备份汇总统计。

    统计全部备份记录的总数、总大小、各类型数量及状态分布。

    Args:
        current_user: 当前认证用户（需 system:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        BackupStatsResponse: 备份汇总统计。
    """
    async with session_factory() as session:
        total_count: int = await session.scalar(
            sa.select(sa.func.count()).select_from(BackupRecord)
        ) or 0
        total_size: int = await session.scalar(
            sa.select(sa.func.coalesce(sa.func.sum(BackupRecord.file_size), 0)).select_from(
                BackupRecord
            )
        ) or 0
        daily_count: int = await session.scalar(
            sa.select(sa.func.count()).select_from(BackupRecord).where(
                BackupRecord.backup_type == BackupType.DAILY.value
            )
        ) or 0
        milestone_count: int = await session.scalar(
            sa.select(sa.func.count()).select_from(BackupRecord).where(
                BackupRecord.backup_type == BackupType.MILESTONE.value
            )
        ) or 0
        succeeded_count: int = await session.scalar(
            sa.select(sa.func.count()).select_from(BackupRecord).where(
                BackupRecord.status == BackupStatus.SUCCEEDED.value
            )
        ) or 0
        failed_count: int = await session.scalar(
            sa.select(sa.func.count()).select_from(BackupRecord).where(
                BackupRecord.status == BackupStatus.FAILED.value
            )
        ) or 0

    return BackupStatsResponse(
        total_count=total_count,
        total_size_bytes=total_size,
        daily_count=daily_count,
        milestone_count=milestone_count,
        succeeded_count=succeeded_count,
        failed_count=failed_count,
    )


@backups_router.get("/", response_model=BackupRecordListResponse)
async def list_backups(
    current_user: SystemManageDep,
    session_factory: BackupsSessionFactoryDep,
    type: str | None = Query(None, description="按备份类型筛选（daily / milestone / pre_restore）"),
    status: str | None = Query(None, description="按状态筛选（pending / succeeded / failed）"),
    cursor: str | None = Query(None, description="分页游标（上一页最后一条的 record_id）"),
    limit: int = Query(20, ge=1, le=100, description="每页数量（最大 100）"),
) -> BackupRecordListResponse:
    """列出备份记录（分页）。

    按 created_at DESC 排列，支持按 backup_type 和 status 过滤。

    Args:
        current_user: 当前认证用户（需 system:manage 权限）。
        session_factory: 数据库会话工厂。
        type: 备份类型筛选（daily / milestone / pre_restore）。
        status: 状态筛选（pending / succeeded / failed）。
        cursor: 分页游标（上一页最后一条记录的 id UUID 字符串）。
        limit: 每页数量。

    Returns:
        BackupRecordListResponse: 分页备份记录列表。
    """
    backup_service: BackupRecordService = BackupRecordService(session_factory)

    cursor_uuid: UUID | None = None
    if cursor is not None:
        try:
            cursor_uuid = UUID(cursor)
        except ValueError as exc:
            raise AppError(
                code="invalid_cursor",
                message="无效的分页游标",
                retryable=False,
                fields={"cursor": cursor},
            ) from exc

    records, has_more = await backup_service.list_by_type(
        backup_type=type, status=status, limit=limit, cursor=cursor_uuid
    )

    next_cursor: str | None = None
    if has_more and records:
        next_cursor = str(records[-1].id)

    return BackupRecordListResponse(
        items=[_to_record_response(r) for r in records],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@backups_router.get("/{record_id}", response_model=BackupRecordResponse)
async def get_backup_detail(
    record_id: UUID,
    current_user: SystemManageDep,
    session_factory: BackupsSessionFactoryDep,
) -> BackupRecordResponse:
    """获取备份记录详情。

    Args:
        record_id: 备份记录 UUID。
        current_user: 当前认证用户（需 system:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        BackupRecordResponse: 备份记录详情。

    Raises:
        AppError: code="not_found"，当备份记录不存在时。
    """
    backup_service: BackupRecordService = BackupRecordService(session_factory)
    record: BackupRecord = await backup_service.get(record_id)
    return _to_record_response(record)


@backups_router.post("/{record_id}/restore", response_model=RestoreJobResponse, status_code=202)
async def create_restore(
    record_id: UUID,
    body: CreateRestoreRequest,
    current_user: SystemManageDep,
    session_factory: BackupsSessionFactoryDep,
) -> RestoreJobResponse:
    """从备份记录创建恢复作业（异步）。

    验证备份记录状态为 succeeded，然后提交恢复作业。
    Worker 执行时会先创建 pre_restore 备份再执行 pg_restore。

    Args:
        record_id: 备份记录 UUID。
        body: 恢复请求体。
        current_user: 当前认证用户（需 system:manage 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        RestoreJobResponse: 恢复作业信息（202 Accepted）。

    Raises:
        AppError: code="not_found"，当备份记录不存在时。
        AppError: code="validation_failed"，当备份记录状态非 succeeded 时。
    """
    backup_service: BackupRecordService = BackupRecordService(session_factory)
    record: BackupRecord = await backup_service.get(record_id)

    if record.status != BackupStatus.SUCCEEDED.value:
        raise AppError(
            code="validation_failed",
            message=f"备份记录状态非 succeeded，无法恢复（当前状态: {record.status}）",
            retryable=False,
            fields={"status": record.status},
        )

    org_id: UUID = (
        current_user.organization_id
        if current_user.organization_id is not None
        else current_user.user_id
    )
    restore_job_id: UUID = new_id()
    now: datetime = datetime.now(UTC)

    async with session_scope(session_factory) as session:
        job = Job(
            id=restore_job_id,
            organization_id=org_id,
            kind=RESTORE_JOB_KIND,
            status=JobStatus.ACCEPTED.value,
            payload={
                "backup_id": str(record_id),
                "backup_dir": record.file_path,
                "skip_migrations": body.skip_migrations,
                "recovery_target_time": body.recovery_target_time,
                "triggered_by": str(current_user.user_id),
                "pre_restore_created": False,
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

    return RestoreJobResponse(
        job_id=str(restore_job_id),
        backup_id=str(record_id),
        status=JobStatus.ACCEPTED.value,
        kind=RESTORE_JOB_KIND,
        created_at=now,
    )


@backups_router.delete("/{record_id}", status_code=204)
async def delete_backup(
    record_id: UUID,
    current_user: SystemManageDep,
    session_factory: BackupsSessionFactoryDep,
) -> None:
    """删除备份记录。

    删除规则：
    - milestone: 允许手动删除（删除文件 + 记录）；
    - daily: 运行中（未过期）不可手动删除，已过期由自动清理处理；
    - pre_restore: 不可手动删除（回滚安全网）。

    Args:
        record_id: 备份记录 UUID。
        current_user: 当前认证用户（需 system:manage 权限）。
        session_factory: 数据库会话工厂。

    Raises:
        AppError: code="not_found"，当备份记录不存在时。
        AppError: code="validation_failed"，当类型不允许手动删除时。
    """
    backup_service: BackupRecordService = BackupRecordService(session_factory)
    record: BackupRecord = await backup_service.get(record_id)

    if record.backup_type == BackupType.PRE_RESTORE.value:
        raise AppError(
            code="validation_failed",
            message="pre_restore 备份不可手动删除（回滚安全网）",
            retryable=False,
            fields={"backup_type": record.backup_type},
        )

    if record.backup_type == BackupType.DAILY.value:
        now: datetime = datetime.now(UTC)
        if record.expires_at is not None and record.expires_at > now:
            raise AppError(
                code="validation_failed",
                message="运行中的每日备份不可手动删除，只能等自动过期",
                retryable=False,
                fields={"backup_type": "daily", "expires_at": str(record.expires_at)},
            )

    # 删除文件系统目录
    backup_dir: Path = Path(record.file_path)
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)

    # 删除数据库记录
    await backup_service.delete(record_id)
