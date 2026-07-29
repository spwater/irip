"""时间对齐组件。

将时间序列数据按指定频率对齐到规则时间网格。

参数：
- observations: 输入 ObservationTable（必填）。
- time_column: 时间戳列名（必填）。
- frequency: 对齐频率（如 '1s'/'1min'/'5min'/'1h'，必填）。
- method: 对齐方法（nearest/ffill/bfill，可选，默认 nearest）。
- value_columns: 需对齐的数值列（可选，默认除时间列外全部）。
"""

from typing import Any

import pandas as pd

from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult

#: 频率别名映射。
_FREQ_ALIASES: dict[str, str] = {
    "1s": "1s",
    "s": "1s",
    "1min": "1min",
    "min": "1min",
    "5min": "5min",
    "1h": "1h",
    "h": "1h",
    "1D": "1D",
    "D": "1D",
    "1d": "1D",
    "d": "1D",
}


class TimeAlignment:
    """时间戳对齐组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行时间对齐。"""
        table: ObservationTable = params["observations"]
        time_column: str = params["time_column"]
        frequency: str = params["frequency"]
        method: str = params.get("method", "nearest")
        value_columns: list[str] | None = params.get("value_columns")

        if not table.rows:
            return ComponentResult(
                outputs={"observations": table},
                summary="输入为空，跳过时间对齐",
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

        freq = _FREQ_ALIASES.get(frequency, frequency)

        if value_columns is None:
            value_columns = [c for c in df.columns if c != time_column]

        # 按频率重采样对齐
        resampled = df[value_columns].resample(freq)
        if method == "ffill":
            aligned = resampled.ffill()
        elif method == "bfill":
            aligned = resampled.bfill()
        else:
            aligned = resampled.interpolate(method="nearest")

        aligned = aligned.dropna(how="all").reset_index()

        new_columns: tuple[str, ...] = tuple(aligned.columns)
        new_rows: list[dict[str, Any]] = []
        for _, row in aligned.iterrows():
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
            summary=f"时间对齐（{freq}/{method}）: {result_table.row_count()} 行",
            metadata={
                "frequency": freq,
                "method": method,
                "row_count": result_table.row_count(),
            },
        )
