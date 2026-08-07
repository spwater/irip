"""AI 数值计算工具 — 受限表达式引擎。

使用 ``ast.parse(expression, mode="eval")`` 只生成表达式 AST。
两阶段处理：
1. ``ExpressionValidator`` 遍历整棵树，验证节点种类、总数、深度、标识符和函数调用；
2. ``ExpressionInterpreter`` 递归解释已验证节点，对标量或 NumPy float64 数组调用内部白名单函数。

安全红线：
- 绝不使用 ``compile``/``eval``/``exec``；
- 不暴露 NumPy 模块或 Python builtins；
- 解释器只对显式支持的节点分支执行，不存在通用求值回退。

设计文档 §9：受限表达式引擎
"""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from packages.ai.numeric.contracts import (
    ExpressionOptions,
    NumericError,
    NumericLimits,
    NumericValue,
    ResolvedNumericInput,
)
from packages.ai.numeric.units import (
    UnitTag,
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
# 白名单定义
# =============================================================================

#: 初等函数（逐元素）
_ELEMENTWISE_FUNCS: frozenset[str] = frozenset(
    {
        "abs",
        "sqrt",
        "exp",
        "log",
        "log10",
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "atan2",
        "floor",
        "ceil",
        "round",
    }
)

#: 逐元素选择/裁剪函数
_SELECT_FUNCS: frozenset[str] = frozenset({"minimum", "maximum", "clip", "where"})

#: 聚合函数
_AGGREGATE_FUNCS: frozenset[str] = frozenset(
    {
        "count",
        "sum",
        "mean",
        "min",
        "max",
        "median",
        "var",
        "std",
        "quantile",
    }
)

#: 全部白名单函数
_ALL_FUNCS: frozenset[str] = _ELEMENTWISE_FUNCS | _SELECT_FUNCS | _AGGREGATE_FUNCS

#: 允许的常量名
_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e}

#: 允许的二元运算符
_BINOPS: dict[type[ast.AST], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.Pow: "**",
    ast.Mod: "%",
}

#: 允许的一元运算符
_UNARYOPS: set[type[ast.AST]] = {ast.UAdd, ast.USub}

#: 允许的比较运算符
_COMPAREOPS: dict[type[ast.AST], str] = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
}


# =============================================================================
# 内部求值值
# =============================================================================


@dataclass
class _EvalValue:
    """解释器中间值 — 标量或向量，带空值掩码和单位。

    Attributes:
        kind: "scalar" 或 "vector"。
        scalar: 标量值（None 表示 null 标量）。
        vector: 向量 float64 数组。
        null_mask: 向量空值掩码。
        unit: 单位标签。
        is_null_scalar: 标量是否为 null。
        is_condition: 是否为比较结果（只能用于 where 条件）。
    """

    kind: str
    scalar: float | None = None
    vector: NDArray[np.float64] | None = None
    null_mask: NDArray[np.bool_] | None = None
    unit: UnitTag = field(default_factory=UnitTag.unknown)
    is_null_scalar: bool = False
    is_condition: bool = False

    @classmethod
    def scalar_val(cls, value: float, unit: UnitTag) -> _EvalValue:
        return cls(kind="scalar", scalar=value, unit=unit)

    @classmethod
    def null_scalar(cls, unit: UnitTag) -> _EvalValue:
        return cls(kind="scalar", scalar=None, unit=unit, is_null_scalar=True)

    @classmethod
    def vector_val(
        cls,
        values: NDArray[np.float64],
        null_mask: NDArray[np.bool_],
        unit: UnitTag,
    ) -> _EvalValue:
        return cls(kind="vector", vector=values, null_mask=null_mask, unit=unit)

    @classmethod
    def condition_val(
        cls,
        kind: str,
        scalar: float | None = None,
        vector: NDArray[np.float64] | None = None,
        null_mask: NDArray[np.bool_] | None = None,
    ) -> _EvalValue:
        return cls(
            kind=kind,
            scalar=scalar,
            vector=vector,
            null_mask=null_mask,
            unit=UnitTag.dimensionless(),
            is_condition=True,
        )


# =============================================================================
# AST 验证器
# =============================================================================


