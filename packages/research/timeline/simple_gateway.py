"""Simple gateway adapter for recommendation/extraction services.

Wraps OpenAICompatibleProvider to expose a simple .call(system_prompt, user_prompt) interface.
"""

from __future__ import annotations

import logging

from packages.ai.openai_compatible import OpenAICompatibleProvider
from packages.ai.providers import AIRequest

logger = logging.getLogger("research.timeline.gateway")


class SimpleGateway:
    """Simple gateway that wraps OpenAICompatibleProvider.

    Exposes a simple async .call(system_prompt, user_prompt) -> str interface
    for the recommendation and extraction services.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        thinking_enabled: bool = False,
    ) -> None:
        self._provider = OpenAICompatibleProvider(
            api_key=api_key,
            base_url=base_url,
            model=model,
            thinking_enabled=thinking_enabled,
        )

    async def call(self, system_prompt: str, user_prompt: str) -> str:
        """Call the model and return raw text content.

        Args:
            system_prompt: System prompt.
            user_prompt: User prompt.

        Returns:
            Raw model response text.
        """
        request = AIRequest(
            messages=(
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ),
            tools=(),
        )
        response = await self._provider.complete(request)
        return response.answer


async def build_gateway_from_config() -> SimpleGateway | None:
    """Build a SimpleGateway from the active AI config in the database.

    Returns:
        SimpleGateway instance, or None if no config is available.
    """
    import os

    from apps.api.routers.ai_config import get_active_ai_config, set_session_factory
    from packages.common.database import build_session_factory

    db_url = os.environ.get(
        "IRIP_DATABASE_URL",
        "postgresql+psycopg://irip_app:irip_dev_password@localhost:5432/irip",
    )
    factory = build_session_factory(db_url)
    set_session_factory(factory)

    config = await get_active_ai_config()
    if config is None:
        logger.warning("No active AI config found")
        return None

    base_url = config.get("base_url")
    api_key = config.get("api_key")
    model_name = config.get("research_model_name") or config.get("model_name", "")
    thinking_raw = config.get("thinking_enabled", "false")
    thinking = thinking_raw.lower() in ("1", "true", "yes", "on")

    if not base_url or not api_key:
        logger.warning("AI config missing base_url or api_key")
        return None

    logger.info(
        "Building SimpleGateway: model=%s, base_url=%s",
        model_name,
        base_url,
    )

    return SimpleGateway(
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        thinking_enabled=thinking,
    )
