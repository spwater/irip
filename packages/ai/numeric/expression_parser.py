"""AI 数值计算工具 — 受限表达式引擎：AST 验证器。

使用 ``ast.parse(expression, mode="eval")`` 生成表达式 AST 后，由
``ExpressionValidator`` 遍历整棵树，验证节点种类、总数、深度、标识符和
函数调用。设计文档 §9：受限表达式引擎。
"""

from __future__ import annotations

import ast

from packages.ai.numeric.contracts import NumericError, NumericLimits
from packages.ai.numeric.expression_core import _ALL_FUNCS, _CONSTANTS


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
