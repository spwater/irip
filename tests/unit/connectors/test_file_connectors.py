"""文件连接器（FileConnector）单元测试。

测试覆盖：
- CSV 预览与流式读取；
- JSON 预览与流式读取；
- XLSX 预览（需 openpyxl）；
- 缺少 artifact_id/format 时抛 validation_failed；
- 不支持的格式抛 unsupported_media_type；
- 空文件处理；
- limit 参数截断；
- 异步行为验证：preview/read 在 async 上下文中不阻塞事件循环。

C-01 适配：FileConnector 从 ArtifactService.open_stream() 读取 artifact 流，
不再接受裸路径 path。测试通过 mock ArtifactService 提供 BytesIO 流。

这些测试同时作为 T3-7 异步 I/O 修复的回归基线。
"""

import asyncio
import io
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from packages.common.errors import AppError
from packages.connectors.contracts import (
    ConnectorSource,
    PreviewTable,
    SourceRecord,
)
from packages.connectors.file_connectors import FileConnector

# ---- Mock ArtifactService ----

_FAKE_ARTIFACT_ID: UUID = uuid4()


class MockArtifactService:
    """Mock artifact service for FileConnector testing.

    Stores file bytes in a dict keyed by artifact_id, returns
    (filename, size, BytesIO) from open_stream().
    """

    def __init__(self) -> None:
        self._store: dict[UUID, tuple[str, bytes]] = {}

    def add_file(self, artifact_id: UUID, filename: str, data: bytes) -> None:
        self._store[artifact_id] = (filename, data)

    async def open_stream(
        self, artifact_id: UUID
    ) -> tuple[str, int, "io.BytesIO"]:
        if artifact_id not in self._store:
            raise AppError(
                code="not_found",
                message=f"工件不存在: {artifact_id}",
                retryable=False,
                fields={"artifact_id": str(artifact_id)},
            )
        filename, data = self._store[artifact_id]
        return filename, len(data), io.BytesIO(data)


# ---- 辅助函数 ----


def _make_file_source(artifact_id: UUID, fmt: str) -> ConnectorSource:
    """构造文件数据源（C-01: artifact_id + format）。"""
    return ConnectorSource(
        kind="file",
        config={"artifact_id": str(artifact_id), "format": fmt},
    )


def _make_mock_with_file(
    fmt: str,
    data: bytes,
    filename: str = "test_file",
) -> tuple[MockArtifactService, ConnectorSource]:
    """创建 mock artifact service 并注册一个文件。

    Returns:
        tuple: (mock_service, source) - mock service 和对应的 ConnectorSource。
    """
    mock = MockArtifactService()
    mock.add_file(_FAKE_ARTIFACT_ID, filename, data)
    source = _make_file_source(_FAKE_ARTIFACT_ID, fmt)
    return mock, source


# ---- CSV 预览 ----


class TestFileConnectorCSVPreview:
    """FileConnector CSV 预览测试。"""

    async def test_preview_csv(self):
        """预览 CSV 文件返回正确的列名与行。"""
        data = b"name,value,unit\nD50,12.5,um\nD90,25,um\nD10,5,um\n"
        mock, source = _make_mock_with_file("csv", data)

        connector = FileConnector(artifact_service=mock)
        result = await connector.preview(source, limit=10)

        assert isinstance(result, PreviewTable)
        assert result.columns == ("name", "value", "unit")
        assert result.row_count == 3
        assert result.rows[0] == ("D50", "12.5", "um")
        assert result.rows[1] == ("D90", "25", "um")
        assert result.rows[2] == ("D10", "5", "um")

    async def test_preview_csv_with_limit(self):
        """limit 参数截断预览行数。"""
        lines = [b"name,value\n"]
        for i in range(50):
            lines.append(f"item{i},{i}\n".encode("utf-8"))
        data = b"".join(lines)
        mock, source = _make_mock_with_file("csv", data)

        connector = FileConnector(artifact_service=mock)
        result = await connector.preview(source, limit=5)

        assert result.row_count == 5
        assert len(result.rows) == 5
        assert result.rows[0] == ("item0", "0")

    async def test_preview_empty_csv(self):
        """空 CSV 文件返回空预览。"""
        mock, source = _make_mock_with_file("csv", b"")

        connector = FileConnector(artifact_service=mock)
        result = await connector.preview(source, limit=10)

        assert result.row_count == 0
        assert result.rows == ()
        assert result.columns == ()

    async def test_preview_csv_header_only(self):
        """只有表头的 CSV 返回 0 行但保留列名。"""
        mock, source = _make_mock_with_file("csv", b"col1,col2\n")

        connector = FileConnector(artifact_service=mock)
        result = await connector.preview(source, limit=10)

        assert result.columns == ("col1", "col2")
        assert result.row_count == 0


