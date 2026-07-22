"""集成测试：离线 Provider 引用生成。

覆盖：
- 离线 D50 问题返回引用（parameter_version + fact_revision + derivation_run）；
- 引用包含完整字段（object_type / object_id / version / label / href）；
- 离线 ROM 问题返回引用（model_version）；
- 离线标准/事实/参数问题返回引用；
- 离线 Provider 确定性（相同输入 → 相同输出）；
- 工具调用摘要非空。
"""

import pytest

from packages.ai.citations import Citation
from packages.ai.offline_provider import OfflineProvider
from packages.ai.providers import AIRequest, AIResponse


class TestOfflineD50Citations:
    """离线 D50 问题引用验证。"""

    @pytest.fixture
    def provider(self) -> OfflineProvider:
        """离线 Provider 实例。"""
        return OfflineProvider()

    @pytest.fixture
    def d50_request(self) -> AIRequest:
        """D50 问题请求。"""
        return AIRequest(
            messages=(
                {"role": "user", "content": "D50 参数的溯源链路是什么？"},
            ),
            tools=("search_parameters", "explain_provenance"),
            user_context={"user_id": "test", "organization_id": "test"},
            provider_mode="offline",
        )

    async def test_d50_returns_response(self, provider: OfflineProvider, d50_request: AIRequest) -> None:
        """D50 问题返回 AIResponse。"""
        response = await provider.complete(d50_request)
        assert isinstance(response, AIResponse)

    async def test_d50_answer_mentions_d50(self, provider: OfflineProvider, d50_request: AIRequest) -> None:
        """回答中提及 D50。"""
        response = await provider.complete(d50_request)
        assert "D50" in response.answer

    async def test_d50_returns_citations(self, provider: OfflineProvider, d50_request: AIRequest) -> None:
        """D50 问题返回引用。"""
        response = await provider.complete(d50_request)
        assert len(response.citations) >= 3

    async def test_d50_citation_has_parameter_version(self, provider: OfflineProvider, d50_request: AIRequest) -> None:
        """引用包含 parameter_version。"""
        response = await provider.complete(d50_request)
        types = [c.object_type for c in response.citations]
        assert "parameter_version" in types

    async def test_d50_citation_has_fact_revision(self, provider: OfflineProvider, d50_request: AIRequest) -> None:
        """引用包含 fact_revision。"""
        response = await provider.complete(d50_request)
        types = [c.object_type for c in response.citations]
        assert "fact_revision" in types

    async def test_d50_citation_has_derivation_run(self, provider: OfflineProvider, d50_request: AIRequest) -> None:
        """引用包含 derivation_run。"""
        response = await provider.complete(d50_request)
        types = [c.object_type for c in response.citations]
        assert "derivation_run" in types

    async def test_d50_citations_have_all_fields(self, provider: OfflineProvider, d50_request: AIRequest) -> None:
        """引用包含完整字段。"""
        response = await provider.complete(d50_request)
        for c in response.citations:
            assert isinstance(c, Citation)
            assert c.object_type != ""
            assert c.object_id != ""
            assert c.version != ""
            assert c.label != ""
            assert c.href != ""

    async def test_d50_citations_have_clickable_href(self, provider: OfflineProvider, d50_request: AIRequest) -> None:
        """引用 href 以 / 开头（可点击跳转）。"""
        response = await provider.complete(d50_request)
        for c in response.citations:
            assert c.href.startswith("/")

    async def test_d50_tool_calls_non_empty(self, provider: OfflineProvider, d50_request: AIRequest) -> None:
        """D50 回答包含工具调用摘要。"""
        response = await provider.complete(d50_request)
        assert len(response.tool_calls) >= 1
        for tc in response.tool_calls:
            assert "tool" in tc
            assert "summary" in tc

    async def test_d50_provider_mode_offline(self, provider: OfflineProvider, d50_request: AIRequest) -> None:
        """provider_mode 为 offline。"""
        response = await provider.complete(d50_request)
        assert response.provider_mode == "offline"


