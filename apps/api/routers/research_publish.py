"""研究发布与复用 API 路由（阶段 4 新增）。

端点分组（research_publish_router, prefix=/api/v1/research）：

# ── 成果包发布 ──
POST   /workspaces/{id}/results                     — 组装并发布成果包
GET    /workspaces/{id}/results                     — 列出工作空间成果包
GET    /workspaces/{id}/results/{result_id}          — 成果包详情
PATCH  /workspaces/{id}/results/{result_id}          — 编辑成果包元数据
POST   /workspaces/{id}/results/{result_id}/versions — 发布新版本
GET    /workspaces/{id}/results/{result_id}/versions — 版本历史
GET    /workspaces/{id}/results/{result_id}/versions/{vn} — 版本详情

# ── 版本管理 ──
POST   /workspaces/{id}/results/{result_id}/versions/{vn}/withdraw — 撤回版本

# ── ACL 管理 ──
GET    /workspaces/{id}/results/{result_id}/acl      — 查看 ACL
PUT    /workspaces/{id}/results/{result_id}/acl      — 修改 ACL
POST   /workspaces/{id}/results/{result_id}/declassify — 突破权限包络

# ── 成果包搜索与发现（跨 Workspace） ──
GET    /publications                                 — 搜索已发布成果包
GET    /publications/{result_id}                     — 成果包详情
GET    /publications/{result_id}/versions/{vn}        — 版本详情
GET    /publications/{result_id}/items/{item_type}/{item_id} — 内部对象引用
GET    /publications/{result_id}/provenance           — 来源信息

# ── 复用 ──
POST   /workspaces/{id}/evidence/from-publication     — 从已发布成果添加证据
POST   /workspaces/from-publication/{result_id}       — 基于此成果新建 Workspace

# ── 收藏 ──
POST   /publications/{result_id}/favorite             — 收藏
DELETE /publications/{result_id}/favorite            — 取消收藏
GET    /publications/favorites                        — 收藏列表

# ── ResearchCatalog 扩展 ──
GET    /catalog/search-published                      — 搜索已发布 DerivedDataset

参照 apps/api/routers/research_products.py 的 DI 占位 + Pydantic 模型模式。
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.authorization import require_permission
from packages.research.products.catalog import ResearchCatalogImpl
from packages.research.publication import PublicationService
from packages.research.publication.search import ResultSearchService

#: 需 research:use 权限的当前用户依赖。
ResearchUserDep = Annotated[CurrentUser, Depends(require_permission("research:use"))]

#: 需 research:publish 权限的当前用户依赖。
PublishUserDep = Annotated[CurrentUser, Depends(require_permission("research:publish"))]

#: 需 research:declassify 权限的当前用户依赖。
DeclassifyUserDep = Annotated[CurrentUser, Depends(require_permission("research:declassify"))]


# ---- DI 占位 ----


def get_publication_service() -> PublicationService:
    """获取 PublicationService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_publication_service must be overridden via dependency_overrides")


PublicationServiceDep = Annotated[PublicationService, Depends(get_publication_service)]


