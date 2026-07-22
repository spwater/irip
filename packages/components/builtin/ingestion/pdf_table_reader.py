"""PDF 表格提取组件。

使用 pdfplumber 从 PDF 文件中提取表格，输出 ObservationTable。

参数：
- path: PDF 文件路径（必填）。
- page_numbers: 页码列表（可选，默认全部页面，1-based）。
- table_index: 每页提取第几个表格（可选，默认 0，即第一个）。
"""

from pathlib import Path
from typing import Any

import pdfplumber

from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult


class PDFTableReader:
    """PDF 表格提取组件（基于 pdfplumber）。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """从 PDF 提取表格并输出 ObservationTable。"""
        path_str: str = params["path"]
        page_numbers: list[int] | None = params.get("page_numbers")
        table_index: int = int(params.get("table_index", 0))

        all_rows: list[dict[str, Any]] = []
        source_locs: list[dict[str, Any]] = []
        columns: tuple[str, ...] = ()

        with pdfplumber.open(Path(path_str)) as pdf:
            pages = pdf.pages
            if page_numbers:
                pages = [
                    pages[p - 1]
                    for p in page_numbers
                    if 1 <= p <= len(pages)
                ]

            for page in pages:
                tables = page.extract_tables()
                if not tables:
                    continue
                idx = min(table_index, len(tables) - 1)
                table = tables[idx]
                if not table:
                    continue

                # 第一行作为表头
                header = [
                    str(c).strip() if c else f"col_{i}"
                    for i, c in enumerate(table[0])
                ]
                if not columns:
                    columns = tuple(header)

                for row_idx, row in enumerate(table[1:], start=1):
                    if all(c is None or c == "" for c in row):
                        continue
                    record: dict[str, Any] = {}
                    for col_name, cell in zip(columns, row):
                        record[col_name] = cell
                    all_rows.append(record)
                    source_locs.append(
                        {
                            "file": Path(path_str).name,
                            "page": page.page_number,
                            "row": row_idx,
                        }
                    )

        if not columns:
            return ComponentResult(
                outputs={},
                summary="未从 PDF 提取到表格",
                metadata={"row_count": 0},
                diagnostics={"warnings": ["no_tables_found"]},
            )

        table = ObservationTable(
            columns=columns,
            rows=tuple(all_rows),
            source_locations=tuple(source_locs),
        )
        return ComponentResult(
            outputs={"observations": table},
            summary=f"从 {Path(path_str).name} 提取 {table.row_count()} 行表格数据",
            metadata={
                "row_count": table.row_count(),
                "column_count": table.column_count(),
            },
        )
