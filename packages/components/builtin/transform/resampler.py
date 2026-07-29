"""时间序列重采样组件。

按指定频率对时间序列进行聚合重采样。

参数：
- observations: 输入 ObservationTable（必填）。
- time_column: 时间戳列名（必填）。
- frequency: 重采样频率（如 '1min'/'5min'/'1h'，必填）。
- aggregation: 聚合方法（mean/sum/max/min/median/count/std，必填）。
- value_columns: 需聚合的数值列（可选，默认除时间列外全部数值列）。
"""

from typing import Any

import pandas as pd

from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult

#: 支持的聚合方法。
_AGG_METHODS: frozenset[str] = frozenset(
    {
        "mean",
        "sum",
        "max",
        "min",
        "median",
        "count",
        "std",
        "var",
    }
)


class Resampler:
    """时间序列重采样组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行时间序列重采样。"""
        table: ObservationTable = params["observations"]
        time_column: str = params["time_column"]
        frequency: str = params["frequency"]
        aggregation: str = params["aggregation"]
        value_columns: list[str] | None = params.get("value_columns")

        if aggregation not in _AGG_METHODS:
            return ComponentResult(
                outputs={"observations": table},
                summary=f"不支持的聚合方法: {aggregation}",
                metadata={"row_count": table.row_count()},
                diagnostics={"warnings": [f"unsupported_aggregation:{aggregation}"]},
            )

        if not table.rows:
            return ComponentResult(
                outputs={"observations": table},
                summary="输入为空",
                metadata={"row_count": 0},
            )

        df = pd.DataFrame(list(table.rows))
        if time_column not in df.columns:
            return ComponentResult(
                outputs={"observations": table},
                summary=f"时间列 {time_column} 不存在",
                metadata={"row_count": table.row_count()},
                diagnostics={"warnings": [f"column_not_found:{time_column}"]},
            )

        df[time_column] = pd.to_datetime(df[time_column], errors="coerce")
        df = df.dropna(subset=[time_column]).sort_values(time_column)
        df = df.set_index(time_column)

        if value_columns is None:
            value_columns = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]

        agg_df = df[value_columns].resample(frequency).agg(aggregation)
        agg_df = agg_df.dropna(how="all").reset_index()

        new_columns: tuple[str, ...] = tuple(agg_df.columns)
        new_rows: list[dict[str, Any]] = []
        for _, row in agg_df.iterrows():
            record: dict[str, Any] = {}
            for col in new_columns:
                val = row[col]
                if isinstance(val, pd.Timestamp):
                    record[col] = val.isoformat()
                elif pd.isna(val):
                    record[col] = None
                else:
                    record[col] = val.item() if hasattr(val, "item") else val
            new_rows.append(record)

        result_table = ObservationTable(
            columns=new_columns,
            rows=tuple(new_rows),
            source_locations=table.source_locations,
        )
        return ComponentResult(
            outputs={"observations": result_table},
            summary=f"重采样（{frequency}/{aggregation}）: {result_table.row_count()} 行",
            metadata={
                "frequency": frequency,
                "aggregation": aggregation,
                "row_count": result_table.row_count(),
            },
        )
