"""研究发布与复用依赖覆盖 provider（阶段 4 新增）。

注册：
- PublicationService（成果包生命周期管理服务）；
- ResultSearchService（成果包搜索服务）；
- ResearchCatalogImpl（复用 research_products 中已注册的实例，通过 get_publish_catalog 单独注册）。

参照 apps/api/composition/research_products.py 模式。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.composition import CompositionContext, lookup_dept_id
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.research_publish import (
    get_publication_service,
    get_publish_catalog,
    get_search_service,
)


def register(ctx: CompositionContext) -> None:
    """注册研究发布与复用依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    # 复用 research_products 中已注册的 artifact_service
    # 此处重新构建 ProductService（需要 ArtifactService，从 ctx 获取 s3_repo 构建）
    from packages.research.lineage import LineageEdgeService
    from packages.research.products import ProductService
    from packages.research.products.artifact_service import RunArtifactService
    from packages.research.products.catalog import ResearchCatalogImpl
    from packages.research.publication import PublicationService
    from packages.research.publication.search import ResultSearchService

    artifact_service = RunArtifactService(
        session_factory=ctx.session_factory,
        s3_repo=ctx.s3_repo,
    )

    # LineageEdgeService（无状态，共享 session_factory）
    lineage_service = LineageEdgeService(
        session_factory=ctx.session_factory,
    )

    # PublicationService
    async def _get_publication_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> PublicationService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        # 构建 ProductService（注入 artifact_service）
        product_service = ProductService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            artifact_service=artifact_service,
        )
        service = PublicationService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            product_service=product_service,
            lineage_service=lineage_service,
        )
        from apps.api.dependencies.dept_scope import get_rls_dept_id

        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id
        return service

    ctx.app.dependency_overrides[get_publication_service] = _get_publication_service_dep

    # ResultSearchService
    async def _get_search_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ResultSearchService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = ResultSearchService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
        )
        from apps.api.dependencies.dept_scope import get_rls_dept_id

        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id
        return service

    ctx.app.dependency_overrides[get_search_service] = _get_search_service_dep

    # ResearchCatalogImpl（用于 search-published 端点）
    async def _get_publish_catalog_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ResearchCatalogImpl:
        catalog = ResearchCatalogImpl(
            session_factory=ctx.session_factory,
            actor_id=current_user.user_id,
        )
        return catalog

    ctx.app.dependency_overrides[get_publish_catalog] = _get_publish_catalog_dep
