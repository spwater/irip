"""事实管理路由。

端点分组（facts_router, prefix=/api/v1/facts）：
  POST   /api/v1/facts                        — 创建事实（fact:write）
  GET    /api/v1/facts                        — 列表过滤（fact:read）
  GET    /api/v1/facts/search?q=              — 全文搜索（fact:read）
  GET    /api/v1/facts/search-data            — 按数据内容搜索（fact:read）
  GET    /api/v1/facts/{id}                   — 获取事实（fact:read）
  GET    /api/v1/facts/{id}/data              — 获取事实数据（fact:read）
  POST   /api/v1/facts/{id}/archive           — 归档事实（fact:write）
  DELETE /api/v1/facts/{id}                   — 删除事实（fact:write）
  DELETE /api/v1/facts/by-task/{task_code}    — 按任务删除事实（fact:write）

重构后 Router 仅保留：权限依赖、请求/响应模型、映射函数、归属校验、
MinIO Artifact 删除编排。不含任何 sa.* / session.execute。
"""

import logging
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

_logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Query  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from apps.api.dependencies.auth import CurrentUser  # noqa: E402
from apps.api.dependencies.authorization import require_permission  # noqa: E402
from apps.api.schemas.facts import FactListResponse, FactResponse  # noqa: E402, F401
from packages.common.errors import AppError  # noqa: E402
from packages.facts.observations import FactDetailRow, FactRef  # noqa: E402
from packages.facts.query_service import FactQueryService  # noqa: E402
from packages.facts.service import CreateFactCommand, FactService  # noqa: E402

#: 需 fact:write 权限的当前用户依赖。
WriteUserDep = Annotated[CurrentUser, Depends(require_permission("fact:write"))]

#: 需 fact:read 权限的当前用户依赖。
ReadUserDep = Annotated[CurrentUser, Depends(require_permission("fact:read"))]


# ---- DI 占位 ----


def get_fact_service() -> FactService:
    """获取 FactService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_fact_service must be overridden via dependency_overrides")


FactServiceDep = Annotated[FactService, Depends(get_fact_service)]


def get_fact_query_service() -> FactQueryService:
    """获取 FactQueryService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_fact_query_service must be overridden via dependency_overrides")


FactQueryServiceDep = Annotated[FactQueryService, Depends(get_fact_query_service)]


# ---- 路由实例 ----

facts_router = APIRouter(prefix="/api/v1/facts", tags=["facts"])


# ---- 请求模型 ----


class CreateFactRequest(BaseModel):
    """创建事实请求。"""

    fact_type: Literal["experiment_run", "simulation_run", "document_record", "model_execution"]
    object_id: UUID
    subject_id: str = Field(..., min_length=1, max_length=256)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    idempotency_key: str | None = Field(None, max_length=256)


# ---- 辅助函数 ----


def _ref_to_response(ref: FactRef) -> FactResponse:
    """将 FactRef 转为响应模型。"""
    return FactResponse(
        fact_id=str(ref.fact_id),
        fact_type=ref.fact_type,
        subject_id=ref.subject_id,
        status=ref.status,
    )


