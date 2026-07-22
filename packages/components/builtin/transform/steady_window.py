"""稳态窗口识别组件。

识别时间序列中的稳态窗口（连续区间内值波动小于容差）。

参数：
- observations: 输入 ObservationTable（必填）。
- value_column: 数值列名（必填）。
- window_size: 窗口大小（点数，必填）。
- tolerance: 稳态判定容差（最大标准差或最大极差，必填）。
- min_duration: 稳态窗口最小持续点数（可选，默认 window_size）。
- metric: 稳态判定指标（std/range，可选，默认 std）。
"""

import statistics
from typing import Any

from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult

#: 支持的稳态判定指标。
_METRICS: frozenset[str] = frozenset({"std", "range"})


class SteadyWindow:
    """稳态窗口识别组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """识别稳态窗口。"""
        table: ObservationTable = params["observations"]
        value_column: str = params["value_column"]
        window_size: int = int(params["window_size"])
        tolerance: float = float(params["tolerance"])
        min_duration: int = int(params.get("min_duration", window_size))
        metric: str = params.get("metric", "std")

        if metric not in _METRICS:
            return ComponentResult(
                outputs={"windows": [], "observations": table},
                summary=f"不支持的指标: {metric}",
                metadata={"window_count": 0},
                diagnostics={"warnings": [f"unsupported_metric:{metric}"]},
            )

        values: list[float] = []
        for row in table.rows:
            val = row.get(value_column)
            if val is not None and isinstance(val, (int, float)):
                values.append(float(val))

        if len(values) < window_size:
            return ComponentResult(
                outputs={"windows": [], "observations": table},
                summary="数据不足，无法识别稳态窗口",
                metadata={"window_count": 0},
            )

        windows: list[dict[str, Any]] = []
        n = len(values)
        i = 0
        while i + window_size <= n:
            window_vals = values[i : i + window_size]
            if metric == "std":
                deviation = statistics.stdev(window_vals) if len(window_vals) > 1 else 0.0
            else:  # range
                deviation = max(window_vals) - min(window_vals)

            if deviation <= tolerance:
                # 扩展窗口
                end = i + window_size
                while end < n:
                    extended = values[i : end + 1]
                    if metric == "std":
                        deviation_ext = statistics.stdev(extended) if len(extended) > 1 else 0.0
                    else:
                        deviation_ext = max(extended) - min(extended)
                    if deviation_ext > tolerance:
                        break
                    end += 1

                duration = end - i
                if duration >= min_duration:
                    window_vals_final = values[i:end]
                    windows.append({
                        "start_index": i,
                        "end_index": end - 1,
                        "start_row": i,
                        "end_row": end - 1,
                        "duration": duration,
                        "mean": statistics.mean(window_vals_final),
                        "std": (
                            statistics.stdev(window_vals_final)
                            if len(window_vals_final) > 1
                            else 0.0
                        ),
                        "min": min(window_vals_final),
                        "max": max(window_vals_final),
                    })
                i = end
            else:
                i += 1

        return ComponentResult(
            outputs={
                "windows": tuple(windows),
                "observations": table,
            },
            summary=f"识别 {len(windows)} 个稳态窗口（容差 {tolerance}）",
            metadata={
                "window_count": len(windows),
                "total_points": n,
                "metric": metric,
                "tolerance": tolerance,
            },
        )
