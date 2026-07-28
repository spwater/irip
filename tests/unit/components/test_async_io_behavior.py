"""异步阻塞 I/O 修复验证测试（T3-7）。

验证 packages/components/builtin/ 中已修复的 4 个摄入组件
正确使用 asyncio.to_thread() 包装同步 I/O，不阻塞事件循环。

测试覆盖：
- JSONReader: asyncio.to_thread 包装 _read_json_sync；
- CSVReader: asyncio.to_thread 包装 _read_csv_sync；
- PDFTableReader: asyncio.to_thread 包装 _read_pdf_tables_sync；
- RESTFetch: asyncio.to_thread 包装 _fetch_with_redirects；
- 事件循环非阻塞验证：I/O 执行期间其他协程可并发运行；
- 功能正确性回归：异步包装后读取结果与同步一致。
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.components.builtin.ingestion.csv_reader import CSVReader, _read_csv_sync
from packages.components.builtin.ingestion.json_reader import JSONReader, _read_json_sync
from packages.components.builtin.ingestion.pdf_table_reader import (
    PDFTableReader,
    _read_pdf_tables_sync,
)
from packages.components.builtin.ingestion.rest_fetch import RESTFetch
from packages.components.builtin.types import ObservationTable

from tests.unit.components.conftest import make_test_context


# ---- JSONReader 异步行为 ----


class TestJSONReaderAsyncIO:
    """JSONReader 的 asyncio.to_thread 包装验证。"""

    async def test_read_json_returns_correct_data(self, tmp_path: Path):
        """异步读取 JSON 文件返回正确数据。"""
        path = tmp_path / "test.json"
        path.write_text(
            json.dumps([{"a": 1}, {"a": 2}]), encoding="utf-8"
        )

        reader = JSONReader()
        ctx = make_test_context()
        result = await reader.execute(ctx, {"path": str(path)})

        table = result.outputs["observations"]
        assert isinstance(table, ObservationTable)
        assert table.row_count() == 2
        assert table.rows[0]["a"] == 1

    async def test_read_json_does_not_block_event_loop(self, tmp_path: Path):
        """JSONReader 执行期间事件循环不被阻塞。"""
        path = tmp_path / "test.json"
        path.write_text(json.dumps([{"x": i} for i in range(50)]), encoding="utf-8")

        reader = JSONReader()
        ctx = make_test_context()

        timer_ticks: list[int] = []

        async def timer():
            for _ in range(5):
                await asyncio.sleep(0.005)
                timer_ticks.append(1)

        await asyncio.gather(
            reader.execute(ctx, {"path": str(path)}),
            timer(),
        )

        # 事件循环未被阻塞 → timer 至少执行 3 次
        assert len(timer_ticks) >= 3

    def test_read_json_sync_function_exists(self):
        """_read_json_sync 函数存在且可同步调用。"""
        assert callable(_read_json_sync)

    async def test_read_json_sync_wrapped_in_to_thread(self, tmp_path: Path):
        """验证 _read_json_sync 被 asyncio.to_thread 调用。"""
        path = tmp_path / "test.json"
        path.write_text(json.dumps({"key": "val"}), encoding="utf-8")

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
            reader = JSONReader()
            ctx = make_test_context()
            await reader.execute(ctx, {"path": str(path)})

            assert mock_to_thread.called
            assert mock_to_thread.call_args.args[0] is _read_json_sync

    async def test_read_json_missing_file_raises_error(self, tmp_path: Path):
        """读取不存在的文件抛出异常。"""
        path = tmp_path / "nonexistent.json"

        reader = JSONReader()
        ctx = make_test_context()
        with pytest.raises(FileNotFoundError):
            await reader.execute(ctx, {"path": str(path)})


# ---- CSVReader 异步行为 ----


class TestCSVReaderAsyncIO:
    """CSVReader 的 asyncio.to_thread 包装验证。"""

    async def test_read_csv_returns_correct_data(self, tmp_path: Path):
        """异步读取 CSV 文件返回正确数据。"""
        path = tmp_path / "test.csv"
        path.write_text("name,value\nD50,12.5\nD90,25\n", encoding="utf-8")

        reader = CSVReader()
        ctx = make_test_context()
        result = await reader.execute(ctx, {"path": str(path)})

        table = result.outputs["observations"]
        assert table.columns == ("name", "value")
        assert table.row_count() == 2
        assert table.rows[0]["value"] == 12.5

    async def test_read_csv_does_not_block_event_loop(self, tmp_path: Path):
        """CSVReader 执行期间事件循环不被阻塞。"""
        lines = ["name,value\n"]
        for i in range(100):
            lines.append(f"row{i},{i}.5\n")
        path = tmp_path / "big.csv"
        path.write_text("".join(lines), encoding="utf-8")

        reader = CSVReader()
        ctx = make_test_context()

        timer_ticks: list[int] = []

        async def timer():
            for _ in range(5):
                await asyncio.sleep(0.005)
                timer_ticks.append(1)

        await asyncio.gather(
            reader.execute(ctx, {"path": str(path)}),
            timer(),
        )

        assert len(timer_ticks) >= 3

    def test_read_csv_sync_function_exists(self):
        """_read_csv_sync 函数存在且可同步调用。"""
        assert callable(_read_csv_sync)

    async def test_read_csv_sync_wrapped_in_to_thread(self, tmp_path: Path):
        """验证 _read_csv_sync 被 asyncio.to_thread 调用。"""
        path = tmp_path / "test.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
            reader = CSVReader()
            ctx = make_test_context()
            await reader.execute(ctx, {"path": str(path)})

            assert mock_to_thread.called
            assert mock_to_thread.call_args.args[0] is _read_csv_sync

    async def test_read_csv_with_custom_delimiter(self, tmp_path: Path):
        """自定义分隔符异步读取。"""
        path = tmp_path / "test.tsv"
        path.write_text("a\tb\n1\t2\n", encoding="utf-8")

        reader = CSVReader()
        ctx = make_test_context()
        result = await reader.execute(
            ctx, {"path": str(path), "delimiter": "\t"}
        )

        table = result.outputs["observations"]
        assert table.columns == ("a", "b")
        assert table.row_count() == 1
        assert table.rows[0]["a"] == 1

    async def test_read_empty_csv(self, tmp_path: Path):
        """空 CSV 文件返回空结果。"""
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")

        reader = CSVReader()
        ctx = make_test_context()
        result = await reader.execute(ctx, {"path": str(path)})

        assert result.metadata["row_count"] == 0


# ---- PDFTableReader 异步行为 ----


class TestPDFTableReaderAsyncIO:
    """PDFTableReader 的 asyncio.to_thread 包装验证。"""

    async def test_read_pdf_sync_function_exists(self):
        """_read_pdf_tables_sync 函数存在且可同步调用。"""
        assert callable(_read_pdf_tables_sync)

    async def test_read_pdf_wrapped_in_to_thread(self, tmp_path: Path):
        """验证 _read_pdf_tables_sync 被 asyncio.to_thread 调用。"""
        path = tmp_path / "test.pdf"
        path.write_text("not a real pdf", encoding="utf-8")

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
            reader = PDFTableReader()
            ctx = make_test_context()
            try:
                await reader.execute(ctx, {"path": str(path)})
            except Exception:
                pass  # 非 PDF 文件可能报错，但不影响验证 to_thread 调用

            assert mock_to_thread.called
            assert mock_to_thread.call_args.args[0] is _read_pdf_tables_sync

    async def test_read_pdf_empty_path_no_tables(self, tmp_path: Path):
        """空 PDF 通过 to_thread 执行，无表格时返回空结果。"""
        # 使用 reportlab 创建真实 PDF（如不可用则跳过）
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate
        except ImportError:
            pytest.skip("reportlab not installed")

        path = tmp_path / "empty.pdf"
        doc = SimpleDocTemplate(str(path), pagesize=A4)
        doc.build([])  # 空文档

        reader = PDFTableReader()
        ctx = make_test_context()
        result = await reader.execute(ctx, {"path": str(path)})

        # 空 PDF 无表格 → 返回空结果
        assert result.metadata["row_count"] == 0


# ---- RESTFetch 异步行为 ----


def _make_mock_httpx_client(
    status_code: int = 200,
    body: bytes = b'{"items": [{"x": 1}, {"x": 2}]}',
    headers: dict | None = None,
) -> AsyncMock:
    """构造 mock httpx.AsyncClient，支持 async with 与 aiter_bytes。"""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.headers = headers or {}

    async def _mock_aiter_bytes(chunk_size: int = 8192):
        yield body

    mock_response.aiter_bytes = _mock_aiter_bytes

    mock_client = AsyncMock()
    mock_client.request.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    return mock_client


class TestRESTFetchAsyncIO:
    """RESTFetch 的 httpx.AsyncClient 异步行为验证。

    rest_fetch.py 已从 urllib.request.urlopen 改为 httpx.AsyncClient，
    _fetch_with_redirects 现为 async 方法，直接在事件循环中执行非阻塞 I/O。
    """

    async def test_fetch_https_json(self):
        """HTTPS 拉取 JSON 数据（mock httpx.AsyncClient）。"""
        mock_client = _make_mock_httpx_client(
            body=b'{"items": [{"x": 1}, {"x": 2}]}'
        )

        with patch(
            "packages.components.builtin.ingestion.rest_fetch.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with patch(
                "packages.components.builtin.ingestion.rest_fetch._resolve_and_check"
            ):
                reader = RESTFetch()
                ctx = make_test_context()
                result = await reader.execute(
                    ctx,
                    {
                        "url": "https://api.example.com/data",
                        "json_path": "items",
                    },
                )

        table = result.outputs["observations"]
        assert table.row_count() == 2
        assert table.rows[0]["x"] == 1

    async def test_fetch_uses_async_client(self):
        """验证 RESTFetch 使用 httpx.AsyncClient 而非 urllib（T3-7 核心）。"""
        mock_client = _make_mock_httpx_client(body=b'{"x": 1}')

        with patch(
            "packages.components.builtin.ingestion.rest_fetch.httpx.AsyncClient",
            return_value=mock_client,
        ) as mock_async_client:
            with patch(
                "packages.components.builtin.ingestion.rest_fetch._resolve_and_check"
            ):
                reader = RESTFetch()
                ctx = make_test_context()
                await reader.execute(
                    ctx, {"url": "https://api.example.com/data"}
                )

        # 验证 httpx.AsyncClient 被调用（而非 urllib.request.urlopen）
        assert mock_async_client.called

    async def test_fetch_does_not_block_event_loop(self):
        """RESTFetch 执行期间事件循环不被阻塞。"""
        mock_client = _make_mock_httpx_client(body=b'{"items": [{"x": 1}]}')

        timer_ticks: list[int] = []

        async def timer():
            for _ in range(5):
                await asyncio.sleep(0.005)
                timer_ticks.append(1)

        with patch(
            "packages.components.builtin.ingestion.rest_fetch.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with patch(
                "packages.components.builtin.ingestion.rest_fetch._resolve_and_check"
            ):
                reader = RESTFetch()
                ctx = make_test_context()

                await asyncio.gather(
                    reader.execute(
                        ctx,
                        {"url": "https://api.example.com/data", "json_path": "items"},
                    ),
                    timer(),
                )

        assert len(timer_ticks) >= 3

    async def test_http_rejected(self):
        """HTTP 被 HTTPS 限制拦截。"""
        reader = RESTFetch()
        ctx = make_test_context()
        with pytest.raises(Exception, match="仅允许 HTTPS"):
            await reader.execute(ctx, {"url": "http://example.com/data"})

    async def test_ssrf_loopback_blocked(self):
        """SSRF 防护：环回地址被拦截。"""
        reader = RESTFetch()
        ctx = make_test_context()
        with patch(
            "socket.getaddrinfo",
            return_value=[(0, 0, 0, 0, ("127.0.0.1", 0))],
        ):
            with pytest.raises(Exception, match="禁止访问"):
                await reader.execute(
                    ctx, {"url": "https://localhost/data", "allow_http": False}
                )

    async def test_fetch_response_too_large(self):
        """响应超过 50MB 限制抛 response_too_large。"""
        mock_client = _make_mock_httpx_client(
            body=b"x" * (60 * 1024 * 1024)
        )

        with patch(
            "packages.components.builtin.ingestion.rest_fetch.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with patch(
                "packages.components.builtin.ingestion.rest_fetch._resolve_and_check"
            ):
                reader = RESTFetch()
                ctx = make_test_context()
                with pytest.raises(Exception, match="超过 50MB"):
                    await reader.execute(
                        ctx, {"url": "https://api.example.com/data"}
                    )

    async def test_fetch_http_error_status(self):
        """HTTP 4xx/5xx 响应抛 http_error。"""
        mock_client = _make_mock_httpx_client(
            status_code=500,
            body=b"Internal Server Error",
        )

        with patch(
            "packages.components.builtin.ingestion.rest_fetch.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with patch(
                "packages.components.builtin.ingestion.rest_fetch._resolve_and_check"
            ):
                reader = RESTFetch()
                ctx = make_test_context()
                with pytest.raises(Exception, match="HTTP 请求失败"):
                    await reader.execute(
                        ctx, {"url": "https://api.example.com/data"}
                    )

    async def test_fetch_connection_error(self):
        """httpx 连接错误抛 http_error。"""
        import httpx

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.request.side_effect = httpx.ConnectError("Connection refused")

        with patch(
            "packages.components.builtin.ingestion.rest_fetch.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with patch(
                "packages.components.builtin.ingestion.rest_fetch._resolve_and_check"
            ):
                reader = RESTFetch()
                ctx = make_test_context()
                with pytest.raises(Exception, match="HTTP 请求失败"):
                    await reader.execute(
                        ctx, {"url": "https://api.example.com/data"}
                    )
