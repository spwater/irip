"""CSV 读取组件。

使用标准库 csv 模块读取 CSV/TSV 文件，输出 ObservationTable。

参数：
- path: 文件路径（必填）。
- delimiter: 分隔符（可选，默认自动检测）。
- encoding: 文件编码（可选，默认 utf-8）。
- has_header: 是否包含表头（可选，默认 True）。
"""

import asyncio
import csv
from pathlib import Path
from typing import Any

from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult


def _read_csv_sync(
    path_str: str,
    delimiter: str | None,
    encoding: str,
) -> list[list[str]]:
    """同步读取 CSV 文件并返回所有行（在线程池中执行，F-21）。"""
    with open(
        Path(path_str), newline="", encoding=encoding
    ) as f:
        if delimiter:
            reader = csv.reader(f, delimiter=delimiter)
        else:
            # 自动检测分隔符
            sample = f.readline()
            f.seek(0)
            if not sample.strip():
                reader = csv.reader(f, delimiter=",")
            else:
                try:
                    detected = csv.Sniffer().sniff(
                        sample, delimiters=",\t;|"
                    )
                    reader = csv.reader(f, delimiter=detected.delimiter)
                except csv.Error:
                    reader = csv.reader(f, delimiter=",")

        return list(reader)


class CSVReader:
    """CSV/TSV 文件读取组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """读取 CSV 文件并输出 ObservationTable。"""
        path_str: str = params["path"]
        delimiter: str | None = params.get("delimiter")
        encoding: str = params.get("encoding", "utf-8")
        has_header: bool = params.get("has_header", True)

        # F-21: 同步文件 I/O 放 asyncio.to_thread() 避免阻塞事件循环
        all_rows = await asyncio.to_thread(
            _read_csv_sync, path_str, delimiter, encoding
        )

        if not all_rows:
            return ComponentResult(
                outputs={},
                summary="空 CSV 文件",
                metadata={"row_count": 0},
            )

        if has_header:
            columns: tuple[str, ...] = tuple(all_rows[0])
            data_rows_raw = all_rows[1:]
        else:
            col_count = len(all_rows[0])
            columns = tuple(f"col_{i}" for i in range(col_count))
            data_rows_raw = all_rows

        data_rows: list[dict[str, Any]] = []
        source_locs: list[dict[str, Any]] = []
        for idx, row in enumerate(data_rows_raw, start=1):
            if not row:
                continue
            record: dict[str, Any] = {}
            for col_name, cell in zip(columns, row):
                record[col_name] = _coerce(cell)
            data_rows.append(record)
            source_locs.append(
                {"file": Path(path_str).name, "row": idx}
            )

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
            },
        )


def _coerce(value: str) -> Any:
    """尝试将字符串值转换为 int/float，失败则保留字符串。"""
    if value == "" or value is None:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value
