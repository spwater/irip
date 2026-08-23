"""AI 数值计算工具 — 受限表达式引擎：内置函数。

``_FuncsMixin`` 提供 ExpressionInterpreter 的白名单函数调用能力：
- 初等函数（abs / sqrt / exp / log / 三角 / 反三角 / floor / ceil / round）
- 选择/裁剪函数（minimum / maximum / clip / where）
- 聚合函数（count / sum / mean / min / max / median / var / std / quantile）
继承 ``_InterpreterBase`` 共享的分发、广播、取值与有限性校验等方法。
设计文档 §9：受限表达式引擎。
"""

from __future__ import annotations

import ast
import math
from typing import Any

import numpy as np

from packages.ai.numeric.contracts import NumericError
from packages.ai.numeric.expression_core import (
    _AGGREGATE_FUNCS,
    _ELEMENTWISE_FUNCS,
    _SELECT_FUNCS,
    _EvalValue,
    _InterpreterBase,
)
from packages.ai.numeric.units import (
    UnitTag,
    check_clip_bounds,
    combine_minimum_maximum,
    combine_where,
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


class _FuncsMixin(_InterpreterBase):
    """内置函数调用相关方法 mixin。"""

    # ---- 函数调用 ----

    def _visit_call(self, node: ast.Call) -> _EvalValue:
        """白名单函数调用。"""
        func_name = node.func.id  # type: ignore[attr-defined]
        args = [self.interpret(a) for a in node.args]

        if func_name in _ELEMENTWISE_FUNCS:
            return self._call_elementwise(func_name, args)
        if func_name in _SELECT_FUNCS:
            return self._call_select(func_name, args, node)
        if func_name in _AGGREGATE_FUNCS:
            return self._call_aggregate(func_name, args, node)

        raise NumericError(
            code="numeric_expression_rejected",
            message=f"unknown function: {func_name}",
        )

    # ---- 初等函数 ----

    def _call_elementwise(self, name: str, args: list[_EvalValue]) -> _EvalValue:
        x = args[0]
        self._reject_condition(x, name)

        if name == "abs":
            return self._elementwise_unary(x, np.abs, propagate_abs(x.unit), "abs")

        if name == "sqrt":
            unit, w = propagate_sqrt(x.unit)
            self._warnings.extend(w)
            return self._elementwise_unary(
                x,
                lambda v: self._domain_sqrt(v, "sqrt"),
                unit,
                "sqrt",
                domain_check=lambda v: self._check_sqrt_domain(v, "sqrt"),
            )

        if name == "exp":
            w = require_dimensionless(x.unit, "exp")
            self._warnings.extend(w)
            return self._elementwise_unary(x, np.exp, UnitTag.dimensionless(), "exp")

        if name == "log":
            w = require_dimensionless(x.unit, "log")
            self._warnings.extend(w)
            return self._elementwise_unary(
                x,
                lambda v: self._domain_log(v, "log"),
                UnitTag.dimensionless(),
                "log",
                domain_check=lambda v: self._check_log_domain(v, "log"),
            )

        if name == "log10":
            w = require_dimensionless(x.unit, "log10")
            self._warnings.extend(w)
            return self._elementwise_unary(
                x,
                lambda v: self._domain_log(v, "log10"),
                UnitTag.dimensionless(),
                "log10",
                domain_check=lambda v: self._check_log_domain(v, "log10"),
            )

        if name == "sin":
            w = require_dimensionless(x.unit, "sin")
            self._warnings.extend(w)
            return self._elementwise_unary(
                x,
                lambda v: self._trig(v, np.sin, "sin"),
                UnitTag.dimensionless(),
                "sin",
            )

        if name == "cos":
            w = require_dimensionless(x.unit, "cos")
            self._warnings.extend(w)
            return self._elementwise_unary(
                x,
                lambda v: self._trig(v, np.cos, "cos"),
                UnitTag.dimensionless(),
                "cos",
            )

        if name == "tan":
            w = require_dimensionless(x.unit, "tan")
            self._warnings.extend(w)
            return self._elementwise_unary(
                x,
                lambda v: self._trig(v, np.tan, "tan"),
                UnitTag.dimensionless(),
                "tan",
            )

        if name == "asin":
            unit, w = propagate_inverse_trig(x.unit, self._options.angle_unit)
            self._warnings.extend(w)
            return self._elementwise_unary(
                x,
                lambda v: self._inverse_trig(v, np.arcsin, "asin", check_domain="asin"),
                unit,
                "asin",
                domain_check=lambda v: self._check_asin_acos_domain(v, "asin"),
            )

        if name == "acos":
            unit, w = propagate_inverse_trig(x.unit, self._options.angle_unit)
            self._warnings.extend(w)
            return self._elementwise_unary(
                x,
                lambda v: self._inverse_trig(v, np.arccos, "acos", check_domain="acos"),
                unit,
                "acos",
                domain_check=lambda v: self._check_asin_acos_domain(v, "acos"),
            )

        if name == "atan":
            unit, w = propagate_inverse_trig(x.unit, self._options.angle_unit)
            self._warnings.extend(w)
            return self._elementwise_unary(
                x,
                lambda v: self._inverse_trig(v, np.arctan, "atan", check_domain=None),
                unit,
                "atan",
            )

        if name == "atan2":
            return self._call_atan2(args)

        if name == "floor":
            return self._elementwise_unary(x, np.floor, propagate_floor_ceil_round(x.unit), "floor")

        if name == "ceil":
            return self._elementwise_unary(x, np.ceil, propagate_floor_ceil_round(x.unit), "ceil")

        if name == "round":
            return self._call_round(args)

        raise NumericError(
            code="numeric_expression_rejected",
            message=f"unknown elementwise function: {name}",
        )

    def _call_atan2(self, args: list[_EvalValue]) -> _EvalValue:
        y = args[0]
        x = args[1]
        self._reject_condition(y, "atan2")
        self._reject_condition(x, "atan2")

        unit, w = propagate_atan2(y.unit, x.unit, self._options.angle_unit)
        self._warnings.extend(w)

        shape = self._broadcast(y, x)

        if len(shape) == 0:
            if y.is_null_scalar or x.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            result = math.atan2(y.sval, x.sval)
            if self._options.angle_unit == "degree":
                result = math.degrees(result)
            self._check_finite(result, "atan2")
            return _EvalValue.scalar_val(result, unit)

        yv = self._get_values(y, shape)
        xv = self._get_values(x, shape)
        if self._null_policy == "propagate":
            null_mask = self._combine_null_masks(y, x, shape)
        else:
            null_mask = np.zeros(shape[0], dtype=np.bool_)
        assert null_mask is not None

        vec_result = np.zeros(shape[0], dtype=np.float64)
        non_null = ~null_mask
        if np.any(non_null):
            vec_result[non_null] = np.arctan2(yv[non_null], xv[non_null])
            if self._options.angle_unit == "degree":
                vec_result[non_null] = np.degrees(vec_result[non_null])
            self._check_finite_vector(vec_result, non_null, "atan2")

        return _EvalValue.vector_val(vec_result, null_mask, unit)

    def _call_round(self, args: list[_EvalValue]) -> _EvalValue:
        x = args[0]
        digits_val = args[1]

        # digits 必须是整数字面量
        if digits_val.kind != "scalar" or digits_val.is_null_scalar:
            raise NumericError(
                code="numeric_expression_rejected",
                message="round digits must be an integer literal",
            )
        digits_float = digits_val.sval
        if not float(digits_float).is_integer():
            raise NumericError(
                code="numeric_expression_rejected",
                message="round digits must be an integer",
            )
        digits = int(digits_float)
        if abs(digits) > self._limits.max_round_digits:
            raise NumericError(
                code="numeric_expression_rejected",
                message=(
                    f"round digits out of range"
                    f" [-{self._limits.max_round_digits},"
                    f" {self._limits.max_round_digits}]"
                ),
            )

        unit = propagate_floor_ceil_round(x.unit)

        if x.kind == "scalar":
            if x.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            result = round(x.sval, digits)
            return _EvalValue.scalar_val(result, unit)

        null_mask = (
            x.mval.astype(np.bool_)
            if self._null_policy == "propagate"
            else np.zeros(len(x.vval), dtype=np.bool_)
        )
        vec_result = np.zeros_like(x.vval)
        non_null = ~null_mask
        if np.any(non_null):
            vec_result[non_null] = np.round(x.vval[non_null], digits)
        return _EvalValue.vector_val(vec_result, null_mask, unit)

    def _elementwise_unary(
        self,
        x: _EvalValue,
        func: Any,
        unit: UnitTag,
        name: str,
        domain_check: Any = None,
    ) -> _EvalValue:
        """逐元素一元函数。"""
        if x.kind == "scalar":
            if x.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            val = x.sval
            if domain_check is not None:
                domain_check(val)
            result = float(func(val))
            self._check_finite(result, name)
            return _EvalValue.scalar_val(result, unit)

        # vector
        null_mask = (
            x.mval.astype(np.bool_)
            if self._null_policy == "propagate"
            else np.zeros(len(x.vval), dtype=np.bool_)
        )
        non_null = ~null_mask
        vec_result = np.zeros(len(x.vval), dtype=np.float64)

        if domain_check is not None and np.any(non_null):
            vals = x.vval[non_null]
            domain_check(vals)

        if np.any(non_null):
            vec_result[non_null] = func(x.vval[non_null])
            self._check_finite_vector(vec_result, non_null, name)

        return _EvalValue.vector_val(vec_result, null_mask, unit)

    # ---- 选择/裁剪函数 ----

    def _call_select(self, name: str, args: list[_EvalValue], node: ast.Call) -> _EvalValue:
        if name == "minimum":
            return self._call_min_max(args, "minimum", is_min=True)
        if name == "maximum":
            return self._call_min_max(args, "maximum", is_min=False)
        if name == "clip":
            return self._call_clip(args)
        if name == "where":
            return self._call_where(args)
        raise NumericError(
            code="numeric_expression_rejected", message=f"unknown select function: {name}"
        )

    def _call_min_max(self, args: list[_EvalValue], name: str, is_min: bool) -> _EvalValue:
        a = args[0]
        b = args[1]
        self._reject_condition(a, name)
        self._reject_condition(b, name)

        unit, w = combine_minimum_maximum(a.unit, b.unit, name)
        self._warnings.extend(w)

        shape = self._broadcast(a, b)
        av = self._get_values(a, shape)
        bv = self._get_values(b, shape)

        if len(shape) == 0:
            if a.is_null_scalar or b.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            result = min(av, bv) if is_min else max(av, bv)
            return _EvalValue.scalar_val(float(result), unit)

        if self._null_policy == "propagate":
            null_mask = self._combine_null_masks(a, b, shape)
        else:
            null_mask = np.zeros(shape[0], dtype=np.bool_)
        assert null_mask is not None

        vec_result = np.zeros(shape[0], dtype=np.float64)
        non_null = ~null_mask
        if np.any(non_null):
            if is_min:
                vec_result[non_null] = np.minimum(av[non_null], bv[non_null])
            else:
                vec_result[non_null] = np.maximum(av[non_null], bv[non_null])
            self._check_finite_vector(vec_result, non_null, name)

        return _EvalValue.vector_val(vec_result, null_mask, unit)

    def _call_clip(self, args: list[_EvalValue]) -> _EvalValue:
        x = args[0]
        low = args[1]
        high = args[2]
        self._reject_condition(x, "clip")
        self._reject_condition(low, "clip")
        self._reject_condition(high, "clip")

        # 边界必须为标量
        if low.kind != "scalar" or high.kind != "scalar":
            raise NumericError(
                code="numeric_expression_rejected",
                message="clip bounds must be scalars",
            )
        if low.is_null_scalar or high.is_null_scalar:
            raise NumericError(
                code="numeric_expression_rejected",
                message="clip bounds cannot be null",
            )

        low_val = low.sval
        high_val = high.sval
        if low_val > high_val:
            raise NumericError(
                code="numeric_expression_rejected",
                message="clip low must be <= high",
            )

        unit, w = check_clip_bounds(x.unit, low.unit, high.unit)
        self._warnings.extend(w)

        if x.kind == "scalar":
            if x.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            result = max(low_val, min(x.sval, high_val))
            return _EvalValue.scalar_val(float(result), unit)

        null_mask = (
            x.mval.astype(np.bool_)
            if self._null_policy == "propagate"
            else np.zeros(len(x.vval), dtype=np.bool_)
        )
        vec_result = np.zeros(len(x.vval), dtype=np.float64)
        non_null = ~null_mask
        if np.any(non_null):
            vec_result[non_null] = np.clip(x.vval[non_null], low_val, high_val)
            self._check_finite_vector(vec_result, non_null, "clip")

        return _EvalValue.vector_val(vec_result, null_mask, unit)

    def _call_where(self, args: list[_EvalValue]) -> _EvalValue:
        cond = args[0]
        a = args[1]
        b = args[2]

        if not cond.is_condition:
            raise NumericError(
                code="numeric_expression_rejected",
                message="where condition must be a comparison result",
            )
        self._reject_condition(a, "where value")
        self._reject_condition(b, "where value")

        unit, w = combine_where(a.unit, b.unit)
        self._warnings.extend(w)

        if cond.kind == "scalar" and a.kind == "scalar" and b.kind == "scalar":
            if cond.is_null_scalar or a.is_null_scalar or b.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            return _EvalValue.scalar_val(a.sval if cond.scalar else b.sval, unit)

        # 至少一个是向量
        shape = self._broadcast(a, b)
        if cond.kind == "vector":
            shape = (cond.vval.shape[0],)

        # 广播所有到 shape
        av = self._get_values(a, shape)
        bv = self._get_values(b, shape)
        cv = self._get_values(cond, shape)

        if self._null_policy == "propagate":
            null_mask = (
                self._get_mask(cond, shape) | self._get_mask(a, shape) | self._get_mask(b, shape)
            )
        else:
            null_mask = np.zeros(shape[0], dtype=np.bool_)

        result = np.where(cv > 0.5, av, bv)
        non_null = ~null_mask
        if np.any(non_null):
            self._check_finite_vector(result, non_null, "where")

        # 规范化 -0.0
        result = np.where(result == 0.0, 0.0, result)

        return _EvalValue.vector_val(result, null_mask, unit)

    # ---- 聚合函数 ----

    def _call_aggregate(self, name: str, args: list[_EvalValue], node: ast.Call) -> _EvalValue:
        x = args[0]
        self._reject_condition(x, name)

        # count 是特殊的：返回非 null 元素数
        if name == "count":
            return self._aggregate_count(x)

        # 检查 null（propagate 策略下，除 count 外聚合遇到 null 返回 null 标量）
        has_null = self._has_null(x)
        if self._null_policy == "propagate" and has_null and name != "count":
            unit = self._aggregate_unit(name, x.unit)
            self._warnings.append("null_in_aggregate")
            return _EvalValue.null_scalar(unit)

        # 获取有效值（非 null）
        valid_vals = self._get_valid_values(x)
        len(valid_vals)

        if name == "sum":
            return self._aggregate_sum(valid_vals, x.unit)
        if name == "mean":
            return self._aggregate_mean(valid_vals, x.unit)
        if name == "min":
            return self._aggregate_min(valid_vals, x.unit)
        if name == "max":
            return self._aggregate_max(valid_vals, x.unit)
        if name == "median":
            return self._aggregate_median(valid_vals, x.unit)
        if name == "var":
            ddof = self._get_ddof(args, node)
            return self._aggregate_var(valid_vals, x.unit, ddof)
        if name == "std":
            ddof = self._get_ddof(args, node)
            return self._aggregate_std(valid_vals, x.unit, ddof)
        if name == "quantile":
            return self._aggregate_quantile(args, valid_vals, x.unit, node)

        raise NumericError(code="numeric_expression_rejected", message=f"unknown aggregate: {name}")

    def _aggregate_count(self, x: _EvalValue) -> _EvalValue:
        if x.kind == "scalar":
            count = 0.0 if x.is_null_scalar else 1.0
        else:
            if self._null_policy == "propagate":
                count = float(np.sum(~x.mval))
            else:
                count = float(len(x.vval))
        return _EvalValue.scalar_val(count, UnitTag.dimensionless())

    def _aggregate_sum(self, vals: np.ndarray, unit: UnitTag) -> _EvalValue:
        if len(vals) == 0:
            self._warnings.append("empty_aggregate")
            return _EvalValue.scalar_val(0.0, propagate_aggregation(unit))
        # 使用 math.fsum 实现稳定求和（避免大数抵消导致精度丢失）
        result = math.fsum(float(v) for v in vals)
        self._check_finite(result, "sum")
        return _EvalValue.scalar_val(self._normalize_zero(result), propagate_aggregation(unit))

    def _aggregate_mean(self, vals: np.ndarray, unit: UnitTag) -> _EvalValue:
        if len(vals) == 0:
            raise NumericError(
                code="numeric_domain_error",
                message="mean of empty series is undefined",
            )
        result = float(np.mean(vals))
        self._check_finite(result, "mean")
        return _EvalValue.scalar_val(self._normalize_zero(result), propagate_aggregation(unit))

    def _aggregate_min(self, vals: np.ndarray, unit: UnitTag) -> _EvalValue:
        if len(vals) == 0:
            raise NumericError(
                code="numeric_domain_error",
                message="min of empty series is undefined",
            )
        result = float(np.min(vals))
        return _EvalValue.scalar_val(self._normalize_zero(result), propagate_aggregation(unit))

    def _aggregate_max(self, vals: np.ndarray, unit: UnitTag) -> _EvalValue:
        if len(vals) == 0:
            raise NumericError(
                code="numeric_domain_error",
                message="max of empty series is undefined",
            )
        result = float(np.max(vals))
        return _EvalValue.scalar_val(self._normalize_zero(result), propagate_aggregation(unit))

    def _aggregate_median(self, vals: np.ndarray, unit: UnitTag) -> _EvalValue:
        if len(vals) == 0:
            raise NumericError(
                code="numeric_domain_error",
                message="median of empty series is undefined",
            )
        result = float(np.median(vals))
        return _EvalValue.scalar_val(self._normalize_zero(result), propagate_aggregation(unit))

    def _aggregate_var(self, vals: np.ndarray, unit: UnitTag, ddof: int) -> _EvalValue:
        n = len(vals)
        if ddof == 0 and n < 1:
            raise NumericError(
                code="numeric_domain_error",
                message="population variance requires at least 1 value",
            )
        if ddof == 1 and n < 2:
            raise NumericError(
                code="numeric_domain_error",
                message="sample variance requires at least 2 values",
            )
        result = float(np.var(vals, ddof=ddof))
        self._check_finite(result, "var")
        return _EvalValue.scalar_val(self._normalize_zero(result), propagate_variance(unit))

    def _aggregate_std(self, vals: np.ndarray, unit: UnitTag, ddof: int) -> _EvalValue:
        n = len(vals)
        if ddof == 0 and n < 1:
            raise NumericError(
                code="numeric_domain_error",
                message="population std requires at least 1 value",
            )
        if ddof == 1 and n < 2:
            raise NumericError(
                code="numeric_domain_error",
                message="sample std requires at least 2 values",
            )
        result = float(np.std(vals, ddof=ddof))
        self._check_finite(result, "std")
        return _EvalValue.scalar_val(self._normalize_zero(result), propagate_std(unit))

    def _aggregate_quantile(
        self, args: list[_EvalValue], vals: np.ndarray, unit: UnitTag, node: ast.Call
    ) -> _EvalValue:
        q_val = args[1]
        if q_val.kind != "scalar" or q_val.is_null_scalar:
            raise NumericError(
                code="numeric_expression_rejected",
                message="quantile q must be a scalar",
            )
        q = q_val.sval
        if q < 0.0 or q > 1.0:
            raise NumericError(
                code="numeric_domain_error",
                message=f"quantile q must be in [0, 1], got {q}",
            )
        if len(vals) == 0:
            raise NumericError(
                code="numeric_domain_error",
                message="quantile of empty series is undefined",
            )
        result = float(np.quantile(vals, q))
        return _EvalValue.scalar_val(self._normalize_zero(result), propagate_aggregation(unit))

    def _get_ddof(self, args: list[_EvalValue], node: ast.Call) -> int:
        """获取 var/std 的 ddof 参数（只允许 0 或 1 的整数字面量）。"""
        if len(args) < 2:
            return 0  # 默认总体口径
        ddof_val = args[1]
        if ddof_val.kind != "scalar" or ddof_val.is_null_scalar:
            raise NumericError(
                code="numeric_expression_rejected",
                message="ddof must be an integer literal (0 or 1)",
            )
        # 检查是否为字面量
        if not isinstance(node.args[1], ast.Constant):
            raise NumericError(
                code="numeric_expression_rejected",
                message="ddof must be an integer literal",
            )
        raw = node.args[1].value
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise NumericError(
                code="numeric_expression_rejected",
                message="ddof must be an integer",
            )
        if raw not in (0, 1):
            raise NumericError(
                code="numeric_expression_rejected",
                message="ddof must be 0 or 1",
            )
        return raw

    def _aggregate_unit(self, name: str, unit: UnitTag) -> UnitTag:
        if name in ("var",):
            return propagate_variance(unit)
        if name in ("std",):
            return propagate_std(unit)
        if name == "count":
            return UnitTag.dimensionless()
        return propagate_aggregation(unit)

    # ---- 聚合辅助方法 ----

    def _has_null(self, val: _EvalValue) -> bool:
        if val.kind == "scalar":
            return val.is_null_scalar
        return bool(np.any(val.mval))

    def _get_valid_values(self, val: _EvalValue) -> np.ndarray:
        """获取非 null 值数组。"""
        if val.kind == "scalar":
            if val.is_null_scalar:
                return np.array([], dtype=np.float64)
            return np.array([val.sval], dtype=np.float64)
        mask = val.mval
        non_null = ~mask
        if self._null_policy == "fail":
            return val.vval.astype(np.float64)
        return val.vval[non_null].astype(np.float64)

    # ---- 定义域校验 ----

    def _check_sqrt_domain(self, val: Any, name: str) -> None:
        """检查 sqrt 定义域 x >= 0。"""
        if isinstance(val, np.ndarray):
            if np.any(val < 0.0):
                count = int(np.sum(val < 0.0))
                raise NumericError(
                    code="numeric_domain_error",
                    message=f"sqrt requires non-negative input, {count} negative values",
                    details={"invalid_count": count},
                )
        else:
            if val < 0.0:
                raise NumericError(
                    code="numeric_domain_error",
                    message="sqrt requires non-negative input",
                    details={"invalid_count": 1},
                )

    def _check_log_domain(self, val: Any, name: str) -> None:
        """检查 log/log10 定义域 x > 0。"""
        if isinstance(val, np.ndarray):
            if np.any(val <= 0.0):
                count = int(np.sum(val <= 0.0))
                raise NumericError(
                    code="numeric_domain_error",
                    message=f"{name} requires positive input, {count} non-positive values",
                    details={"invalid_count": count},
                )
        else:
            if val <= 0.0:
                raise NumericError(
                    code="numeric_domain_error",
                    message=f"{name} requires positive input",
                    details={"invalid_count": 1},
                )

    def _check_asin_acos_domain(self, val: Any, name: str) -> None:
        """检查 asin/acos 定义域 -1 <= x <= 1。"""
        if isinstance(val, np.ndarray):
            invalid = (val < -1.0) | (val > 1.0)
            if np.any(invalid):
                count = int(np.sum(invalid))
                raise NumericError(
                    code="numeric_domain_error",
                    message=f"{name} requires input in [-1, 1], {count} out-of-range values",
                    details={"invalid_count": count},
                )
        else:
            if val < -1.0 or val > 1.0:
                raise NumericError(
                    code="numeric_domain_error",
                    message=f"{name} requires input in [-1, 1]",
                    details={"invalid_count": 1},
                )

    def _domain_sqrt(self, v: np.ndarray, name: str) -> np.ndarray:
        return np.sqrt(v)

    def _domain_log(self, v: np.ndarray, name: str) -> np.ndarray:
        if name == "log10":
            return np.log10(v)
        return np.log(v)

    def _trig(self, v: np.ndarray, func: Any, name: str) -> np.ndarray:
        if self._options.angle_unit == "degree":
            v = np.radians(v)
        return func(v)

    def _inverse_trig(
        self, v: np.ndarray, func: Any, name: str, check_domain: str | None
    ) -> np.ndarray:
        result = func(v)
        if self._options.angle_unit == "degree":
            result = np.degrees(result)
        return result

    def _normalize_zero(self, val: float) -> float:
        """规范化 -0.0 为 0.0。"""
        if val == 0.0:
            return 0.0
        return val
