"""研究域 API 路由。

端点分组（research_router, prefix=/api/v1/research）：
  POST   /workspaces                    — 创建工作空间（只需名称）
  GET    /workspaces                     — 列表（status/cursor/page_size）
  GET    /workspaces/{id}                — 详情
  PATCH  /workspaces/{id}                — 更新名称
  DELETE /workspaces/{id}                — 删除
  POST   /workspaces/{id}/archive        — 归档
  POST   /workspaces/{id}/evidence       — 加入证据
  DELETE /workspaces/{id}/evidence/{ref_id}  — 移除证据
  GET    /workspaces/{id}/evidence        — 证据列表
  POST   /workspaces/{id}/snapshot        — 冻结快照
  GET    /workspaces/{id}/snapshots       — 快照列表
  GET    /facts/search                    — 搜索 Fact

Timeline refactoring: 移除 POST /fork, PUT /question 端点。
所有端点使用 require_permission("research:use") 权限依赖。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.research.dtos import (
    EvidenceRefDTO,
    FactSummary,
    SnapshotRef,
    WorkspaceDetail,
    WorkspaceRef,
)
from packages.research.service import WorkspaceService
from packages.research.snapshots import EvidenceSnapshotService

#: 需 research:use 权限的当前用户依赖。
ResearchUserDep = Annotated[CurrentUser, Depends(require_permission("research:use"))]


# ---- DI 占位 ----


def get_workspace_service() -> WorkspaceService:
    """获取 WorkspaceService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_workspace_service must be overridden via dependency_overrides")


WorkspaceServiceDep = Annotated[WorkspaceService, Depends(get_workspace_service)]


