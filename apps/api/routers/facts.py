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
from packages.common.errors import AppError  # noqa: E402
from packages.facts.observations import FactRef  # noqa: E402
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


class CreateFactRequest(BaseModel):
    """创建事实请求。"""

    fact_type: Literal["experiment_run", "simulation_run", "document_record", "model_execution"]
    object_id: UUID
    subject_id: str = Field(..., min_length=1, max_length=256)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    idempotency_key: str | None = Field(None, max_length=256)


# ---- 响应模型 ----


class FactResponse(BaseModel):
    """事实响应。"""

    fact_id: str
    fact_type: str
    subject_id: str
    status: str
    task_code: str | None = None
    task_name: str | None = None
    department_name: str | None = None
    operator: str | None = None
    run_operator: str | None = None
    equipment_name: str | None = None
    data_summary: str | None = None
    created_at: str | None = None


class FactListResponse(BaseModel):
    """事实分页列表响应。"""

    items: list[FactResponse]
    next_cursor: str | None
    group_counts: dict[str, int] = Field(
        default_factory=dict,
        description="每个 task_code 对应的事实总数（不受分页限制）",
    )


# ---- 辅助函数 ----


def _ref_to_response(ref: FactRef) -> FactResponse:
    """将 FactRef 转为响应模型。"""
    return FactResponse(
        fact_id=str(ref.fact_id),
        fact_type=ref.fact_type,
        subject_id=ref.subject_id,
        status=ref.status,
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

    items = [_ref_to_response(r) for r in refs]
    group_counts: dict[str, int] = {}

    if items:
        import sqlalchemy as sa

        from packages.facts.entities import Fact

        fact_ids = [__import__("uuid").UUID(item.fact_id) for item in items]
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
                    Fact.id,
                    Fact.task_code,
                    sa.func.coalesce(_FD.display_name, Fact.task_name).label("task_name"),
                    Fact.department_name,
                    Fact.operator,
                    Fact.run_operator,
                    Fact.equipment_name,
                    Fact.created_at,
                )
                .outerjoin(_FR, Fact.flow_run_id == _FR.id)
                .outerjoin(_FV, _FR.flow_version_id == _FV.id)
                .outerjoin(_FD, _FV.flow_definition_id == _FD.id)
                .where(Fact.id.in_(fact_ids))
            )
            snap_result = await session.execute(snap_stmt)
            snap_map: dict[str, tuple[str | None, ...]] = {}
            for row in snap_result:
                snap_map[str(row[0])] = (row[1], row[2], row[3], row[4], row[5], row[6], row[7])
            for item in items:
                snap = snap_map.get(item.fact_id)
                if snap:
                    item.task_code = snap[0]
                    item.task_name = snap[1]
                    item.department_name = snap[2]
                    item.operator = snap[3]
                    item.run_operator = snap[4]
                    item.equipment_name = snap[5]
                    item.created_at = snap[6].isoformat() if snap[6] else None

            # 查每个 task_code 的总数（不受分页限制）
            count_stmt = (
                sa.select(
                    Fact.task_code, func.count(func.distinct(Fact.id))
                )
                .where(Fact.task_code.isnot(None))
                .group_by(Fact.task_code)
            )
            count_result = await session.execute(count_stmt)
            group_counts = {str(row[0]): row[1] for row in count_result}

    # 查数据摘要（在独立 session 中执行，避免 ResourceClosedError）
    if items:
        import json as json_mod

        from apps.api.main import _build_s3_repo
        from packages.common.artifacts import Artifact, ArtifactService

        s3_repo = _build_s3_repo()
        artifact_svc = ArtifactService(
            s3_repo=s3_repo,
            session_factory=service.session_factory,
            department_id=service.department_id,
            uploaded_by=current_user.user_id,
        )
        async with service.session_factory() as session:
            for item in items:
                try:
                    fa_stmt = (
                        sa.select(Artifact.id, Artifact.media_type, Artifact.filename)
                        .where(
                            Artifact.id
                            == sa.select(Fact.source_artifact_id)
                            .where(Fact.id == __import__("uuid").UUID(item.fact_id))
                            .scalar_subquery(),
                            Artifact.media_type == "application/json",
                        )
                        .limit(1)
                    )
                    fa_result = await session.execute(fa_stmt)
                    art_row = fa_result.first()

                    # fallback: source_artifact_id 指向原始文件（非 JSON），
                    # 通过 flow_run_id 查找 JSON 结果 artifact
                    if art_row is None:
                        flow_run_row = await session.execute(
                            sa.select(Fact.flow_run_id).where(
                                Fact.id == __import__("uuid").UUID(item.fact_id)
                            )
                        )
                        flow_run_id_row = flow_run_row.scalar_one_or_none()
                        if flow_run_id_row:
                            fb_result = await session.execute(
                                sa.select(Artifact.id, Artifact.media_type, Artifact.filename)
                                .where(
                                    Artifact.media_type == "application/json",
                                    Artifact.filename
                                    == f"extract_{flow_run_id_row}.json",
                                )
                                .order_by(Artifact.created_at.desc())
                                .limit(1)
                            )
                            art_row = fb_result.first()

                    if art_row:
                        artifact_id = art_row[0]
                        data_bytes = await artifact_svc.get_bytes(artifact_id)
                        parsed = json_mod.loads(data_bytes.decode("utf-8"))
                        pts = parsed.get("points", [])
                        srs = parsed.get("series", [])
                        if pts:
                            pairs = [f"{p.get('name', '')}={p.get('value', '')}" for p in pts[:3]]
                            total = len(pts)
                            item.data_summary = (
                                f"共{total}个指标："
                                + "，".join(pairs)
                                + ("..." if total > 3 else "")
                            )
                        elif srs:
                            names = [s.get("name", f"序列{i + 1}") for i, s in enumerate(srs[:3])]
                            total = len(srs)
                            item.data_summary = (
                                f"共{total}组序列："
                                + "，".join(names)
                                + ("..." if total > 3 else "")
                            )
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

        from packages.facts.entities import Fact

        fact_ids = [__import__("uuid").UUID(item.fact_id) for item in items]
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
                    Fact.id,
                    Fact.task_code,
                    sa.func.coalesce(_FD.display_name, Fact.task_name).label("task_name"),
                    Fact.department_name,
                    Fact.operator,
                    Fact.run_operator,
                    Fact.equipment_name,
                    Fact.created_at,
                )
                .outerjoin(_FR, Fact.flow_run_id == _FR.id)
                .outerjoin(_FV, _FR.flow_version_id == _FV.id)
                .outerjoin(_FD, _FV.flow_definition_id == _FD.id)
                .where(Fact.id.in_(fact_ids))
            )
            snap_result = await session.execute(snap_stmt)
            snap_map: dict[str, tuple[str | None, ...]] = {}
            for row in snap_result:
                snap_map[str(row[0])] = (row[1], row[2], row[3], row[4], row[5], row[6], row[7])
            for item in items:
                snap = snap_map.get(item.fact_id)
                if snap:
                    item.task_code = snap[0]
                    item.task_name = snap[1]
                    item.department_name = snap[2]
                    item.operator = snap[3]
                    item.run_operator = snap[4]
                    item.equipment_name = snap[5]
                    item.created_at = snap[6].isoformat() if snap[6] else None

            count_stmt = (
                sa.select(Fact.task_code, func.count(func.distinct(Fact.id)))
                .where(Fact.task_code.isnot(None))
                .group_by(Fact.task_code)
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

    from packages.facts.entities import FactDataIndex, Fact

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
        # 查匹配的 fact_id（去重）
        stmt = (
            sa.select(FactDataIndex.fact_id)
            .where(sa.and_(*conditions))
            .distinct()
            .limit(page_size)
        )
        result = await session.execute(stmt)
        fact_ids = [row[0] for row in result]

        if not fact_ids:
            return FactListResponse(items=[], next_cursor=None, group_counts={})

        # 查这些 fact 的快照信息（JOIN FlowDefinition 拿当前 display_name）
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
                Fact.id,
                Fact.fact_type,
                Fact.subject_id,
                Fact.status,
                Fact.task_code,
                sa.func.coalesce(_FD.display_name, Fact.task_name).label("task_name"),
                Fact.department_name,
                Fact.operator,
                Fact.run_operator,
                Fact.equipment_name,
            )
            .outerjoin(_FR, Fact.flow_run_id == _FR.id)
            .outerjoin(_FV, _FR.flow_version_id == _FV.id)
            .outerjoin(_FD, _FV.flow_definition_id == _FD.id)
            .where(Fact.id.in_(fact_ids))
        )
        snap_result = await session.execute(snap_stmt)

        items: list[FactResponse] = []
        for row in snap_result:
            items.append(
                FactResponse(
                    fact_id=str(row[0]),
                    fact_type=row[1],
                    subject_id=row[2],
                    status=row[3],
                    task_code=row[4],
                    task_name=row[5],
                    department_name=row[6],
                    operator=row[7],
                    run_operator=row[8],
                    equipment_name=row[9],
                )
            )

        # 查 group_counts
        count_stmt = (
            sa.select(Fact.task_code, sa.func.count(sa.func.distinct(Fact.id)))
            .where(
                Fact.id.in_(fact_ids),
                Fact.task_code.isnot(None),
            )
            .group_by(Fact.task_code)
        )
        count_result = await session.execute(count_stmt)
        group_counts = {str(row[0]): row[1] for row in count_result}

        # 查 data_summary（从 artifact JSON 取前3行）
        import json as json_mod

        from apps.api.main import _build_s3_repo
        from packages.common.artifacts import Artifact, ArtifactService

        s3_repo = _build_s3_repo()
        artifact_svc = ArtifactService(
            s3_repo=s3_repo,
            session_factory=service.session_factory,
            department_id=service.department_id,
            uploaded_by=current_user.user_id,
        )
        for item in items:
            try:
                fa_stmt = (
                    sa.select(Artifact.id)
                    .where(
                        Artifact.id
                        == sa.select(Fact.source_artifact_id)
                        .where(Fact.id == __import__("uuid").UUID(item.fact_id))
                        .scalar_subquery(),
                        Artifact.media_type == "application/json",
                    )
                    .limit(1)
                )
                fa_result = await session.execute(fa_stmt)
                artifact_id = fa_result.scalar_one_or_none()

                # fallback: source_artifact_id 指向原始文件（非 JSON），
                # 通过 flow_run_id 查找 JSON 结果 artifact
                if artifact_id is None:
                    flow_run_row = await session.execute(
                        sa.select(Fact.flow_run_id).where(
                            Fact.id == __import__("uuid").UUID(item.fact_id)
                        )
                    )
                    flow_run_id_row = flow_run_row.scalar_one_or_none()
                    if flow_run_id_row:
                        fb_result = await session.execute(
                            sa.select(Artifact.id)
                            .where(
                                Artifact.media_type == "application/json",
                                Artifact.filename
                                == f"extract_{flow_run_id_row}.json",
                            )
                            .order_by(Artifact.created_at.desc())
                            .limit(1)
                        )
                        artifact_id = fb_result.scalar_one_or_none()

                if artifact_id:
                    data_bytes = await artifact_svc.get_bytes(artifact_id)
                    parsed = json_mod.loads(data_bytes.decode("utf-8"))
                    pts = parsed.get("points", [])
                    srs = parsed.get("series", [])
                    if pts:
                        pairs = [f"{p.get('name', '')}={p.get('value', '')}" for p in pts[:3]]
                        total = len(pts)
                        item.data_summary = (
                            f"共{total}个指标：" + "，".join(pairs) + ("..." if total > 3 else "")
                        )
                    elif srs:
                        names = [s.get("name", f"序列{i + 1}") for i, s in enumerate(srs[:3])]
                        total = len(srs)
                        item.data_summary = (
                            f"共{total}组序列：" + "，".join(names) + ("..." if total > 3 else "")
                        )
            except Exception as _e:
                _logger.warning("生成 data_summary 失败: %s", _e, exc_info=True)

    return FactListResponse(
        items=items,
        next_cursor=None,
        group_counts=group_counts,
    )


