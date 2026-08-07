"""test_contracts.py — 契约测试：字段组合、变量名、限制、schema 一致性。

设计文档 §19.1 契约测试。
"""

from __future__ import annotations

import pytest

from packages.ai.numeric.contracts import (
    DEFAULT_QUANTILES,
    DEFAULT_STATISTICS,
    DESCRIBE_SERIES_SCHEMA,
    EVALUATE_EXPRESSION_SCHEMA,
    NUMERIC_ENGINE_VERSION,
    VARIABLE_NAME_PATTERN,
    DescribeSeriesRequest,
    ExpressionOptions,
    NumericError,
    NumericLimits,
    NumericSource,
)


# =============================================================================
# NumericLimits 默认值
# =============================================================================


class TestNumericLimits:
    """资源限制配置默认值。"""

    def test_default_limits(self) -> None:
        limits = NumericLimits()
        assert limits.max_expression_length == 512
        assert limits.max_ast_nodes == 128
        assert limits.max_ast_depth == 16
        assert limits.max_variables == 16
        assert limits.max_inline_series_length == 10_000
        assert limits.max_platform_series_length == 100_000
        assert limits.vector_preview_threshold == 1_000
        assert limits.computation_timeout_seconds == 3.0

    def test_limits_are_frozen(self) -> None:
        limits = NumericLimits()
        with pytest.raises(Exception):
            limits.max_expression_length = 999  # type: ignore[misc]


# =============================================================================
# ExpressionOptions.from_dict
# =============================================================================


