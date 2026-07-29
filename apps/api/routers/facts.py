"""事实管理路由（IRIP Task 15）。

端点分组（facts_router, prefix=/api/v1/facts）：
  POST   /api/v1/facts                        — 创建事实（fact:write）
  GET    /api/v1/facts                        — 列表过滤（fact:read）
  GET    /api/v1/facts/search?q=              — 全文搜索（fact:read）
  GET    /api/v1/facts/{id}                   — 获取最新修订（fact:read）
  GET    /api/v1/facts/{id}/revisions         — 列出所有修订（fact:read）
  GET    /api/v1/facts/{id}/revisions/{r}    — 获取特定修订（fact:read）
  GET    /api/v1/facts/{id}/observations      — 获取观察值（fact:read）
  POST   /api/v1/facts/{id}/revise           — 创建新修订（fact:write）
"""

import logging
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

_logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Query  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from sqlalchemy import func  # noqa: E402

from apps.api.dependencies.auth import CurrentUser  # noqa: E402
from apps.api.dependencies.authorization import require_permission  # noqa: E402
from apps.api.dependencies.dept_scope import should_filter_by_department  # noqa: E402
from packages.common.errors import AppError  # noqa: E402
from packages.facts.observations import (  # noqa: E402
    FactRevisionRef,
    NormalizedObservation,
    RawObservation,
)
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


# ---- 路由实例 ----

facts_router = APIRouter(prefix="/api/v1/facts", tags=["facts"])


# ---- 请求模型 ----


class RawObservationItem(BaseModel):
    """原始观察值请求项。"""

    source_path: str = Field(..., min_length=1, max_length=500)
    source_value: str = Field(..., min_length=1)
    source_unit: str | None = Field(None, max_length=64)
    source_name: str | None = Field(None, max_length=256)
    artifact_id: UUID | None = None
    id: UUID | None = Field(None, description="预生成 ID，用于标准化观察值引用")


class NormalizedObservationItem(BaseModel):
    """标准化观察值请求项。"""

    variable_version_id: UUID
    raw_observation_id: UUID | None = Field(None, description="原始观察值 ID（必须非空）")
    value: str = Field(..., min_length=1)
    unit: str | None = Field(None, max_length=64)


class CreateFactRequest(BaseModel):
    """创建事实请求。"""

    fact_type: Literal["experiment_run", "simulation_run", "document_record", "model_execution"]
    template_version_id: UUID
    object_id: UUID
    subject_id: str = Field(..., min_length=1, max_length=256)
    started_at: datetime
    ended_at: datetime | None = None
    method_version_id: UUID | None = None
    raw: list[RawObservationItem] = Field(default_factory=list)
    normalized: list[NormalizedObservationItem] = Field(default_factory=list)
    artifacts: list[UUID] = Field(default_factory=list)
    idempotency_key: str | None = Field(None, max_length=256)


class ReviseFactRequest(BaseModel):
    """修订事实请求。"""

    reason: str = Field(..., min_length=1, max_length=2000)
    subject_id: str | None = Field(None, max_length=256)
    method_version_id: UUID | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    raw: list[RawObservationItem] | None = None
    normalized: list[NormalizedObservationItem] | None = None
    artifacts: list[UUID] | None = None


# ---- 响应模型 ----


class FactRevisionResponse(BaseModel):
    """事实修订响应。"""

    fact_id: str
    revision: int
    revision_id: str
    fact_type: str
    subject_id: str
    status: str
    task_code: str | None = None
    task_name: str | None = None
    department_name: str | None = None
    operator: str | None = None
    data_summary: str | None = None


class FactListResponse(BaseModel):
    """事实分页列表响应。"""

    items: list[FactRevisionResponse]
    next_cursor: str | None
    group_counts: dict[str, int] = Field(
        default_factory=dict,
        description="每个 task_code 对应的事实总数（不受分页限制）",
    )


class RawObservationResponse(BaseModel):
    """原始观察值响应。"""

    id: str
    fact_revision_id: str
    source_path: str
    source_value: str
    source_unit: str | None
    source_name: str | None
    artifact_id: str | None


class NormalizedObservationResponse(BaseModel):
    """标准化观察值响应。"""

    id: str
    fact_revision_id: str
    variable_version_id: str
    raw_observation_id: str
    value: str
    unit: str | None


class ObservationsResponse(BaseModel):
    """观察值响应。"""

    raw: list[RawObservationResponse]
    normalized: list[NormalizedObservationResponse]


# ---- 辅助函数 ----


def _ref_to_response(ref: FactRevisionRef) -> FactRevisionResponse:
    """将 FactRevisionRef 转为响应模型。"""
    return FactRevisionResponse(
        fact_id=str(ref.fact_id),
        revision=ref.revision,
        revision_id=str(ref.revision_id),
        fact_type=ref.fact_type,
        subject_id=ref.subject_id,
        status=ref.status,
    )


def _raw_to_response(r: RawObservation) -> RawObservationResponse:
    """将 RawObservation 转为响应模型。"""
    return RawObservationResponse(
        id=str(r.id),
        fact_revision_id=str(r.fact_revision_id),
        source_path=r.source_path,
        source_value=r.source_value,
        source_unit=r.source_unit,
        source_name=r.source_name,
        artifact_id=str(r.artifact_id) if r.artifact_id else None,
    )


