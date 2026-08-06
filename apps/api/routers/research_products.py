"""研究产物 API 路由（阶段 3 新增）。

端点分组（research_products_router, prefix=/api/v1/research）：

# ── 候选产物 ──
GET    /workspaces/{id}/runs/{run_id}/candidates                    — 列出 Run 的全部候选产物
GET    /workspaces/{id}/runs/{run_id}/candidates/{candidate_id}      — 候选详情

# ── Derived Dataset ──
POST   /workspaces/{id}/derived-datasets                              — 从 RunArtifact 创建
GET    /workspaces/{id}/derived-datasets                              — 列表
GET    /workspaces/{id}/derived-datasets/{dataset_id}                  — 详情
PATCH  /workspaces/{id}/derived-datasets/{dataset_id}                  — 编辑元数据
GET    /workspaces/{id}/derived-datasets/{dataset_id}/versions         — 版本历史
GET    /workspaces/{id}/derived-datasets/{dataset_id}/versions/{vn}    — 版本详情

# ── ResearchView ──
POST   /workspaces/{id}/views                                         — 从 RunArtifact 创建
GET    /workspaces/{id}/views                                         — 列表
GET    /workspaces/{id}/views/{view_id}                                — 详情
PATCH  /workspaces/{id}/views/{view_id}                                — 编辑元数据
GET    /workspaces/{id}/views/{view_id}/versions                       — 版本历史
GET    /workspaces/{id}/views/{view_id}/versions/{vn}                  — 版本详情
GET    /workspaces/{id}/views/{view_id}/versions/{vn}/image            — 下载图片

# ── Insight ──
GET    /workspaces/{id}/insights                                      — 列表
GET    /workspaces/{id}/insights/{insight_id}                          — 详情
PATCH  /workspaces/{id}/insights/{insight_id}                          — 编辑元数据
GET    /workspaces/{id}/insights/{insight_id}/versions                 — 版本历史

# ── Insight Candidate ──
GET    /workspaces/{id}/runs/{run_id}/insight-candidates              — 列出候选
GET    /workspaces/{id}/runs/{run_id}/insight-candidates/{cid}         — 候选详情
POST   /workspaces/{id}/runs/{run_id}/insight-candidates/{cid}/accept — 接受
POST   /workspaces/{id}/runs/{run_id}/insight-candidates/{cid}/modify  — 修改
POST   /workspaces/{id}/runs/{run_id}/insight-candidates/{cid}/reject — 拒绝

# ── 产物列表 ──
GET    /workspaces/{id}/products                                      — 列出全部产物

# ── ResearchCatalog ──
GET    /catalog/search                                                — 搜索衍生数据

所有写端点使用 require_permission("research:use") 权限依赖。
参照 apps/api/routers/research_run.py 的 DI 占位 + Pydantic 模型模式。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.research.candidates import CandidateService
from packages.research.catalog import ResearchCatalogImpl
from packages.research.products import ProductService

#: 需 research:use 权限的当前用户依赖。
ResearchUserDep = Annotated[CurrentUser, Depends(require_permission("research:use"))]


# ---- DI 占位 ----


def get_product_service() -> ProductService:
    """获取 ProductService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_product_service must be overridden via dependency_overrides"
    )


ProductServiceDep = Annotated[ProductService, Depends(get_product_service)]


def get_candidate_service() -> CandidateService:
    """获取 CandidateService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_candidate_service must be overridden via dependency_overrides"
    )


CandidateServiceDep = Annotated[CandidateService, Depends(get_candidate_service)]


def get_catalog() -> ResearchCatalogImpl:
    """获取 ResearchCatalog 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError(
        "get_catalog must be overridden via dependency_overrides"
    )


CatalogDep = Annotated[ResearchCatalogImpl, Depends(get_catalog)]


# ---- 路由实例 ----

research_products_router = APIRouter(prefix="/api/v1/research", tags=["research-products"])


# ============================================================
# Pydantic 请求/响应模型
# ============================================================


class CreateDatasetRequest(BaseModel):
    """创建 DerivedDataset 请求。"""

    artifact_id: UUID
    name: str
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)


class UpdateDatasetMetadataRequest(BaseModel):
    """编辑 DerivedDataset 元数据请求。"""

    name: str | None = None
    summary: str | None = None
    tags: list[str] | None = None


class CreateViewRequest(BaseModel):
    """创建 ResearchView 请求。"""

    artifact_id: UUID
    name: str
    caption: str | None = None
    display_order: int = 0