class TestExpressionOptions:
    """ExpressionOptions.from_dict 枚举值校验。"""

    def test_default_options(self) -> None:
        opts = ExpressionOptions.from_dict(None)
        assert opts.angle_unit == "radian"
        assert opts.null_policy == "fail"
        assert opts.numeric_coercion == "strict"
        assert opts.broadcast_policy == "scalar_only"
        assert opts.domain_error == "fail"
        assert opts.numeric_type == "float64"

    def test_explicit_valid_options(self) -> None:
        opts = ExpressionOptions.from_dict({
            "angle_unit": "degree",
            "null_policy": "propagate",
        })
        assert opts.angle_unit == "degree"
        assert opts.null_policy == "propagate"

    def test_unsupported_angle_unit(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            ExpressionOptions.from_dict({"angle_unit": "gradian"})
        assert exc_info.value.code == "numeric_invalid_source"
        assert "angle_unit" in exc_info.value.path

    def test_unsupported_null_policy(self) -> None:
        with pytest.raises(NumericError):
            ExpressionOptions.from_dict({"null_policy": "omit"})

    def test_unsupported_numeric_coercion(self) -> None:
        with pytest.raises(NumericError):
            ExpressionOptions.from_dict({"numeric_coercion": "loose"})

    def test_unsupported_broadcast_policy(self) -> None:
        with pytest.raises(NumericError):
            ExpressionOptions.from_dict({"broadcast_policy": "numpy"})

    def test_unsupported_domain_error(self) -> None:
        with pytest.raises(NumericError):
            ExpressionOptions.from_dict({"domain_error": "clamp"})

    def test_unsupported_numeric_type(self) -> None:
        with pytest.raises(NumericError):
            ExpressionOptions.from_dict({"numeric_type": "float32"})

    def test_to_audit_dict(self) -> None:
        opts = ExpressionOptions.from_dict({"angle_unit": "degree"})
        audit = opts.to_audit_dict()
        assert audit["angle_unit"] == "degree"
        assert "null_policy" in audit
        assert "numeric_type" in audit
        assert "broadcast_policy" in audit


# =============================================================================
# DescribeSeriesRequest
# =============================================================================


class TestDescribeSeriesRequest:
    """DescribeSeriesRequest 参数解析。"""

    def test_default_statistics(self) -> None:
        req = DescribeSeriesRequest()
        assert req.statistics is None
        assert req.quantiles == DEFAULT_QUANTILES
        assert req.variance_mode == "both"
        assert req.null_policy == "fail"

    def test_effective_statistics_default(self) -> None:
        req = DescribeSeriesRequest()
        assert req.effective_statistics == DEFAULT_STATISTICS

    def test_effective_statistics_subset(self) -> None:
        req = DescribeSeriesRequest(statistics=("sum", "mean", "sum"))
        effective = req.effective_statistics
        assert "sum" in effective
        assert "mean" in effective
        assert effective.count("sum") == 1  # dedup

    def test_effective_statistics_order(self) -> None:
        req = DescribeSeriesRequest(statistics=("kurtosis", "count", "mean"))
        effective = req.effective_statistics
        # Should be in DEFAULT_STATISTICS order, not request order
        assert effective.index("count") < effective.index("mean")
        assert effective.index("mean") < effective.index("kurtosis")

    def test_to_audit_dict(self) -> None:
        req = DescribeSeriesRequest(variance_mode="sample", null_policy="omit")
        audit = req.to_audit_dict()
        assert audit["variance_mode"] == "sample"
        assert audit["null_policy"] == "omit"


# =============================================================================
# NumericSource 字段组合
# =============================================================================


class TestNumericSourceFields:
    """四种来源的合法/非法字段组合。"""

    def test_scalar_source_valid(self) -> None:
        src = NumericSource(name="T", source_type="scalar", value=900, unit="K")
        assert src.value == 900
        assert src.unit == "K"

    def test_inline_source_valid(self) -> None:
        src = NumericSource(name="x", source_type="inline", values=[1.0, 2.0, None], unit="MPa")
        assert src.values == [1.0, 2.0, None]
        assert src.unit == "MPa"

    def test_fact_series_valid(self) -> None:
        src = NumericSource(
            name="x",
            source_type="fact_series",
            fact_id="018f0000-0000-7000-8000-000000000001",
            series_index=0,
            column_name="value",
        )
        assert src.fact_id is not None
        assert src.series_index == 0

    def test_artifact_series_valid(self) -> None:
        src = NumericSource(
            name="x",
            source_type="artifact_series",
            artifact_id="018f0000-0000-7000-8000-000000000002",
            series_index=0,
            column_name="value",
        )
        assert src.artifact_id is not None

    def test_scalar_missing_value(self) -> None:
        src = NumericSource(name="T", source_type="scalar")
        assert src.value is None

    def test_inline_missing_values(self) -> None:
        src = NumericSource(name="x", source_type="inline")
        assert src.values is None


# =============================================================================
# 变量名格式
# =============================================================================


class TestVariableNamePattern:
    """变量名格式校验。"""

    @pytest.mark.parametrize("valid_name", [
        "x", "T", "_var", "my_var_2", "a" * 64,
        "UPPER", "lower", "Mixed_Case",
    ])
    def test_valid_names(self, valid_name: str) -> None:
        import re
        assert re.match(VARIABLE_NAME_PATTERN, valid_name)

    @pytest.mark.parametrize("invalid_name", [
        "1abc",       # starts with digit
        "",           # empty
        "a" * 65,     # too long
        "my-var",     # hyphen
        "my.var",     # dot
        "my var",     # space
        "x!",         # special char
    ])
    def test_invalid_names(self, invalid_name: str) -> None:
        import re
        assert not re.match(VARIABLE_NAME_PATTERN, invalid_name)


# =============================================================================
# Schema 一致性
# =============================================================================


class TestSchemaConsistency:
    """EVALUATE_EXPRESSION_SCHEMA / DESCRIBE_SERIES_SCHEMA 与 tools.py ToolSpec 一致性。"""

    def test_evaluate_schema_in_tools(self) -> None:
        from packages.ai.tools import WHITELIST_TOOLS
        specs = {s.name: s for s in WHITELIST_TOOLS}
        assert "evaluate_expression" in specs
        spec = specs["evaluate_expression"]
        assert spec.parameters_schema is EVALUATE_EXPRESSION_SCHEMA

    def test_describe_schema_in_tools(self) -> None:
        from packages.ai.tools import WHITELIST_TOOLS
        specs = {s.name: s for s in WHITELIST_TOOLS}
        assert "describe_series" in specs
        spec = specs["describe_series"]
        assert spec.parameters_schema is DESCRIBE_SERIES_SCHEMA

    def test_evaluate_schema_structure(self) -> None:
        schema = EVALUATE_EXPRESSION_SCHEMA
        assert schema["type"] == "object"
        assert "expression" in schema["properties"]
        assert "variables" in schema["properties"]
        assert "options" in schema["properties"]
        assert schema["required"] == ["expression", "variables"]
        assert schema["additionalProperties"] is False

    def test_evaluate_expression_length_limits(self) -> None:
        schema = EVALUATE_EXPRESSION_SCHEMA
        expr_prop = schema["properties"]["expression"]
        assert expr_prop["minLength"] == 1
        assert expr_prop["maxLength"] == 512

    def test_evaluate_variables_limits(self) -> None:
        schema = EVALUATE_EXPRESSION_SCHEMA
        var_prop = schema["properties"]["variables"]
        assert var_prop["minItems"] == 0
        assert var_prop["maxItems"] == 16

    def test_evaluate_options_enums(self) -> None:
        schema = EVALUATE_EXPRESSION_SCHEMA
        options = schema["properties"]["options"]["properties"]
        assert options["angle_unit"]["enum"] == ["radian", "degree"]
        assert options["null_policy"]["enum"] == ["fail", "propagate"]
        assert options["numeric_coercion"]["enum"] == ["strict"]
        assert options["broadcast_policy"]["enum"] == ["scalar_only"]
        assert options["domain_error"]["enum"] == ["fail"]
        assert options["numeric_type"]["enum"] == ["float64"]

    def test_describe_schema_structure(self) -> None:
        schema = DESCRIBE_SERIES_SCHEMA
        assert schema["type"] == "object"
        assert "series" in schema["properties"]
        assert "statistics" in schema["properties"]
        assert "quantiles" in schema["properties"]
        assert "variance_mode" in schema["properties"]
        assert "null_policy" in schema["properties"]
        assert schema["required"] == ["series"]
        assert schema["additionalProperties"] is False

    def test_describe_series_no_scalar(self) -> None:
        schema = DESCRIBE_SERIES_SCHEMA
        series_st = schema["properties"]["series"]["properties"]["source_type"]
        assert "scalar" not in series_st["enum"]

    def test_describe_variance_mode_enum(self) -> None:
        schema = DESCRIBE_SERIES_SCHEMA
        assert schema["properties"]["variance_mode"]["enum"] == ["population", "sample", "both"]

    def test_describe_null_policy_enum(self) -> None:
        schema = DESCRIBE_SERIES_SCHEMA
        assert schema["properties"]["null_policy"]["enum"] == ["fail", "omit", "propagate"]

    def test_describe_quantiles_max(self) -> None:
        schema = DESCRIBE_SERIES_SCHEMA
        assert schema["properties"]["quantiles"]["maxItems"] == 20

    def test_variable_source_schema_additional_properties_false(self) -> None:
        schema = EVALUATE_EXPRESSION_SCHEMA
        var_item = schema["properties"]["variables"]["items"]
        assert var_item["additionalProperties"] is False

    def test_tools_required_permission(self) -> None:
        from packages.ai.tools import WHITELIST_TOOLS
        specs = {s.name: s for s in WHITELIST_TOOLS}
        assert specs["evaluate_expression"].required_permission == "assistant:use"
        assert specs["describe_series"].required_permission == "assistant:use"

    def test_tools_category(self) -> None:
        from packages.ai.tools import WHITELIST_TOOLS
        specs = {s.name: s for s in WHITELIST_TOOLS}
        assert specs["evaluate_expression"].category == "ai_tool"
        assert specs["describe_series"].category == "ai_tool"

    def test_engine_version(self) -> None:
        assert NUMERIC_ENGINE_VERSION == "numeric-v1"

    def test_error_codes_registered(self) -> None:
        from packages.common.error_codes import ErrorCode
        codes = ErrorCode.all_codes()
        for code in [
            "numeric_expression_rejected",
            "numeric_invalid_source",
            "numeric_field_not_found",
            "numeric_non_numeric",
            "numeric_domain_error",
            "numeric_divide_by_zero",
            "numeric_unit_conflict",
            "numeric_size_limit",
            "numeric_non_finite_result",
            "numeric_timeout",
            "numeric_internal_error",
        ]:
            assert code in codes, f"error code {code} not registered"

    def test_error_code_http_status(self) -> None:
        from packages.common.error_codes import ErrorCode
        assert ErrorCode.NUMERIC_SIZE_LIMIT.http_status == 413
        assert ErrorCode.NUMERIC_INTERNAL_ERROR.http_status == 500
        assert ErrorCode.NUMERIC_DOMAIN_ERROR.http_status == 422
        assert ErrorCode.NUMERIC_DIVIDE_BY_ZERO.http_status == 422
        assert ErrorCode.NUMERIC_UNIT_CONFLICT.http_status == 422

    def test_all_tool_names_include_numeric(self) -> None:
        from packages.ai.tools import ALL_TOOL_NAMES
        assert "evaluate_expression" in ALL_TOOL_NAMES
        assert "describe_series" in ALL_TOOL_NAMES

    def test_ai_tool_names_include_numeric(self) -> None:
        from packages.ai.tools import AI_TOOL_NAMES
        assert "evaluate_expression" in AI_TOOL_NAMES
        assert "describe_series" in AI_TOOL_NAMES
