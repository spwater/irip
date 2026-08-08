"""研究产物依赖覆盖 provider（阶段 3 新增）。

注册：
- ProductService（产物生命周期管理服务）；
- CandidateService（候选产物识别服务）；
- ResearchCatalogImpl（替换 ResearchCatalogStub）；
- InsightExtractor（供 Orchestrator 使用）。

参照 apps/api/composition/research_run.py 模式。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.composition import CompositionContext, lookup_dept_id
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.research_products import (
    get_candidate_service,
    get_catalog,
    get_product_service,
)


def register(ctx: CompositionContext) -> None:
    """注册研究产物依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from packages.research.planning.model_gateway import ModelGateway
    from packages.research.products import ProductService
    from packages.research.products.artifact_service import RunArtifactService
    from packages.research.products.candidates import CandidateService
    from packages.research.products.catalog import ResearchCatalogImpl
    from packages.research.products.insight_extractor import InsightExtractor

    # 复用 research_run 中已注册的 artifact_service
    # 此处重新构建（与 research_run 中相同的 s3_repo）
    artifact_service = RunArtifactService(
        session_factory=ctx.session_factory,
        s3_repo=ctx.s3_repo,
    )

    # 构建 InsightExtractor（注入 ModelGateway，API 进程中 provider=None）
    model_gateway = ModelGateway(
        provider=None,
        audit_recorder=None,
        model_registry=ModelGateway.get_default_registry(),
    )
    insight_extractor = InsightExtractor(model_gateway=model_gateway)

    # ProductService
    async def _get_product_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ProductService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = ProductService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            artifact_service=artifact_service,
        )
        from apps.api.dependencies.dept_scope import get_rls_dept_id

        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id
        return service

    ctx.app.dependency_overrides[get_product_service] = _get_product_service_dep

    # CandidateService
    async def _get_candidate_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CandidateService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = CandidateService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            artifact_service=artifact_service,
        )
        from apps.api.dependencies.dept_scope import get_rls_dept_id

        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id
        return service

    ctx.app.dependency_overrides[get_candidate_service] = _get_candidate_service_dep

    # ResearchCatalogImpl（替换 Stub）
    async def _get_catalog_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ResearchCatalogImpl:
        catalog = ResearchCatalogImpl(
            session_factory=ctx.session_factory,
            actor_id=current_user.user_id,
        )
        return catalog

    ctx.app.dependency_overrides[get_catalog] = _get_catalog_dep

    # 将 InsightExtractor 存储到 ctx 供 Orchestrator 使用
    # （Worker 进程中会从 ctx 获取）
    if not hasattr(ctx, "_insight_extractor"):
        ctx._insight_extractor = insight_extractor  # type: ignore[attr-defined]
