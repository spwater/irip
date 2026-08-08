"""单元测试：OfflineProvider 离线 Provider。

覆盖：
- complete 返回未配置提示且不含工具调用；
- provider_mode 固定为 offline；
- uncertainty 字段提示大模型未配置；
- 多次调用结果一致（确定性）。
"""

import pytest

from packages.ai.offline_provider import OfflineProvider
from packages.ai.providers import AIRequest, AIResponse


class TestOfflineProvider:
    """OfflineProvider 离线模拟测试。"""

    @pytest.fixture
    def provider(self) -> OfflineProvider:
        """OfflineProvider 实例。"""
        return OfflineProvider()

    @pytest.fixture
    def ai_request(self) -> AIRequest:
        """AIRequest 测试请求。"""
        return AIRequest(messages=({"role": "user", "content": "hello"},), tools=())

    async def test_complete_returns_unconfigured_message(
        self, provider: OfflineProvider, ai_request: AIRequest
    ) -> None:
        """complete 返回未配置提示。"""
        response = await provider.complete(ai_request)
        assert isinstance(response, AIResponse)
        assert "未配置" in response.answer
        assert "大模型" in response.answer

    async def test_provider_mode_is_offline(
        self, provider: OfflineProvider, ai_request: AIRequest
    ) -> None:
        """provider_mode 固定为 offline。"""
        assert provider.provider_mode == "offline"
        response = await provider.complete(ai_request)
        assert response.provider_mode == "offline"

    async def test_no_tool_calls_or_citations(
        self, provider: OfflineProvider, ai_request: AIRequest
    ) -> None:
        """离线响应不含工具调用和引用。"""
        response = await provider.complete(ai_request)
        assert response.tool_calls == ()
        assert response.citations == ()

    async def test_uncertainty_set(self, provider: OfflineProvider, ai_request: AIRequest) -> None:
        """uncertainty 字段提示大模型未配置。"""
        response = await provider.complete(ai_request)
        assert response.uncertainty is not None
        assert "未配置" in response.uncertainty

    async def test_deterministic_output(
        self, provider: OfflineProvider, ai_request: AIRequest
    ) -> None:
        """多次调用返回一致的回答。"""
        r1 = await provider.complete(ai_request)
        r2 = await provider.complete(ai_request)
        assert r1.answer == r2.answer
        assert r1.uncertainty == r2.uncertainty
