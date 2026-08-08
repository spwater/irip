"""AI 数值计算工具 — 受限表达式引擎：核心数据结构与解释器共享基类。

本模块集中：
- 白名单定义（函数 / 常量 / 运算符）；
- 解释器中间值 ``_EvalValue``；
- ``_InterpreterBase``：ExpressionInterpreter 各功能域 mixin（算术运算 / 内置函数）
  共享的实例属性、AST 分发 ``interpret``、叶节点访问与跨域复用的辅助方法。

设计文档 §9：受限表达式引擎。
"""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from packages.ai.numeric.contracts import (
    ExpressionOptions,
    NumericError,
    NumericLimits,
    ResolvedNumericInput,
)
from packages.ai.numeric.units import UnitTag, constant_unit, literal_unit

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

    使用 ``sval`` / ``vval`` / ``mval`` 访问器替代直接访问 ``scalar`` /
    ``vector`` / ``null_mask``，在运行时断言非 None，消除 type: ignore。

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

    # -- 类型安全访问器（运行时断言非 None，消除 type: ignore）--

    @property
    def sval(self) -> float:
        """获取标量值（断言非 None）。"""
        assert self.scalar is not None
        return self.scalar

    @property
    def vval(self) -> NDArray[np.float64]:
        """获取向量值（断言非 None）。"""
        assert self.vector is not None
        return self.vector

    @property
    def mval(self) -> NDArray[np.bool_]:
        """获取 null_mask（断言非 None）。"""
        assert self.null_mask is not None
        return self.null_mask

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
# 解释器共享基类
# =============================================================================


class _InterpreterBase:
    """ExpressionInterpreter 各功能域 mixin 的共享基类。

    集中声明 ``__init__`` 注入的实例属性、AST 分发 ``interpret``、叶节点访问
    以及算术运算与内置函数两个 mixin 都复用的辅助方法（广播 / 取值 /
    空值合并 / 有限性校验等）。各功能域 mixin（expression_ops / expression_funcs）
    继承本基类后即可访问这些共享成员。

    ``_visit_unaryop`` / ``_visit_binop`` / ``_visit_compare`` 由 expression_ops
    实现，``_visit_call`` 由 expression_funcs 实现；本基类提供占位声明供
    ``interpret`` 分发与 mypy 类型检查。
    """

    # -- 由 __init__ 注入的实例属性（类型声明供 mypy 严格检查）--
    _variables: Mapping[str, ResolvedNumericInput]
    _options: ExpressionOptions
    _limits: NumericLimits
    _warnings: list[str]
    _null_policy: str

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

    # ---- 占位：由功能域 mixin 实现 ----

    def _visit_unaryop(self, node: ast.UnaryOp) -> _EvalValue:
        """一元运算（由 expression_ops._OpsMixin 实现）。"""
        raise NotImplementedError

    def _visit_binop(self, node: ast.BinOp) -> _EvalValue:
        """二元运算（由 expression_ops._OpsMixin 实现）。"""
        raise NotImplementedError

    def _visit_compare(self, node: ast.Compare) -> _EvalValue:
        """比较运算（由 expression_ops._OpsMixin 实现）。"""
        raise NotImplementedError

    def _visit_call(self, node: ast.Call) -> _EvalValue:
        """函数调用（由 expression_funcs._FuncsMixin 实现）。"""
        raise NotImplementedError

    # ---- 跨域复用辅助方法 ----

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
            return (right.vval.shape[0],)
        if left.kind == "vector" and right.kind == "scalar":
            return (left.vval.shape[0],)
        # both vector
        left_len = left.vval.shape[0]
        right_len = right.vval.shape[0]
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
        return val.mval.astype(np.bool_)

    def _get_values(self, val: _EvalValue, shape: tuple[int, ...]) -> np.ndarray:
        """获取与目标形状匹配的值数组。"""
        if len(shape) == 0:
            return np.float64(val.scalar if not val.is_null_scalar else 0.0)  # type: ignore[return-value]
        if val.kind == "scalar":
            return np.full(
                shape[0], val.scalar if not val.is_null_scalar else 0.0, dtype=np.float64
            )
        return val.vval.astype(np.float64)

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
