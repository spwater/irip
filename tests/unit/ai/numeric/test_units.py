"""test_units.py — 轻量单位标签系统测试。

设计文档 §19.5 单位测试、§11 轻量单位策略。
"""

from __future__ import annotations

import pytest

from packages.ai.numeric.contracts import NumericError
from packages.ai.numeric.units import (
    UnitTag,
    UnitTagState,
    check_clip_bounds,
    combine_additive,
    combine_division,
    combine_minimum_maximum,
    combine_multiplication,
    combine_power,
    combine_where,
    constant_unit,
    literal_unit,
    propagate_abs,
    propagate_aggregation,
    propagate_atan2,
    propagate_floor_ceil_round,
    propagate_inverse_trig,
    propagate_sqrt,
    propagate_std,
    propagate_variance,
    require_dimensionless,
)


# =============================================================================
# UnitTag 基本属性
# =============================================================================


class TestUnitTagBasics:
    """UnitTag 三态系统。"""

    def test_known_unit(self) -> None:
        tag = UnitTag.known("MPa")
        assert tag.state == UnitTagState.KNOWN
        assert tag.label == "MPa"
        assert tag.is_known
        assert not tag.is_dimensionless
        assert not tag.is_unknown

    def test_dimensionless(self) -> None:
        tag = UnitTag.dimensionless()
        assert tag.state == UnitTagState.DIMENSIONLESS
        assert tag.label == "1"
        assert tag.is_known
        assert tag.is_dimensionless
        assert not tag.is_unknown

    def test_unknown(self) -> None:
        tag = UnitTag.unknown()
        assert tag.state == UnitTagState.UNKNOWN
        assert tag.label is None
        assert not tag.is_known
        assert not tag.is_dimensionless
        assert tag.is_unknown

    def test_from_unit_string_none(self) -> None:
        tag = UnitTag.from_unit_string(None)
        assert tag.is_unknown

    def test_from_unit_string_one(self) -> None:
        tag = UnitTag.from_unit_string("1")
        assert tag.is_dimensionless
        assert tag.label == "1"

    def test_from_unit_string_known(self) -> None:
        tag = UnitTag.from_unit_string("MPa")
        assert tag.is_known
        assert tag.label == "MPa"

    def test_to_output_known(self) -> None:
        assert UnitTag.known("K").to_output() == "K"

    def test_to_output_dimensionless(self) -> None:
        assert UnitTag.dimensionless().to_output() == "1"

    def test_to_output_unknown(self) -> None:
        assert UnitTag.unknown().to_output() is None

    def test_literal_unit(self) -> None:
        assert literal_unit().is_dimensionless

    def test_constant_unit(self) -> None:
        assert constant_unit("pi").is_dimensionless


# =============================================================================
# 加减取模：同单位检查
# =============================================================================


class TestAdditiveUnits:
    """加减取模的单位传播。"""

    def test_same_known_unit_add(self) -> None:
        left = UnitTag.known("MPa")
        right = UnitTag.known("MPa")
        result, warnings = combine_additive(left, right, "addition")
        assert result.label == "MPa"
        assert warnings == []

    def test_different_known_unit_reject(self) -> None:
        left = UnitTag.known("MPa")
        right = UnitTag.known("K")
        with pytest.raises(NumericError) as exc_info:
            combine_additive(left, right, "addition")
        assert exc_info.value.code == "numeric_unit_conflict"

    def test_known_plus_unknown_warning(self) -> None:
        left = UnitTag.known("MPa")
        right = UnitTag.unknown()
        result, warnings = combine_additive(left, right, "addition")
        assert result.label == "MPa"
        assert "unit_unverified" in warnings

    def test_unknown_plus_known_warning(self) -> None:
        left = UnitTag.unknown()
        right = UnitTag.known("K")
        result, warnings = combine_additive(left, right, "addition")
        assert result.label == "K"
        assert "unit_unverified" in warnings

    def test_both_unknown_warning(self) -> None:
        left = UnitTag.unknown()
        right = UnitTag.unknown()
        result, warnings = combine_additive(left, right, "addition")
        assert result.is_unknown
        assert "unit_unverified" in warnings

    def test_dimensionless_plus_dimensionless(self) -> None:
        left = UnitTag.dimensionless()
        right = UnitTag.dimensionless()
        result, warnings = combine_additive(left, right, "addition")
        assert result.is_dimensionless
        assert warnings == []

    def test_dimensionless_plus_known_reject(self) -> None:
        left = UnitTag.dimensionless()
        right = UnitTag.known("MPa")
        with pytest.raises(NumericError):
            combine_additive(left, right, "addition")


# =============================================================================
# 乘除
# =============================================================================