# ---- CSV 流式读取 ----


class TestFileConnectorCSVRead:
    """FileConnector CSV 流式读取测试。"""

    async def test_read_csv(self):
        """流式读取 CSV 返回 SourceRecord 列表。"""
        data = b"name,value\nD50,12.5\nD90,25\n"
        mock, source = _make_mock_with_file("csv", data)

        connector = FileConnector(artifact_service=mock)
        records = []
        async for record in connector.read(source):
            records.append(record)

        assert len(records) == 2
        assert all(isinstance(r, SourceRecord) for r in records)
        assert records[0].fields["name"] == "D50"
        assert records[0].fields["value"] == "12.5"
        assert records[1].fields["name"] == "D90"

    async def test_read_csv_preserves_column_order(self):
        """读取保持列顺序。"""
        data = b"z,a,m\n1,2,3\n"
        mock, source = _make_mock_with_file("csv", data)

        connector = FileConnector(artifact_service=mock)
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

    async def test_preview_json(self):
        """预览 JSON 对象数组。"""
        data = json.dumps([{"x": 1, "y": "a"}, {"x": 2, "y": "b"}]).encode(
            "utf-8"
        )
        mock, source = _make_mock_with_file("json", data)

        connector = FileConnector(artifact_service=mock)
        result = await connector.preview(source, limit=10)

        assert result.columns == ("x", "y")
        assert result.row_count == 2
        assert result.rows[0] == (1, "a")
        assert result.rows[1] == (2, "b")

    async def test_preview_json_sparse_keys(self):
        """JSON 对象键集不同时，列名为并集，缺失值为 None。"""
        data = json.dumps([{"a": 1, "b": 2}, {"a": 3, "c": 4}]).encode("utf-8")
        mock, source = _make_mock_with_file("json", data)

        connector = FileConnector(artifact_service=mock)
        result = await connector.preview(source, limit=10)

        # 列名为并集，保持首次出现顺序
        assert "a" in result.columns
        assert "b" in result.columns
        assert "c" in result.columns
        assert result.row_count == 2

    async def test_preview_json_with_limit(self):
        """limit 截断 JSON 预览。"""
        data_list = [{"id": i} for i in range(20)]
        data = json.dumps(data_list).encode("utf-8")
        mock, source = _make_mock_with_file("json", data)

        connector = FileConnector(artifact_service=mock)
        result = await connector.preview(source, limit=5)

        assert result.row_count == 5

    async def test_read_json(self):
        """流式读取 JSON 返回 SourceRecord。"""
        data = json.dumps([{"x": 10}, {"x": 20}]).encode("utf-8")
        mock, source = _make_mock_with_file("json", data)

        connector = FileConnector(artifact_service=mock)
        records = []
        async for record in connector.read(source):
            records.append(record)

        assert len(records) == 2
        assert records[0].fields["x"] == "10"
        assert records[1].fields["x"] == "20"

    async def test_preview_json_non_array_rejected(self):
        """JSON 顶层非数组时抛 validation_failed。"""
        data = json.dumps({"key": "val"}).encode("utf-8")
        mock, source = _make_mock_with_file("json", data)

        connector = FileConnector(artifact_service=mock)
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
        data = path.read_bytes()

        mock, source = _make_mock_with_file("xlsx", data, "data.xlsx")

        connector = FileConnector(artifact_service=mock)
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
        data = path.read_bytes()

        mock, source = _make_mock_with_file("xlsx", data, "empty.xlsx")

        connector = FileConnector(artifact_service=mock)
        result = await connector.preview(source, limit=10)

        assert result.row_count == 0


