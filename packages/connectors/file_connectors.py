"""文件连接器：CSV / XLSX / JSON 预览与流式读取。

实现 Connector 协议：
- preview(source, limit): 读取前 limit 行返回 PreviewTable；
- read(source): 异步迭代器，逐条 yield SourceRecord。

支持格式：
- csv: 标准库 csv.DictReader；
- xlsx: openpyxl（若未安装则抛 AppError(unsupported_media_type)）；
- json: json.load，期望顶层为对象数组。

安全约定：文件路径由调用方传入，连接器只读取不写入。
"""

import csv
import json
from collections.abc import AsyncIterator

from packages.common.errors import AppError
from packages.connectors.contracts import (
    ConnectorSource,
    PreviewTable,
    SourceRecord,
)


class FileConnector:
    """文件数据源连接器（CSV / XLSX / JSON）。

    无状态连接器，可安全共享。每次 preview/read 都重新打开文件。
    """

    async def preview(
        self, source: ConnectorSource, limit: int = 100
    ) -> PreviewTable:
        """预览文件前 limit 行。

        Args:
            source: 文件数据源，config 须含 path 与 format。
            limit: 预览行数上限。

        Returns:
            PreviewTable: 列名 + 行 + 总行数（总行数同预览行数，文件不预统计全量）。

        Raises:
            AppError: code="validation_failed"，当缺少 path/format 时。
            AppError: code="unsupported_media_type"，当格式不支持或依赖缺失时。
        """
        path = source.config.get("path")
        fmt = source.config.get("format")
        if not path:
            raise AppError(
                code="validation_failed",
                message="文件数据源缺少 path",
                retryable=False,
                fields={"field": "path"},
            )
        if not fmt:
            raise AppError(
                code="validation_failed",
                message="文件数据源缺少 format",
                retryable=False,
                fields={"field": "format"},
            )

        columns, rows = await self._read_rows(path, fmt, limit)
        return PreviewTable(
            columns=columns,
            rows=tuple(tuple(r) for r in rows),
            row_count=len(rows),
        )

    async def read(
        self, source: ConnectorSource
    ) -> AsyncIterator[SourceRecord]:
        """流式读取文件全部记录。

        Args:
            source: 文件数据源。

        Yields:
            SourceRecord: 每行一条记录，字段名→值。

        Raises:
            AppError: code="validation_failed"，当缺少 path/format 时。
            AppError: code="unsupported_media_type"，当格式不支持时。
        """
        path = source.config.get("path")
        fmt = source.config.get("format")
        if not path:
            raise AppError(
                code="validation_failed",
                message="文件数据源缺少 path",
                retryable=False,
                fields={"field": "path"},
            )
        if not fmt:
            raise AppError(
                code="validation_failed",
                message="文件数据源缺少 format",
                retryable=False,
                fields={"field": "format"},
            )

        for record in await self._read_records(path, fmt):
            yield record

    # ---- 内部读取 ----

    async def _read_rows(
        self, path: str, fmt: str, limit: int
    ) -> tuple[tuple[str, ...], list[list]]:
        """读取前 limit 行，返回 (列名元组, 行列表)。"""
        if fmt == "csv":
            return self._read_csv(path, limit)
        if fmt == "xlsx":
            return self._read_xlsx(path, limit)
        if fmt == "json":
            return self._read_json(path, limit)
        raise AppError(
            code="unsupported_media_type",
            message=f"不支持的文件格式：{fmt}",
            retryable=False,
            fields={"format": fmt},
        )

    async def _read_records(
        self, path: str, fmt: str
    ) -> list[SourceRecord]:
        """读取全部记录为 SourceRecord 列表。"""
        columns, rows = await self._read_rows(path, fmt, limit=10**9)
        records: list[SourceRecord] = []
        for row in rows:
            fields: dict[str, str | None] = {}
            for idx, col in enumerate(columns):
                value = row[idx] if idx < len(row) else None
                fields[col] = None if value is None else str(value)
            records.append(SourceRecord(fields=fields))
        return records

    # ---- CSV ----

    @staticmethod
    def _read_csv(
        path: str, limit: int
    ) -> tuple[tuple[str, ...], list[list]]:
        """读取 CSV 文件前 limit 行。"""
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                return ((), [])
            columns = tuple(header)
            rows: list[list] = []
            for row in reader:
                if len(rows) >= limit:
                    break
                rows.append(list(row))
            return columns, rows

    # ---- XLSX ----

    @staticmethod
    def _read_xlsx(
        path: str, limit: int
    ) -> tuple[tuple[str, ...], list[list]]:
        """读取 XLSX 文件首个工作表前 limit 行。

        Raises:
            AppError: code="unsupported_media_type"，当 openpyxl 未安装时。
        """
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise AppError(
                code="unsupported_media_type",
                message="读取 XLSX 需要 openpyxl 依赖，请安装 openpyxl",
                retryable=False,
                fields={"format": "xlsx"},
            ) from exc

        wb = load_workbook(filename=path, read_only=True, data_only=True)
        try:
            ws = wb.active
            if ws is None:
                return ((), [])
            rows_iter = ws.iter_rows(values_only=True)
            try:
                header = next(rows_iter)
            except StopIteration:
                return ((), [])
            columns = tuple(str(c) if c is not None else "" for c in header)
            rows: list[list] = []
            for row in rows_iter:
                if len(rows) >= limit:
                    break
                rows.append(list(row))
            return columns, rows
        finally:
            wb.close()

    # ---- JSON ----

    @staticmethod
    def _read_json(
        path: str, limit: int
    ) -> tuple[tuple[str, ...], list[list]]:
        """读取 JSON 文件（对象数组）前 limit 行。

        列名为首个对象的键的并集（保持首次出现顺序）。
        """
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise AppError(
                code="validation_failed",
                message="JSON 文件顶层必须是对象数组",
                retryable=False,
                fields={"format": "json"},
            )

        columns: list[str] = []
        seen: set[str] = set()
        for obj in data[:limit]:
            if not isinstance(obj, dict):
                continue
            for key in obj:
                if key not in seen:
                    seen.add(key)
                    columns.append(key)

        col_index = {col: idx for idx, col in enumerate(columns)}
        rows: list[list] = []
        for obj in data[:limit]:
            if not isinstance(obj, dict):
                continue
            row = [None] * len(columns)
            for key, value in obj.items():
                idx = col_index.get(key)
                if idx is not None:
                    row[idx] = value
            rows.append(row)
        return tuple(columns), rows