class UpdateViewMetadataRequest(BaseModel):
    """编辑 ResearchView 元数据请求。"""

    name: str | None = None
    caption: str | None = None
    display_order: int | None = None


class UpdateInsightMetadataRequest(BaseModel):
    """编辑 Insight 元数据请求。"""

    name: str


class ModifyCandidateRequest(BaseModel):
    """修改 Insight 候选请求。"""

    conclusion: str | None = None
    scope: str | None = None
    evidence_refs: list[dict] | None = None
    method_refs: list[dict] | None = None
    confidence_level: str | None = None
    limitations: str | None = None
    evidence_source_label: str | None = None
    modification_note: str


class RejectCandidateRequest(BaseModel):
    """拒绝 Insight 候选请求。"""

    reason: str | None = None


class DatasetResponse(BaseModel):
    dataset_id: UUID
    name: str
    status: str
    current_version: int
    workspace_id: UUID


class ViewResponse(BaseModel):
    view_id: UUID
    name: str
    status: str
    current_version: int
    caption: str | None = None
    display_order: int = 0


class InsightResponse(BaseModel):
    insight_id: UUID
    name: str
    status: str
    current_version: int


# ============================================================
# ── 候选产物 ──
# ============================================================


@research_products_router.get(
    "/workspaces/{workspace_id}/runs/{run_id}/candidates",
)
async def list_candidates(
    workspace_id: UUID,
    run_id: UUID,
    _user: ResearchUserDep,
    candidate_service: CandidateServiceDep,
) -> dict:
    """列出 Run 的全部候选产物。"""
    candidates = await candidate_service.identify_candidates(workspace_id, run_id)
    return {
        "items": [
            {
                "candidate_type": c.candidate_type,
                "source_artifact_id": str(c.source_artifact_id) if c.source_artifact_id else None,
                "candidate_id": str(c.candidate_id),
                "source_run_id": str(c.source_run_id),
                "source_step_id": str(c.source_step_id) if c.source_step_id else None,
                "step_name": c.step_name,
                "step_status": c.step_status,
                "preview_data": c.preview_data,
                "status": c.status,
                "error_reason": c.error_reason,
            }
            for c in candidates
        ]
    }


@research_products_router.get(
    "/workspaces/{workspace_id}/runs/{run_id}/candidates/{candidate_id}",
)
async def get_candidate(
    workspace_id: UUID,
    run_id: UUID,
    candidate_id: UUID,
    _user: ResearchUserDep,
    candidate_service: CandidateServiceDep,
) -> dict:
    """获取候选产物详情。"""
    detail = await candidate_service.get_candidate_detail(
        workspace_id, run_id, candidate_id
    )
    return {
        "candidate_type": detail.candidate_type,
        "candidate_id": str(detail.candidate_id),
        "source_run_id": str(detail.source_run_id),
        "source_step_id": str(detail.source_step_id) if detail.source_step_id else None,
        "preview_data": detail.preview_data,
    }


# ============================================================
# ── Derived Dataset ──
# ============================================================


