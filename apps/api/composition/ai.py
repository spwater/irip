"""AI 助手服务依赖覆盖 provider（F-20）。

注册：
- AIService（AI 助手服务，优先从配置读取真实模型，未配置时用离线模式）；
- ai_config 会话工厂；
- assistant 会话工厂。
"""

from apps.api.composition import CompositionContext
from apps.api.routers.account import (
    get_account_session_factory,
    get_s3_repo,
)
from apps.api.routers.ai_config import (
    get_active_ai_config,
)
from apps.api.routers.ai_config import (
    set_session_factory as set_ai_config_session_factory,
)
from apps.api.routers.ai_tools import (
    set_session_factory as set_ai_tools_session_factory,
)
from apps.api.routers.assistant import (
    get_ai_service,
)
from apps.api.routers.assistant import (
    set_ai_session_factory as set_assistant_session_factory,
)
from apps.api.routers.collaboration import (
    get_ai_service as get_collaboration_ai_service,
)
from apps.api.routers.object_types import (
    set_session_factory as set_object_types_session_factory,
)


def register(ctx: CompositionContext) -> None:
    """注册 AI 相关依赖覆盖。

    Args:
        ctx: 组合根共享上下文。
    """
    from packages.ai.offline_provider import OfflineProvider
    from packages.ai.openai_compatible import OpenAICompatibleProvider
    from packages.ai.service import AIService
    from packages.ai.tools import ToolRegistry

    set_ai_config_session_factory(ctx.session_factory)
    set_assistant_session_factory(ctx.session_factory)
    set_ai_tools_session_factory(ctx.session_factory)
    set_object_types_session_factory(ctx.session_factory)

    async def _get_ai_service_dep() -> AIService:
        config = await get_active_ai_config()
        if config and config.get("base_url") and config.get("api_key"):
            provider = OpenAICompatibleProvider(
                api_key=config["api_key"],
                base_url=config["base_url"],
                model=config.get("assistant_model_name") or config["model_name"],
                thinking_enabled=config.get("thinking_enabled", False),
            )
        else:
            provider = OfflineProvider()
        tool_registry = ToolRegistry()
        return AIService(
            provider=provider,
            tool_registry=tool_registry,
            session_factory=ctx.session_factory,
        )

    ctx.app.dependency_overrides[get_ai_service] = _get_ai_service_dep
    # irip-ai-collab: 协作路由复用同一个 AIService 依赖
    ctx.app.dependency_overrides[get_collaboration_ai_service] = _get_ai_service_dep

    # irip-ai-collab: 账户路由的 session_factory + S3 注入
    def _get_account_session_factory_dep() -> object:
        return ctx.session_factory

    def _get_s3_repo_dep() -> object:
        return ctx.s3_repo

    ctx.app.dependency_overrides[get_account_session_factory] = _get_account_session_factory_dep
    ctx.app.dependency_overrides[get_s3_repo] = _get_s3_repo_dep
