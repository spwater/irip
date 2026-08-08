"""审计事件 API 路由：查询审计事件 + 异步导出作业。

端点（IRIP V3-T02）：
  GET  /api/v1/audit-events          — 查询审计事件（分页，每页最多 100 条）
  POST /api/v1/audit-events/export    — 创建审计导出作业（异步）

支持过滤参数：
  ?object_id=, ?object_type=, ?user_id=, ?action=, ?start_date=, ?end_date=

导出限制：单个导出作业最多导出 100,000 行。

安全约定：
- 全部端点需 Authorization: Bearer <jwt> + require_permission("audit:read")；
- 错误响应统一格式：{"error": {"code", "message", "retryable", "fields"}}。
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.audit.events import AuditEvent
from packages.audit.repository import AuditQueryRepository
from packages.common.database import session_scope
from packages.common.errors import AppError
from packages.common.ids import new_id
from packages.jobs.entities import Job, JobStatus
from packages.jobs.outbox import OutboxDispatcher

#: 路由实例。
audit_router = APIRouter(prefix="/api/v1/audit-events", tags=["audit"])

#: 需 audit:read 权限的当前用户依赖。
AuditReaderDep = Annotated[CurrentUser, Depends(require_permission("audit:read"))]

#: 审计导出单次最大行数限制。
AUDIT_EXPORT_MAX_ROWS: int = 100_000

#: 查询每页最大行数。
AUDIT_QUERY_MAX_PAGE_SIZE: int = 100


# ---- 依赖占位（由应用启动或测试覆盖）----


def get_audit_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取数据库会话工厂（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_audit_session_factory must be overridden via dependency_overrides"
    )


AuditSessionFactoryDep = Annotated[
    async_sessionmaker[AsyncSession], Depends(get_audit_session_factory)
]


# ---- 响应模型 ----


class AuditEventResponse(BaseModel):
    """审计事件响应体。"""

    id: str
    occurred_at: datetime
    actor_user_id: str | None
    department_id: str
    action: str
    resource_type: str | None
    resource_id: str | None
    payload: dict[str, object] | None
    ip: str | None
    user_agent: str | None


class AuditEventListResponse(BaseModel):
    """审计事件分页列表响应。"""

    items: list[AuditEventResponse]
    next_cursor: str | None
    has_more: bool


class AuditExportRequest(BaseModel):
    """审计导出作业请求体。"""

    object_id: str | None = Field(None, description="按对象 ID 过滤")
    object_type: str | None = Field(None, description="按对象类型过滤")
    user_id: str | None = Field(None, description="按操作者 ID 过滤")
    action: str | None = Field(None, description="按动作过滤")
    start_date: datetime | None = Field(None, description="起始日期")
    end_date: datetime | None = Field(None, description="截止日期")
    format: str = Field("csv", description="导出格式（csv / json）")


class AuditExportResponse(BaseModel):
    """审计导出作业创建响应。"""

    job_id: str
    status: str
    kind: str


# ---- 辅助函数 ----


def _to_event_response(event: AuditEvent) -> AuditEventResponse:
    """将 AuditEvent ORM 实体转换为响应模型。"""
    return AuditEventResponse(
        id=str(event.id),
        occurred_at=event.occurred_at,
        actor_user_id=str(event.actor_user_id) if event.actor_user_id is not None else None,
        department_id=str(event.department_id),
        action=event.action,
        resource_type=event.resource_type,
        resource_id=str(event.resource_id) if event.resource_id is not None else None,
        payload=event.payload,
        ip=event.ip,
        user_agent=event.user_agent,
    )


def _parse_uuid(value: str, field_name: str) -> UUID:
    """将字符串参数解析为 UUID，无效时抛 AppError(validation_failed)。"""
    try:
        return UUID(value)
    except ValueError as exc:
        raise AppError(
            code="validation_failed",
            message=f"无效的{field_name}",
            retryable=False,
            fields={field_name: value},
        ) from exc


# ---- 端点 ----


@audit_router.get("/", response_model=AuditEventListResponse)
async def list_audit_events(
    current_user: AuditReaderDep,
    session_factory: AuditSessionFactoryDep,
    object_id: str | None = Query(None, description="按对象 ID 过滤"),
    object_type: str | None = Query(None, description="按对象类型过滤"),
    user_id: str | None = Query(None, description="按操作者 ID 过滤"),
    action: str | None = Query(None, description="按动作过滤"),
    start_date: datetime | None = Query(None, description="起始日期（ISO 8601）"),  # noqa: B008
    end_date: datetime | None = Query(None, description="截止日期（ISO 8601）"),  # noqa: B008
    cursor: str | None = Query(None, description="分页游标（上一页最后一条的 occurred_at）"),
    limit: int = Query(50, ge=1, le=AUDIT_QUERY_MAX_PAGE_SIZE, description="每页数量（最大 100）"),
) -> AuditEventListResponse:
    """查询审计事件（游标分页）。

    按时间倒序排列（最新优先），每页最多 100 条。
    游标为上一页最后一条记录的 occurred_at ISO 字符串。

    Args:
        current_user: 当前认证用户（需 audit:read 权限）。
        session_factory: 数据库会话工厂。
        object_id: 资源 ID 过滤。
        object_type: 资源类型过滤。
        user_id: 操作者 ID 过滤。
        action: 动作过滤。
        start_date: 起始日期。
        end_date: 截止日期。
        cursor: 分页游标。
        limit: 每页数量。

    Returns:
        AuditEventListResponse: 分页审计事件列表。
    """
    # 参数校验：UUID 格式
    object_uuid: UUID | None = _parse_uuid(object_id, "对象 ID") if object_id is not None else None
    user_uuid: UUID | None = _parse_uuid(user_id, "用户 ID") if user_id is not None else None

    # 解析游标
    cursor_dt: datetime | None = None
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

    # ORM 查询已下沉到 AuditQueryRepository
    async with session_factory() as session:
        rows: list[AuditEvent] = await AuditQueryRepository.list_events(
            session,
            object_id=object_uuid,
            object_type=object_type,
            user_id=user_uuid,
            action=action,
            start_date=start_date,
            end_date=end_date,
            cursor_dt=cursor_dt,
            limit=limit,
        )

    has_more: bool = len(rows) > limit
    page_items: list[AuditEvent] = rows[:limit]
    next_cursor: str | None = None
    if has_more and page_items:
        next_cursor = page_items[-1].occurred_at.isoformat()

    return AuditEventListResponse(
        items=[_to_event_response(e) for e in page_items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@audit_router.post("/export", response_model=AuditExportResponse, status_code=202)
async def create_audit_export(
    body: AuditExportRequest,
    current_user: AuditReaderDep,
    session_factory: AuditSessionFactoryDep,
) -> AuditExportResponse:
    """创建审计导出作业（异步）。

    将导出请求提交为异步作业，返回 202 Accepted + 作业 ID。
    Worker 在后台执行导出，最多导出 AUDIT_EXPORT_MAX_ROWS 行。

    Args:
        body: 导出请求体（含过滤条件）。
        current_user: 当前认证用户（需 audit:read 权限）。
        session_factory: 数据库会话工厂。

    Returns:
        AuditExportResponse: 导出作业信息（202 Accepted）。
    """
    # 验证 UUID 参数格式
    if body.object_id is not None:
        try:
            UUID(body.object_id)
        except ValueError as exc:
            raise AppError(
                code="validation_failed",
                message="无效的对象 ID",
                retryable=False,
                fields={"object_id": body.object_id},
            ) from exc

    if body.user_id is not None:
        try:
            UUID(body.user_id)
        except ValueError as exc:
            raise AppError(
                code="validation_failed",
                message="无效的用户 ID",
                retryable=False,
                fields={"user_id": body.user_id},
            ) from exc

    if body.format not in ("csv", "json"):
        raise AppError(
            code="validation_failed",
            message=f"不支持的导出格式: {body.format}",
            retryable=False,
            fields={"format": body.format},
        )

    job_id: UUID = new_id()

    async with session_scope(session_factory) as session:
        job = Job(
            id=job_id,
            department_id=current_user.department_id
            if current_user.department_id is not None
            else current_user.user_id,
            kind="audit_export",
            status=JobStatus.ACCEPTED.value,
            payload={
                "object_id": body.object_id,
                "object_type": body.object_type,
                "user_id": body.user_id,
                "action": body.action,
                "start_date": body.start_date.isoformat() if body.start_date else None,
                "end_date": body.end_date.isoformat() if body.end_date else None,
                "format": body.format,
                "max_rows": AUDIT_EXPORT_MAX_ROWS,
            },
            idempotency_key=f"audit_export:{job_id}",
            attempt=0,
            max_attempts=1,
            created_by=current_user.user_id,
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
                "kind": "audit_export",
            },
        )

    return AuditExportResponse(
        job_id=str(job_id),
        status=JobStatus.ACCEPTED.value,
        kind="audit_export",
    )
