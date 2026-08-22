"""可信执行依赖覆盖 provider（阶段 2 新增）。

注册：
- PlanService（计划生成与确认服务）；
- AnalysisRunService（Run 生命周期管理服务）；
- AIConversationService（AI 对话服务）；
- ModelGateway（模型网关）；
- ContextRouter（上下文路由器）；
- ResearchScheduler（调度器）；
- RunArtifactService（工件服务）；
- ResearchMemoryService（研究记忆服务）；
- ResearchOrchestrator（编排器，供 Worker 使用）。

参照 apps/api/composition/research.py 模式。
"""

from typing import Annotated, Any

from fastapi import Depends

from apps.api.composition import CompositionContext, lookup_dept_id
from apps.api.dependencies.auth import CurrentUser, get_current_user
from apps.api.routers.research_run import (
    _set_artifact_service,
    get_conversation_service,
    get_plan_service,
    get_run_service,
)


def register(ctx: CompositionContext) -> None:
    """注册可信执行依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    import redis as redis_lib

    from packages.research.conversation_service import AIConversationService
    from packages.research.execution.run_service import AnalysisRunService
    from packages.research.execution.scheduler import ResearchScheduler
    from packages.research.memory_service import ResearchMemoryService
    from packages.research.planning.context_router import ContextRouter
    from packages.research.planning.model_gateway import ModelGateway
    from packages.research.planning.plan_service import PlanService
    from packages.research.products.artifact_service import RunArtifactService

    # 构建共享单例
    redis_client = redis_lib.from_url(ctx.redis_url)
    context_router = ContextRouter()
    scheduler = ResearchScheduler(redis_client=redis_client)

    # 从 YAML 配置读取研发助手模型配置，构建真实 AI provider
    import logging as _logging

    _logger = _logging.getLogger(__name__)
    from packages.ai.openai_compatible import OpenAICompatibleProvider
    from packages.ai.yaml_config import get_scenario_config

    ai_provider = None
    research_model_name = None
    try:
        config = get_scenario_config("research")
        research_model_name = config.model
        ai_provider = OpenAICompatibleProvider(
            api_key=config.api_key,
            base_url=config.base_url,
            model=research_model_name,
            thinking_enabled=config.thinking_enabled,
        )
        _logger.info(
            "API AI provider initialized: model=%s, base_url=%s, thinking=%s",
            research_model_name,
            config.base_url,
            config.thinking_enabled,
        )
    except Exception as exc:
        _logger.warning("Failed to load AI config for API PlanService: %s", exc)

    # 构建模型注册表：所有任务类型使用研发助手模型
    from packages.research.planning.model_gateway import ModelConfig, TaskType

    if research_model_name:
        model_registry = {
            task: ModelConfig(
                provider="openai_compatible",
                model=research_model_name,
                version="custom",
                context_limit=128000,
            )
            for task in TaskType
        }
    else:
        model_registry = ModelGateway.get_default_registry()

    model_gateway = ModelGateway(
        provider=ai_provider,
        audit_recorder=None,
        model_registry=model_registry,
    )
    artifact_service = RunArtifactService(
        session_factory=ctx.session_factory,
        s3_repo=ctx.s3_repo,
    )
    ResearchMemoryService(session_factory=ctx.session_factory)

    # 注册工件服务（供路由端点使用）
    _set_artifact_service(artifact_service)

    # 构建 NumericToolFacade（供 PlanService 数值工具调用）
    numeric_tools = None
    try:
        from packages.ai.numeric import (
            NumericDataResolver,
            NumericLimits,
            NumericToolFacade,
            SafeExpressionEngine,
            SeriesStatisticsService,
        )

        def _fact_query_factory(principal: Any) -> Any:
            from packages.facts.query_service import FactQueryService

            return FactQueryService(
                session_factory=ctx.session_factory,
                department_id=principal.department_id,
                actor_id=principal.user_id,
                s3_repo=ctx.s3_repo,
            )

        _limits = NumericLimits()
        numeric_tools = NumericToolFacade(
            data_resolver=NumericDataResolver(
                fact_query_factory=_fact_query_factory,
                limits=_limits,
            ),
            expression_engine=SafeExpressionEngine(_limits),
            statistics_service=SeriesStatisticsService(_limits),
            limits=_limits,
            max_concurrent=4,
        )
    except Exception as exc:
        _logger.warning("Failed to build NumericToolFacade for PlanService: %s", exc)

    # PlanService
    async def _get_plan_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> PlanService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        from apps.api.dependencies.dept_scope import get_rls_dept_id
        from packages.facts.query_service import FactQueryService
        from packages.research.lineage.core_adapter import CoreFactProviderImpl

        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        fact_query_service = FactQueryService(
            session_factory=ctx.session_factory,
            department_id=current_user.department_id,
            actor_id=current_user.user_id,
            s3_repo=ctx.s3_repo,
            rls_dept_id=rls_dept_id,
        )
        if rls_dept_id is not None:
            fact_query_service.set_rls_override(rls_dept_id)
        fact_provider = CoreFactProviderImpl(query_service=fact_query_service)

        service = PlanService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            model_gateway=model_gateway,
            context_router=context_router,
            fact_provider=fact_provider,
            numeric_tools=numeric_tools,
        )
        if rls_dept_id is not None:
            service.set_rls_override(rls_dept_id)
        return service

    ctx.app.dependency_overrides[get_plan_service] = _get_plan_service_dep

    # AnalysisRunService
    async def _get_run_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> AnalysisRunService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = AnalysisRunService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            scheduler=scheduler,
        )
        from apps.api.dependencies.dept_scope import get_rls_dept_id

        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service.set_rls_override(rls_dept_id)
        return service

    ctx.app.dependency_overrides[get_run_service] = _get_run_service_dep

    # AIConversationService
    async def _get_conversation_service_dep(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> AIConversationService:
        dept_id = await lookup_dept_id(ctx.session_factory, current_user.user_id)
        service = AIConversationService(
            session_factory=ctx.session_factory,
            department_id=dept_id,
            actor_id=current_user.user_id,
            model_gateway=model_gateway,
        )
        from apps.api.dependencies.dept_scope import get_rls_dept_id

        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        if rls_dept_id is not None:
            service.set_rls_override(rls_dept_id)
        return service

    ctx.app.dependency_overrides[get_conversation_service] = _get_conversation_service_dep
