"""事实与溯源域依赖覆盖 provider（F-20）。

注册：
- FactService（事实服务）；
- EvidenceService（证据集服务）；
- RecipeService（推导配方服务）；
- DerivationService（推导运行服务）；
- ProvenanceGraphService（溯源图服务）。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.composition import CompositionContext, lookup_dept_id
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.facts import get_fact_query_service, get_fact_service
from apps.api.routers.provenance import (
    get_derivation_service,
    get_evidence_service,
    get_provenance_graph_service,
    get_recipe_service,
)


def register(ctx: CompositionContext) -> None:
    """注册事实与溯源域依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from apps.api.dependencies.dept_scope import get_rls_dept_id
    from packages.facts.query_service import FactQueryService
    from packages.facts.service import FactService
    from packages.provenance.derivations import DerivationService
    from packages.provenance.evidence import EvidenceService
    from packages.provenance.graph import ProvenanceGraphService
    from packages.provenance.recipes import RecipeService

    # 事实服务
    async def _get_fact_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> FactService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = FactService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        return service

    ctx.app.dependency_overrides[get_fact_service] = _get_fact_service_dep

    # 事实查询服务（复杂读，注入 s3_repo）
    async def _get_fact_query_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> FactQueryService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        service = FactQueryService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            s3_repo=ctx.s3_repo,
            rls_dept_id=rls_dept_id,
        )
        return service

    ctx.app.dependency_overrides[get_fact_query_service] = _get_fact_query_service_dep

    # 证据集服务
    async def _get_evidence_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> EvidenceService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = EvidenceService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        return service

    ctx.app.dependency_overrides[get_evidence_service] = _get_evidence_service_dep

    # 推导配方服务
    async def _get_recipe_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> RecipeService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = RecipeService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        return service

    ctx.app.dependency_overrides[get_recipe_service] = _get_recipe_service_dep

    # 推导运行服务
    async def _get_derivation_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> DerivationService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = DerivationService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        return service

    ctx.app.dependency_overrides[get_derivation_service] = _get_derivation_service_dep

    # 溯源图服务
    async def _get_provenance_graph_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> ProvenanceGraphService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = ProvenanceGraphService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id  # type: ignore[attr-defined]
        return service

    ctx.app.dependency_overrides[get_provenance_graph_service] = _get_provenance_graph_service_dep
