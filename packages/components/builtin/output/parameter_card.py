"""L3 参数候选生成组件。

从观测数据中生成 L3 参数候选列表（ParameterCandidate）。

参数：
- observations: 输入 ObservationTable（必填）。
- variable_code: 变量代码（必填）。
- value_column: 值列名（必填）。
- unit_column: 单位列名（可选）。
- confidence: 默认置信度（可选，默认 0.8）。
- exclusion_rules: 排除规则列表（可选，每项含 column, operator, value）。
"""

from typing import Any

from packages.components.builtin.types import (
    DiagnosticReport,
    ObservationTable,
    ParameterCandidate,
)
from packages.components.sdk import ComponentContext, ComponentResult

#: 支持的排除操作符。
_OPERATORS: dict[str, Any] = {
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
}


class ParameterCard:
    """L3 参数候选生成组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """生成参数候选。"""
        table: ObservationTable = params["observations"]
        variable_code: str = params["variable_code"]
        value_column: str = params["value_column"]
        unit_column: str | None = params.get("unit_column")
        default_confidence: float = float(params.get("confidence", 0.8))
        exclusion_rules: list[dict[str, Any]] = params.get("exclusion_rules", [])

        warnings: list[str] = []
        candidates: list[ParameterCandidate] = []
        row_annotations: list[dict[str, Any]] = []

        for idx, row in enumerate(table.rows):
            value = row.get(value_column)
            unit = row.get(unit_column) if unit_column else None

            if value is None:
                row_annotations.append(
                    {
                        "row_index": idx,
                        "status": "skip",
                        "detail": "value_missing",
                    }
                )
                continue

            # 检查排除规则
            exclusion_reasons: list[str] = []
            for rule in exclusion_rules:
                col = rule["column"]
                op = rule.get("operator", "eq")
                rule_val = rule.get("value")
                cell_val = row.get(col)

                if cell_val is None:
                    continue

                if op in _OPERATORS:
                    try:
                        if _OPERATORS[op](cell_val, rule_val):
                            exclusion_reasons.append(f"{col}_{op}_{rule_val}")
                    except TypeError:
                        pass

            candidate = ParameterCandidate(
                variable_code=variable_code,
                value=str(value),
                unit=str(unit) if unit else None,
                confidence=default_confidence if not exclusion_reasons else 0.0,
                exclusion_reasons=tuple(exclusion_reasons),
            )
            candidates.append(candidate)

            row_annotations.append(
                {
                    "row_index": idx,
                    "status": "excluded" if exclusion_reasons else "active",
                    "detail": ";".join(exclusion_reasons) if exclusion_reasons else "",
                }
            )

        active_count = sum(1 for c in candidates if not c.exclusion_reasons)
        excluded_count = len(candidates) - active_count

        report = DiagnosticReport(
            component="parameter_card",
            input_rows=table.row_count(),
            output_rows=len(candidates),
            warnings=tuple(warnings),
            row_annotations=tuple(row_annotations),
        )
        return ComponentResult(
            outputs={
                "candidates": tuple(candidates),
                "diagnostics": report,
            },
            summary=f"参数候选: {active_count} 个有效，{excluded_count} 个排除",
            metadata={
                "variable_code": variable_code,
                "total_candidates": len(candidates),
                "active_candidates": active_count,
                "excluded_candidates": excluded_count,
            },
        )
