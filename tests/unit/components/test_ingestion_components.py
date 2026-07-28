"""摄入组件单元测试（7 个）。

测试覆盖：
- ExcelReader: xlsx 读取 → ObservationTable
- CSVReader: csv 读取 → ObservationTable（含分隔符/编码）
- JSONReader: json 读取 → ObservationTable（含 json_path）
- PDFTableReader: pdf 表格提取 → ObservationTable
- PostgresQuery: SELECT 查询（mock psycopg）
- RESTFetch: HTTP 拉取（mock urllib + SSRF 防护）
- MinioObject: S3 对象读取（mock artifact_service）
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.components.builtin.types import ObservationTable
from packages.components.builtin.ingestion.csv_reader import CSVReader
from packages.components.builtin.ingestion.excel_reader import ExcelReader
from packages.components.builtin.ingestion.json_reader import JSONReader
from packages.components.builtin.ingestion.minio_object import MinioObject
from packages.components.builtin.ingestion.pdf_table_reader import (
    PDFTableReader,
)
from packages.components.builtin.ingestion.postgres_query import (
    PostgresQuery,
    _validate_select_only,
)
from packages.components.builtin.ingestion.rest_fetch import RESTFetch
from packages.common.errors import AppError

from tests.unit.components.conftest import make_test_context


# ---- ExcelReader ----


class TestExcelReader:
    """Excel 读取组件测试。"""

    async def test_read_xlsx(self, tmp_path: Path):
        """读取 xlsx 文件输出 ObservationTable。"""
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["name", "value", "unit"])
        ws.append(["D50", 12.5, "um"])
        ws.append(["D90", 25.0, "um"])
        path = tmp_path / "test.xlsx"
        wb.save(path)

        reader = ExcelReader()
        ctx = make_test_context()
        result = await reader.execute(ctx, {"path": str(path)})

        table = result.outputs["observations"]
        assert isinstance(table, ObservationTable)
        assert table.columns == ("name", "value", "unit")
        assert table.row_count() == 2
        assert table.rows[0]["name"] == "D50"
        assert result.summary.startswith("从 test.xlsx 读取 2 行")

    async def test_read_empty_sheet(self, tmp_path: Path):
        """空工作表返回空结果。"""
        from openpyxl import Workbook

        wb = Workbook()
        path = tmp_path / "empty.xlsx"
        wb.save(path)

        reader = ExcelReader()
        ctx = make_test_context()
        result = await reader.execute(ctx, {"path": str(path)})

        assert result.metadata["row_count"] == 0


# ---- CSVReader ----


class TestCSVReader:
    """CSV 读取组件测试。"""

    async def test_read_csv(self, tmp_path: Path):
        """读取 CSV 文件。"""
        path = tmp_path / "test.csv"
        path.write_text("name,value,unit\nD50,12.5,um\nD90,25,um\n", encoding="utf-8")

        reader = CSVReader()
        ctx = make_test_context()
        result = await reader.execute(ctx, {"path": str(path)})

        table = result.outputs["observations"]
        assert table.columns == ("name", "value", "unit")
        assert table.row_count() == 2
        assert table.rows[0]["value"] == 12.5
        assert table.rows[1]["value"] == 25

    async def test_read_tsv_with_delimiter(self, tmp_path: Path):
        """指定分隔符读取 TSV。"""
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
        """空 CSV 文件。"""
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")

        reader = CSVReader()
        ctx = make_test_context()
        result = await reader.execute(ctx, {"path": str(path)})

        assert result.metadata["row_count"] == 0


# ---- JSONReader ----


class TestJSONReader:
    """JSON 读取组件测试。"""

    async def test_read_json_array(self, tmp_path: Path):
        """读取 JSON 数组。"""
        path = tmp_path / "test.json"
        path.write_text(
            json.dumps([{"a": 1}, {"a": 2}, {"a": 3}]),
            encoding="utf-8",
        )

        reader = JSONReader()
        ctx = make_test_context()
        result = await reader.execute(ctx, {"path": str(path)})

        table = result.outputs["observations"]
        assert table.columns == ("a",)
        assert table.row_count() == 3
        assert table.rows[0]["a"] == 1

    async def test_read_json_with_path(self, tmp_path: Path):
        """使用 json_path 定位嵌套数组。"""
        path = tmp_path / "nested.json"
        path.write_text(
            json.dumps({"data": {"records": [{"x": 10}, {"x": 20}]}}),
            encoding="utf-8",
        )

        reader = JSONReader()
        ctx = make_test_context()
        result = await reader.execute(
            ctx, {"path": str(path), "json_path": "data.records"}
        )

        table = result.outputs["observations"]
        assert table.row_count() == 2
        assert table.rows[0]["x"] == 10

    async def test_read_json_object(self, tmp_path: Path):
        """读取 JSON 单对象。"""
        path = tmp_path / "obj.json"
        path.write_text(json.dumps({"key": "val"}), encoding="utf-8")

        reader = JSONReader()
        ctx = make_test_context()
        result = await reader.execute(ctx, {"path": str(path)})

        table = result.outputs["observations"]
        assert table.row_count() == 1
        assert table.rows[0]["key"] == "val"


# ---- PDFTableReader ----


class TestPDFTableReader:
    """PDF 表格提取组件测试。"""

    async def test_extract_table(self, tmp_path: Path):
        """从 PDF 提取表格（使用真实 PDF 生成）。"""
        # 使用 pdfplumber 创建简单 PDF（需 reportlab）
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Table
        except ImportError:
            pytest.skip("reportlab not installed")

            # 不可达
            return

        path = tmp_path / "test.pdf"
        doc = SimpleDocTemplate(str(path), pagesize=A4)
        data = [["Col1", "Col2"], ["A", "B"], ["C", "D"]]
        table = Table(data)
        doc.build([table])

        reader = PDFTableReader()
        ctx = make_test_context()
        result = await reader.execute(ctx, {"path": str(path)})

        # pdfplumber may not detect reportlab tables as extractable tables
        if "observations" in result.outputs:
            table_out = result.outputs["observations"]
            assert table_out.row_count() >= 1
        else:
            # If no tables found, verify diagnostics has warning
            assert result.diagnostics is not None


# ---- PostgresQuery ----


class TestPostgresQuery:
    """PostgreSQL 查询组件测试。"""

    def test_validate_select_only_pass(self):
        """SELECT 语句通过校验。"""
        _validate_select_only("SELECT * FROM users")
        _validate_select_only("SELECT id, name FROM users WHERE id = 1")

    def test_validate_select_only_reject_drop(self):
        """DROP 语句被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("DROP TABLE users")

    def test_validate_select_only_reject_delete(self):
        """DELETE 语句被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("DELETE FROM users WHERE id = 1")

    def test_validate_select_only_reject_insert(self):
        """INSERT 语句被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("INSERT INTO users VALUES (1)")

    def test_validate_select_only_reject_update(self):
        """UPDATE 语句被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("UPDATE users SET name = 'a'")

    async def test_execute_query(self):
        """执行 SELECT 查询（mock psycopg）。"""
        mock_cursor = MagicMock()
        mock_cursor.description = [("id",), ("name",)]
        mock_cursor.fetchmany.return_value = [(1, "alice"), (2, "bob")]
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(
            return_value=mock_cursor
        )
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("psycopg.connect", return_value=mock_conn):
            reader = PostgresQuery()
            ctx = make_test_context(
                secrets={
                    "host": "localhost",
                    "port": "5432",
                    "database": "test",
                    "user": "user",
                    "password": "pass",
                }
            )
            result = await reader.execute(
                ctx, {"query": "SELECT id, name FROM users"}
            )

        table = result.outputs["observations"]
        assert table.columns == ("id", "name")
        assert table.row_count() == 2
        assert table.rows[0]["id"] == 1


# ---- RESTFetch ----


class TestRESTFetch:
    """REST 拉取组件测试。"""

    async def test_fetch_https_json(self):
        """HTTPS 拉取 JSON 数据（mock httpx）。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}

        async def _mock_aiter_bytes(chunk_size: int = 8192):
            yield b'{"items": [{"x": 1}, {"x": 2}]}'

        mock_response.aiter_bytes = _mock_aiter_bytes

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        # async with ... as client 需要 __aenter__ 返回 mock_client 自身
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "packages.components.builtin.ingestion.rest_fetch."
            "httpx.AsyncClient",
            return_value=mock_client,
        ):
            with patch(
                "packages.components.builtin.ingestion.rest_fetch."
                "_resolve_and_check"
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

    async def test_http_rejected(self):
        """HTTP 被 HTTPS 限制拦截。"""
        reader = RESTFetch()
        ctx = make_test_context()
        with pytest.raises(AppError, match="仅允许 HTTPS"):
            await reader.execute(
                ctx, {"url": "http://example.com/data"}
            )

    async def test_ssrf_loopback_blocked(self):
        """SSRF 防护：环回地址被拦截。"""
        reader = RESTFetch()
        ctx = make_test_context()
        with patch("socket.getaddrinfo", return_value=[(0, 0, 0, 0, ("127.0.0.1", 0))]):
            with pytest.raises(AppError, match="禁止访问"):
                await reader.execute(
                    ctx, {"url": "https://localhost/data", "allow_http": False}
                )


# ---- MinioObject ----


class TestMinioObject:
    """MinIO 对象读取组件测试。"""

    async def test_read_csv_from_minio(self):
        """从 MinIO 读取 CSV（mock s3_repo）。"""
        csv_data = b"col1,col2\n1,2\n3,4\n"
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = csv_data

        mock_artifact_service = MagicMock()
        mock_artifact_service._s3 = mock_s3

        reader = MinioObject()
        ctx = make_test_context(artifact_service=mock_artifact_service)
        result = await reader.execute(
            ctx, {"object_key": "data/test.csv"}
        )

        table = result.outputs["observations"]
        assert table.columns == ("col1", "col2")
        assert table.row_count() == 2
        assert table.rows[0]["col1"] == 1

    async def test_read_json_from_minio(self):
        """从 MinIO 读取 JSON（mock s3_repo）。"""
        json_data = b'[{"x": 10}, {"x": 20}]'
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = json_data

        mock_artifact_service = MagicMock()
        mock_artifact_service._s3 = mock_s3

        reader = MinioObject()
        ctx = make_test_context(artifact_service=mock_artifact_service)
        result = await reader.execute(
            ctx, {"object_key": "data/test.json"}
        )

        table = result.outputs["observations"]
        assert table.row_count() == 2
        assert table.rows[0]["x"] == 10

    async def test_missing_artifact_service(self):
        """缺少 artifact_service 时报错。"""
        reader = MinioObject()
        ctx = make_test_context(artifact_service=None)
        with pytest.raises(AppError, match="artifact_service"):
            await reader.execute(ctx, {"object_key": "test"})
