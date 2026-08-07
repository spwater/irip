"""test_service.py — NumericToolFacade 审计/引用/digest 测试。

设计文档 §19.7 审计与引用测试。
"""

from __future__ import annotations

import asyncio
import hashlib
from uuid import UUID

from packages.ai.numeric.contracts import (
    NUMERIC_ENGINE_VERSION,
    NumericPrincipal,
)
from packages.ai.numeric.data_resolver import NumericDataResolver
from packages.ai.numeric.service import NumericToolFacade

# =============================================================================
# 辅助
# =============================================================================


def make_principal() -> NumericPrincipal:
    return NumericPrincipal(
        user_id=UUID("018f0000-0000-7000-8000-000000000001"),
        department_id=UUID("018f0000-0000-7000-8000-000000000002"),
        roles=("lab_member",),
    )


def make_facade() -> NumericToolFacade:
    resolver = NumericDataResolver()
    return NumericToolFacade(resolver)


def run_eval(args: dict) -> any:
    facade = make_facade()
    return asyncio.run(facade.evaluate_expression(args, make_principal()))


def run_describe(args: dict) -> any:
    facade = make_facade()
    return asyncio.run(facade.describe_series(args, make_principal()))


# =============================================================================
# evaluate_expression 返回结构
# =============================================================================


class TestEvaluateResultStructure:
    """evaluate_expression 返回 {summary, llm_data, audit_data, citation_params}。"""

    def test_result_has_all_fields(self) -> None:
        result = run_eval(
            {
                "expression": "1 + 2",
                "variables": [
                    {"name": "x", "source_type": "scalar", "value": 1},
                    {"name": "y", "source_type": "scalar", "value": 2},
                ],
            }
        )
        assert hasattr(result, "summary")
        assert hasattr(result, "llm_data")
        assert hasattr(result, "audit_data")
        assert hasattr(result, "citation_params")

    def test_scalar_result_llm_data(self) -> None:
        result = run_eval(
            {
                "expression": "x + y",
                "variables": [
                    {"name": "x", "source_type": "scalar", "value": 3},
                    {"name": "y", "source_type": "scalar", "value": 4},
                ],
            }
        )
        assert result.llm_data["result_type"] == "scalar"
        assert result.llm_data["value"] == 7.0

    def test_summary_contains_result(self) -> None:
        result = run_eval(
            {
                "expression": "sum(x)",
                "variables": [
                    {"name": "x", "source_type": "inline", "values": [1, 2, 3]},
                ],
            }
        )
        assert "6.0" in result.summary or "6" in result.summary


# =============================================================================
# 审计数据不含原始数组
# =============================================================================