def get_search_service() -> ResultSearchService:
    """获取 ResultSearchService 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_search_service must be overridden via dependency_overrides")


SearchServiceDep = Annotated[ResultSearchService, Depends(get_search_service)]


def get_publish_catalog() -> ResearchCatalogImpl:
    """获取 ResearchCatalog 实例（由 DI 容器或测试覆盖提供）。"""
    raise NotImplementedError("get_publish_catalog must be overridden via dependency_overrides")


PublishCatalogDep = Annotated[ResearchCatalogImpl, Depends(get_publish_catalog)]


# ---- 路由实例 ----

research_publish_router = APIRouter(prefix="/api/v1/research", tags=["research-publish"])


# ---- 请求/响应模型 ----


class PublishResultRequest(BaseModel):
    """发布成果包请求。"""

    title: str = Field(..., min_length=1, max_length=256)
    summary: str = Field(default="", max_length=4096)
    tags: list[str] = Field(default_factory=list)
    release_notes: str = Field(default="", max_length=4096)
    dataset_ids: list[UUID] = Field(default_factory=list)
    view_ids: list[UUID] = Field(default_factory=list)
    insight_ids: list[UUID] = Field(default_factory=list)
    requested_acl: str = Field(default="private")
    explicit_user_ids: list[UUID] = Field(default_factory=list)
    is_declassify: bool = False
    declassify_reason: str = ""


class PublishNewVersionRequest(BaseModel):
    """发布新版本请求。"""

    title: str = Field(..., min_length=1, max_length=256)
    summary: str = Field(default="", max_length=4096)
    tags: list[str] = Field(default_factory=list)
    release_notes: str = Field(default="", max_length=4096)
    dataset_ids: list[UUID] = Field(default_factory=list)
    view_ids: list[UUID] = Field(default_factory=list)
    insight_ids: list[UUID] = Field(default_factory=list)
    requested_acl: str = Field(default="private")
    explicit_user_ids: list[UUID] = Field(default_factory=list)
    is_declassify: bool = False
    declassify_reason: str = ""


class UpdateResultMetadataRequest(BaseModel):
    """编辑成果包元数据请求。"""

    name: str = Field(..., min_length=1, max_length=256)


class WithdrawVersionRequest(BaseModel):
    """撤回版本请求。"""

    reason: str = Field(default="", max_length=1024)


class UpdateAclRequest(BaseModel):
    """修改 ACL 请求。"""

    acl_type: str = Field(..., max_length=32)
    explicit_user_ids: list[UUID] = Field(default_factory=list)
    reason: str = Field(default="", max_length=1024)


class DeclassifyRequest(BaseModel):
    """declassify 请求。"""

    acl_type: str = Field(..., max_length=32)
    explicit_user_ids: list[UUID] = Field(default_factory=list)
    declassify_reason: str = Field(..., min_length=1, max_length=1024)


class AddFromPublicationRequest(BaseModel):
    """从已发布成果添加证据请求。"""

    result_id: UUID
    dataset_id: UUID
    version_number: int | None = None


class NewWorkspaceFromPublicationRequest(BaseModel):
    """基于此成果新建 Workspace 请求。"""

    workspace_name: str = Field(..., min_length=1, max_length=256)
    question_text: str = Field(..., min_length=1, max_length=4096)


# ---- 辅助函数 ----


def _result_ref_to_dict(ref: Any) -> dict[str, Any]:
    return {
        "result_id": str(ref.result_id),
        "name": ref.name,
        "status": ref.status,
        "current_version": ref.current_version,
        "current_acl_type": ref.current_acl_type,
    }


def _version_ref_to_dict(ref: Any) -> dict[str, Any]:
    return {
        "result_id": str(ref.result_id),
        "version_number": ref.version_number,
        "title": ref.title,
        "status": ref.status,
        "published_at": ref.published_at.isoformat() if ref.published_at else None,
    }


def _version_detail_to_dict(detail: Any) -> dict[str, Any]:
    return {
        "result_id": str(detail.result_id),
        "version_number": detail.version_number,
        "title": detail.title,
        "summary": detail.summary,
        "tags": detail.tags,
        "release_notes": detail.release_notes,
        "dataset_version_refs": detail.dataset_version_refs,
        "view_version_refs": detail.view_version_refs,
        "insight_version_refs": detail.insight_version_refs,
        "evidence_snapshot_ids": detail.evidence_snapshot_ids,
        "analysis_run_ids": detail.analysis_run_ids,
        "source_run_statuses": detail.source_run_statuses,
        "publisher": str(detail.publisher),
        "published_at": detail.published_at.isoformat() if detail.published_at else None,
        "content_hash": detail.content_hash,
        "published_permission_envelope": detail.published_permission_envelope,
        "status": detail.status,
    }


def _acl_revision_to_dict(ref: Any) -> dict[str, Any]:
    return {
        "revision_number": ref.revision_number,
        "acl_type": ref.acl_type,
        "explicit_user_ids": ref.explicit_user_ids,
        "previous_acl_type": ref.previous_acl_type,
        "previous_explicit_user_ids": ref.previous_explicit_user_ids,
        "changed_by": str(ref.changed_by),
        "changed_at": ref.changed_at.isoformat() if ref.changed_at else None,
        "change_reason": ref.change_reason,
        "is_declassify": ref.is_declassify,
        "declassify_reason": ref.declassify_reason,
    }


def _search_item_to_dict(item: Any) -> dict[str, Any]:
    return {
        "result_id": str(item.result_id),
        "name": item.name,
        "title": item.title,
        "summary": item.summary,
        "tags": item.tags,
        "publisher": str(item.publisher),
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "current_version": item.current_version,
        "current_acl_type": item.current_acl_type,
        "dataset_count": item.dataset_count,
        "view_count": item.view_count,
        "insight_count": item.insight_count,
        "workspace_id": str(item.workspace_id),
    }


# ============================================================
# 成果包发布
# ============================================================


@research_publish_router.post("/workspaces/{workspace_id}/results")
async def publish_result(
    workspace_id: UUID,
    request: PublishResultRequest,
    service: PublicationServiceDep,
    user: PublishUserDep,
) -> dict[str, Any]:
    """组装并发布研究成果包。"""
    from packages.research.dtos import PublishRequest as PublishRequestDC

    req = PublishRequestDC(
        title=request.title,
        summary=request.summary,
        tags=request.tags,
        release_notes=request.release_notes,
        dataset_ids=request.dataset_ids,
        view_ids=request.view_ids,
        insight_ids=request.insight_ids,
        requested_acl=request.requested_acl,
        explicit_user_ids=request.explicit_user_ids,
        is_declassify=request.is_declassify,
        declassify_reason=request.declassify_reason,
    )
    ref = await service.publish_result(workspace_id, req)
    return _version_ref_to_dict(ref)


@research_publish_router.get("/workspaces/{workspace_id}/results")
async def list_workspace_results(
    workspace_id: UUID,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> list[dict[str, Any]]:
    """列出工作空间成果包。"""
    from packages.research.repository import ResearchRepository

    # 使用 PublicationService 的 session 获取数据
    async with service._scoped_session() as session:
        results = await ResearchRepository.list_results_by_workspace(session, workspace_id)
        return [
            {
                "result_id": str(r.id),
                "name": r.name,
                "status": r.status,
                "current_version": r.current_version,
                "current_acl_type": r.current_acl_type,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in results
        ]


@research_publish_router.get("/workspaces/{workspace_id}/results/{result_id}")
async def get_workspace_result(
    workspace_id: UUID,
    result_id: UUID,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """成果包详情。"""
    detail = await service.get_result_detail(result_id)
    return {
        "result": _result_ref_to_dict(detail.result_ref),
        "current_version": (
            _version_detail_to_dict(detail.current_version) if detail.current_version else None
        ),
        "version_history": [_version_ref_to_dict(v) for v in detail.version_history],
        "acl_revisions": [_acl_revision_to_dict(r) for r in detail.acl_revisions],
        "is_favorited": detail.is_favorited,
    }


@research_publish_router.patch("/workspaces/{workspace_id}/results/{result_id}")
async def update_result_metadata(
    workspace_id: UUID,
    result_id: UUID,
    request: UpdateResultMetadataRequest,
    service: PublicationServiceDep,
    user: PublishUserDep,
) -> dict[str, Any]:
    """编辑成果包元数据（仅 name）。"""
    ref = await service.update_result_metadata(result_id, request.name)
    return _result_ref_to_dict(ref)


@research_publish_router.post("/workspaces/{workspace_id}/results/{result_id}/versions")
async def publish_new_version(
    workspace_id: UUID,
    result_id: UUID,
    request: PublishNewVersionRequest,
    service: PublicationServiceDep,
    user: PublishUserDep,
) -> dict[str, Any]:
    """发布新版本。"""
    from packages.research.dtos import PublishRequest as PublishRequestDC

    req = PublishRequestDC(
        title=request.title,
        summary=request.summary,
        tags=request.tags,
        release_notes=request.release_notes,
        dataset_ids=request.dataset_ids,
        view_ids=request.view_ids,
        insight_ids=request.insight_ids,
        requested_acl=request.requested_acl,
        explicit_user_ids=request.explicit_user_ids,
        is_declassify=request.is_declassify,
        declassify_reason=request.declassify_reason,
    )
    ref = await service.publish_new_version(result_id, workspace_id, req)
    return _version_ref_to_dict(ref)


@research_publish_router.get("/workspaces/{workspace_id}/results/{result_id}/versions")
async def list_versions(
    workspace_id: UUID,
    result_id: UUID,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> list[dict[str, Any]]:
    """版本历史列表。"""
    versions = await service.list_versions(result_id)
    return [_version_ref_to_dict(v) for v in versions]


@research_publish_router.get(
    "/workspaces/{workspace_id}/results/{result_id}/versions/{version_number}"
)
async def get_version_detail(
    workspace_id: UUID,
    result_id: UUID,
    version_number: int,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """版本详情。"""
    detail = await service.get_version_detail(result_id, version_number)
    return _version_detail_to_dict(detail)


# ============================================================
# 版本管理
# ============================================================


@research_publish_router.post(
    "/workspaces/{workspace_id}/results/{result_id}/versions/{version_number}/withdraw"
)
async def withdraw_version(
    workspace_id: UUID,
    result_id: UUID,
    version_number: int,
    request: WithdrawVersionRequest,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """撤回版本。"""
    await service.withdraw_result(result_id, version_number, request.reason)
    return {"status": "withdrawn"}


@research_publish_router.patch("/publications/{result_id}/withdraw")
async def withdraw_publication(
    result_id: UUID,
    request: WithdrawVersionRequest,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """撤回成果包（全部版本）。"""
    await service.withdraw_result(result_id, None, request.reason)
    return {"status": "withdrawn"}


# ============================================================
# ACL 管理
# ============================================================


@research_publish_router.get("/workspaces/{workspace_id}/results/{result_id}/acl")
async def get_acl(
    workspace_id: UUID,
    result_id: UUID,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """查看 ACL 当前状态和历史。"""
    revisions = await service.list_acl_revisions(result_id)
    return {
        "revisions": [_acl_revision_to_dict(r) for r in revisions],
    }


@research_publish_router.put("/workspaces/{workspace_id}/results/{result_id}/acl")
async def update_acl(
    workspace_id: UUID,
    result_id: UUID,
    request: UpdateAclRequest,
    service: PublicationServiceDep,
    user: PublishUserDep,
) -> dict[str, Any]:
    """修改 ACL。"""
    ref = await service.update_acl(
        result_id=result_id,
        acl_type=request.acl_type,
        explicit_user_ids=request.explicit_user_ids,
        reason=request.reason or None,
        is_declassify=False,
        declassify_reason=None,
    )
    return _acl_revision_to_dict(ref)


@research_publish_router.post("/workspaces/{workspace_id}/results/{result_id}/declassify")
async def declassify(
    workspace_id: UUID,
    result_id: UUID,
    request: DeclassifyRequest,
    service: PublicationServiceDep,
    user: DeclassifyUserDep,
) -> dict[str, Any]:
    """突破权限包络（需 research:declassify 权限）。"""
    ref = await service.update_acl(
        result_id=result_id,
        acl_type=request.acl_type,
        explicit_user_ids=request.explicit_user_ids,
        reason=request.declassify_reason,
        is_declassify=True,
        declassify_reason=request.declassify_reason,
    )
    return _acl_revision_to_dict(ref)


# ============================================================
# 成果包搜索与发现（跨 Workspace）
# ============================================================


@research_publish_router.get("/publications")
async def search_publications(
    service: SearchServiceDep,
    user: ResearchUserDep,
    query: str | None = Query(default=None),
    publisher: UUID | None = Query(default=None),  # noqa: B008
    tags: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    data_type: str | None = Query(default=None),
    workspace_id: UUID | None = Query(default=None),  # noqa: B008
    view_mode: str = Query(default="all"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """搜索已发布成果包。"""
    filters: dict[str, Any] = {}
    if publisher is not None:
        filters["publisher"] = str(publisher)
    if tags is not None:
        filters["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if date_from is not None:
        filters["date_from"] = date_from
    if date_to is not None:
        filters["date_to"] = date_to
    if data_type is not None:
        filters["data_type"] = data_type
    if workspace_id is not None:
        filters["workspace_id"] = str(workspace_id)

    result = await service.search(
        query=query,
        filters=filters if filters else None,
        view_mode=view_mode,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [_search_item_to_dict(item) for item in result.items],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
    }


@research_publish_router.get("/publications/{result_id}")
async def get_publication_detail(
    result_id: UUID,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """已发布成果包详情。"""
    detail = await service.get_result_detail(result_id)
    return {
        "result": _result_ref_to_dict(detail.result_ref),
        "current_version": (
            _version_detail_to_dict(detail.current_version) if detail.current_version else None
        ),
        "version_history": [_version_ref_to_dict(v) for v in detail.version_history],
        "acl_revisions": [_acl_revision_to_dict(r) for r in detail.acl_revisions],
        "is_favorited": detail.is_favorited,
    }


@research_publish_router.get("/publications/{result_id}/versions/{version_number}")
async def get_publication_version(
    result_id: UUID,
    version_number: int,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """已发布版本详情。"""
    detail = await service.get_version_detail(result_id, version_number)
    return _version_detail_to_dict(detail)


@research_publish_router.get("/publications/{result_id}/items/{item_type}/{item_id}")
async def get_publication_item(
    result_id: UUID,
    item_type: str,
    item_id: UUID,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """成果包内部对象独立引用详情。"""
    return await service.get_result_internal_object(result_id, item_type, item_id)


@research_publish_router.get("/publications/{result_id}/provenance")
async def get_publication_provenance(
    result_id: UUID,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """成果包来源信息。"""
    detail = await service.get_result_detail(result_id)
    current_version = detail.current_version

    # 查询 snapshot 和 run 的可读名称（复用 service session）
    snapshot_labels: list[dict[str, Any]] = []
    run_labels: list[dict[str, Any]] = []
    if current_version:
        from sqlalchemy import text as _sa_text

        async with service._scoped_session() as session:
            for sid in current_version.evidence_snapshot_ids:
                try:
                    r = await session.execute(
                        _sa_text(
                            "SELECT snapshot_number, workspace_id"
                            " FROM research_evidence_snapshot WHERE id = :sid"
                        ),
                        {"sid": str(sid)},
                    )
                    row = r.fetchone()
                    label = f"快照 #{row[0]}" if row else str(sid)[:8]
                    snapshot_labels.append({"id": str(sid), "label": label})
                except Exception:
                    snapshot_labels.append({"id": str(sid), "label": str(sid)[:8]})
            for rid in current_version.analysis_run_ids:
                try:
                    r = await session.execute(
                        _sa_text("SELECT run_number FROM research_analysis_run WHERE id = :rid"),
                        {"rid": str(rid)},
                    )
                    row = r.fetchone()
                    label = f"Run #{row[0]}" if row else str(rid)[:8]
                    run_labels.append({"id": str(rid), "label": label})
                except Exception:
                    run_labels.append({"id": str(rid), "label": str(rid)[:8]})

    return {
        "result_id": str(result_id),
        "name": detail.result_ref.name,
        "current_version": detail.result_ref.current_version,
        "evidence_snapshot_ids": ([s["id"] for s in snapshot_labels]),
        "evidence_snapshot_labels": snapshot_labels,
        "analysis_run_ids": ([r["id"] for r in run_labels]),
        "analysis_run_labels": run_labels,
        "source_run_statuses": (current_version.source_run_statuses if current_version else {}),
        "publisher": (current_version.publisher if current_version else None),
        "published_at": (
            current_version.published_at.isoformat()
            if current_version and current_version.published_at
            else None
        ),
    }


# ============================================================
# 复用
# ============================================================


@research_publish_router.post("/workspaces/{workspace_id}/evidence/from-publication")
async def add_evidence_from_publication(
    workspace_id: UUID,
    request: AddFromPublicationRequest,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """从已发布成果包添加 DerivedDataset 到当前 Workspace。"""
    ref = await service.add_to_workspace(
        result_id=request.result_id,
        workspace_id=workspace_id,
        dataset_id=request.dataset_id,
        version_number=request.version_number,
    )
    return {
        "ref_id": str(ref.ref_id),
        "source_namespace": ref.source_namespace,
        "source_id": str(ref.source_id),
        "source_version": ref.source_version,
        "source_name": ref.source_name,
        "status": ref.status,
    }


@research_publish_router.post("/workspaces/from-publication/{result_id}")
async def new_workspace_from_publication(
    result_id: UUID,
    request: NewWorkspaceFromPublicationRequest,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """基于已发布成果包新建 Workspace。"""
    ref = await service.new_workspace_from_result(
        result_id=result_id,
        workspace_name=request.workspace_name,
        question_text=request.question_text,
    )
    return {
        "workspace_id": str(ref.workspace_id),
        "name": ref.name,
        "status": ref.status,
    }


# ============================================================
# 收藏
# ============================================================


@research_publish_router.post("/publications/{result_id}/favorite")
async def add_favorite(
    result_id: UUID,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """收藏成果包。"""
    await service.toggle_favorite(result_id, True)
    return {"status": "favorited"}


@research_publish_router.delete("/publications/{result_id}/favorite")
async def remove_favorite(
    result_id: UUID,
    service: PublicationServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """取消收藏。"""
    await service.toggle_favorite(result_id, False)
    return {"status": "unfavorited"}


@research_publish_router.get("/publications/favorites")
async def list_favorites(
    service: SearchServiceDep,
    user: ResearchUserDep,
) -> dict[str, Any]:
    """收藏列表。"""
    result = await service.list_results(
        view_mode="favorites",
        page=1,
        page_size=100,
    )
    return {
        "items": [_search_item_to_dict(item) for item in result.items],
        "total": result.total,
    }


# ============================================================
# ResearchCatalog 扩展
# ============================================================


@research_publish_router.get("/catalog/search-published")
async def search_published_catalog(
    catalog: PublishCatalogDep,
    user: ResearchUserDep,
    query: str | None = Query(default=None),
    result_id: UUID | None = Query(default=None),  # noqa: B008
) -> dict[str, Any]:
    """搜索已发布成果包中的 DerivedDataset（跨用户，ACL 过滤）。"""
    filters: dict[str, Any] = {}
    if result_id is not None:
        filters["result_id"] = str(result_id)
    results = await catalog.search_published_derived_data(
        query=query or "",
        filters=filters if filters else None,
    )
    return {"items": results}