def get_snapshot_service() -> EvidenceSnapshotService:
    """获取 EvidenceSnapshotService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_snapshot_service must be overridden via dependency_overrides")


SnapshotServiceDep = Annotated[EvidenceSnapshotService, Depends(get_snapshot_service)]


# ---- 路由实例 ----

research_router = APIRouter(prefix="/api/v1/research", tags=["research"])


# ---- 请求模型 ----


class CreateWorkspaceRequest(BaseModel):
    """创建工作空间请求。

    Timeline refactoring: 只需要名称，不再需要 question_text。
    """

    name: str = Field(..., min_length=1, max_length=256)


class UpdateWorkspaceRequest(BaseModel):
    """更新工作空间名称请求。"""

    name: str = Field(..., min_length=1, max_length=256)


class AddEvidenceRequest(BaseModel):
    """加入证据请求。"""

    source_namespace: str = Field(..., min_length=1, max_length=64)
    source_id: UUID


# ---- 响应模型 ----


class WorkspaceResponse(BaseModel):
    """工作空间响应。

    Timeline refactoring: 移除 current_question_version 和 forked_from_id，
    新增 latest_snapshot_number、turn_count 和 active_run_status。
    """

    workspace_id: str
    name: str
    status: str
    latest_snapshot_number: int | None = None
    turn_count: int = 0
    active_run_status: str | None = None


class WorkspaceListResponse(BaseModel):
    """工作空间分页列表响应。"""

    items: list[WorkspaceResponse]
    next_cursor: str | None


class SnapshotResponse(BaseModel):
    """快照响应。"""

    snapshot_id: str
    snapshot_number: int
    content_hash: str
    captured_at: str


class WorkspaceDetailResponse(BaseModel):
    """工作空间详情响应。

    Timeline refactoring: 移除 current_question，新增快照/轮次/活跃状态。
    """

    workspace_id: str
    name: str
    status: str
    evidence_count: int
    snapshots: list[SnapshotResponse]
    latest_snapshot_number: int | None = None
    turn_count: int = 0
    active_run_status: str | None = None


class EvidenceRefResponse(BaseModel):
    """证据引用响应。"""

    ref_id: str
    source_namespace: str
    source_id: str
    source_version: str | None
    source_name: str | None
    status: str


class EvidenceListResponse(BaseModel):
    """证据引用列表响应。"""

    items: list[EvidenceRefResponse]


class SnapshotListResponse(BaseModel):
    """快照列表响应。"""

    items: list[SnapshotResponse]


class FactSearchItemResponse(BaseModel):
    """Fact 搜索结果项响应。"""

    fact_id: str
    fact_type: str
    subject_id: str
    status: str
    department_name: str | None = None


class FactSearchResponse(BaseModel):
    """Fact 搜索响应。"""

    items: list[FactSearchItemResponse]
    next_cursor: str | None


# ---- 辅助函数 ----


def _workspace_ref_to_response(ref: WorkspaceRef) -> WorkspaceResponse:
    """将 WorkspaceRef 转为响应模型。"""
    return WorkspaceResponse(
        workspace_id=str(ref.workspace_id),
        name=ref.name,
        status=ref.status,
        latest_snapshot_number=ref.latest_snapshot_number,
        turn_count=ref.turn_count,
        active_run_status=ref.active_run_status,
    )


def _evidence_ref_to_response(ref: EvidenceRefDTO) -> EvidenceRefResponse:
    """将 EvidenceRefDTO 转为响应模型。"""
    return EvidenceRefResponse(
        ref_id=str(ref.ref_id),
        source_namespace=ref.source_namespace,
        source_id=str(ref.source_id),
        source_version=ref.source_version,
        source_name=ref.source_name,
        status=ref.status,
    )


def _snapshot_ref_to_response(ref: SnapshotRef) -> SnapshotResponse:
    """将 SnapshotRef 转为响应模型。"""
    return SnapshotResponse(
        snapshot_id=str(ref.snapshot_id),
        snapshot_number=ref.snapshot_number,
        content_hash=ref.content_hash,
        captured_at=ref.captured_at.isoformat() if ref.captured_at else "",
    )


def _fact_summary_to_response(summary: FactSummary) -> FactSearchItemResponse:
    """将 FactSummary 转为响应模型。"""
    return FactSearchItemResponse(
        fact_id=str(summary.fact_id),
        fact_type=summary.fact_type,
        subject_id=summary.subject_id,
        status=summary.status,
        department_name=summary.department_name,
    )


def _workspace_detail_to_response(detail: WorkspaceDetail) -> WorkspaceDetailResponse:
    """将 WorkspaceDetail 转为响应模型。"""
    return WorkspaceDetailResponse(
        workspace_id=str(detail.workspace_id),
        name=detail.name,
        status=detail.status,
        evidence_count=detail.evidence_count,
        snapshots=[_snapshot_ref_to_response(s) for s in detail.snapshots],
        latest_snapshot_number=detail.latest_snapshot_number,
        turn_count=detail.turn_count,
        active_run_status=detail.active_run_status,
    )


# ---- 端点 ----


@research_router.post("/workspaces", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    body: CreateWorkspaceRequest,
    current_user: ResearchUserDep,
    service: WorkspaceServiceDep,
) -> WorkspaceResponse:
    """创建研究工作空间（只需名称）。"""
    from packages.research.dtos import CreateWorkspaceCommand

    command = CreateWorkspaceCommand(name=body.name)
    ref = await service.create_workspace(command)
    return _workspace_ref_to_response(ref)


@research_router.get("/workspaces", response_model=WorkspaceListResponse)
async def list_workspaces(
    current_user: ResearchUserDep,
    service: WorkspaceServiceDep,
    status: str | None = Query(None, description="按状态过滤（draft/archived）"),
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> WorkspaceListResponse:
    """分页列出当前用户的研究工作空间。"""
    refs, next_cursor = await service.list_workspaces(
        status=status,
        cursor=cursor,
        page_size=page_size,
    )
    return WorkspaceListResponse(
        items=[_workspace_ref_to_response(r) for r in refs],
        next_cursor=next_cursor,
    )


@research_router.get("/workspaces/{workspace_id}", response_model=WorkspaceDetailResponse)
async def get_workspace(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    service: WorkspaceServiceDep,
) -> WorkspaceDetailResponse:
    """获取研究工作空间详情。"""
    detail = await service.get_workspace(workspace_id)
    return _workspace_detail_to_response(detail)


@research_router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: UUID,
    body: UpdateWorkspaceRequest,
    current_user: ResearchUserDep,
    service: WorkspaceServiceDep,
) -> WorkspaceResponse:
    """更新工作空间名称。"""
    ref = await service.update_workspace_name(workspace_id, body.name)
    return _workspace_ref_to_response(ref)


@research_router.delete("/workspaces/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    service: WorkspaceServiceDep,
) -> None:
    """删除研究工作空间（物理删除，CASCADE 级联子表）。"""
    await service.delete_workspace(workspace_id)


@research_router.post("/workspaces/{workspace_id}/archive", status_code=204)
async def archive_workspace(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    service: WorkspaceServiceDep,
) -> None:
    """归档研究工作空间。"""
    await service.archive_workspace(workspace_id)


@research_router.post("/workspaces/{workspace_id}/restore", status_code=204)
async def restore_workspace(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    service: WorkspaceServiceDep,
) -> None:
    """恢复已归档的研究工作空间为活跃状态。"""
    await service.restore_workspace(workspace_id)


# NOTE: fork_workspace and update_question endpoints removed in timeline refactoring.


@research_router.post(
    "/workspaces/{workspace_id}/evidence",
    response_model=EvidenceRefResponse,
    status_code=201,
)
async def add_evidence(
    workspace_id: UUID,
    body: AddEvidenceRequest,
    current_user: ResearchUserDep,
    service: WorkspaceServiceDep,
) -> EvidenceRefResponse:
    """加入证据引用。"""
    ref = await service.add_evidence(
        workspace_id,
        body.source_namespace,
        body.source_id,
    )
    return _evidence_ref_to_response(ref)


@research_router.delete(
    "/workspaces/{workspace_id}/evidence/{ref_id}",
    status_code=204,
)
async def remove_evidence(
    workspace_id: UUID,
    ref_id: UUID,
    current_user: ResearchUserDep,
    service: WorkspaceServiceDep,
) -> None:
    """移除证据引用（软删除）。"""
    await service.remove_evidence(workspace_id, ref_id)


@research_router.get(
    "/workspaces/{workspace_id}/evidence",
    response_model=EvidenceListResponse,
)
async def list_evidence(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    service: WorkspaceServiceDep,
) -> EvidenceListResponse:
    """列出工作空间的活跃证据引用。"""
    refs = await service.list_evidence(workspace_id)
    return EvidenceListResponse(
        items=[_evidence_ref_to_response(r) for r in refs],
    )


@research_router.post(
    "/workspaces/{workspace_id}/snapshot",
    response_model=SnapshotResponse,
    status_code=201,
)
async def freeze_snapshot(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    snapshot_service: SnapshotServiceDep,
) -> SnapshotResponse:
    """冻结证据快照。"""
    ref = await snapshot_service.freeze_snapshot(workspace_id)
    return _snapshot_ref_to_response(ref)


@research_router.get(
    "/workspaces/{workspace_id}/snapshots",
    response_model=SnapshotListResponse,
)
async def list_snapshots(
    workspace_id: UUID,
    current_user: ResearchUserDep,
    snapshot_service: SnapshotServiceDep,
) -> SnapshotListResponse:
    """列出工作空间的全部快照。"""
    refs = await snapshot_service.list_snapshots(workspace_id)
    return SnapshotListResponse(
        items=[_snapshot_ref_to_response(r) for r in refs],
    )


@research_router.get("/facts/search", response_model=FactSearchResponse)
async def search_facts(
    current_user: ResearchUserDep,
    service: WorkspaceServiceDep,
    q: str = Query(..., min_length=1, description="搜索查询"),
    cursor: str | None = Query(None, description="分页游标"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> FactSearchResponse:
    """搜索 Fact（委托 CoreFactProvider）。"""
    summaries, next_cursor = await service.search_facts(
        query=q,
        cursor=cursor,
        page_size=page_size,
    )
    return FactSearchResponse(
        items=[_fact_summary_to_response(s) for s in summaries],
        next_cursor=next_cursor,
    )
