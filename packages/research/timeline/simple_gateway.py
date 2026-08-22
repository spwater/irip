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
    """Build a SimpleGateway from the YAML AI config (research scenario).

    Returns:
        SimpleGateway instance, or None if config is unavailable.
    """
    from packages.ai.yaml_config import get_scenario_config

    try:
        config = get_scenario_config("research")
    except (KeyError, FileNotFoundError, ValueError) as exc:
        logger.warning("Failed to load research AI config: %s", exc)
        return None

    logger.info(
        "Building SimpleGateway: model=%s, base_url=%s",
        config.model,
        config.base_url,
    )

    return SimpleGateway(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        thinking_enabled=config.thinking_enabled,
    )
