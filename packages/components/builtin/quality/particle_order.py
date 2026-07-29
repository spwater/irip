"""粒度序检查组件。

校验粒度分布的有序性：D10 < D50 < D90（严格递增）。

参数：
- observations: 输入 ObservationTable（必填）。
- d10_column: D10 列名（必填）。
- d50_column: D50 列名（必填）。
- d90_column: D90 列名（必填）。
- strict: 是否严格小于（可选，默认 True，即 D10 < D50 < D90）。
"""

from typing import Any

from packages.components.builtin.types import (
    DiagnosticReport,
    ObservationTable,
)
from packages.components.sdk import ComponentContext, ComponentResult


class ParticleOrder:
    """粒度序检查组件（D10<D50<D90）。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行粒度序检查。"""
        table: ObservationTable = params["observations"]
        d10_col: str = params["d10_column"]
        d50_col: str = params["d50_column"]
        d90_col: str = params["d90_column"]
        strict: bool = params.get("strict", True)

        warnings: list[str] = []
        row_annotations: list[dict[str, Any]] = []
        fail_count = 0

        for idx, row in enumerate(table.rows):
            d10 = row.get(d10_col)
            d50 = row.get(d50_col)
            d90 = row.get(d90_col)

            failures: list[str] = []

            if d10 is None or d50 is None or d90 is None:
                failures.append("missing_values")
            elif not all(isinstance(v, (int, float)) for v in (d10, d50, d90)):
                failures.append("non_numeric")
            else:
                if strict:
                    if not (d10 < d50):
                        failures.append(f"D10({d10})>=D50({d50})")
                    if not (d50 < d90):
                        failures.append(f"D50({d50})>=D90({d90})")
                else:
                    if not (d10 <= d50):
                        failures.append(f"D10({d10})>D50({d50})")
                    if not (d50 <= d90):
                        failures.append(f"D50({d50})>D90({d90})")

            if failures:
                fail_count += 1
                row_annotations.append(
                    {
                        "row_index": idx,
                        "status": "fail",
                        "detail": ";".join(failures),
                        "d10": d10,
                        "d50": d50,
                        "d90": d90,
                    }
                )

        report = DiagnosticReport(
            component="particle_order",
            input_rows=table.row_count(),
            output_rows=table.row_count(),
            warnings=tuple(warnings),
            row_annotations=tuple(row_annotations),
        )
        return ComponentResult(
            outputs={
                "observations": table,
                "diagnostics": report,
            },
            summary=f"粒度序检查: {table.row_count() - fail_count} 通过，{fail_count} 失败",
            metadata={
                "pass_count": table.row_count() - fail_count,
                "fail_count": fail_count,
                "strict": strict,
            },
        )