@research_products_router.post(
    "/workspaces/{workspace_id}/derived-datasets",
    status_code=201,
)
async def create_dataset(
    workspace_id: UUID,
    body: CreateDatasetRequest,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> DatasetResponse:
    """从 RunArtifact 创建 DerivedDataset。"""
    ref = await product_service.create_dataset(
        workspace_id=workspace_id,
        artifact_id=body.artifact_id,
        name=body.name,
        summary=body.summary,
        tags=body.tags,
    )
    return DatasetResponse(
        dataset_id=ref.dataset_id,
        name=ref.name,
        status=ref.status,
        current_version=ref.current_version,
        workspace_id=ref.workspace_id,
    )


@research_products_router.get(
    "/workspaces/{workspace_id}/derived-datasets",
)
async def list_datasets(
    workspace_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> dict:
    """列出 Workspace 内 DerivedDataset。"""
    refs = await product_service.list_datasets(workspace_id)
    return {
        "items": [
            {
                "dataset_id": str(r.dataset_id),
                "name": r.name,
                "status": r.status,
                "current_version": r.current_version,
                "workspace_id": str(r.workspace_id),
            }
            for r in refs
        ]
    }


@research_products_router.get(
    "/workspaces/{workspace_id}/derived-datasets/{dataset_id}",
)
async def get_dataset(
    workspace_id: UUID,
    dataset_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> dict:
    """DerivedDataset 详情。"""
    detail = await product_service.get_dataset(workspace_id, dataset_id)
    return {
        "dataset_id": str(detail.dataset_id),
        "workspace_id": str(detail.workspace_id),
        "name": detail.name,
        "summary": detail.summary,
        "tags": detail.tags,
        "status": detail.status,
        "current_version": detail.current_version,
        "source_run_id": str(detail.source_run_id),
        "source_snapshot_id": str(detail.source_snapshot_id) if detail.source_snapshot_id else None,
        "current_version_data": detail.current_version_data,
    }


@research_products_router.patch(
    "/workspaces/{workspace_id}/derived-datasets/{dataset_id}",
)
async def update_dataset(
    workspace_id: UUID,
    dataset_id: UUID,
    body: UpdateDatasetMetadataRequest,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> DatasetResponse:
    """编辑 DerivedDataset 元数据。"""
    ref = await product_service.update_dataset_metadata(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        name=body.name,
        summary=body.summary,
        tags=body.tags,
    )
    return DatasetResponse(
        dataset_id=ref.dataset_id,
        name=ref.name,
        status=ref.status,
        current_version=ref.current_version,
        workspace_id=ref.workspace_id,
    )


@research_products_router.get(
    "/workspaces/{workspace_id}/derived-datasets/{dataset_id}/versions",
)
async def list_dataset_versions(
    workspace_id: UUID,
    dataset_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> dict:
    """版本历史列表。"""
    refs = await product_service.list_dataset_versions(workspace_id, dataset_id)
    return {
        "items": [
            {
                "version_id": str(r.version_id),
                "dataset_id": str(r.dataset_id),
                "version_number": r.version_number,
                "content_hash": r.content_hash,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in refs
        ]
    }


@research_products_router.get(
    "/workspaces/{workspace_id}/derived-datasets/{dataset_id}/versions/{version_number}",
)
async def get_dataset_version(
    workspace_id: UUID,
    dataset_id: UUID,
    version_number: int,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> dict:
    """版本详情（含三段式数据 + field_manifest）。"""
    detail = await product_service.get_dataset_version(
        workspace_id, dataset_id, version_number
    )
    return {
        "version_id": str(detail.version_id),
        "dataset_id": str(detail.dataset_id),
        "version_number": detail.version_number,
        "metadata_content": detail.metadata_content,
        "points_content": detail.points_content,
        "series_content": detail.series_content,
        "field_manifest": detail.field_manifest,
        "content_hash": detail.content_hash,
        "source_run_id": str(detail.source_run_id),
        "source_step_id": str(detail.source_step_id) if detail.source_step_id else None,
        "source_artifact_id": str(detail.source_artifact_id) if detail.source_artifact_id else None,
        "created_at": detail.created_at.isoformat() if detail.created_at else None,
    }


# ============================================================
# ── ResearchView ──
# ============================================================


@research_products_router.post(
    "/workspaces/{workspace_id}/views",
    status_code=201,
)
async def create_view(
    workspace_id: UUID,
    body: CreateViewRequest,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> ViewResponse:
    """从 RunArtifact 创建 ResearchView。"""
    ref = await product_service.create_view(
        workspace_id=workspace_id,
        artifact_id=body.artifact_id,
        name=body.name,
        caption=body.caption,
        display_order=body.display_order,
    )
    return ViewResponse(
        view_id=ref.view_id,
        name=ref.name,
        status=ref.status,
        current_version=ref.current_version,
        caption=ref.caption,
        display_order=ref.display_order,
    )


@research_products_router.get(
    "/workspaces/{workspace_id}/views",
)
async def list_views(
    workspace_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> dict:
    """列出 Workspace 内 ResearchView。"""
    refs = await product_service.list_views(workspace_id)
    return {
        "items": [
            {
                "view_id": str(r.view_id),
                "name": r.name,
                "status": r.status,
                "current_version": r.current_version,
                "caption": r.caption,
                "display_order": r.display_order,
            }
            for r in refs
        ]
    }


@research_products_router.get(
    "/workspaces/{workspace_id}/views/{view_id}",
)
async def get_view(
    workspace_id: UUID,
    view_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> dict:
    """ResearchView 详情。"""
    detail = await product_service.get_view(workspace_id, view_id)
    return {
        "view_id": str(detail.view_id),
        "workspace_id": str(detail.workspace_id),
        "name": detail.name,
        "caption": detail.caption,
        "display_order": detail.display_order,
        "status": detail.status,
        "current_version": detail.current_version,
        "source_run_id": str(detail.source_run_id),
        "current_version_info": detail.current_version_info,
    }


@research_products_router.patch(
    "/workspaces/{workspace_id}/views/{view_id}",
)
async def update_view(
    workspace_id: UUID,
    view_id: UUID,
    body: UpdateViewMetadataRequest,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> ViewResponse:
    """编辑 ResearchView 元数据。"""
    ref = await product_service.update_view_metadata(
        workspace_id=workspace_id,
        view_id=view_id,
        name=body.name,
        caption=body.caption,
        display_order=body.display_order,
    )
    return ViewResponse(
        view_id=ref.view_id,
        name=ref.name,
        status=ref.status,
        current_version=ref.current_version,
        caption=ref.caption,
        display_order=ref.display_order,
    )


@research_products_router.get(
    "/workspaces/{workspace_id}/views/{view_id}/versions",
)
async def list_view_versions(
    workspace_id: UUID,
    view_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> dict:
    """版本历史列表。"""
    refs = await product_service.list_view_versions(workspace_id, view_id)
    return {
        "items": [
            {
                "version_id": str(r.version_id),
                "view_id": str(r.view_id),
                "version_number": r.version_number,
                "image_storage_path": r.image_storage_path,
                "image_format": r.image_format,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in refs
        ]
    }


@research_products_router.get(
    "/workspaces/{workspace_id}/views/{view_id}/versions/{version_number}",
)
async def get_view_version(
    workspace_id: UUID,
    view_id: UUID,
    version_number: int,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> dict:
    """版本详情。"""
    detail = await product_service.get_view_version(
        workspace_id, view_id, version_number
    )
    return {
        "version_id": str(detail.version_id),
        "view_id": str(detail.view_id),
        "version_number": detail.version_number,
        "image_storage_path": detail.image_storage_path,
        "image_format": detail.image_format,
        "image_width": detail.image_width,
        "image_height": detail.image_height,
        "image_content_hash": detail.image_content_hash,
        "chart_code_artifact_id": str(detail.chart_code_artifact_id) if detail.chart_code_artifact_id else None,
        "image_digest": detail.image_digest,
        "source_run_id": str(detail.source_run_id),
        "source_step_id": str(detail.source_step_id) if detail.source_step_id else None,
        "source_artifact_id": str(detail.source_artifact_id) if detail.source_artifact_id else None,
        "bound_dataset_version_id": str(detail.bound_dataset_version_id) if detail.bound_dataset_version_id else None,
        "chart_description": detail.chart_description,
        "created_at": detail.created_at.isoformat() if detail.created_at else None,
    }


@research_products_router.get(
    "/workspaces/{workspace_id}/views/{view_id}/versions/{version_number}/image",
)
async def download_view_image(
    workspace_id: UUID,
    view_id: UUID,
    version_number: int,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> Response:
    """下载图片（PNG/PDF）。"""
    detail = await product_service.get_view_version(
        workspace_id, view_id, version_number
    )
    media_type = "image/png" if detail.image_format == "png" else "application/pdf"
    # 返回存储路径信息，前端通过 artifact 下载端点获取实际内容
    # 实际实现中可通过 RunArtifactService 下载
    return Response(
        content=f"Redirect to MinIO: {detail.image_storage_path}",
        media_type=media_type,
        headers={"X-Image-Path": detail.image_storage_path},
    )


# ============================================================
# ── Insight ──
# ============================================================


@research_products_router.get(
    "/workspaces/{workspace_id}/insights",
)
async def list_insights(
    workspace_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> dict:
    """列出 Workspace 内 Insight。"""
    refs = await product_service.list_insights(workspace_id)
    return {
        "items": [
            {
                "insight_id": str(r.insight_id),
                "name": r.name,
                "status": r.status,
                "current_version": r.current_version,
            }
            for r in refs
        ]
    }


@research_products_router.get(
    "/workspaces/{workspace_id}/insights/{insight_id}",
)
async def get_insight(
    workspace_id: UUID,
    insight_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> dict:
    """Insight 详情。"""
    detail = await product_service.get_insight(workspace_id, insight_id)
    return {
        "insight_id": str(detail.insight_id),
        "workspace_id": str(detail.workspace_id),
        "name": detail.name,
        "status": detail.status,
        "current_version": detail.current_version,
        "source_run_id": str(detail.source_run_id) if detail.source_run_id else None,
        "current_version_data": detail.current_version_data,
    }


@research_products_router.patch(
    "/workspaces/{workspace_id}/insights/{insight_id}",
)
async def update_insight(
    workspace_id: UUID,
    insight_id: UUID,
    body: UpdateInsightMetadataRequest,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> InsightResponse:
    """编辑 Insight 元数据。"""
    ref = await product_service.update_insight_metadata(
        workspace_id=workspace_id,
        insight_id=insight_id,
        name=body.name,
    )
    return InsightResponse(
        insight_id=ref.insight_id,
        name=ref.name,
        status=ref.status,
        current_version=ref.current_version,
    )


@research_products_router.delete(
    "/workspaces/{workspace_id}/insights/{insight_id}",
    status_code=204,
)
async def delete_insight(
    workspace_id: UUID,
    insight_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> None:
    """删除 Insight（物理删除，不可恢复）。"""
    await product_service.delete_insight(
        workspace_id=workspace_id,
        insight_id=insight_id,
    )


@research_products_router.delete(
    "/workspaces/{workspace_id}/derived-datasets/{dataset_id}",
)
async def delete_dataset(
    workspace_id: UUID,
    dataset_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> None:
    """删除数据集（物理删除，不可恢复）。"""
    await product_service.delete_dataset(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
    )


@research_products_router.delete(
    "/workspaces/{workspace_id}/views/{view_id}",
)
async def delete_view(
    workspace_id: UUID,
    view_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> None:
    """删除视图（物理删除，不可恢复）。"""
    await product_service.delete_view(
        workspace_id=workspace_id,
        view_id=view_id,
    )


@research_products_router.get(
    "/workspaces/{workspace_id}/insights/{insight_id}/versions",
)
async def list_insight_versions(
    workspace_id: UUID,
    insight_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> dict:
    """版本历史列表。"""
    refs = await product_service.list_insight_versions(workspace_id, insight_id)
    return {
        "items": [
            {
                "version_id": str(r.version_id),
                "insight_id": str(r.insight_id),
                "version_number": r.version_number,
                "is_modified": r.is_modified,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in refs
        ]
    }


# ============================================================
# ── Insight Candidate ──
# ============================================================


@research_products_router.get(
    "/workspaces/{workspace_id}/runs/{run_id}/insight-candidates",
)
async def list_insight_candidates(
    workspace_id: UUID,
    run_id: UUID,
    _user: ResearchUserDep,
    candidate_service: CandidateServiceDep,
) -> dict:
    """列出 Run 的 Insight 候选。"""
    refs = await candidate_service.list_insight_candidates(workspace_id, run_id)
    return {
        "items": [
            {
                "candidate_id": str(r.candidate_id),
                "run_id": str(r.run_id),
                "step_id": str(r.step_id) if r.step_id else None,
                "status": r.status,
                "conclusion": r.conclusion,
                "evidence_source_label": r.evidence_source_label,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in refs
        ]
    }


@research_products_router.get(
    "/workspaces/{workspace_id}/runs/{run_id}/insight-candidates/{candidate_id}",
)
async def get_insight_candidate(
    workspace_id: UUID,
    run_id: UUID,
    candidate_id: UUID,
    _user: ResearchUserDep,
    candidate_service: CandidateServiceDep,
) -> dict:
    """候选详情。"""
    detail = await candidate_service.get_candidate_detail(
        workspace_id, run_id, candidate_id
    )
    return {
        "candidate_type": detail.candidate_type,
        "candidate_id": str(detail.candidate_id),
        "source_run_id": str(detail.source_run_id),
        "source_step_id": str(detail.source_step_id) if detail.source_step_id else None,
        "preview_data": detail.preview_data,
    }


@research_products_router.post(
    "/workspaces/{workspace_id}/runs/{run_id}/insight-candidates/{candidate_id}/accept",
    status_code=201,
)
async def accept_insight_candidate(
    workspace_id: UUID,
    run_id: UUID,
    candidate_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> InsightResponse:
    """接受候选 → 创建 Insight + v1。"""
    ref = await product_service.create_insight_from_accept(
        workspace_id=workspace_id,
        candidate_id=candidate_id,
    )
    return InsightResponse(
        insight_id=ref.insight_id,
        name=ref.name,
        status=ref.status,
        current_version=ref.current_version,
    )


@research_products_router.post(
    "/workspaces/{workspace_id}/runs/{run_id}/insight-candidates/{candidate_id}/modify",
    status_code=201,
)
async def modify_insight_candidate(
    workspace_id: UUID,
    run_id: UUID,
    candidate_id: UUID,
    body: ModifyCandidateRequest,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> InsightResponse:
    """修改候选 → 创建 Insight + v1。"""
    modified_fields: dict = {}
    if body.conclusion is not None:
        modified_fields["conclusion"] = body.conclusion
    if body.scope is not None:
        modified_fields["scope"] = body.scope
    if body.evidence_refs is not None:
        modified_fields["evidence_refs"] = body.evidence_refs
    if body.method_refs is not None:
        modified_fields["method_refs"] = body.method_refs
    if body.confidence_level is not None:
        modified_fields["confidence_level"] = body.confidence_level
    if body.limitations is not None:
        modified_fields["limitations"] = body.limitations
    if body.evidence_source_label is not None:
        modified_fields["evidence_source_label"] = body.evidence_source_label

    ref = await product_service.create_insight_from_modify(
        workspace_id=workspace_id,
        candidate_id=candidate_id,
        modified_fields=modified_fields,
        modification_note=body.modification_note,
    )
    return InsightResponse(
        insight_id=ref.insight_id,
        name=ref.name,
        status=ref.status,
        current_version=ref.current_version,
    )


@research_products_router.post(
    "/workspaces/{workspace_id}/runs/{run_id}/candidates/{candidate_id}/reject",
    status_code=204,
)
async def reject_candidate(
    workspace_id: UUID,
    run_id: UUID,
    candidate_id: UUID,
    body: RejectCandidateRequest,
    _user: ResearchUserDep,
    candidate_service: CandidateServiceDep,
) -> None:
    """拒绝任意类型候选 → 物理删除 artifact 或 insight 候选。"""
    import os
    from sqlalchemy import text as sa_text
    from packages.common.database import build_session_factory, session_scope
    url = os.getenv("IRIP_DATABASE_URL", "").replace("postgresql+psycopg://", "postgresql+psycopg_async://")
    factory = build_session_factory(url)
    # 先查是否为 insight 候选
    async with session_scope(factory, principal=_user) as session:
        result = await session.execute(
            sa_text("SELECT 1 FROM research_insight_candidate WHERE id = :cid"),
            {"cid": str(candidate_id)},
        )
        is_insight = result.fetchone() is not None

    if is_insight:
        await candidate_service.reject_insight_candidate(
            workspace_id=workspace_id,
            run_id=run_id,
            candidate_id=candidate_id,
            reason=body.reason,
        )
    else:
        # dataset/view 候选 → 物理删除 artifact
        async with session_scope(factory, principal=_user) as session:
            await session.execute(
                sa_text("DELETE FROM research_run_artifact WHERE id = :aid AND run_id = :rid"),
                {"aid": str(candidate_id), "rid": str(run_id)},
            )


@research_products_router.post(
    "/workspaces/{workspace_id}/runs/{run_id}/insight-candidates/{candidate_id}/reject",
    status_code=204,
)
async def reject_insight_candidate(
    workspace_id: UUID,
    run_id: UUID,
    candidate_id: UUID,
    body: RejectCandidateRequest,
    _user: ResearchUserDep,
    candidate_service: CandidateServiceDep,
) -> None:
    """拒绝 Insight 候选 → 物理删除（幂等）。"""
    await candidate_service.reject_insight_candidate(
        workspace_id=workspace_id,
        run_id=run_id,
        candidate_id=candidate_id,
        reason=body.reason,
    )


# ============================================================
# ── 产物列表 ──
# ============================================================


@research_products_router.get(
    "/workspaces/{workspace_id}/products",
)
async def list_products(
    workspace_id: UUID,
    _user: ResearchUserDep,
    product_service: ProductServiceDep,
) -> dict:
    """列出 Workspace 全部已确认产物（按类型分组）。"""
    products = await product_service.list_products(workspace_id)
    return {
        "items": [
            {
                "product_type": p.product_type,
                "product_id": str(p.product_id),
                "name": p.name,
                "status": p.status,
                "current_version": p.current_version,
            }
            for p in products
        ]
    }


# ============================================================
# ── ResearchCatalog ──
# ============================================================


@research_products_router.get(
    "/catalog/search",
)
async def search_catalog(
    _user: ResearchUserDep,
    catalog: CatalogDep,
    query: str = Query(default=""),
    workspace_id: UUID | None = Query(default=None),
) -> dict:
    """搜索当前用户已确认 DerivedDataset。"""
    filters: dict = {}
    if workspace_id is not None:
        filters["workspace_id"] = str(workspace_id)
    results = await catalog.search_derived_data(query=query, filters=filters)
    return {"items": results}
