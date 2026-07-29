"""MAD 异常值检测组件。

使用中位数绝对偏差（MAD）检测异常值，仅标记不删除。

判定规则：|x - median| / (1.4826 * MAD) > threshold 则标记为异常。

参数：
- observations: 输入 ObservationTable（必填）。
- columns: 待检测的数值列列表（必填）。
- threshold: MAD 倍数阈值（可选，默认 3.5）。
"""

import statistics
from typing import Any

from packages.components.builtin.types import (
    DiagnosticReport,
    ObservationTable,
)
from packages.components.sdk import ComponentContext, ComponentResult

#: MAD 一致性常数（正态分布下 1 MAD ≈ 0.6745 σ）。
_MAD_CONSTANT: float = 1.4826

#: 默认阈值。
_DEFAULT_THRESHOLD: float = 3.5


class MADOutliers:
    """MAD 异常值检测组件（标记不删除）。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行 MAD 异常值检测。"""
        table: ObservationTable = params["observations"]
        columns: list[str] = params["columns"]
        threshold: float = float(params.get("threshold", _DEFAULT_THRESHOLD))

        warnings: list[str] = []
        row_annotations: list[dict[str, Any]] = []
        outlier_count = 0

        for col in columns:
            values: list[float] = []
            for row in table.rows:
                val = row.get(col)
                if val is not None and isinstance(val, (int, float)):
                    values.append(float(val))

            if len(values) < 3:
                warnings.append(f"列 {col} 有效值不足 3 个，跳过")
                continue

            median = statistics.median(values)
            abs_devs = [abs(v - median) for v in values]
            mad = statistics.median(abs_devs)

            if mad == 0:
                warnings.append(f"列 {col} MAD=0，无法检测异常值")
                continue

            scaled_mad = _MAD_CONSTANT * mad

            # 标记异常行
            row_idx = 0
            for row in table.rows:
                val = row.get(col)
                if val is not None and isinstance(val, (int, float)):
                    z_score = abs(float(val) - median) / scaled_mad
                    if z_score > threshold:
                        outlier_count += 1
                        row_annotations.append(
                            {
                                "row_index": row_idx,
                                "column": col,
                                "value": float(val),
                                "median": median,
                                "mad": mad,
                                "z_score": z_score,
                                "status": "outlier",
                            }
                        )
                row_idx += 1

        # 输出表 = 原表 + 异常标记列
        new_columns = list(table.columns)
        for col in columns:
            flag_col = f"{col}_outlier"
            if flag_col not in new_columns:
                new_columns.append(flag_col)

        new_rows: list[dict[str, Any]] = []
        outlier_map: dict[int, set[str]] = {}
        for ann in row_annotations:
            outlier_map.setdefault(ann["row_index"], set()).add(ann["column"])

        for idx, row in enumerate(table.rows):
            new_row = dict(row)
            for col in columns:
                flag_col = f"{col}_outlier"
                new_row[flag_col] = idx in outlier_map and col in outlier_map[idx]
            new_rows.append(new_row)

        result_table = ObservationTable(
            columns=tuple(new_columns),
            rows=tuple(new_rows),
            source_locations=table.source_locations,
        )

        report = DiagnosticReport(
            component="mad_outliers",
            input_rows=table.row_count(),
            output_rows=result_table.row_count(),
            warnings=tuple(warnings),
            row_annotations=tuple(row_annotations),
        )
        return ComponentResult(
            outputs={
                "observations": result_table,
                "diagnostics": report,
            },
            summary=f"MAD 异常检测: {outlier_count} 个异常值（阈值 {threshold}）",
            metadata={
                "outlier_count": outlier_count,
                "columns_checked": len(columns),
                "threshold": threshold,
            },
        )
