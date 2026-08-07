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

from typing import Annotated

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

    from packages.research.artifact_service import RunArtifactService
    from packages.research.context_router import ContextRouter
    from packages.research.conversation_service import AIConversationService
    from packages.research.memory_service import ResearchMemoryService
    from packages.research.model_gateway import ModelGateway
    from packages.research.plan_service import PlanService
    from packages.research.run_service import AnalysisRunService
    from packages.research.scheduler import ResearchScheduler

    # 构建共享单例
    redis_client = redis_lib.from_url(ctx.redis_url)  # type: ignore[no-untyped-call]
    context_router = ContextRouter()
    scheduler = ResearchScheduler(redis_client=redis_client)

    # 从 ai_config 表读取研发助手模型配置，构建真实 AI provider
    # 注意：api_key 在数据库中是加密存储的，必须通过 get_active_ai_config 解密
    import logging as _logging

    _logger = _logging.getLogger(__name__)
    ai_provider = None
    research_model_name = None
    try:
        import asyncio as _asyncio

        from apps.api.routers.ai_config import get_active_ai_config, set_session_factory

        set_session_factory(ctx.session_factory)

        async def _load_config() -> None:
            return await get_active_ai_config()  # type: ignore[return-value]

        # 在新事件循环中运行（composition register 在 uvicorn lifespan 中，可能已有 loop）
        try:
            _loop = _asyncio.get_running_loop()
            # 已在事件循环中，用 ensure_future + 同步等待
            import concurrent.futures as _cf

            with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
                _ai_config = _pool.submit(_asyncio.run, _load_config()).result(timeout=10)
        except RuntimeError:
            _ai_config = _asyncio.run(_load_config())

        if _ai_config and _ai_config.get("base_url") and _ai_config.get("api_key"):
            from packages.ai.openai_compatible import OpenAICompatibleProvider

            research_model_name = _ai_config.get("research_model_name") or _ai_config.get(
                "model_name", ""
            )
            _thinking = _ai_config.get("thinking_enabled", False)
            ai_provider = OpenAICompatibleProvider(
                api_key=_ai_config["api_key"],
                base_url=_ai_config["base_url"],
                model=research_model_name,
                thinking_enabled=_thinking,
            )
            _logger.info(
                "API AI provider initialized: model=%s, base_url=%s, thinking=%s",
                research_model_name,
                _ai_config["base_url"],
                _thinking,
            )
        else:
            _logger.warning("No active AI config found, PlanService will use mock provider")
    except Exception as exc:
        _logger.warning("Failed to load AI config for API PlanService: %s", exc)

    # 构建模型注册表：所有任务类型使用研发助手模型
    from packages.research.model_gateway import ModelConfig, TaskType  # type: ignore[attr-defined]

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

        def _fact_query_factory(principal):  # type: ignore[no-untyped-def]
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
        from packages.research.core_adapter import CoreFactProviderImpl

        rls_dept_id = get_rls_dept_id(current_user, ctx.root_dept_id)
        fact_query_service = FactQueryService(
            session_factory=ctx.session_factory,
            department_id=current_user.department_id,
            actor_id=current_user.user_id,
            s3_repo=ctx.s3_repo,
            rls_dept_id=rls_dept_id,
        )
        if rls_dept_id is not None:
            fact_query_service._rls_dept_id = rls_dept_id
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
            service._rls_dept_id = rls_dept_id
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
            service._rls_dept_id = rls_dept_id
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
            service._rls_dept_id = rls_dept_id
        return service

    ctx.app.dependency_overrides[get_conversation_service] = _get_conversation_service_dep
