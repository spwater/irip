"""引用完整性检查组件。

校验子表的每行外键值在父表的主键集合中存在。

参数：
- observations: 输入子表 ObservationTable（必填）。
- foreign_key: 子表外键列名（必填）。
- parent_table: 父表 ObservationTable（必填）。
- parent_key: 父表主键列名（必填）。
"""

from typing import Any

from packages.components.builtin.types import (
    DiagnosticReport,
    ObservationTable,
)
from packages.components.sdk import ComponentContext, ComponentResult


class RelationCompleteness:
    """引用完整性检查组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行引用完整性检查。"""
        table: ObservationTable = params["observations"]
        foreign_key: str = params["foreign_key"]
        parent_table: ObservationTable = params["parent_table"]
        parent_key: str = params["parent_key"]

        warnings: list[str] = []
        row_annotations: list[dict[str, Any]] = []

        if foreign_key not in table.columns:
            warnings.append(f"子表缺少外键列: {foreign_key}")
        if parent_key not in parent_table.columns:
            warnings.append(f"父表缺少主键列: {parent_key}")

        # 构建父表主键集合
        parent_keys: set[Any] = set()
        for row in parent_table.rows:
            val = row.get(parent_key)
            if val is not None:
                parent_keys.add(val)

        fail_count = 0
        for idx, row in enumerate(table.rows):
            fk_val = row.get(foreign_key)
            if fk_val is None:
                row_annotations.append(
                    {
                        "row_index": idx,
                        "status": "fail",
                        "detail": f"foreign_key_null:{foreign_key}",
                    }
                )
                fail_count += 1
            elif fk_val not in parent_keys:
                row_annotations.append(
                    {
                        "row_index": idx,
                        "status": "fail",
                        "detail": f"foreign_key_not_found:{fk_val}",
                        "value": fk_val,
                    }
                )
                fail_count += 1

        report = DiagnosticReport(
            component="relation_completeness",
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
            summary=f"引用完整性: {table.row_count() - fail_count} 通过，{fail_count} 失败",
            metadata={
                "pass_count": table.row_count() - fail_count,
                "fail_count": fail_count,
                "parent_keys": len(parent_keys),
            },
        )
