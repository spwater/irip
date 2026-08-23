"""Bootstrap 置信区间组件。

使用 Bootstrap 重采样法计算统计量的置信区间（固定种子保证可重复性）。

参数：
- observations: 输入 ObservationTable（必填）。
- column: 数值列名（必填）。
- statistic: 统计量类型（mean/median/std，可选，默认 mean）。
- confidence: 置信水平（可选，默认 0.95）。
- iterations: 重采样次数（可选，默认 10000）。
- seed: 随机种子（可选，默认 42）。
"""

import random
import statistics
from typing import Any

from packages.components.builtin.types import (
    DiagnosticReport,
    ObservationTable,
)
from packages.components.sdk import ComponentContext, ComponentResult

#: 默认参数。
_DEFAULT_CONFIDENCE: float = 0.95
_DEFAULT_ITERATIONS: int = 10000
_DEFAULT_SEED: int = 42

#: 支持的统计量函数。
_STATISTICS: dict[str, Any] = {
    "mean": statistics.mean,
    "median": statistics.median,
    "std": statistics.stdev,
    "var": statistics.variance,
    "min": min,
    "max": max,
}


class BootstrapInterval:
    """Bootstrap 置信区间组件（固定种子）。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """计算 Bootstrap 置信区间。"""
        table: ObservationTable = params["observations"]
        column: str = params["column"]
        statistic: str = params.get("statistic", "mean")
        confidence: float = float(params.get("confidence", _DEFAULT_CONFIDENCE))
        iterations: int = int(params.get("iterations", _DEFAULT_ITERATIONS))
        seed: int = int(params.get("seed", _DEFAULT_SEED))

        warnings: list[str] = []

        if statistic not in _STATISTICS:
            return ComponentResult(
                outputs={},
                summary=f"不支持的统计量: {statistic}",
                metadata={},
                diagnostics={"warnings": [f"unsupported_statistic:{statistic}"]},
            )

        # 提取数值
        values: list[float] = []
        for row in table.rows:
            val = row.get(column)
            if val is not None and isinstance(val, (int, float)):
                values.append(float(val))

        if len(values) < 2:
            warnings.append("有效数据不足 2 个，无法 Bootstrap")
            report = DiagnosticReport(
                component="bootstrap_interval",
                input_rows=table.row_count(),
                output_rows=0,
                warnings=tuple(warnings),
                row_annotations=(),
            )
            return ComponentResult(
                outputs={"intervals": {}, "diagnostics": report},
                summary="数据不足",
                metadata={"value_count": len(values)},
            )

        stat_func = _STATISTICS[statistic]
        n = len(values)

        # 固定种子重采样
        rng = random.Random(seed)
        boot_stats: list[float] = []
        for _ in range(iterations):
            sample = [rng.choice(values) for _ in range(n)]
            if statistic == "std" and len(sample) < 2:
                continue
            try:
                boot_stats.append(stat_func(sample))
            except statistics.StatisticsError:
                continue

        if not boot_stats:
            warnings.append("Bootstrap 采样失败")
            report = DiagnosticReport(
                component="bootstrap_interval",
                input_rows=table.row_count(),
                output_rows=0,
                warnings=tuple(warnings),
                row_annotations=(),
            )
            return ComponentResult(
                outputs={"intervals": {}, "diagnostics": report},
                summary="Bootstrap 失败",
                metadata={},
            )

        boot_stats.sort()

        # 计算置信区间
        alpha = 1.0 - confidence
        lower_idx = int(alpha / 2 * len(boot_stats))
        upper_idx = int((1 - alpha / 2) * len(boot_stats)) - 1

        point_estimate = stat_func(values)
        lower = boot_stats[lower_idx]
        upper = boot_stats[upper_idx]

        intervals = {
            column: {
                "statistic": statistic,
                "point_estimate": point_estimate,
                "lower": lower,
                "upper": upper,
                "confidence": confidence,
                "iterations": len(boot_stats),
                "seed": seed,
                "bootstrap_mean": statistics.mean(boot_stats),
                "bootstrap_std": (statistics.stdev(boot_stats) if len(boot_stats) > 1 else 0.0),
            }
        }

        report = DiagnosticReport(
            component="bootstrap_interval",
            input_rows=table.row_count(),
            output_rows=1,
            warnings=tuple(warnings),
            row_annotations=(),
        )
        return ComponentResult(
            outputs={"intervals": intervals, "diagnostics": report},
            summary=(
                f"Bootstrap {statistic} 置信区间 "
                f"[{lower:.4f}, {upper:.4f}]"
                f"（{confidence * 100:.0f}%）"
            ),
            metadata={
                "column": column,
                "statistic": statistic,
                "confidence": confidence,
                "iterations": len(boot_stats),
                "seed": seed,
            },
        )
