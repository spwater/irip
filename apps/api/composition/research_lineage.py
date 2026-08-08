"""研究溯源与知识库依赖覆盖 provider（阶段 5 新增）。

注册：
- CoreProvenanceAdapterImpl（只读核心 Provenance 适配器）；
- ResearchLineageAdapterImpl（只读研究 Lineage 适配器）；
- LineageWriterService（溯源边写入服务）；
- MockKnowledgeProvider + KnowledgeProviderService（知识库检索编排）；
- KnowledgeReferenceService（知识引用快照管理）；
- UnifiedProvenanceQueryService（联邦溯源查询服务）。

参照 apps/api/composition/research_publish.py 模式。
"""

from typing import Annotated

from fastapi import Depends

from apps.api.composition import CompositionContext, lookup_dept_id
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.research_lineage import (
    get_knowledge_provider_service,
    get_knowledge_reference_service,
    get_provenance_service,
)


def register(ctx: CompositionContext) -> None:
    """注册研究溯源与知识库依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from packages.research.adapters.core_provenance import CoreProvenanceAdapterImpl
    from packages.research.adapters.research_lineage import (
        ResearchLineageAdapterImpl,
    )
    from packages.research.lineage.lineage_writer import LineageWriterService
    from packages.research.lineage.provenance import UnifiedProvenanceQueryService
    from packages.research.publication.knowledge_provider import (
        KnowledgeProviderService,
        MockKnowledgeProvider,
    )
    from packages.research.publication.knowledge_reference import KnowledgeReferenceService

    # 构建 Adapter（无状态，共享 session_factory）
    core_adapter = CoreProvenanceAdapterImpl(session_factory=ctx.session_factory)
    research_adapter = ResearchLineageAdapterImpl(session_factory=ctx.session_factory)

    # LineageWriterService（无状态）
    lineage_writer = LineageWriterService(session_factory=ctx.session_factory)

    # KnowledgeProviderService（注入 MockKnowledgeProvider）
    mock_provider = MockKnowledgeProvider(provider_name="mock")
    knowledge_provider_service = KnowledgeProviderService(
        session_factory=ctx.session_factory,
        providers={"mock": mock_provider},
    )

    # UnifiedProvenanceQueryService DI 占位覆盖
    async def _get_provenance_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> UnifiedProvenanceQueryService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = UnifiedProvenanceQueryService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            core_adapter=core_adapter,
            research_adapter=research_adapter,
        )
        from apps.api.dependencies.dept_scope import get_rls_dept_id

        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id
        return service

    ctx.app.dependency_overrides[get_provenance_service] = _get_provenance_service_dep

    # KnowledgeProviderService DI 占位覆盖
    async def _get_knowledge_provider_service_dep() -> KnowledgeProviderService:
        return knowledge_provider_service

    ctx.app.dependency_overrides[get_knowledge_provider_service] = (
        _get_knowledge_provider_service_dep
    )

    # KnowledgeReferenceService DI 占位覆盖
    async def _get_knowledge_reference_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> KnowledgeReferenceService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = KnowledgeReferenceService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            lineage_writer=lineage_writer,
            s3=ctx.s3_repo,
        )
        from apps.api.dependencies.dept_scope import get_rls_dept_id

        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service._rls_dept_id = rls_dept_id
        return service

    ctx.app.dependency_overrides[get_knowledge_reference_service] = (
        _get_knowledge_reference_service_dep
    )
