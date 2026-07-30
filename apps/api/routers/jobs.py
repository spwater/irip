"""作业路由：提交、查询、取消、事件流。

端点（docs/arch-v0.md §4.2 时序图 + §2.6 API 概览）：
  POST /api/v1/jobs              — 提交作业（202 Accepted）
  GET  /api/v1/jobs/{id}         — 查询作业状态
  POST /api/v1/jobs/{id}/cancel  — 请求取消作业
  GET  /api/v1/jobs/{id}/events  — SSE 事件流（作业状态变更）

安全约定：
- 所有端点需 Authorization: Bearer <jwt>；
- 错误响应统一格式：{"error": {"code", "message", "retryable", "fields"}}。
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.common.job_policy import JobKindPolicy
from packages.jobs.entities import TERMINAL_STATUSES, JobRef, JobStatus
from packages.jobs.service import JobService

#: 路由实例。
jobs_router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

#: 需 job:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("job:read"))]

#: 需 job:submit 权限的当前用户依赖。
SubmitUserDep = Annotated[CurrentUser, Depends(require_permission("job:submit"))]

#: 需 job:cancel 权限的当前用户依赖。
CancelUserDep = Annotated[CurrentUser, Depends(require_permission("job:cancel"))]


def get_job_service() -> JobService:
    """获取 JobService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_job_service must be overridden via dependency_overrides")


JobServiceDep = Annotated[JobService, Depends(get_job_service)]


# ---- 请求/响应模型 ----


class CreateJobRequest(BaseModel):
    """提交作业请求体。"""

    kind: str
    payload: dict[str, object]
    idempotency_key: str


class JobResponse(BaseModel):
    """作业响应体。"""

    job_id: str
    status: str
    kind: str
    stage: str = ""
    progress: int = 0
    retryable: bool = False


class CancelResponse(BaseModel):
    """取消响应体。"""

    job_id: str
    status: str
    kind: str


class JobListItem(BaseModel):
    """作业列表项。"""

    id: str
    kind: str
    status: str
    stage: str = ""
    progress: int = 0
    retryable: bool = False
    created_at: datetime
    attempt: int = 0
    max_attempts: int = 3
    flow_name: str = ""
    dept_name: str = ""


class JobListResponse(BaseModel):
    """作业分页列表响应。"""

    items: list[JobListItem]
    next_cursor: str | None
    has_more: bool


class JobDetailResponse(BaseModel):
    """作业详情响应体。"""

    id: str
    kind: str
    status: str
    stage: str = ""
    progress: int = 0
    retryable: bool = False
    attempt: int = 0
    max_attempts: int = 3
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None
    last_error: dict[str, object] | None = None
    result: dict[str, object] | None = None
    payload: dict[str, object] | None = None


# ---- 端点 ----


@jobs_router.post("", response_model=JobResponse, status_code=202)
async def create_job(
    body: CreateJobRequest,
    current_user: SubmitUserDep,
    service: JobServiceDep,
) -> JobResponse:
    """提交作业。

    同事务 INSERT job(accepted) + outbox_event(job.accepted)。
    幂等键保证重复提交返回同一作业。

    C-02: 通用接口只允许 allow_general_submit=True 的 kind。
    特权 kind（backup/restore/audit_export）必须通过专用 API 提交。

    Args:
        body: 作业请求体。
        current_user: 当前认证用户。
        service: 作业服务。

    Returns:
        JobResponse: 作业 ID + 状态（202 Accepted）。

    Raises:
        AppError: code="unknown_job_kind"，当 kind 未知时。
        AppError: code="forbidden"，当 kind 不允许通用提交或缺少权限时。
    """
    # C-02: JobKindPolicy 校验
    from packages.auth.permissions import get_role_permissions

    user_permissions: set[str] = set()
    for role in current_user.roles:
        user_permissions.update(get_role_permissions(role))
    try:
        JobKindPolicy.validate(body.kind, user_permissions, via_general=True)
    except ValueError as exc:
        raise AppError(
            code="unknown_job_kind",
            message=str(exc),
            retryable=False,
            fields={"kind": body.kind},
        ) from exc
    except PermissionError as exc:
        raise AppError(
            code="forbidden",
            message=str(exc),
            retryable=False,
            fields={"kind": body.kind},
        ) from exc

    ref: JobRef = await service.accept(
        kind=body.kind,
        payload=body.payload,
        idempotency_key=body.idempotency_key,
    )
    return JobResponse(
        job_id=str(ref.job_id),
        status=ref.status.value,
        kind=ref.kind,
    )


