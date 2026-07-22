"""Excel 读取组件。

使用 openpyxl 读取 .xlsx 文件，输出 ObservationTable。

参数：
- path: xlsx 文件路径（必填）。
- sheet_name: 工作表名称（可选，默认第一个工作表）。
- header_row: 表头所在行号，1-based（可选，默认 1）。
- data_start_row: 数据起始行号，1-based（可选，默认 header_row+1）。
"""

from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult


class ExcelReader:
    """Excel (.xlsx) 文件读取组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """读取 xlsx 文件并输出 ObservationTable。"""
        path_str: str = params["path"]
        sheet_name: str | None = params.get("sheet_name")
        header_row: int = int(params.get("header_row", 1))
        data_start_row: int = int(
            params.get("data_start_row", header_row + 1)
        )

        wb = load_workbook(Path(path_str), read_only=True, data_only=True)
        try:
            ws = wb[sheet_name] if sheet_name else wb.active
            if ws is None:
                return ComponentResult(
                    outputs={},
                    summary="工作簿中无可用工作表",
                    metadata={"row_count": 0},
                    diagnostics={"warnings": ["no_worksheet"]},
                )

            rows_iter = ws.iter_rows(
                min_row=header_row,
                values_only=True,
            )
            all_rows = list(rows_iter)
            if not all_rows:
                return ComponentResult(
                    outputs={},
                    summary="空工作表",
                    metadata={"row_count": 0},
                )

            # 表头
            header = [
                str(c).strip() if c is not None else f"col_{i}"
                for i, c in enumerate(all_rows[0])
            ]
            columns: tuple[str, ...] = tuple(header)

            data_rows: list[dict[str, Any]] = []
            source_locs: list[dict[str, Any]] = []
            offset = data_start_row - (header_row + 1)
            for idx, row in enumerate(all_rows[1 + offset :], start=data_start_row):
                if all(cell is None for cell in row):
                    continue
                record: dict[str, Any] = {}
                for col_name, cell in zip(columns, row):
                    record[col_name] = cell
                data_rows.append(record)
                source_locs.append(
                    {
                        "file": Path(path_str).name,
                        "sheet": ws.title,
                        "row": idx,
                    }
                )
        finally:
            wb.close()

        table = ObservationTable(
            columns=columns,
            rows=tuple(data_rows),
            source_locations=tuple(source_locs),
        )
        return ComponentResult(
            outputs={"observations": table},
            summary=f"从 {Path(path_str).name} 读取 {table.row_count()} 行",
            metadata={
                "row_count": table.row_count(),
                "column_count": table.column_count(),
                "sheet": sheet_name or "active",
            },
        )