def _normalized_to_response(
    n: NormalizedObservation,
) -> NormalizedObservationResponse:
    """将 NormalizedObservation 转为响应模型。"""
    return NormalizedObservationResponse(
        id=str(n.id),
        fact_revision_id=str(n.fact_revision_id),
        variable_version_id=str(n.variable_version_id),
        raw_observation_id=str(n.raw_observation_id),
        value=n.value,
        unit=n.unit,
    )


# ---- 端点 ----


@facts_router.post("", response_model=FactRevisionResponse, status_code=201)
async def create_fact(
    body: CreateFactRequest,
    current_user: WriteUserDep,
    service: FactServiceDep,
) -> FactRevisionResponse:
    """创建事实（revision 1）。

    创建一个新事实，包含原始与标准化观察值和工件链接。
    支持幂等键去重：相同 idempotency_key 不会创建重复事实。
    """
    from packages.facts.observations import (
        NormalizedObservationInput,
        RawObservationInput,
    )

    command = CreateFactCommand(
        fact_type=body.fact_type,
        template_version_id=body.template_version_id,
        organization_id=service.organization_id,
        object_id=body.object_id,
        subject_id=body.subject_id,
        started_at=body.started_at,
        ended_at=body.ended_at,
        method_version_id=body.method_version_id,
        raw=tuple(
            RawObservationInput(
                source_path=r.source_path,
                source_value=r.source_value,
                source_unit=r.source_unit,
                source_name=r.source_name,
                artifact_id=r.artifact_id,
                id=r.id,
            )
            for r in body.raw
        ),
        normalized=tuple(
            NormalizedObservationInput(
                variable_version_id=n.variable_version_id,
                raw_observation_id=n.raw_observation_id,
                value=n.value,
                unit=n.unit,
            )
            for n in body.normalized
        ),
        artifacts=tuple(body.artifacts),
        idempotency_key=body.idempotency_key,
        created_by=current_user.user_id,
    )
    ref = await service.create(command)
    return _ref_to_response(ref)


