"""值域边界检查组件。

校验数值列是否在指定边界范围内。

参数：
- observations: 输入 ObservationTable（必填）。
- rules: 检查规则列表，每项含 column, min, max（可选 min/max）。
- inclusive: 边界是否包含（可选，默认 True）。
"""

from typing import Any

from packages.components.builtin.types import (
    DiagnosticReport,
    ObservationTable,
)
from packages.components.sdk import ComponentContext, ComponentResult


class RangeCheck:
    """值域边界检查组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行值域边界检查。"""
        table: ObservationTable = params["observations"]
        rules: list[dict[str, Any]] = params["rules"]
        inclusive: bool = params.get("inclusive", True)

        warnings: list[str] = []
        row_annotations: list[dict[str, Any]] = []
        fail_count = 0

        for idx, row in enumerate(table.rows):
            row_failures: list[str] = []
            for rule in rules:
                col = rule["column"]
                min_val = rule.get("min")
                max_val = rule.get("max")
                val = row.get(col)

                if val is None or not isinstance(val, (int, float)):
                    continue

                if min_val is not None:
                    if inclusive:
                        if val < min_val:
                            row_failures.append(f"{col}:below_min({val}<{min_val})")
                    else:
                        if val <= min_val:
                            row_failures.append(f"{col}:at_or_below_min({val}<={min_val})")

                if max_val is not None:
                    if inclusive:
                        if val > max_val:
                            row_failures.append(f"{col}:above_max({val}>{max_val})")
                    else:
                        if val >= max_val:
                            row_failures.append(f"{col}:at_or_above_max({val}>={max_val})")

            if row_failures:
                fail_count += 1
                row_annotations.append(
                    {
                        "row_index": idx,
                        "status": "fail",
                        "detail": ";".join(row_failures),
                    }
                )

        report = DiagnosticReport(
            component="range_check",
            input_rows=table.row_count(),
            output_rows=table.row_count(),
            warnings=tuple(warnings),
            row_annotations=tuple(row_annotations),
        )
        return ComponentResult(
            outputs={
                "observations": table,
                "diagnostics": report,
            },
            summary=f"值域检查: {table.row_count() - fail_count} 通过，{fail_count} 失败",
            metadata={
                "pass_count": table.row_count() - fail_count,
                "fail_count": fail_count,
                "rules_count": len(rules),
            },
        )
