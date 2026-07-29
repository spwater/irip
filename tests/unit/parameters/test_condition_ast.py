"""条件 AST 引擎单元测试（IRIP Task 18）。

验证：
- 条件 AST 正确评估白名单字段；
- 各操作符（eq/ne/lt/lte/gt/gte/in/and/or）正确求值；
- 嵌套 AND/OR 组合正确求值；
- 非白名单字段被拒绝；
- 未知操作符被拒绝。
"""

import pytest

from packages.common.errors import AppError
from packages.parameters.conditions import ConditionEngine


class TestConditionAST:
    """条件 AST 解析与求值测试。"""

    @pytest.fixture
    def engine(self) -> ConditionEngine:
        """条件引擎实例。"""
        return ConditionEngine()

    def test_condition_ast_evaluates_whitelisted_fields(self, engine: ConditionEngine) -> None:
        """白名单字段的 AND 条件正确求值。

        {"and": [{"field":"temperature_c","gte":20},
                  {"field":"temperature_c","lte":30}]}
        with {"temperature_c":25} → True
        """
        condition = {
            "and": [
                {"field": "temperature_c", "gte": 20},
                {"field": "temperature_c", "lte": 30},
            ]
        }
        context: dict[str, object] = {"temperature_c": 25}
        assert engine.matches(condition, context) is True

    def test_condition_eq(self, engine: ConditionEngine) -> None:
        """相等操作符 eq 正确求值。"""
        condition = {"field": "material_code", "eq": "MAT-001"}
        context: dict[str, object] = {"material_code": "MAT-001"}
        assert engine.matches(condition, context) is True

    def test_condition_ne(self, engine: ConditionEngine) -> None:
        """不等操作符 ne 正确求值。"""
        condition = {"field": "material_code", "ne": "MAT-001"}
        context: dict[str, object] = {"material_code": "MAT-002"}
        assert engine.matches(condition, context) is True

    def test_condition_in(self, engine: ConditionEngine) -> None:
        """包含操作符 in 正确求值。"""
        condition = {
            "field": "material_code",
            "in": ["MAT-001", "MAT-002"],
        }
        context: dict[str, object] = {"material_code": "MAT-001"}
        assert engine.matches(condition, context) is True

    def test_condition_or(self, engine: ConditionEngine) -> None:
        """或操作符 or 正确求值。"""
        condition = {
            "or": [
                {"field": "temperature_c", "gt": 30},
                {"field": "temperature_c", "lt": 10},
            ]
        }
        context: dict[str, object] = {"temperature_c": 5}
        assert engine.matches(condition, context) is True

    def test_condition_rejects_non_whitelisted_field(self, engine: ConditionEngine) -> None:
        """非白名单字段被拒绝。"""
        condition = {"field": "evil_sql", "eq": "x"}
        with pytest.raises(AppError) as exc_info:
            engine.matches(condition, {})
        assert exc_info.value.code == "validation_failed"

    def test_condition_rejects_unknown_operator(self, engine: ConditionEngine) -> None:
        """未知操作符被拒绝。"""
        condition = {"field": "temperature_c", "xxx": 20}
        with pytest.raises(AppError) as exc_info:
            engine.matches(condition, {})
        assert exc_info.value.code == "validation_failed"

    def test_condition_nested_and_or(self, engine: ConditionEngine) -> None:
        """嵌套 AND/OR 组合正确求值。

        {"and": [
            {"or": [
                {"field": "temperature_c", "gt": 30},
                {"field": "temperature_c", "lt": 10},
            ]},
            {"field": "humidity_pct", "gte": 50},
        ]}
        with {"temperature_c": 5, "humidity_pct": 60} → True
        with {"temperature_c": 20, "humidity_pct": 60} → False
        with {"temperature_c": 5, "humidity_pct": 30} → False
        """
        condition = {
            "and": [
                {
                    "or": [
                        {"field": "temperature_c", "gt": 30},
                        {"field": "temperature_c", "lt": 10},
                    ]
                },
                {"field": "humidity_pct", "gte": 50},
            ]
        }
        ctx1: dict[str, object] = {"temperature_c": 5, "humidity_pct": 60}
        assert engine.matches(condition, ctx1) is True

        ctx2: dict[str, object] = {"temperature_c": 20, "humidity_pct": 60}
        assert engine.matches(condition, ctx2) is False

        ctx3: dict[str, object] = {"temperature_c": 5, "humidity_pct": 30}
        assert engine.matches(condition, ctx3) is False

    def test_condition_lt_lte_gt_gte(self, engine: ConditionEngine) -> None:
        """数值比较操作符 lt/lte/gt/gte 正确求值。"""
        assert (
            engine.matches(
                {"field": "temperature_c", "lt": 20},
                {"temperature_c": 10},
            )
            is True
        )
        assert (
            engine.matches(
                {"field": "temperature_c", "lt": 10},
                {"temperature_c": 10},
            )
            is False
        )
        assert (
            engine.matches(
                {"field": "temperature_c", "lte": 10},
                {"temperature_c": 10},
            )
            is True
        )
        assert (
            engine.matches(
                {"field": "temperature_c", "gt": 10},
                {"temperature_c": 20},
            )
            is True
        )
        assert (
            engine.matches(
                {"field": "temperature_c", "gt": 20},
                {"temperature_c": 20},
            )
            is False
        )
        assert (
            engine.matches(
                {"field": "temperature_c", "gte": 20},
                {"temperature_c": 20},
            )
            is True
        )

    def test_condition_in_not_matching(self, engine: ConditionEngine) -> None:
        """in 操作符不匹配时返回 False。"""
        condition = {
            "field": "material_code",
            "in": ["MAT-001", "MAT-002"],
        }
        context: dict[str, object] = {"material_code": "MAT-003"}
        assert engine.matches(condition, context) is False

    def test_condition_field_not_in_context(self, engine: ConditionEngine) -> None:
        """上下文中缺少字段时返回 False。"""
        condition = {"field": "temperature_c", "gte": 20}
        context: dict[str, object] = {}
        assert engine.matches(condition, context) is False