class ExpressionValidator:
    """表达式 AST 验证器。

    遍历整棵树验证：
    - 节点种类（只允许白名单节点类型）；
    - 总节点数（不超过 max_ast_nodes）；
    - 深度（不超过 max_ast_depth）；
    - 标识符（变量名和函数名在白名单中）；
    - 函数调用（不允许属性访问、关键字参数、*args/**kwargs）。
    """

    #: 允许的 AST 节点类型
    _ALLOWED_NODES: set[type[ast.AST]] = {
        ast.Expression,
        ast.Constant,
        ast.Name,
        ast.UnaryOp,
        ast.UAdd,
        ast.USub,
        ast.BinOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.Mod,
        ast.Compare,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Eq,
        ast.NotEq,
        ast.Call,
        ast.Load,
    }

    def __init__(self, limits: NumericLimits) -> None:
        self._limits = limits

    def validate(self, tree: ast.AST) -> None:
        """验证整棵 AST 树。

        Args:
            tree: ast.parse(expression, mode="eval") 返回的 AST 根节点。

        Raises:
            NumericError: 任何验证失败时。
        """
        node_count = 0

        def _count_and_check(node: ast.AST, depth: int) -> None:
            nonlocal node_count
            node_count += 1
            if node_count > self._limits.max_ast_nodes:
                raise NumericError(
                    code="numeric_expression_rejected",
                    message=f"expression exceeds max AST nodes ({self._limits.max_ast_nodes})",
                )
            if depth > self._limits.max_ast_depth:
                raise NumericError(
                    code="numeric_expression_rejected",
                    message=f"expression exceeds max AST depth ({self._limits.max_ast_depth})",
                )

            node_type = type(node)
            if node_type not in self._ALLOWED_NODES:
                raise NumericError(
                    code="numeric_expression_rejected",
                    message=f"unsupported syntax: {node_type.__name__}",
                )

            # 检查常量类型
            if isinstance(node, ast.Constant):
                if isinstance(node.value, bool):
                    raise NumericError(
                        code="numeric_expression_rejected",
                        message="boolean literals are not allowed",
                    )
                if not isinstance(node.value, (int, float)):
                    raise NumericError(
                        code="numeric_expression_rejected",
                        message=f"unsupported literal type: {type(node.value).__name__}",
                    )
                # 检查大整数
                if isinstance(node.value, int) and abs(node.value) > 10**18:
                    raise NumericError(
                        code="numeric_expression_rejected",
                        message="integer literal too large",
                    )

            # 检查标识符
            if isinstance(node, ast.Name):
                name = node.id
                if name not in _ALL_FUNCS and name not in _CONSTANTS:
                    # 变量名合法性由调用方保证，这里只做格式检查
                    if not name.isidentifier():
                        raise NumericError(
                            code="numeric_expression_rejected",
                            message=f"invalid identifier: {name}",
                        )

            # 检查函数调用
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name):
                    raise NumericError(
                        code="numeric_expression_rejected",
                        message="only bare function names are allowed, no attribute access",
                    )
                func_name = node.func.id
                if func_name not in _ALL_FUNCS:
                    raise NumericError(
                        code="numeric_expression_rejected",
                        message=f"unknown function: {func_name}",
                    )
                # 不允许关键字参数
                if node.keywords:
                    raise NumericError(
                        code="numeric_expression_rejected",
                        message="keyword arguments are not allowed",
                    )
                # 参数数量检查
                expected = _EXPECTED_ARGS.get(func_name)
                if expected is not None:
                    actual = len(node.args)
                    if actual < expected[0] or actual > expected[1]:
                        raise NumericError(
                            code="numeric_expression_rejected",
                            message=(
                                f"{func_name} expects {expected[0]}-{expected[1]}"
                                f" arguments, got {actual}"
                            ),
                        )

            # 递归子节点
            for child in ast.iter_child_nodes(node):
                # 跳过 Load/Store 等上下文节点（不计入深度）
                child_depth = depth + 1 if not isinstance(child, ast.Load) else depth
                _count_and_check(child, child_depth)

        _count_and_check(tree, 0)

        # 确保根节点是 Expression
        if not isinstance(tree, ast.Expression):
            raise NumericError(
                code="numeric_expression_rejected",
                message="expression must be a single expression",
            )