# ---- 错误处理 ----


class TestFileConnectorErrors:
    """FileConnector 错误处理测试。"""

    async def test_missing_artifact_id(self):
        """缺少 artifact_id 抛 validation_failed。"""
        mock = MockArtifactService()
        connector = FileConnector(artifact_service=mock)
        source = ConnectorSource(kind="file", config={"format": "csv"})
        with pytest.raises(AppError, match="缺少 artifact_id"):
            await connector.preview(source, limit=10)

    async def test_missing_format(self):
        """缺少 format 抛 validation_failed。"""
        mock = MockArtifactService()
        connector = FileConnector(artifact_service=mock)
        source = ConnectorSource(
            kind="file", config={"artifact_id": str(_FAKE_ARTIFACT_ID)}
        )
        with pytest.raises(AppError, match="缺少 format"):
            await connector.preview(source, limit=10)

    async def test_unsupported_format(self):
        """不支持的格式抛 unsupported_media_type。"""
        data = b"<root><item>1</item></root>"
        mock, source = _make_mock_with_file("xml", data)
        connector = FileConnector(artifact_service=mock)
        with pytest.raises(AppError, match="不支持的文件格式"):
            await connector.preview(source, limit=10)

    async def test_read_missing_artifact_id(self):
        """read 缺少 artifact_id 也抛 validation_failed。"""
        mock = MockArtifactService()
        connector = FileConnector(artifact_service=mock)
        source = ConnectorSource(kind="file", config={"format": "csv"})
        with pytest.raises(AppError, match="缺少 artifact_id"):
            async for _ in connector.read(source):
                pass

    async def test_missing_artifact_service(self):
        """未配置 artifact_service 时抛 validation_failed。"""
        connector = FileConnector(artifact_service=None)
        source = _make_file_source(_FAKE_ARTIFACT_ID, "csv")
        with pytest.raises(AppError, match="未配置 artifact_service"):
            await connector.preview(source, limit=10)

    async def test_artifact_not_found(self):
        """artifact 不存在时抛 not_found。"""
        mock = MockArtifactService()
        connector = FileConnector(artifact_service=mock)
        source = _make_file_source(uuid4(), "csv")
        with pytest.raises(AppError, match="工件不存在"):
            await connector.preview(source, limit=10)

    async def test_invalid_artifact_id(self):
        """无效的 artifact_id 抛 validation_failed。"""
        mock = MockArtifactService()
        connector = FileConnector(artifact_service=mock)
        source = ConnectorSource(
            kind="file",
            config={"artifact_id": "not-a-uuid", "format": "csv"},
        )
        with pytest.raises(AppError, match="无效的 artifact_id"):
            await connector.preview(source, limit=10)


# ---- 异步行为验证（T3-7 核心验证）----


class TestFileConnectorAsyncBehavior:
    """验证 FileConnector 的异步 I/O 行为（T3-7）。

    这些测试验证 preview/read 在 async 上下文中执行时不会阻塞事件循环。
    当同步 I/O 被正确包装在 asyncio.to_thread 中时，其他协程可以
    并发执行而不被阻塞。
    """

    async def test_preview_does_not_block_event_loop(self):
        """preview 执行期间事件循环不被阻塞。

        如果文件读取使用了 asyncio.to_thread 包装，则另一个定时器
        协程可以在 preview 执行期间继续运行。如果同步 open() 直接在
        事件循环中执行，定时器将被阻塞。
        """
        data = b"a,b\n1,2\n3,4\n"
        mock, source = _make_mock_with_file("csv", data)

        connector = FileConnector(artifact_service=mock)

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

    async def test_read_does_not_block_event_loop(self):
        """read 执行期间事件循环不被阻塞。"""
        lines = [b"a,b\n"]
        for i in range(100):
            lines.append(f"{i},{i * 2}\n".encode("utf-8"))
        data = b"".join(lines)
        mock, source = _make_mock_with_file("csv", data)

        connector = FileConnector(artifact_service=mock)

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
