"""单位转换组件。

批量进行数值字段的单位转换（如 mm→um、g→mg）。

参数：
- observations: 输入 ObservationTable（必填）。
- conversions: 转换规则列表，每项含 column, from_unit, to_unit, factor。
  factor 为 from→to 的乘数（如 mm→um factor=1000）。
"""

from typing import Any

from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult

#: 预定义单位转换因子（from_unit → {to_unit: factor}）。
_PREDEFINED_FACTORS: dict[str, dict[str, float]] = {
    "mm": {"um": 1000.0, "nm": 1_000_000.0, "cm": 0.1},
    "um": {"mm": 0.001, "nm": 1000.0},
    "cm": {"mm": 10.0, "m": 0.01},
    "m": {"cm": 100.0, "mm": 1000.0},
    "g": {"mg": 1000.0, "kg": 0.001},
    "mg": {"g": 0.001, "ug": 1000.0},
    "s": {"ms": 1000.0, "us": 1_000_000.0},
    "min": {"s": 60.0, "h": 1 / 60},
}


def _resolve_factor(from_unit: str, to_unit: str, factor: float | None) -> float:
    """解析转换因子，优先使用显式 factor，其次查预定义表。"""
    if factor is not None:
        return float(factor)
    table = _PREDEFINED_FACTORS.get(from_unit, {})
    if to_unit in table:
        return table[to_unit]
    if from_unit == to_unit:
        return 1.0
    raise ValueError(f"未知的单位转换: {from_unit} → {to_unit}")


class UnitConverter:
    """批量单位转换组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行批量单位转换。"""
        table: ObservationTable = params["observations"]
        conversions: list[dict[str, Any]] = params["conversions"]

        # 预计算各列的转换因子
        col_factors: dict[str, float] = {}
        warnings: list[str] = []
        for conv in conversions:
            col = conv["column"]
            from_unit = conv.get("from_unit", "")
            to_unit = conv.get("to_unit", "")
            factor = conv.get("factor")
            try:
                col_factors[col] = _resolve_factor(from_unit, to_unit, factor)
            except ValueError as exc:
                warnings.append(str(exc))

        new_rows: list[dict[str, Any]] = []
        for row in table.rows:
            new_row = dict(row)
            for col, factor in col_factors.items():
                val = new_row.get(col)
                if val is not None and isinstance(val, (int, float)):
                    new_row[col] = val * factor
            new_rows.append(new_row)

        result_table = ObservationTable(
            columns=table.columns,
            rows=tuple(new_rows),
            source_locations=table.source_locations,
        )
        return ComponentResult(
            outputs={"observations": result_table},
            summary=f"单位转换: {len(col_factors)} 列，输出 {result_table.row_count()} 行",
            metadata={
                "converted_columns": len(col_factors),
                "row_count": result_table.row_count(),
            },
            diagnostics={"warnings": warnings} if warnings else None,
        )
