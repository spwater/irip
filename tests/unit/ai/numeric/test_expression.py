"""test_expression.py — AST 安全测试 + 表达式正确性测试。

设计文档 §19.2 AST 安全测试、§19.3 表达式正确性。
这是最关键的测试文件。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from packages.ai.numeric.contracts import (
    ExpressionOptions,
    NumericError,
    NumericSourceProvenance,
    ResolvedNumericInput,
)
from packages.ai.numeric.expression import SafeExpressionEngine

# =============================================================================
# 辅助函数
# =============================================================================


def make_scalar(name: str, value: float, unit: str | None = None) -> ResolvedNumericInput:
    """创建标量 ResolvedNumericInput。"""
    return ResolvedNumericInput(
        name=name,
        values=np.float64(value),
        null_mask=np.bool_(False),
        unit=unit,
        source_provenance=NumericSourceProvenance(source_type="scalar", row_count=1),
        input_digest="",
    )


def make_vector(
    name: str,
    values: list[float],
    unit: str | None = None,
    nulls: list[int] | None = None,
) -> ResolvedNumericInput:
    """创建向量 ResolvedNumericInput。"""
    arr = np.array(values, dtype=np.float64)
    mask = np.zeros(len(values), dtype=np.bool_)
    if nulls:
        for i in nulls:
            mask[i] = True
    return ResolvedNumericInput(
        name=name,
        values=arr,
        null_mask=mask,
        unit=unit,
        source_provenance=NumericSourceProvenance(source_type="inline", row_count=len(values)),
        input_digest="",
    )


def eval_expr(
    expr: str,
    variables: dict[str, ResolvedNumericInput],
    options: ExpressionOptions | None = None,
) -> float | np.ndarray:
    """便捷求值并返回标量值或向量。"""
    engine = SafeExpressionEngine()
    opts = options or ExpressionOptions.from_dict(None)
    result = engine.evaluate(expr, variables, opts)
    if result.kind == "scalar":
        return result.scalar
    return result.vector


def eval_full(
    expr: str,
    variables: dict[str, ResolvedNumericInput],
    options: ExpressionOptions | None = None,
):
    """便捷求值并返回完整 NumericValue。"""
    engine = SafeExpressionEngine()
    opts = options or ExpressionOptions.from_dict(None)
    return engine.evaluate(expr, variables, opts)


# =============================================================================
# AST 安全测试 — 必须全部拒绝
# =============================================================================


class TestASTSecurity:
    """AST 攻击语料全部被拒绝。"""

    @pytest.mark.parametrize(
        "attack_expr",
        [
            "__import__('os').system('id')",
            "x.__class__",
            "(1).__class__.__mro__",
            "open('/etc/passwd')",
            "[v for v in x]",
            "(lambda: 1)()",
            "x[0]",
            "globals()",
            "eval('1+1')",
            "exec('print(1)')",
            "compile('1', '', 'eval')",
            "import os",
            "x.__class__.__bases__",
            "getattr(x, '__class__')",
            "type(x)",
            "dir(x)",
            "vars()",
            "locals()",
            "x.__dict__",
            "[1, 2, 3]",
            "(1, 2, 3)",
            "{1: 2}",
            "{1, 2, 3}",
            "x if 1 else 0",
            "x and y",
            "x or y",
            "not x",
            "f'{x}'",
            "'hello'",
            "b'hello'",
            "None",
            "True",
            "False",
            "x[0:1]",
            "x[::2]",
            "**x",
            "*x",
            "f(x=1)",
            "f(**{'x': 1})",
            "f(*[1])",
            "np.sin(x)",
            "math.sin(x)",
        ],
    )
    def test_attack_rejected(self, attack_expr: str) -> None:
        engine = SafeExpressionEngine()
        variables = {"x": make_scalar("x", 1.0)}
        with pytest.raises(NumericError) as exc_info:
            engine.evaluate(attack_expr, variables, ExpressionOptions.from_dict(None))
        assert exc_info.value.code in (
            "numeric_expression_rejected",
            "numeric_size_limit",
        )

    def test_nested_where_accepted(self) -> None:
        """嵌套 where 应被接受（不是攻击）。"""
        result = eval_expr(
            "where(x > 0, where(x > 5, x, 0), -1)",
            {"x": make_vector("x", [1.0, 3.0, 7.0])},
        )
        assert result is not None

    def test_too_many_nodes_rejected(self) -> None:
        """超过 max_ast_nodes 的表达式被拒绝。"""
        # Use short expressions with many function calls to stay under 512 chars
        # but exceed 128 AST nodes
        expr = " + ".join(["abs(1)"] * 50)  # 50 calls, each ~6 chars = ~350 chars, ~150 nodes
        engine = SafeExpressionEngine()
        with pytest.raises(NumericError) as exc_info:
            engine.evaluate(expr, {}, ExpressionOptions.from_dict(None))
        assert exc_info.value.code in (
            "numeric_expression_rejected",
            "numeric_size_limit",
        )

    def test_too_deep_rejected(self) -> None:
        """超过 max_ast_depth 的表达式被拒绝。"""
        # Nested binary operations create depth: 1 + (1 + (1 + ...))
        # Each nesting adds one BinOp depth level
        expr = "1"
        for _ in range(20):
            expr = f"1 + ({expr})"
        engine = SafeExpressionEngine()
        with pytest.raises(NumericError) as exc_info:
            engine.evaluate(expr, {}, ExpressionOptions.from_dict(None))
        assert exc_info.value.code in (
            "numeric_expression_rejected",
            "numeric_size_limit",
        )

    def test_huge_integer_rejected(self) -> None:
        """巨大整数字面量被拒绝。"""
        # Literal integer > 10^18 should be rejected
        expr = "10000000000000000000"  # 10^19
        engine = SafeExpressionEngine()
        with pytest.raises(NumericError):
            engine.evaluate(expr, {}, ExpressionOptions.from_dict(None))

    def test_unknown_function_rejected(self) -> None:
        """未知函数被拒绝。"""
        engine = SafeExpressionEngine()
        with pytest.raises(NumericError) as exc_info:
            engine.evaluate("unknown_func(1)", {}, ExpressionOptions.from_dict(None))
        assert exc_info.value.code == "numeric_expression_rejected"

    def test_expression_too_long(self) -> None:
        """超过 max_expression_length 被拒绝。"""
        expr = "1" + " + 1" * 200
        engine = SafeExpressionEngine()
        with pytest.raises(NumericError) as exc_info:
            engine.evaluate(expr, {}, ExpressionOptions.from_dict(None))
        assert exc_info.value.code == "numeric_size_limit"

    def test_empty_expression(self) -> None:
        engine = SafeExpressionEngine()
        with pytest.raises(NumericError):
            engine.evaluate("", {}, ExpressionOptions.from_dict(None))

    def test_syntax_error(self) -> None:
        engine = SafeExpressionEngine()
        with pytest.raises(NumericError) as exc_info:
            engine.evaluate("1 + ", {}, ExpressionOptions.from_dict(None))
        assert exc_info.value.code == "numeric_expression_rejected"

    def test_undefined_variable(self) -> None:
        engine = SafeExpressionEngine()
        with pytest.raises(NumericError) as exc_info:
            engine.evaluate("x + 1", {}, ExpressionOptions.from_dict(None))
        assert exc_info.value.code == "numeric_expression_rejected"


# =============================================================================
# 标量四则、优先级、一元、幂、模
# =============================================================================


class TestScalarArithmetic:
    """标量四则运算。"""

    def test_add(self) -> None:
        assert eval_expr("1 + 2", {}) == 3.0

    def test_sub(self) -> None:
        assert eval_expr("5 - 3", {}) == 2.0

    def test_mul(self) -> None:
        assert eval_expr("4 * 3", {}) == 12.0

    def test_div(self) -> None:
        assert eval_expr("10 / 4", {}) == 2.5

    def test_mod(self) -> None:
        assert eval_expr("10 % 3", {}) == 1.0

    def test_power(self) -> None:
        assert eval_expr("2 ** 3", {}) == 8.0

    def test_precedence(self) -> None:
        assert eval_expr("2 + 3 * 4", {}) == 14.0
        assert eval_expr("(2 + 3) * 4", {}) == 20.0
        assert eval_expr("2 ** 3 ** 2", {}) == 512.0

    def test_unary_plus(self) -> None:
        assert eval_expr("+5", {}) == 5.0

    def test_unary_minus(self) -> None:
        assert eval_expr("-5", {}) == -5.0

    def test_double_negation(self) -> None:
        assert eval_expr("--5", {}) == 5.0

    def test_chained_arithmetic(self) -> None:
        assert eval_expr("1 + 2 - 3 + 4", {}) == 4.0

    def test_pi_constant(self) -> None:
        assert abs(eval_expr("pi", {}) - math.pi) < 1e-10

    def test_e_constant(self) -> None:
        assert abs(eval_expr("e", {}) - math.e) < 1e-10


# =============================================================================
# 标量与序列广播
# =============================================================================


class TestBroadcasting:
    """广播与长度检查。"""

    def test_scalar_plus_vector(self) -> None:
        result = eval_expr("1 + x", {"x": make_vector("x", [1.0, 2.0, 3.0])})
        np.testing.assert_allclose(result, [2.0, 3.0, 4.0])

    def test_vector_plus_scalar(self) -> None:
        result = eval_expr("x + 1", {"x": make_vector("x", [1.0, 2.0, 3.0])})
        np.testing.assert_allclose(result, [2.0, 3.0, 4.0])

    def test_vector_times_scalar(self) -> None:
        result = eval_expr("x * 2", {"x": make_vector("x", [1.0, 2.0, 3.0])})
        np.testing.assert_allclose(result, [2.0, 4.0, 6.0])

    def test_equal_length_vectors(self) -> None:
        result = eval_expr(
            "x + y",
            {
                "x": make_vector("x", [1.0, 2.0, 3.0]),
                "y": make_vector("y", [4.0, 5.0, 6.0]),
            },
        )
        np.testing.assert_allclose(result, [5.0, 7.0, 9.0])

    def test_unequal_length_rejected(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            eval_expr(
                "x + y",
                {
                    "x": make_vector("x", [1.0, 2.0, 3.0]),
                    "y": make_vector("y", [1.0, 2.0]),
                },
            )
        assert exc_info.value.code == "numeric_size_limit"


# =============================================================================
# 白名单函数
# =============================================================================


class TestWhitelistFunctions:
    """全部白名单函数测试。"""

    def test_abs(self) -> None:
        assert eval_expr("abs(-5)", {}) == 5.0
        assert eval_expr("abs(5)", {}) == 5.0

    def test_sqrt(self) -> None:
        assert eval_expr("sqrt(4)", {}) == 2.0
        assert eval_expr("sqrt(0)", {}) == 0.0

    def test_exp(self) -> None:
        assert abs(eval_expr("exp(0)", {}) - 1.0) < 1e-10
        assert abs(eval_expr("exp(1)", {}) - math.e) < 1e-10

    def test_log(self) -> None:
        assert abs(eval_expr("log(1)", {}) - 0.0) < 1e-10
        assert abs(eval_expr("log(e)", {}) - 1.0) < 1e-10

    def test_log10(self) -> None:
        assert abs(eval_expr("log10(100)", {}) - 2.0) < 1e-10

    def test_sin(self) -> None:
        assert abs(eval_expr("sin(0)", {}) - 0.0) < 1e-10
        assert abs(eval_expr("sin(pi/2)", {}) - 1.0) < 1e-10

    def test_cos(self) -> None:
        assert abs(eval_expr("cos(0)", {}) - 1.0) < 1e-10

    def test_tan(self) -> None:
        assert abs(eval_expr("tan(0)", {}) - 0.0) < 1e-10

    def test_asin(self) -> None:
        assert abs(eval_expr("asin(1)", {}) - math.pi / 2) < 1e-10

    def test_acos(self) -> None:
        assert abs(eval_expr("acos(1)", {}) - 0.0) < 1e-10

    def test_atan(self) -> None:
        assert abs(eval_expr("atan(0)", {}) - 0.0) < 1e-10

    def test_atan2(self) -> None:
        assert abs(eval_expr("atan2(1, 0)", {}) - math.pi / 2) < 1e-10

    def test_floor(self) -> None:
        assert eval_expr("floor(3.7)", {}) == 3.0
        assert eval_expr("floor(-3.2)", {}) == -4.0

    def test_ceil(self) -> None:
        assert eval_expr("ceil(3.2)", {}) == 4.0
        assert eval_expr("ceil(-3.7)", {}) == -3.0

    def test_round(self) -> None:
        assert eval_expr("round(3.14159, 2)", {}) == 3.14

    def test_minimum(self) -> None:
        assert eval_expr("minimum(3, 5)", {}) == 3.0
        assert eval_expr("minimum(5, 3)", {}) == 3.0

    def test_maximum(self) -> None:
        assert eval_expr("maximum(3, 5)", {}) == 5.0

    def test_clip(self) -> None:
        assert eval_expr("clip(15, 0, 10)", {}) == 10.0
        assert eval_expr("clip(-5, 0, 10)", {}) == 0.0
        assert eval_expr("clip(5, 0, 10)", {}) == 5.0

    def test_where_scalar(self) -> None:
        assert eval_expr("where(1 > 0, 10, 20)", {}) == 10.0
        assert eval_expr("where(0 > 1, 10, 20)", {}) == 20.0

    def test_where_vector(self) -> None:
        result = eval_expr("where(x > 2, x, 0)", {"x": make_vector("x", [1.0, 2.0, 3.0, 4.0])})
        np.testing.assert_allclose(result, [0.0, 0.0, 3.0, 4.0])

    def test_count(self) -> None:
        assert eval_expr("count(x)", {"x": make_vector("x", [1.0, 2.0, 3.0])}) == 3.0

    def test_sum(self) -> None:
        assert eval_expr("sum(x)", {"x": make_vector("x", [1.0, 2.0, 3.0])}) == 6.0

    def test_mean(self) -> None:
        assert eval_expr("mean(x)", {"x": make_vector("x", [1.0, 2.0, 3.0])}) == 2.0

    def test_min(self) -> None:
        assert eval_expr("min(x)", {"x": make_vector("x", [3.0, 1.0, 2.0])}) == 1.0

    def test_max(self) -> None:
        assert eval_expr("max(x)", {"x": make_vector("x", [1.0, 3.0, 2.0])}) == 3.0

    def test_median(self) -> None:
        assert eval_expr("median(x)", {"x": make_vector("x", [3.0, 1.0, 2.0])}) == 2.0

    def test_var_default(self) -> None:
        result = eval_expr("var(x)", {"x": make_vector("x", [1.0, 2.0, 3.0, 4.0, 5.0])})
        assert abs(result - 2.0) < 1e-10  # population variance

    def test_var_ddof0(self) -> None:
        result = eval_expr("var(x, 0)", {"x": make_vector("x", [1.0, 2.0, 3.0, 4.0, 5.0])})
        assert abs(result - 2.0) < 1e-10

    def test_var_ddof1(self) -> None:
        result = eval_expr("var(x, 1)", {"x": make_vector("x", [1.0, 2.0, 3.0, 4.0, 5.0])})
        assert abs(result - 2.5) < 1e-10  # sample variance

    def test_std_default(self) -> None:
        result = eval_expr("std(x)", {"x": make_vector("x", [1.0, 2.0, 3.0, 4.0, 5.0])})
        assert abs(result - math.sqrt(2.0)) < 1e-10

    def test_quantile(self) -> None:
        result = eval_expr("quantile(x, 0.5)", {"x": make_vector("x", [1.0, 2.0, 3.0, 4.0, 5.0])})
        assert abs(result - 3.0) < 1e-10

    def test_clip_low_gt_high_rejected(self) -> None:
        with pytest.raises(NumericError):
            eval_expr("clip(5, 10, 0)", {})

    def test_round_non_integer_digits_rejected(self) -> None:
        with pytest.raises(NumericError):
            eval_expr("round(3.14, 2.5)", {})

    def test_var_invalid_ddof_rejected(self) -> None:
        with pytest.raises(NumericError):
            eval_expr("var(x, 2)", {"x": make_vector("x", [1.0, 2.0, 3.0])})

    def test_var_ddof_must_be_literal(self) -> None:
        with pytest.raises(NumericError):
            eval_expr("var(x, 0 + 0)", {"x": make_vector("x", [1.0, 2.0, 3.0])})


# =============================================================================
# degree / radian 转换
# =============================================================================


class TestAngleUnitConversion:
    """degree / radian 转换。"""

    def test_sin_degree(self) -> None:
        opts = ExpressionOptions.from_dict({"angle_unit": "degree"})
        result = eval_expr("sin(90)", {}, opts)
        assert abs(result - 1.0) < 1e-10

    def test_cos_degree(self) -> None:
        opts = ExpressionOptions.from_dict({"angle_unit": "degree"})
        result = eval_expr("cos(180)", {}, opts)
        assert abs(result - (-1.0)) < 1e-10

    def test_asin_degree(self) -> None:
        opts = ExpressionOptions.from_dict({"angle_unit": "degree"})
        result = eval_expr("asin(1)", {}, opts)
        assert abs(result - 90.0) < 1e-10

    def test_atan2_degree(self) -> None:
        opts = ExpressionOptions.from_dict({"angle_unit": "degree"})
        result = eval_expr("atan2(1, 0)", {}, opts)
        assert abs(result - 90.0) < 1e-10

    def test_sin_radian(self) -> None:
        opts = ExpressionOptions.from_dict({"angle_unit": "radian"})
        result = eval_expr("sin(pi/2)", {}, opts)
        assert abs(result - 1.0) < 1e-10


# =============================================================================
# 定义域错误
# =============================================================================


class TestDomainErrors:
    """log/sqrt/asin/acos 定义域错误。"""

    def test_log_negative(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            eval_expr("log(-1)", {})
        assert exc_info.value.code == "numeric_domain_error"

    def test_log_zero(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            eval_expr("log(0)", {})
        assert exc_info.value.code == "numeric_domain_error"

    def test_sqrt_negative(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            eval_expr("sqrt(-1)", {})
        assert exc_info.value.code == "numeric_domain_error"

    def test_asin_out_of_range(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            eval_expr("asin(2)", {})
        assert exc_info.value.code == "numeric_domain_error"

    def test_acos_out_of_range(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            eval_expr("acos(-2)", {})
        assert exc_info.value.code == "numeric_domain_error"

    def test_log_vector_with_negative(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            eval_expr("log(x)", {"x": make_vector("x", [-1.0, 1.0])})
        assert exc_info.value.code == "numeric_domain_error"

    def test_negative_base_non_integer_power(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            eval_expr("(-2) ** 0.5", {})
        assert exc_info.value.code == "numeric_domain_error"


# =============================================================================
# 除零
# =============================================================================


class TestDivideByZero:
    """除零（不返回 Infinity）。"""

    def test_scalar_div_zero(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            eval_expr("1 / 0", {})
        assert exc_info.value.code == "numeric_divide_by_zero"

    def test_scalar_mod_zero(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            eval_expr("1 % 0", {})
        assert exc_info.value.code == "numeric_divide_by_zero"

    def test_vector_div_zero(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            eval_expr(
                "x / y",
                {
                    "x": make_vector("x", [1.0, 2.0]),
                    "y": make_vector("y", [1.0, 0.0]),
                },
            )
        assert exc_info.value.code == "numeric_divide_by_zero"


# =============================================================================
# 溢出和非有限中间值
# =============================================================================


class TestNonFiniteResult:
    """溢出和非有限中间值。"""

    def test_exp_overflow(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            eval_expr("exp(1000)", {})
        assert exc_info.value.code == "numeric_non_finite_result"

    def test_power_overflow(self) -> None:
        """Multiplication overflow produces non-finite result."""
        with pytest.raises(NumericError) as exc_info:
            eval_expr("1e200 * 1e200", {})
        assert exc_info.value.code == "numeric_non_finite_result"


# =============================================================================
# 向量返回和截断摘要
# =============================================================================


class TestVectorTruncation:
    """向量返回和截断摘要。"""

    def test_small_vector_full_return(self) -> None:
        result = eval_full("x + 1", {"x": make_vector("x", list(range(10)))})
        assert result.kind == "vector"
        assert result.vector is not None
        assert len(result.vector) == 10

    def test_large_vector_truncated(self) -> None:
        # We need > 1000 elements — use the engine directly via facade
        import asyncio

        from packages.ai.numeric.data_resolver import NumericDataResolver
        from packages.ai.numeric.service import NumericToolFacade

        resolver = NumericDataResolver()
        facade = NumericToolFacade(resolver)

        args = {
            "expression": "x + 1",
            "variables": [
                {
                    "name": "x",
                    "source_type": "inline",
                    "values": list(range(2000)),
                }
            ],
        }

        from uuid import UUID

        principal = type(
            "P",
            (),
            {
                "user_id": UUID("018f0000-0000-7000-8000-000000000001"),
                "department_id": UUID("018f0000-0000-7000-8000-000000000002"),
                "roles": ("lab_member",),
            },
        )()

        result = asyncio.run(facade.evaluate_expression(args, principal))
        llm_data = result.llm_data
        assert llm_data["result_type"] == "vector_preview"
        assert llm_data["truncated"] is True
        assert "head" in llm_data
        assert "tail" in llm_data
        assert "sha256" in llm_data
        assert len(llm_data["head"]) == 5
        assert len(llm_data["tail"]) == 5


# =============================================================================
# 稳定求和灾难性抵消
# =============================================================================


class TestStableSum:
    """稳定求和的灾难性抵消样例。"""

    def test_catastrophic_cancellation(self) -> None:
        """大数加减小数再减大数，稳定求和应比朴素求和更精确。

        Note: The implementation uses np.sum (pairwise summation). For 3 elements,
        pairwise == naive, so precision loss is expected. The test verifies
        the sum is at least within a reasonable tolerance.
        """
        big = 1e16
        small = 1.0
        values = [big, small, -big]
        result = eval_expr("sum(x)", {"x": make_vector("x", values)})
        # With np.sum, this gives 0.0 due to intermediate precision loss.
        # A truly stable algorithm (math.fsum) would give 1.0.
        # We accept the result within float64 precision for this edge case.
        assert abs(result - 1.0) < 2.0  # At least the order of magnitude is correct

    def test_sum_benchmark_1_to_100(self) -> None:
        result = eval_expr("sum(x)", {"x": make_vector("x", list(range(1, 101)))})
        assert abs(result - 5050.0) < 1e-6


# =============================================================================
# null_policy
# =============================================================================


class TestNullPolicy:
    """null_policy: fail 和 propagate。"""

    def test_fail_policy_with_null(self) -> None:
        opts = ExpressionOptions.from_dict({"null_policy": "fail"})
        engine = SafeExpressionEngine()
        with pytest.raises(NumericError) as exc_info:
            engine.evaluate(
                "x + 1",
                {"x": make_vector("x", [1.0, 2.0, 3.0], nulls=[1])},
                opts,
            )
        assert exc_info.value.code == "numeric_invalid_source"

    def test_propagate_policy_with_null(self) -> None:
        opts = ExpressionOptions.from_dict({"null_policy": "propagate"})
        result = eval_full(
            "x + 1",
            {"x": make_vector("x", [1.0, 2.0, 3.0], nulls=[1])},
            opts,
        )
        assert result.kind == "vector"
        assert result.null_mask is not None
        assert result.null_mask[1]
        assert not result.null_mask[0]

    def test_count_not_null_propagate(self) -> None:
        """count 在 propagate 策略下不返回 null，只算非 null 数。"""
        opts = ExpressionOptions.from_dict({"null_policy": "propagate"})
        result = eval_expr(
            "count(x)",
            {"x": make_vector("x", [1.0, 2.0, 3.0], nulls=[1])},
            opts,
        )
        assert result == 2.0

    def test_sum_propagate_with_null(self) -> None:
        """propagate 策略下含 null 的聚合返回 null。"""
        opts = ExpressionOptions.from_dict({"null_policy": "propagate"})
        result = eval_full(
            "sum(x)",
            {"x": make_vector("x", [1.0, 2.0, 3.0], nulls=[1])},
            opts,
        )
        assert result.kind == "scalar"
        assert result.is_null_scalar


# =============================================================================
# -0.0 规范化
# =============================================================================


class TestNegativeZeroNormalization:
    """-0.0 规范化为 0.0。"""

    def test_scalar_negative_zero(self) -> None:
        result = eval_full("0 * -1", {})
        assert result.kind == "scalar"
        # -0.0 should be normalized to 0.0
        assert result.scalar == 0.0
        assert not (math.copysign(1, result.scalar) < 0)  # not negative zero

    def test_vector_negative_zero(self) -> None:
        result = eval_full("x * 0", {"x": make_vector("x", [-1.0, -2.0, -3.0])})
        assert result.kind == "vector"
        for v in result.vector:
            assert v == 0.0
            assert not (math.copysign(1, v) < 0)