class TestAuditData:
    """审计数据不含原始数组。"""

    def test_audit_no_raw_values(self) -> None:
        result = run_eval(
            {
                "expression": "sum(x)",
                "variables": [
                    {"name": "x", "source_type": "inline", "values": list(range(100))},
                ],
            }
        )
        audit = result.audit_data
        audit_str = str(audit)
        # Should not contain the full array (check for array markers, not individual values)
        assert "[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10," not in audit_str
        # Should contain count, unit, digest, expression
        assert "count" in audit_str or any("count" in str(s) for s in audit.get("sources", []))

    def test_audit_has_engine_version(self) -> None:
        result = run_eval(
            {
                "expression": "1 + 1",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert result.audit_data["engine_version"] == NUMERIC_ENGINE_VERSION

    def test_audit_has_tool_name(self) -> None:
        result = run_eval(
            {
                "expression": "1 + 1",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert result.audit_data["tool"] == "evaluate_expression"

    def test_audit_has_expression(self) -> None:
        result = run_eval(
            {
                "expression": "1 + 1",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert result.audit_data["expression"] == "1 + 1"

    def test_audit_has_expression_sha256(self) -> None:
        result = run_eval(
            {
                "expression": "1 + 1",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert "expression_sha256" in result.audit_data
        expected = hashlib.sha256(b"1 + 1").hexdigest()
        assert result.audit_data["expression_sha256"] == expected

    def test_audit_has_sources_with_digest(self) -> None:
        result = run_eval(
            {
                "expression": "sum(x)",
                "variables": [
                    {"name": "x", "source_type": "inline", "values": [1, 2, 3]},
                ],
            }
        )
        sources = result.audit_data["sources"]
        assert len(sources) == 1
        assert sources[0]["name"] == "x"
        assert sources[0]["source_type"] == "inline"
        assert "input_sha256" in sources[0]
        assert sources[0]["count"] == 3

    def test_audit_has_policies(self) -> None:
        result = run_eval(
            {
                "expression": "1 + 1",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert "policies" in result.audit_data
        assert "null_policy" in result.audit_data["policies"]

    def test_audit_has_result_info(self) -> None:
        result = run_eval(
            {
                "expression": "sum(x)",
                "variables": [
                    {"name": "x", "source_type": "inline", "values": [1, 2, 3]},
                ],
            }
        )
        assert "result" in result.audit_data
        assert result.audit_data["result"]["result_type"] == "scalar"
        assert "sha256" in result.audit_data["result"]
        assert result.audit_data["result"]["truncated"] is False

    def test_audit_has_warnings(self) -> None:
        result = run_eval(
            {
                "expression": "sum(x)",
                "variables": [
                    {"name": "x", "source_type": "inline", "values": [1, 2, 3]},
                ],
            }
        )
        assert "warnings" in result.audit_data
        assert isinstance(result.audit_data["warnings"], list)

    def test_audit_has_duration(self) -> None:
        result = run_eval(
            {
                "expression": "1 + 1",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert "duration_ms" in result.audit_data


# =============================================================================
# citation_params
# =============================================================================


class TestCitationParams:
    """citation_params 包含引擎版本、来源 hash、策略和结果 digest。"""

    def test_citation_has_engine_version(self) -> None:
        result = run_eval(
            {
                "expression": "1 + 1",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert result.citation_params["engine_version"] == NUMERIC_ENGINE_VERSION

    def test_citation_has_tool_name(self) -> None:
        result = run_eval(
            {
                "expression": "1 + 1",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert result.citation_params["tool"] == "evaluate_expression"

    def test_citation_has_expression(self) -> None:
        result = run_eval(
            {
                "expression": "1 + 1",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert result.citation_params["expression"] == "1 + 1"

    def test_citation_has_sources(self) -> None:
        result = run_eval(
            {
                "expression": "sum(x)",
                "variables": [
                    {"name": "x", "source_type": "inline", "values": [1, 2, 3]},
                ],
            }
        )
        assert "sources" in result.citation_params
        assert len(result.citation_params["sources"]) == 1
        assert "input_sha256" in result.citation_params["sources"][0]

    def test_citation_has_result_digest(self) -> None:
        result = run_eval(
            {
                "expression": "sum(x)",
                "variables": [
                    {"name": "x", "source_type": "inline", "values": [1, 2, 3]},
                ],
            }
        )
        assert "result_sha256" in result.citation_params

    def test_citation_has_timestamp(self) -> None:
        result = run_eval(
            {
                "expression": "1 + 1",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert "timestamp" in result.citation_params

    def test_citation_sources_sorted(self) -> None:
        """citation sources are sorted by name (stable ordering)。"""
        result = run_eval(
            {
                "expression": "x + y",
                "variables": [
                    {"name": "y", "source_type": "scalar", "value": 2},
                    {"name": "x", "source_type": "scalar", "value": 1},
                ],
            }
        )
        sources = result.citation_params["sources"]
        names = [s["name"] for s in sources]
        assert names == sorted(names)


# =============================================================================
# digest 稳定性
# =============================================================================


class TestDigestStability:
    """digest 对相同输入稳定，对输入变化敏感。"""

    def test_same_input_same_audit_digest(self) -> None:
        args = {
            "expression": "sum(x)",
            "variables": [
                {"name": "x", "source_type": "inline", "values": [1, 2, 3]},
            ],
        }
        result1 = run_eval(args)
        result2 = run_eval(args)
        assert result1.audit_data["result"]["sha256"] == result2.audit_data["result"]["sha256"]

    def test_different_input_different_audit_digest(self) -> None:
        result1 = run_eval(
            {
                "expression": "sum(x)",
                "variables": [{"name": "x", "source_type": "inline", "values": [1, 2, 3]}],
            }
        )
        result2 = run_eval(
            {
                "expression": "sum(x)",
                "variables": [{"name": "x", "source_type": "inline", "values": [1, 2, 4]}],
            }
        )
        assert result1.audit_data["result"]["sha256"] != result2.audit_data["result"]["sha256"]

    def test_same_input_same_citation_result(self) -> None:
        args = {
            "expression": "sum(x)",
            "variables": [{"name": "x", "source_type": "inline", "values": [1, 2, 3]}],
        }
        result1 = run_eval(args)
        result2 = run_eval(args)
        assert result1.citation_params["result_sha256"] == result2.citation_params["result_sha256"]


# =============================================================================
# 向量截断状态和 warnings 进入审计
# =============================================================================


class TestVectorTruncationInAudit:
    """向量截断状态和 warnings 进入审计。"""

    def test_truncated_in_audit(self) -> None:
        result = run_eval(
            {
                "expression": "x + 1",
                "variables": [
                    {"name": "x", "source_type": "inline", "values": list(range(2000))},
                ],
            }
        )
        assert result.audit_data["result"]["truncated"] is True

    def test_not_truncated_in_audit(self) -> None:
        result = run_eval(
            {
                "expression": "x + 1",
                "variables": [
                    {"name": "x", "source_type": "inline", "values": list(range(10))},
                ],
            }
        )
        assert result.audit_data["result"]["truncated"] is False

    def test_warnings_in_audit(self) -> None:
        # Use unit-unknown to trigger unit_unverified warning
        result = run_eval(
            {
                "expression": "x + y",
                "variables": [
                    {"name": "x", "source_type": "inline", "values": [1, 2], "unit": "MPa"},
                    {"name": "y", "source_type": "inline", "values": [3, 4]},
                ],
            }
        )
        # y has unknown unit, x has known unit -> unit_unverified warning
        assert "unit_unverified" in result.audit_data["warnings"]


# =============================================================================
# 错误转换为结构化 NumericError
# =============================================================================


class TestErrorStructuredResult:
    """错误转换为结构化 NumericError。"""

    def test_domain_error_in_llm_data(self) -> None:
        result = run_eval(
            {
                "expression": "log(-1)",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert "error" in result.llm_data
        assert result.llm_data["error"]["code"] == "numeric_domain_error"

    def test_divide_by_zero_in_llm_data(self) -> None:
        result = run_eval(
            {
                "expression": "1 / 0",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert result.llm_data["error"]["code"] == "numeric_divide_by_zero"

    def test_error_summary(self) -> None:
        result = run_eval(
            {
                "expression": "log(-1)",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert "failed" in result.summary

    def test_error_in_audit(self) -> None:
        result = run_eval(
            {
                "expression": "log(-1)",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert "error" in result.audit_data
        assert result.audit_data["error"]["code"] == "numeric_domain_error"

    def test_error_in_citation(self) -> None:
        result = run_eval(
            {
                "expression": "log(-1)",
                "variables": [{"name": "x", "source_type": "scalar", "value": 1}],
            }
        )
        assert result.citation_params["error"] == "numeric_domain_error"

    def test_error_no_raw_values_in_details(self) -> None:
        result = run_eval(
            {
                "expression": "log(x)",
                "variables": [{"name": "x", "source_type": "inline", "values": [-1, 1]}],
            }
        )
        error = result.llm_data.get("error", {})
        if "details" in error:
            details_str = str(error["details"])
            assert "-1" not in details_str  # no raw values


# =============================================================================
# describe_series 返回结构
# =============================================================================


class TestDescribeSeriesResult:
    """describe_series 返回结构。"""

    def test_describe_result_has_all_fields(self) -> None:
        result = run_describe(
            {
                "series": {
                    "name": "x",
                    "source_type": "inline",
                    "values": [1, 2, 3, 4, 5],
                },
            }
        )
        assert hasattr(result, "summary")
        assert hasattr(result, "llm_data")
        assert hasattr(result, "audit_data")
        assert hasattr(result, "citation_params")

    def test_describe_llm_data(self) -> None:
        result = run_describe(
            {
                "series": {
                    "name": "x",
                    "source_type": "inline",
                    "values": [1, 2, 3, 4, 5],
                },
            }
        )
        assert result.llm_data["result_type"] == "statistics"
        assert "statistics" in result.llm_data

    def test_describe_audit_engine_version(self) -> None:
        result = run_describe(
            {
                "series": {
                    "name": "x",
                    "source_type": "inline",
                    "values": [1, 2, 3],
                },
            }
        )
        assert result.audit_data["engine_version"] == NUMERIC_ENGINE_VERSION
        assert result.audit_data["tool"] == "describe_series"

    def test_describe_audit_no_raw_values(self) -> None:
        values = list(range(100))
        result = run_describe(
            {
                "series": {
                    "name": "x",
                    "source_type": "inline",
                    "values": values,
                },
            }
        )
        audit_str = str(result.audit_data)
        # Should not have raw array values (check for array markers, not individual digits)
        assert "[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10," not in audit_str

    def test_describe_citation_has_engine_version(self) -> None:
        result = run_describe(
            {
                "series": {
                    "name": "x",
                    "source_type": "inline",
                    "values": [1, 2, 3],
                },
            }
        )
        assert result.citation_params["engine_version"] == NUMERIC_ENGINE_VERSION
        assert "result_sha256" in result.citation_params

    def test_describe_variance_both_mode(self) -> None:
        """默认返回总体和样本两个方差结果。"""
        result = run_describe(
            {
                "series": {
                    "name": "x",
                    "source_type": "inline",
                    "values": [1, 2, 3, 4, 5],
                },
            }
        )
        variance = result.llm_data["statistics"]["variance"]
        assert isinstance(variance, dict)
        assert "population" in variance
        assert "sample" in variance

    def test_describe_error_result(self) -> None:
        result = run_describe(
            {
                "series": {
                    "name": "x",
                    "source_type": "inline",
                    "values": [1, None, 3],
                },
                "null_policy": "fail",
            }
        )
        assert "error" in result.llm_data
        assert result.llm_data["error"]["code"] == "numeric_invalid_source"
