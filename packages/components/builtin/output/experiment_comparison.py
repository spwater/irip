"""多实验对照表组件。

将多个实验的观测数据并排对照，生成对照表。

参数：
- experiments: 实验列表，每项含 label 和 observations (ObservationTable)（必填）。
- key_column: 用于对齐实验的键列名（必填）。
- value_columns: 需对照的数值列名列表（必填）。
"""

from typing import Any

from tabulate import tabulate

from packages.components.builtin.types import (
    DiagnosticReport,
    ObservationTable,
)
from packages.components.sdk import ComponentContext, ComponentResult


class ExperimentComparison:
    """多实验对照表组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """生成多实验对照表。"""
        experiments: list[dict[str, Any]] = params["experiments"]
        key_column: str = params["key_column"]
        value_columns: list[str] = params["value_columns"]

        warnings: list[str] = []

        if not experiments:
            return ComponentResult(
                outputs={"comparison_table": [], "diagnostics": DiagnosticReport(
                    component="experiment_comparison",
                )},
                summary="无实验数据",
                metadata={"experiment_count": 0},
            )

        # 收集所有键值
        all_keys: list[Any] = []
        key_set: set[Any] = set()
        for exp in experiments:
            table: ObservationTable = exp["observations"]
            for row in table.rows:
                key_val = row.get(key_column)
                if key_val is not None and key_val not in key_set:
                    key_set.add(key_val)
                    all_keys.append(key_val)

        # 构建对照行
        labels = [exp["label"] for exp in experiments]
        comparison_rows: list[dict[str, Any]] = []

        for key_val in all_keys:
            row_data: dict[str, Any] = {key_column: key_val}
            for exp in experiments:
                label = exp["label"]
                exp_table: ObservationTable = exp["observations"]
                # 查找匹配行
                matched: dict[str, Any] | None = None
                for r in exp_table.rows:
                    if r.get(key_column) == key_val:
                        matched = r
                        break

                for col in value_columns:
                    col_name = f"{label}.{col}"
                    if matched:
                        row_data[col_name] = matched.get(col)
                    else:
                        row_data[col_name] = None
            comparison_rows.append(row_data)

        # 生成对照列名
        comparison_columns: tuple[str, ...] = tuple(
            [key_column]
            + [
                f"{label}.{col}"
                for label in labels
                for col in value_columns
            ]
        )

        # 生成文本对照表
        table_data = [
            [row.get(col) for col in comparison_columns]
            for row in comparison_rows
        ]
        text_table = tabulate(
            table_data,
            headers=list(comparison_columns),
            tablefmt="pipe",
        )

        report = DiagnosticReport(
            component="experiment_comparison",
            input_rows=sum(
                len(exp["observations"].rows) for exp in experiments
            ),
            output_rows=len(comparison_rows),
            warnings=tuple(warnings),
            row_annotations=(),
        )
        return ComponentResult(
            outputs={
                "comparison_table": ObservationTable(
                    columns=comparison_columns,
                    rows=tuple(comparison_rows),
                    source_locations=(),
                ),
                "text_table": text_table,
                "diagnostics": report,
            },
            summary=f"实验对照: {len(labels)} 个实验，{len(comparison_rows)} 行对照",
            metadata={
                "experiment_count": len(labels),
                "experiment_labels": labels,
                "comparison_rows": len(comparison_rows),
            },
        )
