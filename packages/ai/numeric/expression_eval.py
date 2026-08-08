"""AI 数值计算工具 — 受限表达式引擎：解释器组装与安全求值入口。

``ExpressionInterpreter`` 由 ``_OpsMixin``（算术 / 比较）与 ``_FuncsMixin``（内置函数）
组装而成，共享成员来自 ``_InterpreterBase``。``SafeExpressionEngine`` 为对外入口：
解析 → 验证 → 解释执行，不使用 compile/eval/exec，不暴露 builtins。
设计文档 §9：受限表达式引擎。
"""

from __future__ import annotations

import ast
from collections.abc import Mapping

import numpy as np

from packages.ai.numeric.contracts import (
    ExpressionOptions,
    NumericError,
    NumericLimits,
    NumericValue,
    ResolvedNumericInput,
)
from packages.ai.numeric.expression_core import _EvalValue
from packages.ai.numeric.expression_funcs import _FuncsMixin
from packages.ai.numeric.expression_ops import _OpsMixin
from packages.ai.numeric.expression_parser import ExpressionValidator


class ExpressionInterpreter(_OpsMixin, _FuncsMixin):
    """递归解释已验证的 AST 节点。

    对标量或 NumPy float64 数组调用内部白名单函数。
    不使用 compile/eval/exec，不暴露 builtins。

    算术 / 一元 / 比较运算由 ``_OpsMixin`` 提供，内置函数由 ``_FuncsMixin``
    提供，AST 分发与共享辅助方法来自 ``_InterpreterBase``。
    """


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
        vector = val.vval.copy()
        null_mask = val.mval
        # normalize -0.0
        vector = np.where(vector == 0.0, 0.0, vector)
        return NumericValue(
            kind="vector",
            vector=vector,
            null_mask=null_mask,
            unit=unit_str,
            warnings=list(warnings),
        )
