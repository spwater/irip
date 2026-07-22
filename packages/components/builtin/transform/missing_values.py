"""缺失值处理组件。

按策略处理缺失值：reject（删除含缺失的行）/ null（填充 None）/
forward_fill（前向填充）/ constant（填充常量）。

参数：
- observations: 输入 ObservationTable（必填）。
- strategy: 处理策略（reject/null/forward_fill/constant，必填）。
- columns: 待处理的列名列表（可选，默认全部列）。
- fill_value: constant 策略的填充值（可选）。
"""

from typing import Any

from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult


class MissingValues:
    """缺失值处理组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行缺失值处理。"""
        table: ObservationTable = params["observations"]
        strategy: str = params["strategy"]
        columns: list[str] | None = params.get("columns")
        fill_value: Any = params.get("fill_value")

        target_cols = columns if columns else list(table.columns)
        warnings: list[str] = []

        if strategy == "reject":
            new_rows = [
                row for row in table.rows
                if all(
                    row.get(col) is not None and row.get(col) != ""
                    for col in target_cols
                )
            ]
            removed = table.row_count() - len(new_rows)
            if removed > 0:
                warnings.append(f"删除 {removed} 行含缺失值")
        elif strategy == "null":
            new_rows = []
            for row in table.rows:
                new_row = dict(row)
                for col in target_cols:
                    if new_row.get(col) is None or new_row.get(col) == "":
                        new_row[col] = None
                new_rows.append(new_row)
        elif strategy == "forward_fill":
            new_rows = []
            last_values: dict[str, Any] = {}
            for row in table.rows:
                new_row = dict(row)
                for col in target_cols:
                    val = new_row.get(col)
                    if val is None or val == "":
                        if col in last_values:
                            new_row[col] = last_values[col]
                    else:
                        last_values[col] = val
                new_rows.append(new_row)
        elif strategy == "constant":
            if fill_value is None:
                fill_value = 0
            new_rows = []
            for row in table.rows:
                new_row = dict(row)
                for col in target_cols:
                    if new_row.get(col) is None or new_row.get(col) == "":
                        new_row[col] = fill_value
                new_rows.append(new_row)
        else:
            warnings.append(f"未知策略: {strategy}")
            new_rows = list(table.rows)

        result_table = ObservationTable(
            columns=table.columns,
            rows=tuple(new_rows),
            source_locations=table.source_locations,
        )
        return ComponentResult(
            outputs={"observations": result_table},
            summary=f"缺失值处理（{strategy}）: 输入 {table.row_count()} 行 → 输出 {result_table.row_count()} 行",
            metadata={
                "strategy": strategy,
                "input_rows": table.row_count(),
                "output_rows": result_table.row_count(),
            },
            diagnostics={"warnings": warnings} if warnings else None,
        )
