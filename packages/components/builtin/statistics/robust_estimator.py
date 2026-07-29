"""稳健估计器组件。

计算中位数、MAD、IQR、截尾均值等对异常值不敏感的统计量。

参数：
- observations: 输入 ObservationTable（必填）。
- columns: 数值列名列表（必填）。
- trim_percent: 截尾均值截尾比例（可选，默认 0.1，即各截 10%）。
"""

import statistics
from typing import Any

from packages.components.builtin.types import (
    DiagnosticReport,
    ObservationTable,
)
from packages.components.sdk import ComponentContext, ComponentResult

#: MAD 一致性常数。
_MAD_CONSTANT: float = 1.4826


class RobustEstimator:
    """稳健估计器组件（中位数/MAD）。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """计算稳健统计量。"""
        table: ObservationTable = params["observations"]
        columns: list[str] = params["columns"]
        trim_percent: float = float(params.get("trim_percent", 0.1))

        warnings: list[str] = []
        robust_result: dict[str, dict[str, float]] = {}

        for col in columns:
            values: list[float] = []
            for row in table.rows:
                val = row.get(col)
                if val is not None and isinstance(val, (int, float)):
                    values.append(float(val))

            if not values:
                warnings.append(f"列 {col} 无有效数值")
                continue

            n = len(values)
            sorted_vals = sorted(values)
            median = statistics.median(values)
            abs_devs = [abs(v - median) for v in values]
            mad = statistics.median(abs_devs)
            mad_scaled = _MAD_CONSTANT * mad

            q = (
                statistics.quantiles(sorted_vals, n=4)
                if n >= 4
                else [sorted_vals[0], median, sorted_vals[-1]]
            )
            iqr = q[2] - q[0]

            # 截尾均值
            trim_count = int(n * trim_percent)
            if trim_count > 0 and n > 2 * trim_count:
                trimmed = sorted_vals[trim_count : n - trim_count]
                trimmed_mean = statistics.mean(trimmed)
            else:
                trimmed_mean = statistics.mean(values)

            robust_result[col] = {
                "median": median,
                "mad": mad,
                "mad_scaled": mad_scaled,
                "iqr": iqr,
                "q1": q[0],
                "q3": q[2],
                "trimmed_mean": trimmed_mean,
                "min": sorted_vals[0],
                "max": sorted_vals[-1],
                "count": n,
            }

        report = DiagnosticReport(
            component="robust_estimator",
            input_rows=table.row_count(),
            output_rows=len(robust_result),
            warnings=tuple(warnings),
            row_annotations=(),
        )
        return ComponentResult(
            outputs={
                "statistics": robust_result,
                "diagnostics": report,
            },
            summary=f"稳健估计: {len(robust_result)} 列",
            metadata={
                "columns_analyzed": len(robust_result),
                "trim_percent": trim_percent,
            },
        )