#: 各函数期望的参数数量 [min, max]
_EXPECTED_ARGS: dict[str, tuple[int, int]] = {
    "abs": (1, 1),
    "sqrt": (1, 1),
    "exp": (1, 1),
    "log": (1, 1),
    "log10": (1, 1),
    "sin": (1, 1),
    "cos": (1, 1),
    "tan": (1, 1),
    "asin": (1, 1),
    "acos": (1, 1),
    "atan": (1, 1),
    "atan2": (2, 2),
    "floor": (1, 1),
    "ceil": (1, 1),
    "round": (2, 2),
    "minimum": (2, 2),
    "maximum": (2, 2),
    "clip": (3, 3),
    "where": (3, 3),
    "count": (1, 1),
    "sum": (1, 1),
    "mean": (1, 1),
    "min": (1, 1),
    "max": (1, 1),
    "median": (1, 1),
    "var": (1, 2),
    "std": (1, 2),
    "quantile": (2, 2),
}


# =============================================================================
# 表达式解释器
# =============================================================================


class ExpressionInterpreter:
    """递归解释已验证的 AST 节点。

    对标量或 NumPy float64 数组调用内部白名单函数。
    不使用 compile/eval/exec，不暴露 builtins。
    """

    def __init__(
        self,
        variables: Mapping[str, ResolvedNumericInput],
        options: ExpressionOptions,
        limits: NumericLimits,
    ) -> None:
        self._variables = variables
        self._options = options
        self._limits = limits
        self._warnings: list[str] = []
        self._null_policy = options.null_policy

    @property
    def warnings(self) -> list[str]:
        return self._warnings

    def interpret(self, node: ast.AST) -> _EvalValue:
        """解释 AST 节点，返回求值结果。"""
        if isinstance(node, ast.Expression):
            return self.interpret(node.body)
        if isinstance(node, ast.Constant):
            return self._visit_constant(node)
        if isinstance(node, ast.Name):
            return self._visit_name(node)
        if isinstance(node, ast.UnaryOp):
            return self._visit_unaryop(node)
        if isinstance(node, ast.BinOp):
            return self._visit_binop(node)
        if isinstance(node, ast.Compare):
            return self._visit_compare(node)
        if isinstance(node, ast.Call):
            return self._visit_call(node)
        raise NumericError(
            code="numeric_expression_rejected",
            message=f"unsupported node type: {type(node).__name__}",
        )

    # ---- 叶节点 ----

    def _visit_constant(self, node: ast.Constant) -> _EvalValue:
        """数值字面量。"""
        val = node.value
        if isinstance(val, bool):
            raise NumericError(
                code="numeric_expression_rejected",
                message="boolean literals are not allowed",
            )
        float_val = float(val)  # type: ignore[arg-type]
        if not math.isfinite(float_val):
            raise NumericError(
                code="numeric_non_finite_result",
                message="non-finite literal value",
            )
        return _EvalValue.scalar_val(float_val, literal_unit())

    def _visit_name(self, node: ast.Name) -> _EvalValue:
        """变量名或常量。"""
        name = node.id
        if name in _CONSTANTS:
            return _EvalValue.scalar_val(_CONSTANTS[name], constant_unit(name))

        if name not in self._variables:
            raise NumericError(
                code="numeric_expression_rejected",
                message=f"undefined variable: {name}",
            )

        var = self._variables[name]

        # null_policy=fail 时检查空值
        if self._null_policy == "fail":
            if np.any(var.null_mask):
                raise NumericError(
                    code="numeric_invalid_source",
                    message=f"variable '{name}' contains null values and null_policy is 'fail'",
                    path=f"variables.{name}",
                )

        unit = UnitTag.from_unit_string(var.unit)

        if var.is_scalar:
            val = float(var.values)
            return _EvalValue.scalar_val(val, unit)

        return _EvalValue.vector_val(
            var.values.astype(np.float64),
            var.null_mask.astype(np.bool_),
            unit,
        )

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
            result = -val.scalar  # type: ignore[operator]
            return _EvalValue.scalar_val(result, val.unit)

        # vector
        result = val.vector.copy()  # type: ignore[union-attr]
        null_mask = val.null_mask.copy()  # type: ignore[union-attr]
        non_null = ~null_mask
        result[non_null] = -result[non_null]
        return _EvalValue.vector_val(result, null_mask, val.unit)

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

    def _reject_condition(self, val: _EvalValue, context: str) -> None:
        if val.is_condition:
            raise NumericError(
                code="numeric_expression_rejected",
                message=f"comparison result cannot be used in {context}; use where() instead",
            )

    def _broadcast(self, left: _EvalValue, right: _EvalValue) -> tuple[int, ...]:
        """返回广播后的形状，检查长度一致性。"""
        if left.kind == "scalar" and right.kind == "scalar":
            return ()
        if left.kind == "scalar" and right.kind == "vector":
            return (right.vector.shape[0],)  # type: ignore[union-attr]
        if left.kind == "vector" and right.kind == "scalar":
            return (left.vector.shape[0],)  # type: ignore[union-attr]
        # both vector
        left_len = left.vector.shape[0]  # type: ignore[union-attr]
        right_len = right.vector.shape[0]  # type: ignore[union-attr]
        if left_len != right_len:
            raise NumericError(
                code="numeric_size_limit",
                message=f"vector length mismatch: {left_len} vs {right_len}",
            )
        return (left_len,)

    def _combine_null_masks(
        self, left: _EvalValue, right: _EvalValue, shape: tuple[int, ...]
    ) -> NDArray[np.bool_] | None:
        """合并两个值的 null mask（传播策略下）。"""
        if self._null_policy != "propagate":
            return None
        if len(shape) == 0:
            # scalar
            return None
        left_mask = self._get_mask(left, shape)
        right_mask = self._get_mask(right, shape)
        return left_mask | right_mask

    def _get_mask(self, val: _EvalValue, shape: tuple[int, ...]) -> NDArray[np.bool_]:
        """获取与目标形状匹配的 null mask。"""
        if len(shape) == 0:
            return np.array(val.is_null_scalar, dtype=np.bool_)
        if val.kind == "scalar":
            return np.zeros(shape[0], dtype=np.bool_)
        return val.null_mask.astype(np.bool_)  # type: ignore[union-attr]

    def _get_values(self, val: _EvalValue, shape: tuple[int, ...]) -> np.ndarray:
        """获取与目标形状匹配的值数组。"""
        if len(shape) == 0:
            return np.float64(val.scalar if not val.is_null_scalar else 0.0)  # type: ignore[return-value]
        if val.kind == "scalar":
            return np.full(
                shape[0], val.scalar if not val.is_null_scalar else 0.0, dtype=np.float64
            )
        return val.vector.astype(np.float64)  # type: ignore[union-attr]

    def _binop_add(self, left: _EvalValue, right: _EvalValue) -> _EvalValue:
        shape = self._broadcast(left, right)
        unit, w = combine_additive(left.unit, right.unit, "addition")
        self._warnings.extend(w)

        if len(shape) == 0:
            if left.is_null_scalar or right.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            result = left.scalar + right.scalar  # type: ignore[operator]
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
            result = left.scalar - right.scalar  # type: ignore[operator]
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
            result = left.scalar * right.scalar  # type: ignore[operator]
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
            self._check_div_zero_scalar(right.scalar, "division")  # type: ignore[arg-type]
            result = left.scalar / right.scalar  # type: ignore[operator]
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
            self._check_div_zero_scalar(right.scalar, "modulo")  # type: ignore[arg-type]
            result = left.scalar % right.scalar  # type: ignore[operator]
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
            base_val = left.scalar
            exp_val = right.scalar
            self._check_pow_domain_scalar(base_val, exp_val, exponent_is_int)  # type: ignore[arg-type]
            result = base_val**exp_val  # type: ignore[operator]
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

        non_null = ~null_mask  # type: ignore[operator]

        if check_div_zero:
            self._check_div_zero_vector(b, non_null, op_name)

        if check_pow:
            self._check_pow_domain_vector(a, b, non_null, exponent_is_int)

        result = np.zeros(shape[0], dtype=np.float64)
        if np.any(non_null):
            result[non_null] = op(a[non_null], b[non_null])
            self._check_finite_vector(result, non_null, op_name)

        return _EvalValue.vector_val(result, null_mask, unit)  # type: ignore[arg-type]

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
            result = self._compare_scalar(a, b, op_str)  # type: ignore[arg-type]
            return _EvalValue.condition_val("scalar", scalar=float(result))

        if self._null_policy == "propagate":
            null_mask = self._combine_null_masks(left, right, shape)
        else:
            null_mask = np.zeros(shape[0], dtype=np.bool_)

        result = np.zeros(shape[0], dtype=np.float64)  # type: ignore[assignment]
        non_null = ~null_mask  # type: ignore[operator]
        if np.any(non_null):
            cmp_result = self._compare_vector(a, b, op_str, non_null)
            result[non_null] = cmp_result  # type: ignore[index]

        return _EvalValue.condition_val("vector", vector=result, null_mask=null_mask)  # type: ignore[arg-type]

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
            return (av < bv).astype(np.float64)  # type: ignore[no-any-return]
        if op == "<=":
            return (av <= bv).astype(np.float64)  # type: ignore[no-any-return]
        if op == ">":
            return (av > bv).astype(np.float64)  # type: ignore[no-any-return]
        if op == ">=":
            return (av >= bv).astype(np.float64)  # type: ignore[no-any-return]
        if op == "==":
            return (av == bv).astype(np.float64)  # type: ignore[no-any-return]
        if op == "!=":
            return (av != bv).astype(np.float64)  # type: ignore[no-any-return]
        raise NumericError(code="numeric_expression_rejected", message=f"unknown comparison: {op}")

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
            result = math.atan2(y.scalar, x.scalar)  # type: ignore[arg-type]
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

        result = np.zeros(shape[0], dtype=np.float64)  # type: ignore[assignment]
        non_null = ~null_mask  # type: ignore[operator]
        if np.any(non_null):
            result[non_null] = np.arctan2(yv[non_null], xv[non_null])  # type: ignore[index]
            if self._options.angle_unit == "degree":
                result[non_null] = np.degrees(result[non_null])  # type: ignore[index]
            self._check_finite_vector(result, non_null, "atan2")  # type: ignore[arg-type]

        return _EvalValue.vector_val(result, null_mask, unit)  # type: ignore[arg-type]

    def _call_round(self, args: list[_EvalValue]) -> _EvalValue:
        x = args[0]
        digits_val = args[1]

        # digits 必须是整数字面量
        if digits_val.kind != "scalar" or digits_val.is_null_scalar:
            raise NumericError(
                code="numeric_expression_rejected",
                message="round digits must be an integer literal",
            )
        digits_float = digits_val.scalar
        if not float(digits_float).is_integer():  # type: ignore[arg-type]
            raise NumericError(
                code="numeric_expression_rejected",
                message="round digits must be an integer",
            )
        digits = int(digits_float)  # type: ignore[arg-type]
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
            result = round(x.scalar, digits)  # type: ignore[arg-type]
            return _EvalValue.scalar_val(result, unit)

        null_mask = (
            x.null_mask.astype(np.bool_)  # type: ignore[union-attr]
            if self._null_policy == "propagate"
            else np.zeros(len(x.vector), dtype=np.bool_)  # type: ignore[arg-type]
        )
        result = np.zeros_like(x.vector)  # type: ignore[assignment]
        non_null = ~null_mask
        if np.any(non_null):
            result[non_null] = np.round(x.vector[non_null], digits)  # type: ignore[index]
        return _EvalValue.vector_val(result, null_mask, unit)  # type: ignore[arg-type]

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
            val = x.scalar
            if domain_check is not None:
                domain_check(val)
            result = float(func(val))
            self._check_finite(result, name)
            return _EvalValue.scalar_val(result, unit)

        # vector
        null_mask = (
            x.null_mask.astype(np.bool_)  # type: ignore[union-attr]
            if self._null_policy == "propagate"
            else np.zeros(len(x.vector), dtype=np.bool_)  # type: ignore[arg-type]
        )
        non_null = ~null_mask
        result = np.zeros(len(x.vector), dtype=np.float64)  # type: ignore[assignment, arg-type]

        if domain_check is not None and np.any(non_null):
            vals = x.vector[non_null]  # type: ignore[index]
            domain_check(vals)

        if np.any(non_null):
            result[non_null] = func(x.vector[non_null])  # type: ignore[index]
            self._check_finite_vector(result, non_null, name)  # type: ignore[arg-type]

        return _EvalValue.vector_val(result, null_mask, unit)  # type: ignore[arg-type]

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

        result = np.zeros(shape[0], dtype=np.float64)
        non_null = ~null_mask  # type: ignore[operator]
        if np.any(non_null):
            if is_min:
                result[non_null] = np.minimum(av[non_null], bv[non_null])
            else:
                result[non_null] = np.maximum(av[non_null], bv[non_null])
            self._check_finite_vector(result, non_null, name)

        return _EvalValue.vector_val(result, null_mask, unit)  # type: ignore[arg-type]

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

        low_val = low.scalar
        high_val = high.scalar
        if low_val > high_val:  # type: ignore[operator]
            raise NumericError(
                code="numeric_expression_rejected",
                message="clip low must be <= high",
            )

        unit, w = check_clip_bounds(x.unit, low.unit, high.unit)
        self._warnings.extend(w)

        if x.kind == "scalar":
            if x.is_null_scalar:
                return _EvalValue.null_scalar(unit)
            result = max(low_val, min(x.scalar, high_val))  # type: ignore[type-var]
            return _EvalValue.scalar_val(float(result), unit)  # type: ignore[arg-type]

        null_mask = (
            x.null_mask.astype(np.bool_)  # type: ignore[union-attr]
            if self._null_policy == "propagate"
            else np.zeros(len(x.vector), dtype=np.bool_)  # type: ignore[arg-type]
        )
        result = np.zeros(len(x.vector), dtype=np.float64)  # type: ignore[assignment, arg-type]
        non_null = ~null_mask
        if np.any(non_null):
            result[non_null] = np.clip(x.vector[non_null], low_val, high_val)  # type: ignore[index]
            self._check_finite_vector(result, non_null, "clip")  # type: ignore[arg-type]

        return _EvalValue.vector_val(result, null_mask, unit)  # type: ignore[arg-type]

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
            return _EvalValue.scalar_val(a.scalar if cond.scalar else b.scalar, unit)  # type: ignore[arg-type]

        # 至少一个是向量
        shape = self._broadcast(a, b)
        if cond.kind == "vector":
            shape = (cond.vector.shape[0],)  # type: ignore[union-attr]

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
                count = float(np.sum(~x.null_mask))  # type: ignore[operator]
            else:
                count = float(len(x.vector))  # type: ignore[arg-type]
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
        q = q_val.scalar
        if q < 0.0 or q > 1.0:  # type: ignore[operator]
            raise NumericError(
                code="numeric_domain_error",
                message=f"quantile q must be in [0, 1], got {q}",
            )
        if len(vals) == 0:
            raise NumericError(
                code="numeric_domain_error",
                message="quantile of empty series is undefined",
            )
        result = float(np.quantile(vals, q))  # type: ignore[arg-type]
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

    # ---- 辅助方法 ----

    def _has_null(self, val: _EvalValue) -> bool:
        if val.kind == "scalar":
            return val.is_null_scalar
        return bool(np.any(val.null_mask))

    def _get_valid_values(self, val: _EvalValue) -> np.ndarray:
        """获取非 null 值数组。"""
        if val.kind == "scalar":
            if val.is_null_scalar:
                return np.array([], dtype=np.float64)
            return np.array([val.scalar], dtype=np.float64)
        mask = val.null_mask
        non_null = ~mask  # type: ignore[operator]
        if self._null_policy == "fail":
            return val.vector.astype(np.float64)  # type: ignore[union-attr]
        return val.vector[non_null].astype(np.float64)  # type: ignore[index]

    def _check_finite(self, val: float, op_name: str) -> None:
        if not math.isfinite(val):
            raise NumericError(
                code="numeric_non_finite_result",
                message=f"non-finite result in {op_name}",
            )

    def _check_finite_vector(self, arr: np.ndarray, mask: np.ndarray, op_name: str) -> None:
        if np.any(mask) and not np.all(np.isfinite(arr[mask])):
            raise NumericError(
                code="numeric_non_finite_result",
                message=f"non-finite result in {op_name}",
            )

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
        return a / b  # type: ignore[no-any-return]

    def _safe_mod(self, a: np.ndarray, b: np.ndarray, op_name: str) -> np.ndarray:
        return np.mod(a, b)  # type: ignore[no-any-return]

    def _safe_pow(
        self, a: np.ndarray, b: np.ndarray, op_name: str, exponent_is_int: bool
    ) -> np.ndarray:
        return np.power(a, b)  # type: ignore[no-any-return]

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

    def _domain_sqrt(self, v: np.ndarray, name: str) -> np.ndarray:
        return np.sqrt(v)

    def _domain_log(self, v: np.ndarray, name: str) -> np.ndarray:
        if name == "log10":
            return np.log10(v)
        return np.log(v)

    def _trig(self, v: np.ndarray, func: Any, name: str) -> np.ndarray:
        if self._options.angle_unit == "degree":
            v = np.radians(v)
        return func(v)  # type: ignore[no-any-return]

    def _inverse_trig(
        self, v: np.ndarray, func: Any, name: str, check_domain: str | None
    ) -> np.ndarray:
        result = func(v)
        if self._options.angle_unit == "degree":
            result = np.degrees(result)
        return result  # type: ignore[no-any-return]

    def _normalize_zero(self, val: float) -> float:
        """规范化 -0.0 为 0.0。"""
        if val == 0.0:
            return 0.0
        return val