def _detail_to_response(row: FactDetailRow) -> FactResponse:
    """将 FactDetailRow 转为响应模型。"""
    return FactResponse(
        fact_id=str(row.fact_id),
        fact_type=row.fact_type,
        subject_id=row.subject_id,
        status=row.status,
        task_code=row.task_code,
        task_name=row.task_name,
        project_name=row.project_name,
        department_name=row.department_name,
        operator=row.operator,
        run_operator=row.run_operator,
        equipment_name=row.equipment_name,
        data_summary=row.data_summary,
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


# ---- 端点 ----


@facts_router.post("", response_model=FactResponse, status_code=201)
async def create_fact(
    body: CreateFactRequest,
    current_user: WriteUserDep,
    service: FactServiceDep,
) -> FactResponse:
    """创建事实。

    支持幂等键去重：相同 idempotency_key 不会创建重复事实。
    """
    command = CreateFactCommand(
        fact_type=body.fact_type,
        department_id=service.department_id,
        object_id=body.object_id,
        subject_id=body.subject_id,
        started_at=body.started_at,
        ended_at=body.ended_at,
        idempotency_key=body.idempotency_key,
        created_by=current_user.user_id,
    )
    ref = await service.create(command)
    return _ref_to_response(ref)


@facts_router.get("", response_model=FactListResponse)
async def list_facts(
    current_user: ReadUserDep,
    query_service: FactQueryServiceDep,
    fact_type: str | None = Query(None, description="按事实类型过滤"),
    object_id: UUID | None = Query(None, description="按工业对象过滤"),  # noqa: B008
    status: str | None = Query(None, description="按状态过滤"),
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> FactListResponse:
    """分页列出事实（支持按 fact_type, object_id, status 过滤）。"""
    filters: dict[str, Any] = {}
    if fact_type is not None:
        filters["fact_type"] = fact_type
    if object_id is not None:
        filters["object_id"] = object_id
    if status is not None:
        filters["status"] = status

    rows, next_cursor, group_counts = await query_service.list_facts_detail(
        filters=filters if filters else None,
        cursor=cursor,
        page_size=page_size,
    )

    items = [_detail_to_response(r) for r in rows]
    return FactListResponse(
        items=items,
        next_cursor=next_cursor,
        group_counts=group_counts,
    )


@facts_router.get("/search", response_model=FactListResponse)
async def search_facts(
    current_user: ReadUserDep,
    query_service: FactQueryServiceDep,
    q: str = Query(..., min_length=1, description="搜索查询"),
    fact_type: str | None = Query(None, description="按事实类型过滤"),
    object_id: UUID | None = Query(None, description="按工业对象过滤"),  # noqa: B008
    status: str | None = Query(None, description="按状态过滤"),
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> FactListResponse:
    """全文搜索事实（基于 subject_id 和 fact_type）。"""
    filters: dict[str, Any] = {}
    if fact_type is not None:
        filters["fact_type"] = fact_type
    if object_id is not None:
        filters["object_id"] = object_id
    if status is not None:
        filters["status"] = status

    rows, next_cursor, group_counts = await query_service.search_facts_detail(
        query=q,
        filters=filters if filters else None,
        cursor=cursor,
        page_size=page_size,
    )

    items = [_detail_to_response(r) for r in rows]
    return FactListResponse(
        items=items,
        next_cursor=next_cursor,
        group_counts=group_counts,
    )


@facts_router.get("/search-data", response_model=FactListResponse)
async def search_facts_by_data(
    current_user: ReadUserDep,
    query_service: FactQueryServiceDep,
    q: str | None = Query(None, description="全文搜索（匹配任意 key 或 value）"),
    key: str | None = Query(None, description="精确匹配 key（字段名）"),
    value: str | None = Query(None, description="精确匹配 value（字符串）"),
    min_value: float | None = Query(None, description="数值下限"),
    max_value: float | None = Query(None, description="数值上限"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> FactListResponse:
    """按数据内容搜索事实（跨任务、跨实验类型，通用 KV 索引）。

    支持三种搜索模式：
    - 全文：q=Na2O → 匹配任意 key 或 value_text 包含 "Na2O"
    - 精确键值：key=组分&value=Na2O → 匹配 key="组分" AND value_text="Na2O"
    - 数值范围：key=结果&min_value=5 → 匹配 key="结果" AND value_number >= 5
    """
    # 参数校验保留在 Router（与权限/模型校验同层）
    conditions_exist = any(v is not None for v in (q, key, value, min_value, max_value))
    if not conditions_exist:
        raise AppError(
            code="validation_failed",
            message="至少提供一个搜索条件（q / key / value / min_value / max_value）",
            retryable=False,
        )

    rows, group_counts = await query_service.search_by_data(
        q=q,
        key=key,
        value=value,
        min_value=min_value,
        max_value=max_value,
        page_size=page_size,
    )

    items = [_detail_to_response(r) for r in rows]
    return FactListResponse(
        items=items,
        next_cursor=None,
        group_counts=group_counts,
    )


@facts_router.get("/{fact_id}", response_model=FactResponse)
async def get_fact(
    fact_id: UUID,
    current_user: ReadUserDep,
    query_service: FactQueryServiceDep,
) -> FactResponse:
    """获取事实（含 JOIN 快照字段）。"""
    row = await query_service.get_fact_detail(fact_id)
    return _detail_to_response(row)


@facts_router.get("/{fact_id}/data")
async def get_fact_data(
    fact_id: UUID,
    current_user: ReadUserDep,
    query_service: FactQueryServiceDep,
) -> dict[str, Any]:
    """获取事实关联的提取数据（从 artifact 下载 JSON）。

    返回 {"metadata": {...}, "points": [...], "series": [...]} 格式的干净数据。
    """
    return await query_service.get_fact_data(fact_id)


@facts_router.post("/{fact_id}/archive", status_code=204)
async def archive_fact(
    fact_id: UUID,
    current_user: WriteUserDep,
    service: FactServiceDep,
) -> None:
    """归档实验事实（tombstone，替代物理删除）。

    将 Fact.status 设为 'archived'，不物理删除任何证据记录。

    Args:
        fact_id: 事实 UUID。
        current_user: 当前认证用户（需 fact:write 权限）。
        service: 事实服务。

    Raises:
        AppError: code="not_found"，当事实不存在时。
    """
    await service.archive(fact_id)


@facts_router.delete("/{fact_id}", status_code=204)
async def delete_fact(
    fact_id: UUID,
    current_user: WriteUserDep,
    service: FactServiceDep,
) -> None:
    """物理删除实验事实。"""
    # 先查出关联的 source_artifact_id + flow_run_id + 归属信息
    meta = await service.get_fact_meta(fact_id)

    # 归属检查：所有者+上级模型
    from apps.api.dependencies.dept_scope import check_management_permission

    await check_management_permission(
        current_user=current_user,
        entity_department_id=meta.department_id,
        entity_owner_user_id=meta.owner_user_id,
        session_factory=service.session_factory,
    )

    # 删 MinIO 中的 artifact 文件
    if meta.source_artifact_id is not None:
        try:
            from apps.api.main import _build_s3_repo
            from packages.common.artifacts import ArtifactService

            s3_repo = _build_s3_repo()
            artifact_svc = ArtifactService(
                s3_repo=s3_repo,
                session_factory=service.session_factory,
                department_id=service.department_id,
                uploaded_by=current_user.user_id,
            )
            await artifact_svc.delete_artifact(meta.source_artifact_id)
        except Exception:
            _logger.warning("删除 artifact 文件失败", exc_info=True)

    # 删除 DB 记录（Fact + FlowRun 分两个独立 session）
    await service.delete_fact_record(fact_id, meta.flow_run_id)


@facts_router.delete("/by-task/{task_code}", status_code=204)
async def delete_facts_by_task(
    task_code: str,
    current_user: WriteUserDep,
    service: FactServiceDep,
) -> None:
    """按任务编码批量删除事实。"""
    # 先查出关联的 fact_id、source_artifact_id 和 flow_run_id
    metas = await service.get_facts_meta_by_task(task_code)

    fact_ids = [m.fact_id for m in metas]
    artifact_ids = [m.source_artifact_id for m in metas if m.source_artifact_id is not None]
    flow_run_ids = [m.flow_run_id for m in metas if m.flow_run_id is not None]

    # 删 MinIO 中的 artifact 文件
    if artifact_ids:
        try:
            from apps.api.main import _build_s3_repo
            from packages.common.artifacts import ArtifactService

            s3_repo = _build_s3_repo()
            artifact_svc = ArtifactService(
                s3_repo=s3_repo,
                session_factory=service.session_factory,
                department_id=service.department_id,
                uploaded_by=current_user.user_id,
            )
            for aid in artifact_ids:
                await artifact_svc.delete_artifact(aid)
        except Exception:
            _logger.warning("删除 artifact 文件失败", exc_info=True)

    # 删除 DB 记录（Fact + FlowRun 分两个独立 session）
    await service.delete_facts_records(fact_ids, flow_run_ids)