class TestMultiplicationDivisionUnits:
    """乘除标签传播。"""

    def test_known_times_known(self) -> None:
        left = UnitTag.known("m")
        right = UnitTag.known("s")
        result, warnings = combine_multiplication(left, right)
        assert result.label == "m*s"
        assert warnings == []

    def test_dimensionless_times_known(self) -> None:
        left = UnitTag.dimensionless()
        right = UnitTag.known("MPa")
        result, warnings = combine_multiplication(left, right)
        assert result.label == "MPa"
        assert warnings == []

    def test_known_times_dimensionless(self) -> None:
        left = UnitTag.known("MPa")
        right = UnitTag.dimensionless()
        result, warnings = combine_multiplication(left, right)
        assert result.label == "MPa"
        assert warnings == []

    def test_unknown_times_known(self) -> None:
        left = UnitTag.unknown()
        right = UnitTag.known("MPa")
        result, warnings = combine_multiplication(left, right)
        assert result.is_unknown
        assert "unit_unverified" in warnings

    def test_dimensionless_times_dimensionless(self) -> None:
        left = UnitTag.dimensionless()
        right = UnitTag.dimensionless()
        result, warnings = combine_multiplication(left, right)
        assert result.is_dimensionless
        assert warnings == []

    def test_known_div_known_same(self) -> None:
        left = UnitTag.known("MPa")
        right = UnitTag.known("MPa")
        result, warnings = combine_division(left, right)
        assert result.is_dimensionless
        assert warnings == []

    def test_known_div_known_different(self) -> None:
        left = UnitTag.known("m")
        right = UnitTag.known("s")
        result, warnings = combine_division(left, right)
        assert result.label == "m/s"
        assert warnings == []

    def test_dimensionless_div_known(self) -> None:
        left = UnitTag.dimensionless()
        right = UnitTag.known("s")
        result, warnings = combine_division(left, right)
        assert result.label == "1/s"
        assert warnings == []

    def test_known_div_dimensionless(self) -> None:
        left = UnitTag.known("m")
        right = UnitTag.dimensionless()
        result, warnings = combine_division(left, right)
        assert result.label == "m"
        assert warnings == []

    def test_unknown_div_known(self) -> None:
        left = UnitTag.unknown()
        right = UnitTag.known("s")
        result, warnings = combine_division(left, right)
        assert result.is_unknown
        assert "unit_unverified" in warnings


# =============================================================================
# 幂运算
# =============================================================================


class TestPowerUnits:
    """幂标签传播。"""

    def test_known_power_int(self) -> None:
        base = UnitTag.known("m")
        exp_tag = UnitTag.dimensionless()
        result, warnings = combine_power(base, exp_tag, True, 2)
        assert result.label == "m^2"
        assert warnings == []

    def test_known_power_zero(self) -> None:
        base = UnitTag.known("m")
        exp_tag = UnitTag.dimensionless()
        result, warnings = combine_power(base, exp_tag, True, 0)
        assert result.is_dimensionless
        assert warnings == []

    def test_known_power_one(self) -> None:
        base = UnitTag.known("m")
        exp_tag = UnitTag.dimensionless()
        result, warnings = combine_power(base, exp_tag, True, 1)
        assert result.label == "m"
        assert warnings == []

    def test_dimensionless_power(self) -> None:
        base = UnitTag.dimensionless()
        exp_tag = UnitTag.dimensionless()
        result, warnings = combine_power(base, exp_tag, True, 3)
        assert result.is_dimensionless
        assert warnings == []

    def test_unknown_base(self) -> None:
        base = UnitTag.unknown()
        exp_tag = UnitTag.dimensionless()
        result, warnings = combine_power(base, exp_tag, True, 2)
        assert result.is_unknown
        assert "unit_unverified" in warnings

    def test_known_exponent_reject(self) -> None:
        base = UnitTag.known("m")
        exp_tag = UnitTag.known("s")
        with pytest.raises(NumericError) as exc_info:
            combine_power(base, exp_tag, False, None)
        assert exc_info.value.code == "numeric_unit_conflict"

    def test_unknown_exponent_warning(self) -> None:
        base = UnitTag.known("m")
        exp_tag = UnitTag.unknown()
        result, warnings = combine_power(base, exp_tag, True, 2)
        assert "unit_unverified" in warnings

    def test_non_integer_exponent(self) -> None:
        base = UnitTag.known("m")
        exp_tag = UnitTag.dimensionless()
        result, warnings = combine_power(base, exp_tag, False, None)
        assert "unit_unsimplified" in warnings


# =============================================================================
# sqrt / abs / floor / ceil / round
# =============================================================================


class TestElementaryFunctionUnits:
    """初等函数单位传播。"""

    def test_sqrt_known(self) -> None:
        tag, warnings = propagate_sqrt(UnitTag.known("m"))
        assert tag.label == "sqrt(m)"
        assert warnings == []

    def test_sqrt_dimensionless(self) -> None:
        tag, warnings = propagate_sqrt(UnitTag.dimensionless())
        assert tag.is_dimensionless
        assert warnings == []

    def test_sqrt_unknown(self) -> None:
        tag, warnings = propagate_sqrt(UnitTag.unknown())
        assert tag.is_unknown
        assert "unit_unverified" in warnings

    def test_abs_preserves_unit(self) -> None:
        assert propagate_abs(UnitTag.known("MPa")).label == "MPa"
        assert propagate_abs(UnitTag.dimensionless()).is_dimensionless
        assert propagate_abs(UnitTag.unknown()).is_unknown

    def test_floor_preserves_unit(self) -> None:
        assert propagate_floor_ceil_round(UnitTag.known("K")).label == "K"

    def test_ceil_preserves_unit(self) -> None:
        assert propagate_floor_ceil_round(UnitTag.known("K")).label == "K"

    def test_round_preserves_unit(self) -> None:
        assert propagate_floor_ceil_round(UnitTag.known("K")).label == "K"