# =============================================================================
# 安全表达式引擎
# =============================================================================


class SafeExpressionEngine:
    """安全表达式引擎：AST 校验 + 白名单递归解释。

    不使用 compile/eval/exec，不暴露 NumPy 模块或 builtins。
    """

    def __init__(self, limits: NumericLimits | None = None) -> None:
        self._limits = limits or NumericLimits()

    def evaluate(
        self,
        expression: str,
        variables: Mapping[str, ResolvedNumericInput],
        options: ExpressionOptions,
    ) -> NumericValue:
        """解析、验证并执行表达式。

        Args:
            expression: 数学表达式字符串。
            variables: 变量名 → 已解析输入映射。
            options: 求值选项。

        Returns:
            NumericValue: 标量或向量结果。
        """
        # 检查表达式长度
        if len(expression) > self._limits.max_expression_length:
            raise NumericError(
                code="numeric_size_limit",
                message=f"expression exceeds max length ({self._limits.max_expression_length})",
            )
        if len(expression) == 0:
            raise NumericError(
                code="numeric_expression_rejected",
                message="expression is empty",
            )

        # 解析 AST
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise NumericError(
                code="numeric_expression_rejected",
                message=f"expression syntax error: {exc.msg}",
            ) from exc

        # 验证 AST
        validator = ExpressionValidator(self._limits)
        validator.validate(tree)

        # 检查变量数
        if len(variables) > self._limits.max_variables:
            raise NumericError(
                code="numeric_size_limit",
                message=f"too many variables ({len(variables)} > {self._limits.max_variables})",
            )

        # null_policy=fail 时预检所有变量
        if options.null_policy == "fail":
            for name, var in variables.items():
                if np.any(var.null_mask):
                    raise NumericError(
                        code="numeric_invalid_source",
                        message=f"variable '{name}' contains null values and null_policy is 'fail'",
                        path=f"variables.{name}",
                    )

        # 解释执行
        interpreter = ExpressionInterpreter(variables, options, self._limits)
        result = interpreter.interpret(tree)

        # 转换为 NumericValue
        return self._to_numeric_value(result, interpreter.warnings)

    def _to_numeric_value(self, val: _EvalValue, warnings: list[str]) -> NumericValue:
        """将内部 _EvalValue 转换为对外 NumericValue。"""
        unit_str = val.unit.to_output()

        if val.kind == "scalar":
            if val.is_null_scalar:
                return NumericValue(
                    kind="scalar",
                    scalar=None,
                    unit=unit_str,
                    warnings=list(warnings),
                    is_null_scalar=True,
                )
            scalar_val = val.scalar
            if scalar_val == 0.0:
                scalar_val = 0.0  # normalize -0.0
            return NumericValue(
                kind="scalar",
                scalar=float(scalar_val),  # type: ignore[arg-type]
                unit=unit_str,
                warnings=list(warnings),
            )

        # vector
        vector = val.vector.copy()  # type: ignore[union-attr]
        null_mask = val.null_mask
        # normalize -0.0
        vector = np.where(vector == 0.0, 0.0, vector)
        return NumericValue(
            kind="vector",
            vector=vector,
            null_mask=null_mask,
            unit=unit_str,
            warnings=list(warnings),
        )
