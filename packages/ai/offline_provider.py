"""离线 Provider（未配置大模型时使用）。

OfflineProvider 不调用任何外部 API，直接返回"未配置"提示。
当大模型未配置时，AI 助手不回复任何内容，仅提示管理员配置大模型。
"""

from packages.ai.providers import AIRequest, AIResponse


class OfflineProvider:
    """离线 Provider，实现 AIProvider 协议。

    未配置大模型时使用，不模拟任何回答。

    Attributes:
        provider_mode: 固定为 ``"offline"``。
    """

    provider_mode: str = "offline"

    async def complete(self, request: AIRequest) -> AIResponse:
        """返回未配置提示。

        Args:
            request: AI 请求。

        Returns:
            AIResponse: 未配置提示，不包含任何回答内容。
        """
        return AIResponse(
            answer=(
                "大模型未配置，无法回复。请联系管理员在「平台治理 → AI 配置」中配置大模型 API。"
            ),
            tool_calls=(),
            citations=(),
            uncertainty="大模型未配置",
            provider_mode=self.provider_mode,
        )
