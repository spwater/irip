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
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from apps.api.dependencies.auth import CurrentUser, get_current_user
from packages.common.errors import AppError
from packages.jobs.entities import JobRef
from packages.jobs.service import JobService

#: 路由实例。
jobs_router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def get_job_service() -> JobService:
    """获取 JobService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_job_service must be overridden via dependency_overrides"
    )


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


# ---- 端点 ----


@jobs_router.post("", response_model=JobResponse, status_code=202)
async def create_job(
    body: CreateJobRequest,
    current_user: CurrentUserDep,
    service: JobServiceDep,
) -> JobResponse:
    """提交作业。

    同事务 INSERT job(accepted) + outbox_event(job.accepted)。
    幂等键保证重复提交返回同一作业。

    Args:
        body: 作业请求体。
        current_user: 当前认证用户。
        service: 作业服务。

    Returns:
        JobResponse: 作业 ID + 状态（202 Accepted）。

    Raises:
        AppError: code="validation_failed"，当 kind 为空时。
    """
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


@jobs_router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    current_user: CurrentUserDep,
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
    current_user: CurrentUserDep,
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


@jobs_router.get("/{job_id}/events")
async def job_events(
    job_id: UUID,
    current_user: CurrentUserDep,
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