# =============================================================================
# log / exp / trig — 要求无量纲
# =============================================================================


class TestRequireDimensionless:
    """log/exp/trig 要求无量纲输入。"""

    def test_known_unit_rejected(self) -> None:
        with pytest.raises(NumericError) as exc_info:
            require_dimensionless(UnitTag.known("MPa"), "log")
        assert exc_info.value.code == "numeric_unit_conflict"

    def test_dimensionless_ok(self) -> None:
        warnings = require_dimensionless(UnitTag.dimensionless(), "log")
        assert warnings == []

    def test_unknown_warning(self) -> None:
        warnings = require_dimensionless(UnitTag.unknown(), "log")
        assert "unit_unverified" in warnings


class TestInverseTrigUnits:
    """反三角函数单位传播。"""

    def test_asin_radian(self) -> None:
        tag, warnings = propagate_inverse_trig(UnitTag.dimensionless(), "radian")
        assert tag.label == "rad"
        assert warnings == []

    def test_asin_degree(self) -> None:
        tag, warnings = propagate_inverse_trig(UnitTag.dimensionless(), "degree")
        assert tag.label == "deg"
        assert warnings == []

    def test_asin_known_unit_reject(self) -> None:
        with pytest.raises(NumericError):
            propagate_inverse_trig(UnitTag.known("MPa"), "radian")

    def test_asin_unknown_warning(self) -> None:
        tag, warnings = propagate_inverse_trig(UnitTag.unknown(), "radian")
        assert tag.label == "rad"
        assert "unit_unverified" in warnings

    def test_atan2_compatible_units(self) -> None:
        tag, warnings = propagate_atan2(UnitTag.known("m"), UnitTag.known("m"), "radian")
        assert tag.label == "rad"
        assert warnings == []

    def test_atan2_incompatible_reject(self) -> None:
        with pytest.raises(NumericError):
            propagate_atan2(UnitTag.known("m"), UnitTag.known("s"), "radian")

    def test_atan2_unknown_warning(self) -> None:
        tag, warnings = propagate_atan2(UnitTag.unknown(), UnitTag.known("m"), "degree")
        assert tag.label == "deg"
        assert "unit_unverified" in warnings


# =============================================================================
# 聚合与统计函数
# =============================================================================


class TestAggregationUnits:
    """聚合/统计函数单位传播。"""

    def test_aggregation_preserves_unit(self) -> None:
        assert propagate_aggregation(UnitTag.known("MPa")).label == "MPa"
        assert propagate_aggregation(UnitTag.dimensionless()).is_dimensionless
        assert propagate_aggregation(UnitTag.unknown()).is_unknown

    def test_variance_unit_squared(self) -> None:
        assert propagate_variance(UnitTag.known("MPa")).label == "MPa^2"
        assert propagate_variance(UnitTag.dimensionless()).is_dimensionless
        assert propagate_variance(UnitTag.unknown()).is_unknown

    def test_std_preserves_unit(self) -> None:
        assert propagate_std(UnitTag.known("MPa")).label == "MPa"
        assert propagate_std(UnitTag.dimensionless()).is_dimensionless
        assert propagate_std(UnitTag.unknown()).is_unknown


# =============================================================================
# where / minimum / maximum / clip
# =============================================================================


class TestSelectFunctionUnits:
    """where/minimum/maximum/clip 单位传播。"""

    def test_minimum_same_units(self) -> None:
        result, warnings = combine_minimum_maximum(
            UnitTag.known("MPa"), UnitTag.known("MPa"), "minimum"
        )
        assert result.label == "MPa"
        assert warnings == []

    def test_minimum_different_units_reject(self) -> None:
        with pytest.raises(NumericError):
            combine_minimum_maximum(UnitTag.known("MPa"), UnitTag.known("K"), "minimum")

    def test_where_same_units(self) -> None:
        result, warnings = combine_where(UnitTag.known("MPa"), UnitTag.known("MPa"))
        assert result.label == "MPa"
        assert warnings == []

    def test_where_different_units_reject(self) -> None:
        with pytest.raises(NumericError):
            combine_where(UnitTag.known("MPa"), UnitTag.known("K"))

    def test_clip_bounds_same_unit(self) -> None:
        tag, warnings = check_clip_bounds(
            UnitTag.known("MPa"), UnitTag.known("MPa"), UnitTag.known("MPa")
        )
        assert tag.label == "MPa"

    def test_clip_bounds_different_reject(self) -> None:
        with pytest.raises(NumericError):
            check_clip_bounds(UnitTag.known("MPa"), UnitTag.known("K"), UnitTag.known("MPa"))
