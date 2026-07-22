"""字段映射组件。

将源字段名映射为目标字段名，支持字段重命名、选择与丢弃。

参数：
- observations: 输入 ObservationTable（必填，通过输入端口注入）。
- mapping: 源字段→目标字段映射字典（必填）。
- include_unmapped: 是否保留未映射字段（可选，默认 False）。
"""

from typing import Any

from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult


class FieldMapper:
    """源字段→目标字段映射组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行字段映射。"""
        table: ObservationTable = params["observations"]
        mapping: dict[str, str] = params["mapping"]
        include_unmapped: bool = params.get("include_unmapped", False)

        # 构建目标列名顺序
        target_columns: list[str] = []
        for src_col in table.columns:
            if src_col in mapping:
                target_columns.append(mapping[src_col])
            elif include_unmapped:
                target_columns.append(src_col)

        # 映射行数据
        new_rows: list[dict[str, Any]] = []
        for row in table.rows:
            new_row: dict[str, Any] = {}
            for src_col in table.columns:
                if src_col in mapping:
                    new_row[mapping[src_col]] = row.get(src_col)
                elif include_unmapped:
                    new_row[src_col] = row.get(src_col)
            new_rows.append(new_row)

        result_table = ObservationTable(
            columns=tuple(target_columns),
            rows=tuple(new_rows),
            source_locations=table.source_locations,
        )
        return ComponentResult(
            outputs={"observations": result_table},
            summary=f"字段映射: {len(mapping)} 个字段，输出 {result_table.row_count()} 行",
            metadata={
                "mapped_fields": len(mapping),
                "row_count": result_table.row_count(),
            },
        )
