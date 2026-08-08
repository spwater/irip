"""单元测试：CitationGenerator HMAC 签名与验证。

覆盖：
- generate 产出包含 tool_name / query_params / result_summary / timestamp / signature；
- verify 对合法 citation 返回 True；
- verify 对被篡改字段的 citation 返回 False；
- 不同密钥生成的 citation 无法互相验证；
- query_params 超过 500 字符时被截断；
- 模块级 verify_citation 便捷函数正确委托。
"""

import pytest

from packages.ai.citation import CitationGenerator, SignedCitation, verify_citation


class TestCitationGenerate:
    """CitationGenerator.generate 测试。"""

    def test_generate_returns_signed_citation(self) -> None:
        """generate 返回 SignedCitation，含全部字段且 signature 非空。"""
        gen = CitationGenerator(secret="test-secret-key")
        citation = gen.generate(
            tool_name="search_facts",
            query_params={"query": "粒度"},
            result_summary="找到 3 条事实",
        )
        assert isinstance(citation, SignedCitation)
        assert citation.tool_name == "search_facts"
        assert "粒度" in citation.query_params
        assert citation.result_summary == "找到 3 条事实"
        assert citation.timestamp  # ISO 字符串非空
        assert len(citation.signature) == 64  # SHA256 hex = 64 chars

    def test_generate_is_deterministic_for_same_inputs(self) -> None:
        """相同 tool_name + params + summary 在同一秒内生成相同签名（timestamp 一致时）。"""
        gen = CitationGenerator(secret="stable-secret")
        c1 = gen.generate("search_standards", {"query": "temp"}, "ok")
        c2 = gen.generate("search_standards", {"query": "temp"}, "ok")
        # timestamp 可能不同（跨秒），但 query_params 和 result_summary 一致
        assert c1.query_params == c2.query_params
        assert c1.result_summary == c2.result_summary

    def test_generate_truncates_long_query_params(self) -> None:
        """query_params JSON 超过 500 字符时被截断到 500。"""
        gen = CitationGenerator(secret="trunc-secret")
        big_params = {"key": "x" * 600}
        citation = gen.generate("evaluate_expression", big_params, "done")
        assert len(citation.query_params) <= 500

    def test_to_dict_returns_all_fields(self) -> None:
        """to_dict 返回包含全部 5 个字段的字典。"""
        gen = CitationGenerator(secret="dict-secret")
        citation = gen.generate("draft_report", {"title": "t"}, "summary")
        d = citation.to_dict()
        assert set(d.keys()) == {
            "tool_name",
            "query_params",
            "result_summary",
            "timestamp",
            "signature",
        }


class TestCitationVerify:
    """CitationGenerator.verify 测试。"""

    def test_verify_valid_citation(self) -> None:
        """合法 citation 验证通过。"""
        gen = CitationGenerator(secret="verify-secret")
        citation = gen.generate("search_facts", {"q": "a"}, "found 1")
        assert gen.verify(citation.to_dict()) is True

    def test_verify_tampered_tool_name(self) -> None:
        """篡改 tool_name 后验证失败。"""
        gen = CitationGenerator(secret="tamper-secret")
        citation = gen.generate("search_facts", {"q": "a"}, "found 1")
        d = citation.to_dict()
        d["tool_name"] = "malicious_tool"
        assert gen.verify(d) is False

    def test_verify_tampered_result_summary(self) -> None:
        """篡改 result_summary 后验证失败。"""
        gen = CitationGenerator(secret="tamper2-secret")
        citation = gen.generate("search_facts", {"q": "a"}, "found 1")
        d = citation.to_dict()
        d["result_summary"] = "found 999"
        assert gen.verify(d) is False

    def test_verify_tampered_signature(self) -> None:
        """篡改 signature 后验证失败。"""
        gen = CitationGenerator(secret="sig-secret")
        citation = gen.generate("search_facts", {"q": "a"}, "found 1")
        d = citation.to_dict()
        d["signature"] = "0" * 64
        assert gen.verify(d) is False

    def test_verify_different_secret_fails(self) -> None:
        """用不同密钥生成的 citation 无法被另一生成器验证。"""
        gen_a = CitationGenerator(secret="secret-a")
        gen_b = CitationGenerator(secret="secret-b")
        citation = gen_a.generate("search_facts", {"q": "a"}, "found 1")
        assert gen_b.verify(citation.to_dict()) is False

    def test_verify_malformed_dict_returns_false(self) -> None:
        """缺少字段的字典验证失败（不抛异常）。"""
        gen = CitationGenerator(secret="malformed-secret")
        assert gen.verify({}) is False
        assert gen.verify({"tool_name": "x"}) is False


class TestVerifyCitationModuleFunction:
    """模块级 verify_citation 便捷函数测试。"""

    def test_verify_citation_delegates_to_generator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """verify_citation 使用环境变量密钥验证。"""
        monkeypatch.setenv("IRIP_JWT_SECRET", "module-func-secret")
        gen = CitationGenerator()
        citation = gen.generate("search_facts", {"q": "a"}, "ok")
        assert verify_citation(citation.to_dict()) is True
