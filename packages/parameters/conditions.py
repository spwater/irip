"""条件 AST 解析与求值引擎（IRIP Task 18）。

将 JSON 条件表达式解析为类型化 AST（冻结值对象），然后安全求值。
仅允许白名单字段和预定义操作符，不接受用户提供的 SQL 片段。

支持的 JSON 格式：
- 叶子节点（字段比较）::

    {"field": "temperature_c", "gte": 20}
    {"field": "material_code", "eq": "MAT-001"}
    {"field": "material_code", "in": ["MAT-001", "MAT-002"]}

- 分支节点（逻辑组合）::

    {"and": [{"field": "temperature_c", "gte": 20},
             {"field": "temperature_c", "lte": 30}]}
    {"or": [{"field": "temperature_c", "gt": 30},
            {"field": "temperature_c", "lt": 10}]}

求值时从 context 字典中取字段值，应用操作符比较。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from packages.common.errors import AppError


class ConditionOperator(StrEnum):
    """条件操作符枚举。"""

    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    IN = "in"
    AND = "and"
    OR = "or"


#: 允许在条件中使用的字段白名单。不接受用户提供的任意字段名。
ALLOWED_CONDITION_FIELDS: frozenset[str] = frozenset(
    {
        "department_id",
        "object_id",
        "material_code",
        "temperature_c",
        "humidity_pct",
        "sample_moisture_pct",
    }
)

#: 叶子节点操作符集合（字段比较）。
_LEAF_OPERATORS: frozenset[str] = frozenset(
    {
        ConditionOperator.EQ.value,
        ConditionOperator.NE.value,
        ConditionOperator.LT.value,
        ConditionOperator.LTE.value,
        ConditionOperator.GT.value,
        ConditionOperator.GTE.value,
        ConditionOperator.IN.value,
    }
)

#: 分支节点操作符集合（逻辑组合）。
_BRANCH_OPERATORS: frozenset[str] = frozenset(
    {
        ConditionOperator.AND.value,
        ConditionOperator.OR.value,
    }
)

#: 叶子节点可接受的标量值类型。
_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool)


@dataclass(frozen=True)
class ConditionLeaf:
    """条件叶子节点（字段比较）。

    Attributes:
        field: 字段名（必须在 ALLOWED_CONDITION_FIELDS 白名单中）。
        op: 操作符（eq/ne/lt/lte/gt/gte/in）。
        value: 比较值（标量或列表）。
    """

    field: str
    op: str
    value: object


@dataclass(frozen=True)
class ConditionBranch:
    """条件分支节点（逻辑组合）。

    Attributes:
        op: 逻辑操作符（and/or）。
        children: 子条件元组。
    """

    op: str
    children: tuple[ConditionNode, ...]


#: 条件 AST 节点（叶子或分支）。
ConditionNode = ConditionLeaf | ConditionBranch


class ConditionEngine:
    """条件 AST 解析与求值引擎。

    提供 parse() 将 JSON 条件解析为类型化 AST，matches() 评估条件是否
    匹配给定上下文。仅允许白名单字段和预定义操作符，确保安全求值。
    """

    def parse(self, json_condition: dict[str, Any]) -> ConditionNode:
        """将 JSON 条件解析为类型化 AST。

        支持操作符: eq, ne, lt, lte, gt, gte, in, and, or。
        字段必须在 ALLOWED_CONDITION_FIELDS 白名单中。
        不接受用户提供的 SQL 片段。

        Args:
            json_condition: JSON 条件字典。

        Returns:
            ConditionNode: 解析后的 AST 节点（叶子或分支）。

        Raises:
            AppError: code="validation_failed"，当条件格式无效、字段不在
                白名单、操作符未知、或值类型不匹配时。
        """
        if not isinstance(json_condition, dict):
            raise AppError(
                code="validation_failed",
                message="条件必须是一个 JSON 对象",
                retryable=False,
                fields={},
            )

        # 检查是否为分支节点（and/or）
        branch_op: str | None = None
        for op in _BRANCH_OPERATORS:
            if op in json_condition:
                branch_op = op
                break

        if branch_op is not None:
            children_raw: object = json_condition[branch_op]
            if not isinstance(children_raw, list):
                raise AppError(
                    code="validation_failed",
                    message=f"逻辑操作符 '{branch_op}' 的值必须是一个列表",
                    retryable=False,
                    fields={branch_op: "must_be_list"},
                )
            children: tuple[ConditionNode, ...] = tuple(self.parse(child) for child in children_raw)
            return ConditionBranch(op=branch_op, children=children)

        # 叶子节点（字段比较）
        if "field" not in json_condition:
            raise AppError(
                code="validation_failed",
                message="条件叶子节点必须包含 'field' 字段",
                retryable=False,
                fields={},
            )

        field: object = json_condition["field"]
        if not isinstance(field, str):
            raise AppError(
                code="validation_failed",
                message="条件 'field' 必须是字符串",
                retryable=False,
                fields={"field": "must_be_string"},
            )

        if field not in ALLOWED_CONDITION_FIELDS:
            raise AppError(
                code="validation_failed",
                message=(
                    f"条件字段 '{field}' 不在白名单中，"
                    f"允许的字段: {sorted(ALLOWED_CONDITION_FIELDS)}"
                ),
                retryable=False,
                fields={"field": field},
            )

        # 查找操作符键（除了 "field" 之外的合法叶子操作符）
        op_key: str | None = None
        for key in json_condition:
            if key != "field" and key in _LEAF_OPERATORS:
                op_key = key
                break

        if op_key is None:
            # 检查是否有未知操作符
            unknown_keys = [k for k in json_condition if k != "field"]
            raise AppError(
                code="validation_failed",
                message=(
                    f"条件叶子节点缺少有效操作符，"
                    f"未知键: {unknown_keys}，"
                    f"支持的操作符: {sorted(_LEAF_OPERATORS)}"
                ),
                retryable=False,
                fields={"operator": "unknown"},
            )

        value: object = json_condition[op_key]

        # 验证值类型
        if op_key == ConditionOperator.IN.value:
            if not isinstance(value, list):
                raise AppError(
                    code="validation_failed",
                    message="操作符 'in' 的值必须是一个列表",
                    retryable=False,
                    fields={"in": "must_be_list"},
                )
        else:
            if not isinstance(value, _SCALAR_TYPES):
                raise AppError(
                    code="validation_failed",
                    message=(
                        f"操作符 '{op_key}' 的值必须是标量"
                        f"（str/int/float/bool），"
                        f"实际类型: {type(value).__name__}"
                    ),
                    retryable=False,
                    fields={op_key: "must_be_scalar"},
                )

        return ConditionLeaf(field=field, op=op_key, value=value)

    def matches(self, condition: dict[str, Any], context: dict[str, object]) -> bool:
        """评估条件是否匹配给定上下文。

        示例::

            condition = {"and": [
                {"field": "temperature_c", "gte": 20},
                {"field": "temperature_c", "lte": 30},
            ]}
            context = {"temperature_c": 25}
            engine.matches(condition, context)  # → True

        Args:
            condition: JSON 条件字典。
            context: 上下文字典（字段名 → 值）。

        Returns:
            bool: 条件匹配返回 True，否则 False。

        Raises:
            AppError: 当条件格式无效时（通过 parse 传播）。
        """
        node: ConditionNode = self.parse(condition)
        return self._eval_node(node, context)

    def _eval_node(self, node: ConditionNode, context: dict[str, object]) -> bool:
        """递归评估 AST 节点。

        Args:
            node: AST 节点（叶子或分支）。
            context: 上下文字典。

        Returns:
            bool: 节点求值结果。
        """
        if isinstance(node, ConditionLeaf):
            return self._eval_leaf(node, context)
        if isinstance(node, ConditionBranch):
            if node.op == ConditionOperator.AND.value:
                return all(self._eval_node(child, context) for child in node.children)
            # OR
            return any(self._eval_node(child, context) for child in node.children)
        return False

    def _eval_leaf(self, leaf: ConditionLeaf, context: dict[str, object]) -> bool:
        """评估叶子节点（字段比较）。

        Args:
            leaf: 条件叶子节点。
            context: 上下文字典。

        Returns:
            bool: 比较结果。字段不存在于上下文时返回 False。
        """
        if leaf.field not in context:
            return False
        ctx_value: object = context[leaf.field]

        if leaf.op == ConditionOperator.EQ.value:
            return ctx_value == leaf.value
        if leaf.op == ConditionOperator.NE.value:
            return ctx_value != leaf.value
        if leaf.op == ConditionOperator.LT.value:
            return self._compare(ctx_value, leaf.value) < 0
        if leaf.op == ConditionOperator.LTE.value:
            return self._compare(ctx_value, leaf.value) <= 0
        if leaf.op == ConditionOperator.GT.value:
            return self._compare(ctx_value, leaf.value) > 0
        if leaf.op == ConditionOperator.GTE.value:
            return self._compare(ctx_value, leaf.value) >= 0
        if leaf.op == ConditionOperator.IN.value:
            if not isinstance(leaf.value, list):
                return False
            return ctx_value in leaf.value
        return False

    @staticmethod
    def _compare(a: object, b: object) -> int:
        """安全比较两个值，返回 -1/0/1。

        优先尝试数值比较；若无法转换则退回字符串比较。

        Args:
            a: 左值。
            b: 右值。

        Returns:
            int: a < b → -1，a == b → 0，a > b → 1。
        """
        # 尝试数值比较
        try:
            a_num: float = float(a)  # type: ignore[arg-type]
            b_num: float = float(b)  # type: ignore[arg-type]
            if a_num < b_num:
                return -1
            if a_num > b_num:
                return 1
            return 0
        except (TypeError, ValueError):
            pass
        # 退回字符串比较
        a_str: str = str(a)
        b_str: str = str(b)
        if a_str < b_str:
            return -1
        if a_str > b_str:
            return 1
        return 0
