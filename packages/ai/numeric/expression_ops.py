"""AI 数值计算工具 — 受限表达式引擎：算术 / 一元 / 比较运算。

``_OpsMixin`` 提供 ExpressionInterpreter 的一元运算、二元运算（加减乘除、幂、模）、
比较运算及向量运算辅助。继承 ``_InterpreterBase`` 共享的分发、广播、取值与
有限性校验等方法。设计文档 §9：受限表达式引擎。
"""

from __future__ import annotations

import ast
from typing import Any

import numpy as np

from packages.ai.numeric.contracts import NumericError
from packages.ai.numeric.expression_core import _COMPAREOPS, _EvalValue, _InterpreterBase
from packages.ai.numeric.units import (
    UnitTag,
    combine_additive,
    combine_division,
    combine_multiplication,
    combine_power,
)


class _OpsMixin(_InterpreterBase):
    """算术 / 一元 / 比较运算相关方法 mixin。"""

    # ---- 一元运算 ----

    def _visit_unaryop(self, node: ast.UnaryOp) -> _EvalValue:
        """一元运算 +x / -x。"""
        operand = self.interpret(node.operand)

        if isinstance(node.op, ast.UAdd):
            return operand

        if isinstance(node.op, ast.USub):
            return self._negate(operand)

        raise NumericError(
            code="numeric_expression_rejected",
            message=f"unsupported unary operator: {type(node.op).__name__}",
        )

    def _negate(self, val: _EvalValue) -> _EvalValue:
        if val.is_condition:
            raise NumericError(
                code="numeric_expression_rejected",
                message="cannot negate a comparison result",
            )
        if val.kind == "scalar":
            if val.is_null_scalar:
                return _EvalValue.null_scalar(val.unit)
            result = -val.sval
            return _EvalValue.scalar_val(result, val.unit)

        # vector
        vec_result = val.vval.copy()
        null_mask = val.mval.copy()
        non_null = ~null_mask
        vec_result[non_null] = -vec_result[non_null]
        return _EvalValue.vector_val(vec_result, null_mask, val.unit)

    # ---- 二元运算 ----

    def _visit_binop(self, node: ast.BinOp) -> _EvalValue:
        """二元运算 + - * / ** %"""
        left = self.interpret(node.left)
        right = self.interpret(node.right)

        self._reject_condition(left, "binary operation")
        self._reject_condition(right, "binary operation")

        op_type = type(node.op)

        if op_type == ast.Add:
            return self._binop_add(left, right)
        if op_type == ast.Sub:
            return self._binop_sub(left, right)
        if op_type == ast.Mult:
            return self._binop_mul(left, right)
        if op_type == ast.Div:
            return self._binop_div(left, right)
        if op_type == ast.Pow:
            return self._binop_pow(left, right, node)
        if op_type == ast.Mod:
            return self._binop_mod(left, right)

        raise NumericError(
            code="numeric_expression_rejected",
            message=f"unsupported binary operator: {op_type.__name__}",
        )

    def _binop_add(self, left: _EvalValue, right: _EvalValue) -> _EvalValue:
        shape = self._broadcast(left, right)
        unit, w = combine_additive(left.unit, right.unit, "addition")
        self._warnings.extend(w)

        if len(shape) == 0:
            if left.is_null_scalar or right.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            result = left.sval + right.sval
            self._check_finite(result, "addition")
            return _EvalValue.scalar_val(result, unit)

        return self._vector_binop(left, right, shape, unit, lambda a, b: a + b, "addition")

    def _binop_sub(self, left: _EvalValue, right: _EvalValue) -> _EvalValue:
        shape = self._broadcast(left, right)
        unit, w = combine_additive(left.unit, right.unit, "subtraction")
        self._warnings.extend(w)

        if len(shape) == 0:
            if left.is_null_scalar or right.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            result = left.sval - right.sval
            self._check_finite(result, "subtraction")
            return _EvalValue.scalar_val(result, unit)

        return self._vector_binop(left, right, shape, unit, lambda a, b: a - b, "subtraction")

    def _binop_mul(self, left: _EvalValue, right: _EvalValue) -> _EvalValue:
        shape = self._broadcast(left, right)
        unit, w = combine_multiplication(left.unit, right.unit)
        self._warnings.extend(w)

        if len(shape) == 0:
            if left.is_null_scalar or right.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            result = left.sval * right.sval
            self._check_finite(result, "multiplication")
            return _EvalValue.scalar_val(result, unit)

        return self._vector_binop(left, right, shape, unit, lambda a, b: a * b, "multiplication")

    def _binop_div(self, left: _EvalValue, right: _EvalValue) -> _EvalValue:
        shape = self._broadcast(left, right)
        unit, w = combine_division(left.unit, right.unit)
        self._warnings.extend(w)

        if len(shape) == 0:
            if left.is_null_scalar or right.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            self._check_div_zero_scalar(right.sval, "division")
            result = left.sval / right.sval
            self._check_finite(result, "division")
            return _EvalValue.scalar_val(result, unit)

        return self._vector_binop(
            left,
            right,
            shape,
            unit,
            lambda a, b: self._safe_div(a, b, "division"),
            "division",
            check_div_zero=True,
        )

    def _binop_mod(self, left: _EvalValue, right: _EvalValue) -> _EvalValue:
        shape = self._broadcast(left, right)
        # 取模结果保留左操作数单位
        unit, w = combine_additive(left.unit, right.unit, "modulo")
        self._warnings.extend(w)
        # 结果单位实际取左操作数
        unit = left.unit

        if len(shape) == 0:
            if left.is_null_scalar or right.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            self._check_div_zero_scalar(right.sval, "modulo")
            result = left.sval % right.sval
            self._check_finite(result, "modulo")
            return _EvalValue.scalar_val(result, unit)

        return self._vector_binop(
            left,
            right,
            shape,
            unit,
            lambda a, b: self._safe_mod(a, b, "modulo"),
            "modulo",
            check_div_zero=True,
        )

    def _binop_pow(self, left: _EvalValue, right: _EvalValue, node: ast.BinOp) -> _EvalValue:
        shape = self._broadcast(left, right)

        # 判断指数是否为整数字面量
        exponent_is_int = False
        exponent_int_val: int | None = None
        if (
            isinstance(node.right, ast.Constant)
            and isinstance(node.right.value, int)
            and not isinstance(node.right.value, bool)
        ):
            exponent_is_int = True
            exponent_int_val = node.right.value
            if abs(exponent_int_val) > self._limits.max_pow_exponent_abs:
                raise NumericError(
                    code="numeric_size_limit",
                    message=(
                        f"power exponent absolute value exceeds limit"
                        f" ({self._limits.max_pow_exponent_abs})"
                    ),
                )

        unit, w = combine_power(left.unit, right.unit, exponent_is_int, exponent_int_val)
        self._warnings.extend(w)

        if len(shape) == 0:
            if left.is_null_scalar or right.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            base_val = left.sval
            exp_val = right.sval
            self._check_pow_domain_scalar(base_val, exp_val, exponent_is_int)
            result = base_val**exp_val
            self._check_finite(result, "power")
            return _EvalValue.scalar_val(result, unit)

        return self._vector_binop(
            left,
            right,
            shape,
            unit,
            lambda a, b: self._safe_pow(a, b, "power", exponent_is_int),
            "power",
            check_pow=True,
            exponent_is_int=exponent_is_int,
        )

    def _vector_binop(
        self,
        left: _EvalValue,
        right: _EvalValue,
        shape: tuple[int, ...],
        unit: UnitTag,
        op: Any,
        op_name: str,
        check_div_zero: bool = False,
        check_pow: bool = False,
        exponent_is_int: bool = False,
    ) -> _EvalValue:
        """执行向量二元运算，处理空值传播。"""
        a = self._get_values(left, shape)
        b = self._get_values(right, shape)

        if self._null_policy == "propagate":
            null_mask = self._combine_null_masks(left, right, shape)
        else:
            null_mask = np.zeros(shape[0], dtype=np.bool_)
        assert null_mask is not None

        non_null = ~null_mask

        if check_div_zero:
            self._check_div_zero_vector(b, non_null, op_name)

        if check_pow:
            self._check_pow_domain_vector(a, b, non_null, exponent_is_int)

        result = np.zeros(shape[0], dtype=np.float64)
        if np.any(non_null):
            result[non_null] = op(a[non_null], b[non_null])
            self._check_finite_vector(result, non_null, op_name)

        return _EvalValue.vector_val(result, null_mask, unit)

    # ---- 比较运算 ----

    def _visit_compare(self, node: ast.Compare) -> _EvalValue:
        """比较运算（只允许作为 where 条件）。"""
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise NumericError(
                code="numeric_expression_rejected",
                message="chained comparisons are not allowed",
            )

        left = self.interpret(node.left)
        right = self.interpret(node.comparators[0])
        self._reject_condition(left, "comparison")
        self._reject_condition(right, "comparison")

        op_type = type(node.ops[0])
        op_str = _COMPAREOPS.get(op_type)
        if op_str is None:
            raise NumericError(
                code="numeric_expression_rejected",
                message=f"unsupported comparison operator: {op_type.__name__}",
            )

        shape = self._broadcast(left, right)
        a = self._get_values(left, shape)
        b = self._get_values(right, shape)

        if len(shape) == 0:
            if left.is_null_scalar or right.is_null_scalar:
                return _EvalValue.condition_val("scalar", scalar=0.0)
            result = self._compare_scalar(float(a), float(b), op_str)
            return _EvalValue.condition_val("scalar", scalar=float(result))

        if self._null_policy == "propagate":
            null_mask = self._combine_null_masks(left, right, shape)
        else:
            null_mask = np.zeros(shape[0], dtype=np.bool_)
        assert null_mask is not None

        vec_result = np.zeros(shape[0], dtype=np.float64)
        non_null = ~null_mask
        if np.any(non_null):
            cmp_result = self._compare_vector(a, b, op_str, non_null)
            vec_result[non_null] = cmp_result

        return _EvalValue.condition_val("vector", vector=vec_result, null_mask=null_mask)

    def _compare_scalar(self, a: float, b: float, op: str) -> float:
        if op == "<":
            return float(a < b)
        if op == "<=":
            return float(a <= b)
        if op == ">":
            return float(a > b)
        if op == ">=":
            return float(a >= b)
        if op == "==":
            return float(a == b)
        if op == "!=":
            return float(a != b)
        raise NumericError(code="numeric_expression_rejected", message=f"unknown comparison: {op}")

    def _compare_vector(
        self, a: np.ndarray, b: np.ndarray, op: str, mask: np.ndarray
    ) -> np.ndarray:
        av = a[mask]
        bv = b[mask]
        if op == "<":
            return (av < bv).astype(np.float64)
        if op == "<=":
            return (av <= bv).astype(np.float64)
        if op == ">":
            return (av > bv).astype(np.float64)
        if op == ">=":
            return (av >= bv).astype(np.float64)
        if op == "==":
            return (av == bv).astype(np.float64)
        if op == "!=":
            return (av != bv).astype(np.float64)
        raise NumericError(code="numeric_expression_rejected", message=f"unknown comparison: {op}")

    # ---- 除零 / 幂定义域校验 ----

    def _check_div_zero_scalar(self, val: float, op_name: str) -> None:
        if val == 0.0:
            raise NumericError(
                code="numeric_divide_by_zero",
                message=f"division by zero in {op_name}",
            )

    def _check_div_zero_vector(self, b: np.ndarray, mask: np.ndarray, op_name: str) -> None:
        if np.any(mask) and np.any(b[mask] == 0.0):
            raise NumericError(
                code="numeric_divide_by_zero",
                message=f"division by zero in {op_name}",
            )

    def _safe_div(self, a: np.ndarray, b: np.ndarray, op_name: str) -> np.ndarray:
        return a / b

    def _safe_mod(self, a: np.ndarray, b: np.ndarray, op_name: str) -> np.ndarray:
        return np.mod(a, b)

    def _safe_pow(
        self, a: np.ndarray, b: np.ndarray, op_name: str, exponent_is_int: bool
    ) -> np.ndarray:
        return np.power(a, b)

    def _check_pow_domain_scalar(self, base: float, exp: float, exp_is_int: bool) -> None:
        """检查幂运算定义域。"""
        if base < 0.0 and not exp_is_int:
            if not float(exp).is_integer():
                raise NumericError(
                    code="numeric_domain_error",
                    message="negative base with non-integer exponent",
                    details={"invalid_count": 1},
                )

    def _check_pow_domain_vector(
        self, a: np.ndarray, b: np.ndarray, mask: np.ndarray, exp_is_int: bool
    ) -> None:
        """检查向量幂运算定义域。"""
        if exp_is_int:
            return
        neg_base = a[mask] < 0.0
        non_int_exp = ~np.isclose(b[mask] % 1.0, 0.0)
        invalid = neg_base & non_int_exp
        if np.any(invalid):
            count = int(np.sum(invalid))
            raise NumericError(
                code="numeric_domain_error",
                message=f"negative base with non-integer exponent, {count} invalid values",
                details={"invalid_count": count},
            )