class TestOfflineROMCitations:
    """离线 ROM（降阶模型）问题引用验证。"""

    @pytest.fixture
    def provider(self) -> OfflineProvider:
        return OfflineProvider()

    @pytest.fixture
    def rom_request(self) -> AIRequest:
        return AIRequest(
            messages=(
                {"role": "user", "content": "有哪些降阶模型 ROM 可以用？"},
            ),
            tools=("search_standards", "run_published_model"),
            user_context={"user_id": "test", "organization_id": "test"},
            provider_mode="offline",
        )

    async def test_rom_returns_response(self, provider: OfflineProvider, rom_request: AIRequest) -> None:
        """ROM 问题返回 AIResponse。"""
        response = await provider.complete(rom_request)
        assert isinstance(response, AIResponse)

    async def test_rom_answer_mentions_rom(self, provider: OfflineProvider, rom_request: AIRequest) -> None:
        """回答中提及降阶模型。"""
        response = await provider.complete(rom_request)
        assert "降阶模型" in response.answer or "ROM" in response.answer

    async def test_rom_returns_citations(self, provider: OfflineProvider, rom_request: AIRequest) -> None:
        """ROM 问题返回引用。"""
        response = await provider.complete(rom_request)
        assert len(response.citations) >= 1

    async def test_rom_citation_has_model_version(self, provider: OfflineProvider, rom_request: AIRequest) -> None:
        """引用包含 model_version。"""
        response = await provider.complete(rom_request)
        types = [c.object_type for c in response.citations]
        assert "model_version" in types

    async def test_rom_tool_calls_non_empty(self, provider: OfflineProvider, rom_request: AIRequest) -> None:
        """ROM 回答包含工具调用。"""
        response = await provider.complete(rom_request)
        assert len(response.tool_calls) >= 1

    async def test_rom_has_uncertainty(self, provider: OfflineProvider, rom_request: AIRequest) -> None:
        """ROM 回答包含不确定性说明。"""
        response = await provider.complete(rom_request)
        assert response.uncertainty is not None


class TestOfflineDeterminism:
    """离线 Provider 确定性验证。"""

    @pytest.fixture
    def provider(self) -> OfflineProvider:
        return OfflineProvider()

    async def test_same_input_same_output(self, provider: OfflineProvider) -> None:
        """相同输入产生相同输出。"""
        request = AIRequest(
            messages=({"role": "user", "content": "D50"},),
            tools=(),
            user_context={},
            provider_mode="offline",
        )
        r1 = await provider.complete(request)
        r2 = await provider.complete(request)
        assert r1.answer == r2.answer
        assert r1.citations == r2.citations
        assert r1.tool_calls == r2.tool_calls

    async def test_different_questions_different_answers(self, provider: OfflineProvider) -> None:
        """不同问题产生不同回答。"""
        r1 = await provider.complete(
            AIRequest(
                messages=({"role": "user", "content": "D50"},),
                tools=(),
                provider_mode="offline",
            )
        )
        r2 = await provider.complete(
            AIRequest(
                messages=({"role": "user", "content": "有哪些标准变量？"},),
                tools=(),
                provider_mode="offline",
            )
        )
        assert r1.answer != r2.answer


class TestOfflineOtherQueries:
    """离线其他问题引用验证。"""

    @pytest.fixture
    def provider(self) -> OfflineProvider:
        return OfflineProvider()

    async def test_standards_query_returns_citation(self, provider: OfflineProvider) -> None:
        """标准变量问题返回引用。"""
        response = await provider.complete(
            AIRequest(
                messages=({"role": "user", "content": "有哪些标准变量？"},),
                tools=("search_standards",),
                provider_mode="offline",
            )
        )
        assert len(response.citations) >= 1
        assert any(c.object_type == "standard_variable" for c in response.citations)

    async def test_facts_query_returns_citation(self, provider: OfflineProvider) -> None:
        """事实问题返回引用。"""
        response = await provider.complete(
            AIRequest(
                messages=({"role": "user", "content": "有哪些实验事实？"},),
                tools=("search_facts",),
                provider_mode="offline",
            )
        )
        assert len(response.citations) >= 1
        assert any(c.object_type == "fact_revision" for c in response.citations)

    async def test_parameters_query_returns_citation(self, provider: OfflineProvider) -> None:
        """参数问题返回引用。"""
        response = await provider.complete(
            AIRequest(
                messages=({"role": "user", "content": "搜索参数"},),
                tools=("search_parameters",),
                provider_mode="offline",
            )
        )
        assert len(response.citations) >= 1
        assert any(c.object_type == "parameter_version" for c in response.citations)

    async def test_provenance_query_returns_three_citations(self, provider: OfflineProvider) -> None:
        """溯源问题返回三类引用。"""
        response = await provider.complete(
            AIRequest(
                messages=({"role": "user", "content": "解释溯源链路"},),
                tools=("explain_provenance",),
                provider_mode="offline",
            )
        )
        types = {c.object_type for c in response.citations}
        assert "parameter_version" in types
        assert "derivation_run" in types
        assert "fact_revision" in types

    async def test_generic_query_returns_guidance(self, provider: OfflineProvider) -> None:
        """通用问题返回引导回答（无引用）。"""
        response = await provider.complete(
            AIRequest(
                messages=({"role": "user", "content": "你好"},),
                tools=(),
                provider_mode="offline",
            )
        )
        assert "IRIP AI 助手" in response.answer
        assert response.uncertainty is not None
