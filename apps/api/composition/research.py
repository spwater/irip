"""研究域依赖覆盖 provider。

注册：
- WorkspaceService（研究工作空间服务）；
- EvidenceSnapshotService（证据快照服务）。

两个 Service 均注入 CoreFactProviderImpl（封装 FactQueryService 只读方法），
通过 CoreFactProvider 接口只读访问 Fact 数据，不暴露核心 session。

参照 apps/api/composition/facts.py 模式。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.composition import CompositionContext, lookup_dept_id
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.research import get_snapshot_service, get_workspace_service


def register(ctx: CompositionContext) -> None:
    """注册研究域依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from apps.api.dependencies.dept_scope import get_rls_dept_id
    from packages.facts.query_service import FactQueryService
    from packages.research.core_adapter import CoreFactProviderImpl
    from packages.research.service import WorkspaceService
    from packages.research.snapshots import EvidenceSnapshotService

    def _build_fact_query_service(current_user: CurrentUser) -> FactQueryService:
        """构建 FactQueryService 实例（用于 CoreFactProviderImpl）。"""
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        service = FactQueryService(
            session_factory=ctx.session_factory,
            department_id=current_user.department_id,
            actor_id=current_user.user_id,
            s3_repo=ctx.s3_repo,
            rls_dept_id=rls_dept_id,
        )
        # 平台管理员需要 _rls_dept_id 绕过 RLS 隔离
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id
        return service

    def _build_fact_provider(current_user: CurrentUser) -> CoreFactProviderImpl:
        """构建 CoreFactProviderImpl 实例。"""
        fact_query_service = _build_fact_query_service(current_user)
        return CoreFactProviderImpl(query_service=fact_query_service)

    # WorkspaceService
    async def _get_workspace_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> WorkspaceService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        fact_provider = _build_fact_provider(current_user)
        # 阶段 3：注入 ResearchCatalogImpl（用于 research:derived 证据校验）
        from packages.research.catalog import ResearchCatalogImpl
        research_catalog = ResearchCatalogImpl(
            session_factory=ctx.session_factory,
            actor_id=current_user.user_id,
        )
        service = WorkspaceService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            fact_provider=fact_provider,
            research_catalog=research_catalog,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id
        return service

    ctx.app.dependency_overrides[get_workspace_service] = _get_workspace_service_dep

    # EvidenceSnapshotService
    async def _get_snapshot_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> EvidenceSnapshotService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        fact_provider = _build_fact_provider(current_user)
        service = EvidenceSnapshotService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            fact_provider=fact_provider,
        )
        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id
        return service

    ctx.app.dependency_overrides[get_snapshot_service] = _get_snapshot_service_dep