@facts_router.get("", response_model=FactListResponse)
async def list_facts(
    current_user: ReadUserDep,
    service: FactServiceDep,
    fact_type: str | None = Query(None, description="按事实类型过滤"),
    object_id: UUID | None = Query(None, description="按工业对象过滤"),  # noqa: B008
    status: str | None = Query(None, description="按状态过滤"),
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> FactListResponse:
    """分页列出事实（支持按 fact_type, object_id, status 过滤）。"""
    filters: dict = {}
    if fact_type is not None:
        filters["fact_type"] = fact_type
    if object_id is not None:
        filters["object_id"] = object_id
    if status is not None:
        filters["status"] = status

    refs, next_cursor = await service.list_facts(
        filters=filters if filters else None,
        cursor=cursor,
        page_size=page_size,
    )

    # 直接从 fact_revision 表读快照字段（零 JOIN）
    items = [_ref_to_response(r) for r in refs]
    group_counts: dict[str, int] = {}

    # 实验室级数据隔离：非管理员且无实验室 → 返回空列表
    if should_filter_by_department(current_user) and current_user.department_id is None:
        return FactListResponse(items=[], next_cursor=None, group_counts={})

    if items:
        import sqlalchemy as sa
        from sqlalchemy import func

        from packages.facts.entities import FactRevision

        revision_ids = [__import__("uuid").UUID(item.revision_id) for item in items]
        async with service.session_factory() as session:
            # 实验室级数据隔离：通过 flow_run_id 链路过滤事实
            # FactRevision.flow_run_id → FlowRun → FlowDefinitionVersionORM
            # → FlowDefinition.department_id
            if should_filter_by_department(current_user):
                from packages.components.flow_runtime import (
                    FlowDefinition,
                    FlowDefinitionVersionORM,
                    FlowRun,
                )

                dept_stmt = (
                    sa.select(FactRevision.id)
                    .join(FlowRun, FactRevision.flow_run_id == FlowRun.id)
                    .join(
                        FlowDefinitionVersionORM,
                        FlowRun.flow_version_id == FlowDefinitionVersionORM.id,
                    )
                    .join(
                        FlowDefinition,
                        FlowDefinitionVersionORM.flow_definition_id == FlowDefinition.id,
                    )
                    .where(
                        FactRevision.id.in_(revision_ids),
                        FlowDefinition.department_id == current_user.department_id,
                    )
                )
                dept_result = await session.execute(dept_stmt)
                allowed_ids = {str(row[0]) for row in dept_result}
                items = [item for item in items if item.revision_id in allowed_ids]
                revision_ids = [__import__("uuid").UUID(item.revision_id) for item in items]
                if not items:
                    return FactListResponse(items=[], next_cursor=None, group_counts={})

            # snap 查询：JOIN FlowDefinition 拿当前 display_name 覆盖快照 task_name
            from packages.components.flow_runtime import (
                FlowDefinition as _FD,
            )
            from packages.components.flow_runtime import (
                FlowDefinitionVersionORM as _FV,
            )
            from packages.components.flow_runtime import (
                FlowRun as _FR,
            )

            snap_stmt = (
                sa.select(
                    FactRevision.id,
                    FactRevision.task_code,
                    sa.func.coalesce(_FD.display_name, FactRevision.task_name).label("task_name"),
                    FactRevision.department_name,
                    FactRevision.operator,
                )
                .outerjoin(_FR, FactRevision.flow_run_id == _FR.id)
                .outerjoin(_FV, _FR.flow_version_id == _FV.id)
                .outerjoin(_FD, _FV.flow_definition_id == _FD.id)
                .where(FactRevision.id.in_(revision_ids))
            )
            snap_result = await session.execute(snap_stmt)
            snap_map: dict[str, tuple[str | None, str | None, str | None]] = {}
            for row in snap_result:
                snap_map[str(row[0])] = (row[1], row[2], row[3], row[4])
            for item in items:
                snap = snap_map.get(item.revision_id)
                if snap:
                    item.task_code = snap[0]
                    item.task_name = snap[1]
                    item.department_name = snap[2]
                    item.operator = snap[3]

            # 查每个 task_code 的总数（不受分页限制）
            if should_filter_by_department(current_user):
                count_stmt = (
                    sa.select(
                        FactRevision.task_code,
                        func.count(func.distinct(FactRevision.fact_id)),
                    )
                    .join(FlowRun, FactRevision.flow_run_id == FlowRun.id)
                    .join(
                        FlowDefinitionVersionORM,
                        FlowRun.flow_version_id == FlowDefinitionVersionORM.id,
                    )
                    .join(
                        FlowDefinition,
                        FlowDefinitionVersionORM.flow_definition_id == FlowDefinition.id,
                    )
                    .where(
                        FactRevision.task_code.isnot(None),
                        FlowDefinition.department_id == current_user.department_id,
                    )
                    .group_by(FactRevision.task_code)
                )
            else:
                count_stmt = (
                    sa.select(
                        FactRevision.task_code, func.count(func.distinct(FactRevision.fact_id))
                    )  # noqa: E501
                    .where(FactRevision.task_code.isnot(None))
                    .group_by(FactRevision.task_code)
                )
            count_result = await session.execute(count_stmt)
            group_counts = {str(row[0]): row[1] for row in count_result}

            # 查数据摘要（从 artifact JSON 取前3行）
            import json as json_mod

            from apps.api.main import _build_s3_repo
            from packages.common.artifacts import Artifact, ArtifactService
            from packages.facts.entities import FactArtifact

            s3_repo = _build_s3_repo()
            artifact_svc = ArtifactService(
                s3_repo=s3_repo,
                session_factory=service.session_factory,
                organization_id=service.organization_id,
                uploaded_by=current_user.user_id,
            )
            for item in items:
                try:
                    fa_stmt = (
                        sa.select(FactArtifact.artifact_id)
                        .where(
                            FactArtifact.fact_revision_id
                            == __import__("uuid").UUID(item.revision_id),  # noqa: E501
                            FactArtifact.artifact_id == Artifact.id,
                            Artifact.media_type == "application/json",
                        )
                        .limit(1)
                    )
                    fa_result = await session.execute(fa_stmt)
                    artifact_id = fa_result.scalar_one_or_none()
                    if artifact_id:
                        data_bytes = await artifact_svc.get_bytes(artifact_id)
                        parsed = json_mod.loads(data_bytes.decode("utf-8"))
                        pts = parsed.get("points", [])[:3]
                        if pts:
                            pairs = [f"{p.get('name', '')}={p.get('value', '')}" for p in pts[:3]]
                            total = len(parsed.get("points", []))
                            item.data_summary = (
                                f"共{total}个指标："
                                + "，".join(pairs)
                                + ("..." if total > 3 else "")
                            )  # noqa: E501
                except Exception:
                    _logger.warning("生成 data_summary 失败", exc_info=True)

    return FactListResponse(
        items=items,
        next_cursor=next_cursor,
        group_counts=group_counts,
    )


@facts_router.get("/search", response_model=FactListResponse)
async def search_facts(
    current_user: ReadUserDep,
    service: FactServiceDep,
    q: str = Query(..., min_length=1, description="搜索查询"),
    fact_type: str | None = Query(None, description="按事实类型过滤"),
    object_id: UUID | None = Query(None, description="按工业对象过滤"),  # noqa: B008
    status: str | None = Query(None, description="按状态过滤"),
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> FactListResponse:
    """全文搜索事实（基于 subject_id 和 fact_type）。"""
    filters: dict = {}
    if fact_type is not None:
        filters["fact_type"] = fact_type
    if object_id is not None:
        filters["object_id"] = object_id
    if status is not None:
        filters["status"] = status

    refs, next_cursor = await service.search(
        query=q,
        filters=filters if filters else None,
        cursor=cursor,
        page_size=page_size,
    )
    items = [_ref_to_response(r) for r in refs]
    if items:
        import sqlalchemy as sa

        from packages.facts.entities import FactRevision

        revision_ids = [__import__("uuid").UUID(item.revision_id) for item in items]
        async with service.session_factory() as session:
            # snap 查询：JOIN FlowDefinition 拿当前 display_name 覆盖快照 task_name
            from packages.components.flow_runtime import (
                FlowDefinition as _FD,
            )
            from packages.components.flow_runtime import (
                FlowDefinitionVersionORM as _FV,
            )
            from packages.components.flow_runtime import (
                FlowRun as _FR,
            )

            snap_stmt = (
                sa.select(
                    FactRevision.id,
                    FactRevision.task_code,
                    sa.func.coalesce(_FD.display_name, FactRevision.task_name).label("task_name"),
                    FactRevision.department_name,
                    FactRevision.operator,
                )
                .outerjoin(_FR, FactRevision.flow_run_id == _FR.id)
                .outerjoin(_FV, _FR.flow_version_id == _FV.id)
                .outerjoin(_FD, _FV.flow_definition_id == _FD.id)
                .where(FactRevision.id.in_(revision_ids))
            )
            snap_result = await session.execute(snap_stmt)
            snap_map: dict[str, tuple[str | None, str | None, str | None]] = {}
            for row in snap_result:
                snap_map[str(row[0])] = (row[1], row[2], row[3], row[4])
            for item in items:
                snap = snap_map.get(item.revision_id)
                if snap:
                    item.task_code = snap[0]
                    item.task_name = snap[1]
                    item.department_name = snap[2]
                    item.operator = snap[3]

            count_stmt = (
                sa.select(FactRevision.task_code, func.count(func.distinct(FactRevision.fact_id)))
                .where(FactRevision.task_code.isnot(None))
                .group_by(FactRevision.task_code)
            )
            count_result = await session.execute(count_stmt)
            group_counts = {str(row[0]): row[1] for row in count_result}

    return FactListResponse(
        items=items,
        next_cursor=next_cursor,
        group_counts=group_counts if items else {},
    )


@facts_router.get("/search-data", response_model=FactListResponse)
async def search_facts_by_data(
    current_user: ReadUserDep,
    service: FactServiceDep,
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
    import sqlalchemy as sa

    from packages.facts.entities import FactDataIndex, FactRevision

    # 构建 WHERE 条件
    conditions = []
    if q is not None:
        like_q = f"%{q}%"
        conditions.append(
            sa.or_(
                FactDataIndex.key.ilike(like_q),
                FactDataIndex.value_text.ilike(like_q),
            )
        )
    if key is not None:
        conditions.append(FactDataIndex.key == key)
    if value is not None:
        conditions.append(FactDataIndex.value_text == value)
    if min_value is not None:
        conditions.append(FactDataIndex.value_number >= min_value)
    if max_value is not None:
        conditions.append(FactDataIndex.value_number <= max_value)

    if not conditions:
        raise AppError(
            code="validation_failed",
            message="至少提供一个搜索条件（q / key / value / min_value / max_value）",
            retryable=False,
        )

    async with service.session_factory() as session:
        # 查匹配的 fact_revision_id（去重）
        stmt = (
            sa.select(FactDataIndex.fact_revision_id)
            .where(sa.and_(*conditions))
            .distinct()
            .limit(page_size)
        )
        result = await session.execute(stmt)
        revision_ids = [row[0] for row in result]

        if not revision_ids:
            return FactListResponse(items=[], next_cursor=None, group_counts={})

        # 查这些 revision 的快照信息（JOIN FlowDefinition 拿当前 display_name）
        from packages.components.flow_runtime import (
            FlowDefinition as _FD,
        )
        from packages.components.flow_runtime import (
            FlowDefinitionVersionORM as _FV,
        )
        from packages.components.flow_runtime import (
            FlowRun as _FR,
        )

        snap_stmt = (
            sa.select(
                FactRevision.id,
                FactRevision.fact_id,
                FactRevision.revision,
                FactRevision.fact_type,
                FactRevision.subject_id,
                FactRevision.task_code,
                sa.func.coalesce(_FD.display_name, FactRevision.task_name).label("task_name"),
                FactRevision.department_name,
                FactRevision.operator,
            )
            .outerjoin(_FR, FactRevision.flow_run_id == _FR.id)
            .outerjoin(_FV, _FR.flow_version_id == _FV.id)
            .outerjoin(_FD, _FV.flow_definition_id == _FD.id)
            .where(FactRevision.id.in_(revision_ids))
        )
        snap_result = await session.execute(snap_stmt)

        items: list[FactRevisionResponse] = []
        for row in snap_result:
            items.append(
                FactRevisionResponse(
                    fact_id=str(row[1]),
                    revision=row[2],
                    revision_id=str(row[0]),
                    fact_type=row[3],
                    subject_id=row[4],
                    status="active",  # status 在 Fact 表，这里统一返回 active
                    task_code=row[5],
                    task_name=row[6],
                    department_name=row[7],
                    operator=row[8],
                )
            )

        # 查 group_counts
        count_stmt = (
            sa.select(FactRevision.task_code, sa.func.count(sa.func.distinct(FactRevision.fact_id)))
            .where(
                FactRevision.id.in_(revision_ids),
                FactRevision.task_code.isnot(None),
            )
            .group_by(FactRevision.task_code)
        )
        count_result = await session.execute(count_stmt)
        group_counts = {str(row[0]): row[1] for row in count_result}

        # 查 data_summary（从 artifact JSON 取前3行）
        import json as json_mod

        from apps.api.main import _build_s3_repo
        from packages.common.artifacts import Artifact, ArtifactService
        from packages.facts.entities import FactArtifact

        s3_repo = _build_s3_repo()
        artifact_svc = ArtifactService(
            s3_repo=s3_repo,
            session_factory=service.session_factory,
            organization_id=service.organization_id,
            uploaded_by=current_user.user_id,
        )
        for item in items:
            try:
                fa_stmt = (
                    sa.select(FactArtifact.artifact_id)
                    .join(Artifact, FactArtifact.artifact_id == Artifact.id)
                    .where(
                        FactArtifact.fact_revision_id == __import__("uuid").UUID(item.revision_id),
                        Artifact.media_type == "application/json",
                    )
                    .limit(1)
                )
                fa_result = await session.execute(fa_stmt)
                artifact_id = fa_result.scalar_one_or_none()
                if artifact_id:
                    data_bytes = await artifact_svc.get_bytes(artifact_id)
                    parsed = json_mod.loads(data_bytes.decode("utf-8"))
                    pts = parsed.get("points", [])[:3]
                    if pts:
                        pairs = [f"{p.get('name', '')}={p.get('value', '')}" for p in pts[:3]]
                        total = len(parsed.get("points", []))
                        item.data_summary = (
                            f"共{total}个指标：" + "，".join(pairs) + ("..." if total > 3 else "")
                        )  # noqa: E501
            except Exception:
                _logger.warning("生成 data_summary 失败", exc_info=True)

    return FactListResponse(
        items=items,
        next_cursor=None,
        group_counts=group_counts,
    )


@facts_router.get("/{fact_id}", response_model=FactRevisionResponse)
async def get_fact(
    fact_id: UUID,
    current_user: ReadUserDep,
    service: FactServiceDep,
) -> FactRevisionResponse:
    """获取事实的最新修订。"""
    ref = await service.get(fact_id)
    return _ref_to_response(ref)


@facts_router.get("/{fact_id}/revisions", response_model=FactListResponse)
async def list_revisions(
    fact_id: UUID,
    current_user: ReadUserDep,
    service: FactServiceDep,
) -> FactListResponse:
    """列出事实的所有修订历史。"""
    refs = await service.list_revisions(fact_id)
    return FactListResponse(
        items=[_ref_to_response(r) for r in refs],
        next_cursor=None,
    )


@facts_router.get(
    "/{fact_id}/revisions/{revision}",
    response_model=FactRevisionResponse,
)
async def get_revision(
    fact_id: UUID,
    revision: int,
    current_user: ReadUserDep,
    service: FactServiceDep,
) -> FactRevisionResponse:
    """获取事实的特定修订。"""
    ref = await service.get(fact_id, revision=revision)
    return _ref_to_response(ref)


@facts_router.get("/{fact_id}/observations", response_model=ObservationsResponse)
async def get_observations(
    fact_id: UUID,
    current_user: ReadUserDep,
    service: FactServiceDep,
    revision: int | None = Query(None, description="修订号，None 表示最新"),
) -> ObservationsResponse:
    """获取事实的观察值（原始 + 标准化）。"""
    raws, norms = await service.get_observations(fact_id, revision=revision)
    return ObservationsResponse(
        raw=[_raw_to_response(r) for r in raws],
        normalized=[_normalized_to_response(n) for n in norms],
    )


@facts_router.get("/{fact_id}/data")
async def get_fact_data(
    fact_id: UUID,
    current_user: ReadUserDep,
    service: FactServiceDep,
) -> dict:
    """获取事实关联的提取数据（从 artifact 下载 JSON）。

    返回 {"metadata": {...}, "points": [...], "series": [...]} 格式的干净数据。
    """
    import json as json_mod

    import sqlalchemy as sa

    from apps.api.main import _build_s3_repo
    from packages.common.artifacts import ArtifactService
    from packages.facts.entities import FactArtifact, FactRevision

    # 获取最新修订
    fact = await service.get(fact_id)
    revision_id = fact.revision_id

    async with service.session_factory() as session:
        # 查 fact_artifact + artifact，找 JSON 类型的（提取数据）
        from packages.common.artifacts import Artifact

        result = await session.execute(
            sa.select(FactArtifact, Artifact)
            .where(
                FactArtifact.fact_revision_id == revision_id,
                FactArtifact.artifact_id == Artifact.id,
                Artifact.media_type == "application/json",
            )
            .limit(1)
        )
        row = result.first()
        if row is None:
            return {"metadata": {}, "points": [], "series": []}

        fa = row[0]
        # 下载 artifact 内容
        s3_repo = _build_s3_repo()
        artifact_svc = ArtifactService(
            s3_repo=s3_repo,
            session_factory=service.session_factory,
            organization_id=service.organization_id,
            uploaded_by=current_user.user_id,
        )
        data_bytes = await artifact_svc.get_bytes(fa.artifact_id)
        result_data = json_mod.loads(data_bytes.decode("utf-8"))

        if "points" not in result_data:
            result_data["points"] = []
        if "series" not in result_data:
            result_data["series"] = []

        # 优先从快照字段读任务信息（零 JOIN），旧数据 fallback 到实时反查
        task_info: dict = {}
        try:
            rev_stmt = sa.select(FactRevision).where(FactRevision.id == revision_id)
            rev_record = (await session.execute(rev_stmt)).scalar_one_or_none()
            if rev_record and (rev_record.task_code or rev_record.task_name):
                # 快照命中
                task_info = {
                    "task_name": rev_record.task_name,
                    "task_source": rev_record.department_name,
                    "operator": rev_record.operator,
                    "project_name": None,
                    "data_interface": None,
                    "created_at": None,
                }
                # 通过 flow_run_id 外键补查 project_name, data_interface 和 created_at
                if rev_record.flow_run_id:
                    from packages.components.flow_runtime import (
                        FlowDefinition,
                        FlowDefinitionVersionORM,
                        FlowRun,
                    )

                    run_stmt = sa.select(FlowRun).where(FlowRun.id == rev_record.flow_run_id)
                    run_record = (await session.execute(run_stmt)).scalar_one_or_none()
                    if run_record:
                        fv_stmt = sa.select(FlowDefinitionVersionORM).where(
                            FlowDefinitionVersionORM.id == run_record.flow_version_id
                        )  # noqa: E501
                        fv = (await session.execute(fv_stmt)).scalar_one_or_none()
                        if fv:
                            fd_stmt = sa.select(FlowDefinition).where(
                                FlowDefinition.id == fv.flow_definition_id
                            )  # noqa: E501
                            fd = (await session.execute(fd_stmt)).scalar_one_or_none()
                            if fd:
                                nodes = fv.nodes_json or []
                                comp_names = list(
                                    {
                                        n.get("component_name", "")
                                        for n in nodes
                                        if n.get("component_name")
                                    }
                                )  # noqa: E501
                                task_info["project_name"] = fd.project_name
                                task_info["created_at"] = (
                                    fd.created_at.isoformat() if fd.created_at else None
                                )  # noqa: E501
                                # 查所属单位名称
                                if fd.department_id:
                                    from packages.departments.entities import Department

                                    dept_stmt = sa.select(Department).where(
                                        Department.id == fd.department_id
                                    )  # noqa: E501
                                    dept_record = (
                                        await session.execute(dept_stmt)
                                    ).scalar_one_or_none()  # noqa: E501
                                    task_info["department_name"] = (
                                        dept_record.display_name if dept_record else None
                                    )  # noqa: E501
                                else:
                                    task_info["department_name"] = None
                                # 查每个组件的实验对象→设备→部门链路
                                data_source_list = []
                                for comp_name in comp_names:
                                    ds: dict = {"component": comp_name}
                                    # 查组件的 display_name 和 experimental_object_code
                                    import yaml as yaml_lib

                                    from packages.components.registry import (
                                        Component,
                                        ComponentVersion,
                                    )

                                    cv_stmt = (
                                        sa.select(ComponentVersion)
                                        .join(
                                            Component, ComponentVersion.component_id == Component.id
                                        )  # noqa: E501
                                        .where(Component.name == comp_name)
                                        .order_by(ComponentVersion.created_at.desc())
                                        .limit(1)
                                    )
                                    cv = (await session.execute(cv_stmt)).scalar_one_or_none()
                                    if cv:
                                        try:
                                            manifest = yaml_lib.safe_load(cv.manifest_yaml)
                                            ds["component_display_name"] = manifest.get(
                                                "display_name", comp_name
                                            )  # noqa: E501
                                        except Exception:
                                            ds["component_display_name"] = comp_name
                                    if cv and cv.experimental_object_code:
                                        ds["experimental_object_code"] = cv.experimental_object_code
                                        from packages.standards.objects import IndustrialObject

                                        obj_stmt = sa.select(IndustrialObject).where(
                                            IndustrialObject.code == cv.experimental_object_code
                                        )  # noqa: E501
                                        obj = (await session.execute(obj_stmt)).scalar_one_or_none()
                                        if obj:
                                            ds["object_name"] = obj.display_name
                                            if obj.equipment_id:
                                                from packages.equipment.entities import Equipment

                                                eq_stmt = sa.select(Equipment).where(
                                                    Equipment.id == obj.equipment_id
                                                )  # noqa: E501
                                                eq = (
                                                    await session.execute(eq_stmt)
                                                ).scalar_one_or_none()  # noqa: E501
                                                if eq:
                                                    ds["equipment_name"] = eq.display_name
                                                    if eq.department_id:
                                                        from packages.departments.entities import (
                                                            Department,
                                                        )

                                                        dept_stmt = sa.select(Department).where(
                                                            Department.id == eq.department_id
                                                        )  # noqa: E501
                                                        dept = (
                                                            await session.execute(dept_stmt)
                                                        ).scalar_one_or_none()  # noqa: E501
                                                        if dept:
                                                            ds["department_name"] = (
                                                                dept.display_name
                                                            )  # noqa: E501
                                    data_source_list.append(ds)
                                task_info["data_interface"] = (
                                    ", ".join(comp_names) if comp_names else None
                                )  # noqa: E501
                                task_info["data_source_list"] = data_source_list
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(f"Failed to query data_source_list: {e}")

        # 快照没命中，fallback 到通过 flow_run_id 外键反查（兼容旧数据）
        if not task_info:
            try:
                from packages.components.flow_runtime import (
                    FlowDefinition,
                    FlowDefinitionVersionORM,
                    FlowRun,
                )

                # 优先用 flow_run_id 外键（不再解析 source_path 字符串）
                rev_stmt2 = sa.select(FactRevision.flow_run_id).where(
                    FactRevision.id == revision_id
                )  # noqa: E501
                flow_run_id = (await session.execute(rev_stmt2)).scalar_one_or_none()

                if flow_run_id:
                    run_stmt = sa.select(FlowRun).where(FlowRun.id == flow_run_id)
                    run_record = (await session.execute(run_stmt)).scalar_one_or_none()
                    if run_record:
                        fv_stmt = sa.select(FlowDefinitionVersionORM).where(
                            FlowDefinitionVersionORM.id == run_record.flow_version_id
                        )  # noqa: E501
                        fv = (await session.execute(fv_stmt)).scalar_one_or_none()
                        if fv:
                            fd_stmt = sa.select(FlowDefinition).where(
                                FlowDefinition.id == fv.flow_definition_id
                            )  # noqa: E501
                            fd = (await session.execute(fd_stmt)).scalar_one_or_none()
                            if fd:
                                dept_name = None
                                if fd.department_id:
                                    from packages.departments.entities import Department

                                    dept_stmt = sa.select(Department).where(
                                        Department.id == fd.department_id
                                    )  # noqa: E501
                                    dept_record = (
                                        await session.execute(dept_stmt)
                                    ).scalar_one_or_none()  # noqa: E501
                                    if dept_record:
                                        dept_name = dept_record.display_name

                                nodes = fv.nodes_json or []
                                comp_names = list(
                                    {
                                        n.get("component_name", "")
                                        for n in nodes
                                        if n.get("component_name")
                                    }
                                )  # noqa: E501

                                task_info = {
                                    "task_name": fd.display_name,
                                    "task_source": dept_name,
                                    "project_name": fd.project_name,
                                    "department_name": dept_name,
                                    "data_interface": ", ".join(comp_names) if comp_names else None,
                                    "created_at": fd.created_at.isoformat()
                                    if fd.created_at
                                    else None,  # noqa: E501
                                }
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(
                    f"Failed to query task info for fact {fact_id}: {e}"
                )  # noqa: E501

        # 把任务信息附加到返回结果
        if task_info:
            result_data["task_info"] = task_info

        # 查原始文件（PDF 等）：先从 fact_artifact 找非 JSON 的，再从 raw_observation.source_name 找
        try:
            # 方式1：从 fact_artifact 找非 JSON artifact
            pdf_stmt = (
                sa.select(FactArtifact, Artifact)
                .where(
                    FactArtifact.fact_revision_id == revision_id,
                    FactArtifact.artifact_id == Artifact.id,
                    Artifact.media_type != "application/json",
                )
                .limit(1)
            )
            pdf_result = await session.execute(pdf_stmt)
            pdf_row = pdf_result.first()
            if pdf_row:
                pdf_artifact = pdf_row[1]
                result_data["source_file"] = {
                    "filename": pdf_artifact.filename or "原始文件",
                    "media_type": pdf_artifact.media_type,
                    "artifact_id": str(pdf_artifact.id),
                }
            else:
                # 方式2：从 raw_observation.source_name 提取 artifact:xxx
                from packages.facts.entities import RawObservation

                raw_name_stmt = (
                    sa.select(RawObservation.source_name)
                    .where(RawObservation.fact_revision_id == revision_id)
                    .limit(1)
                )
                raw_name_result = await session.execute(raw_name_stmt)
                source_name = raw_name_result.scalar_one_or_none()
                if source_name and source_name.startswith("artifact:"):
                    artifact_id_str = source_name[len("artifact:") :]
                    # 查 artifact 详情
                    art_stmt = sa.select(Artifact).where(
                        Artifact.id == __import__("uuid").UUID(artifact_id_str)
                    )  # noqa: E501
                    art_result = await session.execute(art_stmt)
                    art_record = art_result.scalar_one_or_none()
                    if art_record:
                        result_data["source_file"] = {
                            "filename": art_record.filename or "原始文件",
                            "media_type": art_record.media_type,
                            "artifact_id": str(art_record.id),
                        }
        except Exception:
            _logger.warning("删除 artifact 文件失败", exc_info=True)

        return result_data


@facts_router.post("/{fact_id}/archive", status_code=204)
async def archive_fact(
    fact_id: UUID,
    current_user: WriteUserDep,
    service: FactServiceDep,
) -> None:
    """归档实验事实（tombstone，替代物理删除）。

    技术设计文档 F-03 §8.3：不可变表通过 tombstone 模式实现逻辑删除，
    将 Fact.status 设为 'archived'，不物理删除任何修订或证据记录。

    安全约定：
    - 事实修订（fact_revision）为不可变表，不允许 UPDATE/DELETE；
    - 仅更新 Fact 主表的 status 字段（tombstone）；
    - 归档后事实在列表查询中不可见（status != 'archived' 过滤）。

    Args:
        fact_id: 事实 UUID。
        current_user: 当前认证用户（需 fact:write 权限）。
        service: 事实服务。

    Raises:
        AppError: code="not_found"，当事实不存在时。
    """
    import sqlalchemy as sa

    from packages.common.database import session_scope
    from packages.facts.entities import Fact

    async with session_scope(service.session_factory) as session:
        result = await session.execute(
            sa.select(Fact).where(
                Fact.organization_id == service.organization_id,
                Fact.id == fact_id,
            )
        )
        fact = result.scalar_one_or_none()
        if fact is None:
            raise AppError(
                code="not_found",
                message=f"事实不存在: {fact_id}",
                retryable=False,
                fields={"fact_id": str(fact_id)},
            )
        fact.status = "archived"
        await session.flush()


@facts_router.delete("/{fact_id}", status_code=204)
async def delete_fact(
    fact_id: UUID,
    current_user: WriteUserDep,
    service: FactServiceDep,
) -> None:
    """物理删除实验事实。"""
    import sqlalchemy as sa

    from apps.api.main import _build_s3_repo
    from packages.common.artifacts import ArtifactService
    from packages.common.database import session_scope
    from packages.facts.entities import Fact, FactArtifact, FactRevision

    # 先查出关联的 artifact_id 列表，用于删 MinIO 文件
    async with service.session_factory() as session:
        art_result = await session.execute(
            sa.select(FactArtifact.artifact_id).where(
                FactArtifact.fact_revision_id.in_(
                    sa.select(FactRevision.id).where(FactRevision.fact_id == fact_id)
                )
            )
        )
        artifact_ids = [row[0] for row in art_result]

    # 删 MinIO 中的 artifact 文件
    if artifact_ids:
        try:
            s3_repo = _build_s3_repo()
            artifact_svc = ArtifactService(
                s3_repo=s3_repo,
                session_factory=service.session_factory,
                organization_id=service.organization_id,
                uploaded_by=current_user.user_id,
            )
            for aid in artifact_ids:
                await artifact_svc.delete_artifact(aid)
        except Exception:
            _logger.warning("删除 artifact 文件失败", exc_info=True)

    async with session_scope(service.session_factory) as session:
        # 删除关联的 FactRevision
        await session.execute(sa.delete(FactRevision).where(FactRevision.fact_id == fact_id))
        # 删除 Fact
        await session.execute(sa.delete(Fact).where(Fact.id == fact_id))
        await session.flush()


@facts_router.delete("/by-task/{task_code}", status_code=204)
async def delete_facts_by_task(
    task_code: str,
    current_user: WriteUserDep,
    service: FactServiceDep,
) -> None:
    """按任务编码批量删除事实。"""
    import sqlalchemy as sa

    from apps.api.main import _build_s3_repo
    from packages.common.artifacts import ArtifactService
    from packages.common.database import session_scope
    from packages.facts.entities import Fact, FactArtifact, FactRevision

    # 先查出关联的 fact_id 和 artifact_id
    async with service.session_factory() as session:
        result = await session.execute(
            sa.select(FactRevision.fact_id, FactRevision.id).where(
                FactRevision.task_code == task_code
            )  # noqa: E501
        )
        rows = result.all()
        fact_ids = list({row[0] for row in rows})
        revision_ids = [row[1] for row in rows]

        art_result = await session.execute(
            sa.select(FactArtifact.artifact_id).where(
                FactArtifact.fact_revision_id.in_(revision_ids)
            )
        )
        artifact_ids = [row[0] for row in art_result]

    # 删 MinIO 中的 artifact 文件
    if artifact_ids:
        try:
            s3_repo = _build_s3_repo()
            artifact_svc = ArtifactService(
                s3_repo=s3_repo,
                session_factory=service.session_factory,
                organization_id=service.organization_id,
                uploaded_by=current_user.user_id,
            )
            for aid in artifact_ids:
                await artifact_svc.delete_artifact(aid)
        except Exception:
            _logger.warning("删除 artifact 文件失败", exc_info=True)

    if fact_ids:
        async with session_scope(service.session_factory) as session:
            await session.execute(sa.delete(FactRevision).where(FactRevision.fact_id.in_(fact_ids)))
            await session.execute(sa.delete(Fact).where(Fact.id.in_(fact_ids)))
            await session.flush()


@facts_router.post("/{fact_id}/revise", response_model=FactRevisionResponse)
async def revise_fact(
    fact_id: UUID,
    body: ReviseFactRequest,
    current_user: WriteUserDep,
    service: FactServiceDep,
) -> FactRevisionResponse:
    """创建事实的新修订（旧修订不可变）。"""
    from packages.facts.observations import (
        NormalizedObservationInput,
        RawObservationInput,
    )

    changes: dict = {"reason": body.reason}
    if body.subject_id is not None:
        changes["subject_id"] = body.subject_id
    if body.method_version_id is not None:
        changes["method_version_id"] = body.method_version_id
    if body.started_at is not None:
        changes["started_at"] = body.started_at
    if body.ended_at is not None:
        changes["ended_at"] = body.ended_at
    if body.raw is not None:
        changes["raw"] = tuple(
            RawObservationInput(
                source_path=r.source_path,
                source_value=r.source_value,
                source_unit=r.source_unit,
                source_name=r.source_name,
                artifact_id=r.artifact_id,
                id=r.id,
            )
            for r in body.raw
        )
    if body.normalized is not None:
        changes["normalized"] = tuple(
            NormalizedObservationInput(
                variable_version_id=n.variable_version_id,
                raw_observation_id=n.raw_observation_id,
                value=n.value,
                unit=n.unit,
            )
            for n in body.normalized
        )
    if body.artifacts is not None:
        changes["artifacts"] = tuple(body.artifacts)

    ref = await service.revise(fact_id, reason=body.reason, changes=changes)
    return _ref_to_response(ref)
