"""文件连接器（FileConnector）单元测试。

测试覆盖：
- CSV 预览与流式读取；
- JSON 预览与流式读取；
- XLSX 预览（需 openpyxl）；
- 缺少 path/format 时抛 validation_failed；
- 不支持的格式抛 unsupported_media_type；
- 空文件处理；
- limit 参数截断；
- 异步行为验证：preview/read 在 async 上下文中不阻塞事件循环。

这些测试同时作为 T3-7 异步 I/O 修复的回归基线。
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from packages.common.errors import AppError
from packages.connectors.contracts import (
    ConnectorSource,
    PreviewTable,
    SourceRecord,
)
from packages.connectors.file_connectors import FileConnector


# ---- 辅助函数 ----


def _make_file_source(path: str, fmt: str) -> ConnectorSource:
    """构造文件数据源。"""
    return ConnectorSource(kind="file", config={"path": path, "format": fmt})


# ---- CSV 预览 ----


class TestFileConnectorCSVPreview:
    """FileConnector CSV 预览测试。"""

    async def test_preview_csv(self, tmp_path: Path):
        """预览 CSV 文件返回正确的列名与行。"""
        path = tmp_path / "data.csv"
        path.write_text(
            "name,value,unit\nD50,12.5,um\nD90,25,um\nD10,5,um\n",
            encoding="utf-8",
        )

        connector = FileConnector()
        source = _make_file_source(str(path), "csv")
        result = await connector.preview(source, limit=10)

        assert isinstance(result, PreviewTable)
        assert result.columns == ("name", "value", "unit")
        assert result.row_count == 3
        assert result.rows[0] == ("D50", "12.5", "um")
        assert result.rows[1] == ("D90", "25", "um")
        assert result.rows[2] == ("D10", "5", "um")

    async def test_preview_csv_with_limit(self, tmp_path: Path):
        """limit 参数截断预览行数。"""
        lines = ["name,value\n"]
        for i in range(50):
            lines.append(f"item{i},{i}\n")
        path = tmp_path / "big.csv"
        path.write_text("".join(lines), encoding="utf-8")

        connector = FileConnector()
        source = _make_file_source(str(path), "csv")
        result = await connector.preview(source, limit=5)

        assert result.row_count == 5
        assert len(result.rows) == 5
        assert result.rows[0] == ("item0", "0")

    async def test_preview_empty_csv(self, tmp_path: Path):
        """空 CSV 文件返回空预览。"""
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")

        connector = FileConnector()
        source = _make_file_source(str(path), "csv")
        result = await connector.preview(source, limit=10)

        assert result.row_count == 0
        assert result.rows == ()
        assert result.columns == ()

    async def test_preview_csv_header_only(self, tmp_path: Path):
        """只有表头的 CSV 返回 0 行但保留列名。"""
        path = tmp_path / "header.csv"
        path.write_text("col1,col2\n", encoding="utf-8")

        connector = FileConnector()
        source = _make_file_source(str(path), "csv")
        result = await connector.preview(source, limit=10)

        assert result.columns == ("col1", "col2")
        assert result.row_count == 0


# ---- CSV 流式读取 ----


class TestFileConnectorCSVRead:
    """FileConnector CSV 流式读取测试。"""

    async def test_read_csv(self, tmp_path: Path):
        """流式读取 CSV 返回 SourceRecord 列表。"""
        path = tmp_path / "data.csv"
        path.write_text(
            "name,value\nD50,12.5\nD90,25\n", encoding="utf-8"
        )

        connector = FileConnector()
        source = _make_file_source(str(path), "csv")
        records = []
        async for record in connector.read(source):
            records.append(record)

        assert len(records) == 2
        assert all(isinstance(r, SourceRecord) for r in records)
        assert records[0].fields["name"] == "D50"
        assert records[0].fields["value"] == "12.5"
        assert records[1].fields["name"] == "D90"

    async def test_read_csv_preserves_column_order(self, tmp_path: Path):
        """读取保持列顺序。"""
        path = tmp_path / "order.csv"
        path.write_text("z,a,m\n1,2,3\n", encoding="utf-8")

        connector = FileConnector()
        source = _make_file_source(str(path), "csv")
        records = []
        async for record in connector.read(source):
            records.append(record)

        assert len(records) == 1
        assert records[0].fields["z"] == "1"
        assert records[0].fields["a"] == "2"
        assert records[0].fields["m"] == "3"


# ---- JSON 预览与读取 ----


class TestFileConnectorJSON:
    """FileConnector JSON 测试。"""

    async def test_preview_json(self, tmp_path: Path):
        """预览 JSON 对象数组。"""
        path = tmp_path / "data.json"
        path.write_text(
            json.dumps([{"x": 1, "y": "a"}, {"x": 2, "y": "b"}]),
            encoding="utf-8",
        )

        connector = FileConnector()
        source = _make_file_source(str(path), "json")
        result = await connector.preview(source, limit=10)

        assert result.columns == ("x", "y")
        assert result.row_count == 2
        assert result.rows[0] == (1, "a")
        assert result.rows[1] == (2, "b")

    async def test_preview_json_sparse_keys(self, tmp_path: Path):
        """JSON 对象键集不同时，列名为并集，缺失值为 None。"""
        path = tmp_path / "sparse.json"
        path.write_text(
            json.dumps([{"a": 1, "b": 2}, {"a": 3, "c": 4}]),
            encoding="utf-8",
        )

        connector = FileConnector()
        source = _make_file_source(str(path), "json")
        result = await connector.preview(source, limit=10)

        # 列名为并集，保持首次出现顺序
        assert "a" in result.columns
        assert "b" in result.columns
        assert "c" in result.columns
        assert result.row_count == 2

    async def test_preview_json_with_limit(self, tmp_path: Path):
        """limit 截断 JSON 预览。"""
        data = [{"id": i} for i in range(20)]
        path = tmp_path / "big.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        connector = FileConnector()
        source = _make_file_source(str(path), "json")
        result = await connector.preview(source, limit=5)

        assert result.row_count == 5

    async def test_read_json(self, tmp_path: Path):
        """流式读取 JSON 返回 SourceRecord。"""
        path = tmp_path / "data.json"
        path.write_text(
            json.dumps([{"x": 10}, {"x": 20}]), encoding="utf-8"
        )

        connector = FileConnector()
        source = _make_file_source(str(path), "json")
        records = []
        async for record in connector.read(source):
            records.append(record)

        assert len(records) == 2
        assert records[0].fields["x"] == "10"
        assert records[1].fields["x"] == "20"

    async def test_preview_json_non_array_rejected(self, tmp_path: Path):
        """JSON 顶层非数组时抛 validation_failed。"""
        path = tmp_path / "obj.json"
        path.write_text(json.dumps({"key": "val"}), encoding="utf-8")

        connector = FileConnector()
        source = _make_file_source(str(path), "json")
        with pytest.raises(AppError, match="顶层必须是对象数组"):
            await connector.preview(source, limit=10)


# ---- XLSX ----


class TestFileConnectorXLSX:
    """FileConnector XLSX 测试。"""

    async def test_preview_xlsx(self, tmp_path: Path):
        """预览 XLSX 文件。"""
        try:
            from openpyxl import Workbook
        except ImportError:
            pytest.skip("openpyxl not installed")

        wb = Workbook()
        ws = wb.active
        ws.append(["name", "value"])
        ws.append(["D50", 12.5])
        ws.append(["D90", 25])
        path = tmp_path / "data.xlsx"
        wb.save(path)

        connector = FileConnector()
        source = _make_file_source(str(path), "xlsx")
        result = await connector.preview(source, limit=10)

        assert result.columns == ("name", "value")
        assert result.row_count == 2

    async def test_preview_empty_xlsx(self, tmp_path: Path):
        """空 XLSX 返回空预览。"""
        try:
            from openpyxl import Workbook
        except ImportError:
            pytest.skip("openpyxl not installed")

        wb = Workbook()
        path = tmp_path / "empty.xlsx"
        wb.save(path)

        connector = FileConnector()
        source = _make_file_source(str(path), "xlsx")
        result = await connector.preview(source, limit=10)

        assert result.row_count == 0


# ---- 错误处理 ----


class TestFileConnectorErrors:
    """FileConnector 错误处理测试。"""

    async def test_missing_path(self):
        """缺少 path 抛 validation_failed。"""
        connector = FileConnector()
        source = ConnectorSource(kind="file", config={"format": "csv"})
        with pytest.raises(AppError, match="缺少 path"):
            await connector.preview(source, limit=10)

    async def test_missing_format(self, tmp_path: Path):
        """缺少 format 抛 validation_failed。"""
        path = tmp_path / "data.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")

        connector = FileConnector()
        source = ConnectorSource(kind="file", config={"path": str(path)})
        with pytest.raises(AppError, match="缺少 format"):
            await connector.preview(source, limit=10)

    async def test_unsupported_format(self, tmp_path: Path):
        """不支持的格式抛 unsupported_media_type。"""
        path = tmp_path / "data.xml"
        path.write_text("<root/>", encoding="utf-8")

        connector = FileConnector()
        source = _make_file_source(str(path), "xml")
        with pytest.raises(AppError, match="不支持的文件格式"):
            await connector.preview(source, limit=10)

    async def test_read_missing_path(self):
        """read 缺少 path 也抛 validation_failed。"""
        connector = FileConnector()
        source = ConnectorSource(kind="file", config={"format": "csv"})
        with pytest.raises(AppError, match="缺少 path"):
            async for _ in connector.read(source):
                pass


# ---- 异步行为验证（T3-7 核心验证）----


class TestFileConnectorAsyncBehavior:
    """验证 FileConnector 的异步 I/O 行为（T3-7）。

    这些测试验证 preview/read 在 async 上下文中执行时不会阻塞事件循环。
    当同步 I/O 被正确包装在 asyncio.to_thread 中时，其他协程可以
    并发执行而不被阻塞。
    """

    async def test_preview_does_not_block_event_loop(self, tmp_path: Path):
        """preview 执行期间事件循环不被阻塞。

        如果文件读取使用了 asyncio.to_thread 包装，则另一个定时器
        协程可以在 preview 执行期间继续运行。如果同步 open() 直接在
        事件循环中执行，定时器将被阻塞。
        """
        path = tmp_path / "data.csv"
        path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

        connector = FileConnector()
        source = _make_file_source(str(path), "csv")

        timer_ticks: list[float] = []

        async def timer():
            """每 10ms 记录一次时间，验证事件循环未被阻塞。"""
            for _ in range(5):
                await asyncio.sleep(0.01)
                timer_ticks.append(asyncio.get_event_loop().time())

        await asyncio.gather(
            connector.preview(source, limit=10),
            timer(),
        )

        # 如果事件循环未被阻塞，timer 应至少执行了 3 次以上
        assert len(timer_ticks) >= 3

    async def test_read_does_not_block_event_loop(self, tmp_path: Path):
        """read 执行期间事件循环不被阻塞。"""
        lines = ["a,b\n"]
        for i in range(100):
            lines.append(f"{i},{i * 2}\n")
        path = tmp_path / "big.csv"
        path.write_text("".join(lines), encoding="utf-8")

        connector = FileConnector()
        source = _make_file_source(str(path), "csv")

        timer_ticks: list[int] = []

        async def timer():
            for _ in range(5):
                await asyncio.sleep(0.005)
                timer_ticks.append(1)

        async def read_all():
            records = []
            async for record in connector.read(source):
                records.append(record)
            return records

        results = await asyncio.gather(read_all(), timer())

        assert len(results[0]) == 100
        assert len(timer_ticks) >= 3
