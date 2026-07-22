"""描述性统计组件。

计算数值列的描述性统计量：均值、标准差、最小值、最大值、
中位数、四分位数、偏度、峰度。

参数：
- observations: 输入 ObservationTable（必填）。
- columns: 数值列名列表（必填）。
"""

import math
import statistics
from typing import Any

from packages.components.builtin.types import (
    DiagnosticReport,
    ObservationTable,
)
from packages.components.sdk import ComponentContext, ComponentResult


class Descriptive:
    """描述性统计组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """计算描述性统计量。"""
        table: ObservationTable = params["observations"]
        columns: list[str] = params["columns"]

        warnings: list[str] = []
        stats_result: dict[str, dict[str, float]] = {}

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
            mean = statistics.mean(values)
            std = statistics.stdev(values) if n > 1 else 0.0
            minimum = min(values)
            maximum = max(values)
            median = statistics.median(values)
            q1 = statistics.quantiles(values, n=4)[0] if n >= 4 else minimum
            q3 = statistics.quantiles(values, n=4)[2] if n >= 4 else maximum

            # 偏度与峰度
            if n > 2 and std > 0:
                skew = (
                    sum((x - mean) ** 3 for x in values)
                    / (n * std ** 3)
                )
                kurt = (
                    sum((x - mean) ** 4 for x in values)
                    / (n * std ** 4)
                    - 3.0
                )
            else:
                skew = 0.0
                kurt = 0.0

            stats_result[col] = {
                "count": n,
                "mean": mean,
                "std": std,
                "min": minimum,
                "max": maximum,
                "median": median,
                "q1": q1,
                "q3": q3,
                "iqr": q3 - q1,
                "skewness": skew,
                "kurtosis": kurt,
            }

        report = DiagnosticReport(
            component="descriptive",
            input_rows=table.row_count(),
            output_rows=len(stats_result),
            warnings=tuple(warnings),
            row_annotations=(),
        )
        return ComponentResult(
            outputs={
                "statistics": stats_result,
                "diagnostics": report,
            },
            summary=f"描述性统计: {len(stats_result)} 列",
            metadata={
                "columns_analyzed": len(stats_result),
                "total_rows": table.row_count(),
            },
        )
