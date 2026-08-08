"""AI 数值计算工具 — 受限表达式引擎（向后兼容 re-export）。

本文件原为受限表达式引擎的单体实现（1765 行），现已按功能域拆分为：
- expression_core.py：_EvalValue / 白名单定义 / _InterpreterBase（共享分发与辅助方法）
- expression_parser.py：ExpressionValidator（AST 验证）
- expression_ops.py：_OpsMixin（算术 / 一元 / 比较运算）
- expression_funcs.py：_FuncsMixin（内置函数）
- expression_eval.py：ExpressionInterpreter 组装 + SafeExpressionEngine

为保持向后兼容，``from packages.ai.numeric.expression import SafeExpressionEngine``
等导入仍可正常工作。设计文档 §9：受限表达式引擎。
"""

from packages.ai.numeric.expression_core import _EvalValue  # noqa: F401
from packages.ai.numeric.expression_eval import ExpressionInterpreter, SafeExpressionEngine
from packages.ai.numeric.expression_parser import ExpressionValidator  # noqa: F401

__all__ = ["SafeExpressionEngine", "ExpressionValidator", "ExpressionInterpreter", "_EvalValue"]
