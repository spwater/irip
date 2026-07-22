"""Schema 检查组件。

校验观测表的字段类型与约束。

参数：
- observations: 输入 ObservationTable（必填）。
- schema: 字段约束字典，键为列名，值为约束对象
  （{type: "number"|"string"|"datetime", required: bool, min/max/enum}）。
"""

from typing import Any

from packages.components.builtin.types import (
    DiagnosticReport,
    ObservationTable,
)
from packages.components.sdk import ComponentContext, ComponentResult

#: 支持的类型校验器。
_TYPE_VALIDATORS: dict[str, Any] = {
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "datetime": lambda v: isinstance(v, str),
}


class SchemaCheck:
    """字段类型与约束检查组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行 schema 检查。"""
        table: ObservationTable = params["observations"]
        schema: dict[str, dict[str, Any]] = params["schema"]

        warnings: list[str] = []
        row_annotations: list[dict[str, Any]] = []
        fail_count = 0

        # 检查列存在性
        for col, constraints in schema.items():
            if col not in table.columns:
                if constraints.get("required", True):
                    warnings.append(f"必需列缺失: {col}")
                continue

        # 逐行校验
        for idx, row in enumerate(table.rows):
            row_failures: list[str] = []
            for col, constraints in schema.items():
                if col not in table.columns:
                    continue

                val = row.get(col)
                required = constraints.get("required", True)
                expected_type = constraints.get("type")

                # required 检查
                if val is None or val == "":
                    if required:
                        row_failures.append(f"{col}:missing")
                    continue

                # type 检查
                if expected_type and expected_type in _TYPE_VALIDATORS:
                    if not _TYPE_VALIDATORS[expected_type](val):
                        row_failures.append(f"{col}:type_mismatch")
                        continue

                # range 检查
                if expected_type in ("number", "integer"):
                    min_val = constraints.get("min")
                    max_val = constraints.get("max")
                    if min_val is not None and isinstance(val, (int, float)):
                        if val < min_val:
                            row_failures.append(f"{col}:below_min")
                    if max_val is not None and isinstance(val, (int, float)):
                        if val > max_val:
                            row_failures.append(f"{col}:above_max")

                # enum 检查
                enum_vals = constraints.get("enum")
                if enum_vals is not None and val not in enum_vals:
                    row_failures.append(f"{col}:not_in_enum")

            if row_failures:
                fail_count += 1
                row_annotations.append({
                    "row_index": idx,
                    "status": "fail",
                    "detail": ";".join(row_failures),
                })

        report = DiagnosticReport(
            component="schema_check",
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
            summary=f"Schema 检查: {table.row_count() - fail_count} 通过，{fail_count} 失败",
            metadata={
                "pass_count": table.row_count() - fail_count,
                "fail_count": fail_count,
                "columns_checked": len(schema),
            },
        )