@jobs_router.get("", response_model=JobListResponse)
async def list_jobs(
    current_user: ReadUserDep,
    service: JobServiceDep,
    status: str | None = Query(None, description="状态筛选"),
    kind: str | None = Query(None, description="类型筛选"),
    cursor: str | None = Query(None, description="分页游标"),
    limit: int = Query(50, ge=1, le=100, description="每页数量"),
) -> JobListResponse:
    """分页查询作业列表。

    按创建时间倒序排列，支持按状态和类型过滤。

    Args:
        current_user: 当前认证用户。
        service: 作业服务。
        status: 状态筛选。
        kind: 类型筛选。
        cursor: 分页游标。
        limit: 每页数量（最大 100）。

    Returns:
        JobListResponse: 分页作业列表。
    """
    items, next_cursor, has_more = await service.list(
        status=status, kind=kind, cursor=cursor, limit=limit
    )
    return JobListResponse(
        items=[
            JobListItem(
                id=str(job.id),
                kind=job.kind,
                status=job.status,
                stage=stage,
                progress=progress,
                retryable=retryable,
                created_at=job.created_at,
                attempt=job.attempt,
                max_attempts=job.max_attempts,
                flow_name=flow_name,
                dept_name=dept_name,
            )
            for job, stage, progress, retryable, flow_name, dept_name in items
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@jobs_router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    current_user: ReadUserDep,
    service: JobServiceDep,
) -> JobResponse:
    """查询作业状态。

    Args:
        job_id: 作业 UUID。
        current_user: 当前认证用户。
        service: 作业服务。

    Returns:
        JobResponse: 作业 ID + 状态 + 类型。

    Raises:
        AppError: code="not_found"，当作业不存在时。
    """
    ref: JobRef = await service.get(job_id)
    return JobResponse(
        job_id=str(ref.job_id),
        status=ref.status.value,
        kind=ref.kind,
        stage=ref.stage,
        progress=ref.progress,
        retryable=ref.retryable,
    )


@jobs_router.post("/{job_id}/cancel", response_model=CancelResponse)
async def cancel_job(
    job_id: UUID,
    current_user: CancelUserDep,
    service: JobServiceDep,
) -> CancelResponse:
    """请求取消作业。

    Args:
        job_id: 作业 UUID。
        current_user: 当前认证用户。
        service: 作业服务。

    Returns:
        CancelResponse: 作业 ID + 状态（cancel_requested）。

    Raises:
        AppError: code="not_found"，当作业不存在时。
        AppError: code="conflict"，当作业已终态时。
    """
    ref: JobRef = await service.request_cancel(job_id, current_user.user_id)
    return CancelResponse(
        job_id=str(ref.job_id),
        status=ref.status.value,
        kind=ref.kind,
    )


@jobs_router.get("/{job_id}/detail", response_model=JobDetailResponse)
async def get_job_detail(
    job_id: UUID,
    current_user: ReadUserDep,
    service: JobServiceDep,
) -> JobDetailResponse:
    """查询作业详情（含 payload、result、last_error）。

    Args:
        job_id: 作业 UUID。
        current_user: 当前认证用户。
        service: 作业服务。

    Returns:
        JobDetailResponse: 作业详情。

    Raises:
        AppError: code="not_found"，当作业不存在时。
    """
    from packages.jobs.entities import Job

    ref: JobRef = await service.get(job_id)
    job: Job = await service.get_raw(job_id)
    return JobDetailResponse(
        id=str(job.id),
        kind=job.kind,
        status=job.status,
        stage=ref.stage,
        progress=ref.progress,
        retryable=ref.retryable,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        created_at=job.created_at,
        updated_at=job.updated_at,
        created_by=str(job.created_by) if job.created_by is not None else None,
        last_error=job.last_error,
        result=job.result,
        payload=job.payload,
    )


@jobs_router.post("/{job_id}/retry", response_model=JobResponse, status_code=202)
async def retry_job(
    job_id: UUID,
    current_user: SubmitUserDep,
    service: JobServiceDep,
) -> JobResponse:
    """重试已失败的作业。

    仅对处于 failed 或 cancelled 终态的作业允许重试。
    创建一个新作业，使用原作业的 kind 和 payload。

    Args:
        job_id: 原作业 UUID。
        current_user: 当前认证用户。
        service: 作业服务。

    Returns:
        JobResponse: 新作业 ID + 状态（202 Accepted）。

    Raises:
        AppError: code="not_found"，当原作业不存在时。
        AppError: code="conflict"，当原作业非终态时。
    """
    from packages.jobs.entities import Job

    original: Job = await service.get_raw(job_id)

    original_status = JobStatus(original.status)
    if original_status not in TERMINAL_STATUSES:
        raise AppError(
            code="conflict",
            message=f"作业未处于终态，无法重试: {original_status.value}",
            retryable=False,
            fields={"status": original_status.value},
        )

    # C-02: JobKindPolicy 校验（重试也必须经过策略校验）
    from packages.auth.permissions import get_role_permissions

    user_permissions: set[str] = set()
    for role in current_user.roles:
        user_permissions.update(get_role_permissions(role))
    try:
        JobKindPolicy.validate(original.kind, user_permissions, via_general=True)
    except ValueError as exc:
        raise AppError(
            code="unknown_job_kind",
            message=str(exc),
            retryable=False,
            fields={"kind": original.kind},
        ) from exc
    except PermissionError as exc:
        raise AppError(
            code="forbidden",
            message=str(exc),
            retryable=False,
            fields={"kind": original.kind},
        ) from exc

    # 创建新作业（同 kind + payload，新幂等键）
    new_idempotency_key = f"retry:{job_id}:{new_id()}"
    payload = dict(original.payload) if original.payload else {}
    ref: JobRef = await service.accept(
        kind=original.kind,
        payload=payload,
        idempotency_key=new_idempotency_key,
    )
    return JobResponse(
        job_id=str(ref.job_id),
        status=ref.status.value,
        kind=ref.kind,
    )


@jobs_router.get("/{job_id}/events")
async def job_events(
    job_id: UUID,
    current_user: ReadUserDep,
    service: JobServiceDep,
) -> StreamingResponse:
    """SSE 事件流：推送作业状态变更。

    使用 Server-Sent Events 协议，每秒轮询作业状态并推送变更。

    Args:
        job_id: 作业 UUID。
        current_user: 当前认证用户。
        service: 作业服务。

    Returns:
        StreamingResponse: text/event-stream 响应。
    """
    from packages.jobs.entities import TERMINAL_STATUSES

    async def event_stream() -> AsyncIterator[str]:
        """SSE 事件流生成器。"""
        last_status: str | None = None
        while True:
            try:
                ref = await service.get(job_id)
                current_status = ref.status.value

                if current_status != last_status:
                    data = json.dumps(
                        {
                            "job_id": str(ref.job_id),
                            "status": current_status,
                            "kind": ref.kind,
                        },
                        ensure_ascii=False,
                    )
                    yield f"data: {data}\n\n"
                    last_status = current_status

                if ref.status in TERMINAL_STATUSES:
                    yield f"event: done\ndata: {json.dumps({'status': current_status})}\n\n"
                    break

            except AppError:
                yield f"event: error\ndata: {json.dumps({'error': 'not_found'})}\n\n"
                break

            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