@facts_router.get("/{fact_id}", response_model=FactResponse)
async def get_fact(
    fact_id: UUID,
    current_user: ReadUserDep,
    service: FactServiceDep,
) -> FactResponse:
    """获取事实。"""
    ref = await service.get(fact_id)
    return _ref_to_response(ref)


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
    from packages.facts.entities import Fact

    fact = await service.get(fact_id)

    if fact is None:
        return {"metadata": {}, "points": [], "series": []}

    async with service.session_factory() as session:
        from packages.common.artifacts import Artifact

        # 查找 JSON artifact：优先用 source_artifact_id（如果它是 JSON），
        # 否则通过 flow_run_id 查找 extract_{run_id}.json 的 artifact
        result = await session.execute(
            sa.select(Artifact)
            .where(
                Artifact.id
                == sa.select(Fact.source_artifact_id)
                .where(Fact.id == fact_id)
                .scalar_subquery(),
                Artifact.media_type == "application/json",
            )
            .limit(1)
        )
        art_record = result.scalar_one_or_none()

        # 如果 source_artifact_id 指向的不是 JSON（指向原始文件），
        # 通过 flow_run_id 查找 JSON 结果 artifact
        if art_record is None:
            flow_run_id_row = (await session.execute(
                sa.select(Fact.flow_run_id).where(Fact.id == fact_id)
            )).scalar_one_or_none()
            if flow_run_id_row is not None:
                result = await session.execute(
                    sa.select(Artifact)
                    .where(
                        Artifact.media_type == "application/json",
                        Artifact.filename == f"extract_{flow_run_id_row}.json",
                    )
                    .order_by(Artifact.created_at.desc())
                    .limit(1)
                )
                art_record = result.scalar_one_or_none()

        if art_record is None:
            return {"metadata": {}, "points": [], "series": []}

        # 下载 artifact 内容（MinIO 文件不存在时返回空数据而非 500）
        s3_repo = _build_s3_repo()
        artifact_svc = ArtifactService(
            s3_repo=s3_repo,
            session_factory=service.session_factory,
            department_id=service.department_id,
            uploaded_by=current_user.user_id,
        )
        data_bytes: bytes | None = None
        json_error: str | None = None
        try:
            data_bytes = await artifact_svc.get_bytes(art_record.id)
        except Exception as exc:
            _logger.warning("JSON artifact 下载失败: %s — %s", art_record.id, exc)
            json_error = str(exc)[:200]

        if data_bytes is not None:
            result_data = json_mod.loads(data_bytes.decode("utf-8"))
        else:
            result_data = {"metadata": {}, "points": [], "series": []}

        if "points" not in result_data:
            result_data["points"] = []
        if "series" not in result_data:
            result_data["series"] = []

        # 优先从快照字段读任务信息（零 JOIN），旧数据 fallback 到实时反查
        task_info: dict = {}
        try:
            fact_stmt = sa.select(Fact).where(Fact.id == fact_id)
            fact_record = (await session.execute(fact_stmt)).scalar_one_or_none()
            if fact_record and (fact_record.task_code or fact_record.task_name):
                # 快照命中
                task_info = {
                    "task_name": fact_record.task_name,
                    "task_source": fact_record.department_name,
                    "operator": fact_record.operator,
                    "run_operator": fact_record.run_operator,
                    "equipment_name": fact_record.equipment_name,
                    "project_name": None,
                    "data_interface": None,
                    "created_at": None,
                }
                # 通过 flow_run_id 外键补查 project_name, data_interface 和 created_at
                if fact_record.flow_run_id:
                    from packages.components.flow_runtime import (
                        FlowDefinition,
                        FlowDefinitionVersionORM,
                        FlowRun,
                    )

                    run_stmt = sa.select(FlowRun).where(FlowRun.id == fact_record.flow_run_id)
                    run_record = (await session.execute(run_stmt)).scalar_one_or_none()
                    if run_record:
                        fv_stmt = sa.select(FlowDefinitionVersionORM).where(
                            FlowDefinitionVersionORM.id == run_record.flow_version_id
                        )
                        fv = (await session.execute(fv_stmt)).scalar_one_or_none()
                        if fv:
                            fd_stmt = sa.select(FlowDefinition).where(
                                FlowDefinition.id == fv.flow_definition_id
                            )
                            fd = (await session.execute(fd_stmt)).scalar_one_or_none()
                            if fd:
                                nodes = fv.nodes_json or []
                                comp_names = list(
                                    {
                                        n.get("component_name", "")
                                        for n in nodes
                                        if n.get("component_name")
                                    }
                                )
                                task_info["project_name"] = fd.project_name
                                task_info["created_at"] = (
                                    fd.created_at.isoformat() if fd.created_at else None
                                )
                                # 查所属单位名称
                                if fd.department_id:
                                    from packages.departments.entities import Department

                                    dept_stmt = sa.select(Department).where(
                                        Department.id == fd.department_id
                                    )
                                    dept_record = (
                                        await session.execute(dept_stmt)
                                    ).scalar_one_or_none()
                                    task_info["department_name"] = (
                                        dept_record.display_name if dept_record else None
                                    )
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
                                        )
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
                                            )
                                        except Exception:
                                            ds["component_display_name"] = comp_name
                                    if cv and cv.experimental_object_code:
                                        ds["experimental_object_code"] = cv.experimental_object_code
                                        from packages.standards.objects import IndustrialObject

                                        obj_stmt = sa.select(IndustrialObject).where(
                                            IndustrialObject.code == cv.experimental_object_code
                                        )
                                        obj = (await session.execute(obj_stmt)).scalar_one_or_none()
                                        if obj:
                                            ds["object_name"] = obj.display_name
                                            if obj.equipment_id:
                                                from packages.equipment.entities import Equipment

                                                eq_stmt = sa.select(Equipment).where(
                                                    Equipment.id == obj.equipment_id
                                                )
                                                eq = (
                                                    await session.execute(eq_stmt)
                                                ).scalar_one_or_none()
                                                if eq:
                                                    ds["equipment_name"] = eq.display_name
                                                    if eq.department_id:
                                                        from packages.departments.entities import (
                                                            Department,
                                                        )

                                                        dept_stmt = sa.select(Department).where(
                                                            Department.id == eq.department_id
                                                        )
                                                        dept = (
                                                            await session.execute(dept_stmt)
                                                        ).scalar_one_or_none()
                                                        if dept:
                                                            ds["department_name"] = (
                                                                dept.display_name
                                                            )
                                    data_source_list.append(ds)
                                task_info["data_interface"] = (
                                    ", ".join(comp_names) if comp_names else None
                                )
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

                # 用 flow_run_id 外键反查
                fr_stmt = sa.select(Fact.flow_run_id).where(Fact.id == fact_id)
                flow_run_id = (await session.execute(fr_stmt)).scalar_one_or_none()

                if flow_run_id:
                    run_stmt = sa.select(FlowRun).where(FlowRun.id == flow_run_id)
                    run_record = (await session.execute(run_stmt)).scalar_one_or_none()
                    if run_record:
                        fv_stmt = sa.select(FlowDefinitionVersionORM).where(
                            FlowDefinitionVersionORM.id == run_record.flow_version_id
                        )
                        fv = (await session.execute(fv_stmt)).scalar_one_or_none()
                        if fv:
                            fd_stmt = sa.select(FlowDefinition).where(
                                FlowDefinition.id == fv.flow_definition_id
                            )
                            fd = (await session.execute(fd_stmt)).scalar_one_or_none()
                            if fd:
                                dept_name = None
                                if fd.department_id:
                                    from packages.departments.entities import Department

                                    dept_stmt = sa.select(Department).where(
                                        Department.id == fd.department_id
                                    )
                                    dept_record = (
                                        await session.execute(dept_stmt)
                                    ).scalar_one_or_none()
                                    if dept_record:
                                        dept_name = dept_record.display_name

                                nodes = fv.nodes_json or []
                                comp_names = list(
                                    {
                                        n.get("component_name", "")
                                        for n in nodes
                                        if n.get("component_name")
                                    }
                                )

                                task_info = {
                                    "task_name": fd.display_name,
                                    "task_source": dept_name,
                                    "operator": fd.operator,
                                    "run_operator": (run_record.input_snapshot or {}).get(
                                        "_operator"
                                    )
                                    if run_record
                                    else None,
                                    "project_name": fd.project_name,
                                    "department_name": dept_name,
                                    "data_interface": ", ".join(comp_names) if comp_names else None,
                                    "created_at": fd.created_at.isoformat()
                                    if fd.created_at
                                    else None,
                                }
            except (sa.exc.SQLAlchemyError, KeyError, ValueError) as e:
                import logging

                logging.getLogger(__name__).warning(
                    "Failed to query task info for fact %s: %s",
                    fact_id,
                    e,
                    exc_info=True,
                )

        if task_info:
            result_data["task_info"] = task_info

        if json_error:
            result_data["data_error"] = f"数据文件丢失: {json_error}"

        # 查原始文件（PDF 等）：通过 Fact.source_artifact_id 找非 JSON artifact
        try:
            pdf_stmt = (
                sa.select(Artifact)
                .where(
                    Artifact.id
                    == sa.select(Fact.source_artifact_id)
                    .where(Fact.id == fact_id)
                    .scalar_subquery(),
                    Artifact.media_type != "application/json",
                )
                .limit(1)
            )
            pdf_result = await session.execute(pdf_stmt)
            pdf_artifact = pdf_result.scalar_one_or_none()
            if pdf_artifact:
                result_data["source_file"] = {
                    "filename": pdf_artifact.filename or "原始文件",
                    "media_type": pdf_artifact.media_type,
                    "artifact_id": str(pdf_artifact.id),
                }
        except Exception:
            _logger.warning("查找原始文件失败", exc_info=True)

        return result_data


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
    import sqlalchemy as sa

    from packages.common.database import session_scope
    from packages.facts.entities import Fact

    async with session_scope(service.session_factory) as session:
        result = await session.execute(
            sa.select(Fact).where(
                Fact.department_id == service.department_id,
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
    from packages.facts.entities import Fact

    # 先查出关联的 source_artifact_id，用于删 MinIO 文件
    async with service.session_factory() as session:
        art_result = await session.execute(
            sa.select(Fact.source_artifact_id).where(Fact.id == fact_id)
        )
        source_artifact_id = art_result.scalar_one_or_none()

    # 删 MinIO 中的 artifact 文件
    if source_artifact_id is not None:
        try:
            s3_repo = _build_s3_repo()
            artifact_svc = ArtifactService(
                s3_repo=s3_repo,
                session_factory=service.session_factory,
                department_id=service.department_id,
                uploaded_by=current_user.user_id,
            )
            await artifact_svc.delete_artifact(source_artifact_id)
        except Exception:
            _logger.warning("删除 artifact 文件失败", exc_info=True)

    async with session_scope(service.session_factory) as session:
        # 删除 Fact（FK CASCADE 会自动删除 FactDataIndex）
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
    from packages.facts.entities import Fact

    # 先查出关联的 fact_id 和 source_artifact_id
    async with service.session_factory() as session:
        result = await session.execute(
            sa.select(Fact.id, Fact.source_artifact_id).where(
                Fact.task_code == task_code
            )
        )
        rows = result.all()
        fact_ids = [row[0] for row in rows]
        artifact_ids = [row[1] for row in rows if row[1] is not None]

    # 删 MinIO 中的 artifact 文件
    if artifact_ids:
        try:
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

    if fact_ids:
        async with session_scope(service.session_factory) as session:
            await session.execute(sa.delete(Fact).where(Fact.id.in_(fact_ids)))
            await session.flush()
