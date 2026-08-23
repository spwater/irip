"""单元测试：PostgreSQL 连接器（无数据库依赖部分）。

覆盖 ``packages/connectors/postgres_connector.py``：
- _to_async_url：URL 转换
- _extract：从 source.config 提取 secret_id / query
- _wrap_query：LIMIT 包装
- _validate_sql：SQL 校验（已有测试覆盖，此处补充边界情况）
- preview / read：使用 mock engine 测试
- _execute：mock engine 测试
- 异常路径：缺少字段、无效 UUID、SQL 校验失败
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from packages.common.errors import AppError
from packages.connectors.contracts import ConnectorSource, PreviewTable, SourceRecord
from packages.connectors.postgres_connector import PostgresConnector, _to_async_url

# ============================================================
# _to_async_url
# ============================================================


class TestToAsyncUrl:
    """_to_async_url URL 转换测试。"""

    def test_psycopg_to_async(self) -> None:
        url = "postgresql+psycopg://user:pass@host:5432/db"
        result = _to_async_url(url)
        assert result == "postgresql+psycopg_async://user:pass@host:5432/db"

    def test_plain_postgres_to_async(self) -> None:
        url = "postgresql://user:pass@host:5432/db"
        result = _to_async_url(url)
        assert result == "postgresql+psycopg_async://user:pass@host:5432/db"

    def test_already_async_unchanged(self) -> None:
        url = "postgresql+psycopg_async://user:pass@host:5432/db"
        result = _to_async_url(url)
        assert result == url

    def test_other_driver_unchanged(self) -> None:
        url = "mysql://user:pass@host:3306/db"
        result = _to_async_url(url)
        assert result == url


# ============================================================
# _extract
# ============================================================


class TestPostgresExtract:
    """PostgresConnector._extract 测试。"""

    def test_extract_valid(self) -> None:
        sid = uuid4()
        source = ConnectorSource(
            kind="postgres",
            config={"secret_id": str(sid), "query": "SELECT 1"},
        )
        result = PostgresConnector._extract(source)
        assert result[0] == sid
        assert result[1] == "SELECT 1"

    def test_extract_missing_secret_id_raises(self) -> None:
        source = ConnectorSource(
            kind="postgres",
            config={"query": "SELECT 1"},
        )
        with pytest.raises(AppError, match="缺少 secret_id"):
            PostgresConnector._extract(source)

    def test_extract_missing_query_raises(self) -> None:
        source = ConnectorSource(
            kind="postgres",
            config={"secret_id": str(uuid4())},
        )
        with pytest.raises(AppError, match="缺少 query"):
            PostgresConnector._extract(source)

    def test_extract_invalid_uuid_raises(self) -> None:
        source = ConnectorSource(
            kind="postgres",
            config={"secret_id": "not-uuid", "query": "SELECT 1"},
        )
        with pytest.raises(AppError, match="合法 UUID"):
            PostgresConnector._extract(source)

    def test_extract_query_as_non_string(self) -> None:
        """query 为非字符串时转为 str。"""
        sid = uuid4()
        source = ConnectorSource(
            kind="postgres",
            config={"secret_id": str(sid), "query": 123},
        )
        result = PostgresConnector._extract(source)
        assert result[1] == "123"


# ============================================================
# _wrap_query (补充已有测试)
# ============================================================


class TestWrapQueryExtra:
    """_wrap_query 补充边界测试。"""

    def test_wrap_complex_query(self) -> None:
        query = "SELECT id, name FROM users WHERE active = true ORDER BY name"
        result = PostgresConnector._wrap_query(query, 50)
        assert "SELECT * FROM (" + query + ") AS _irip_sub LIMIT 50" == result

    def test_wrap_query_negative_limit_clamped(self) -> None:
        result = PostgresConnector._wrap_query("SELECT 1", -5)
        assert "LIMIT 1" in result

    def test_wrap_strips_multiple_trailing_semicolons(self) -> None:
        """rstrip(';') 去除所有尾部分号。"""
        result = PostgresConnector._wrap_query("SELECT 1;;;", 10)
        assert "SELECT 1" in result


# ============================================================
# _validate_sql (补充已有测试)
# ============================================================


class TestValidateSqlExtra:
    """_validate_sql 补充边界测试。"""

    def test_accepts_select_with_cte(self) -> None:
        """CTE + SELECT 通过校验。"""
        PostgresConnector._validate_sql("WITH t AS (SELECT 1) SELECT * FROM t")

    def test_accepts_select_with_subquery(self) -> None:
        PostgresConnector._validate_sql("SELECT * FROM (SELECT 1 AS x) sub")

    def test_rejects_empty_query(self) -> None:
        with pytest.raises(AppError):
            PostgresConnector._validate_sql("")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(AppError):
            PostgresConnector._validate_sql("   ")

    def test_rejects_comment_only(self) -> None:
        with pytest.raises(AppError):
            PostgresConnector._validate_sql("-- just a comment")

    def test_accepts_select_with_comment(self) -> None:
        """带注释的 SELECT 通过。"""
        PostgresConnector._validate_sql("SELECT 1 -- comment")


# ============================================================
# _execute
# ============================================================


class TestPostgresExecute:
    """PostgresConnector._execute 测试（mock engine）。"""

    async def test_execute_success(self) -> None:
        """成功执行 SQL 并返回列名和行。"""
        mock_result = MagicMock()
        mock_result.keys.return_value = ["id", "name"]
        mock_result.fetchall.return_value = [(1, "A"), (2, "B")]

        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = [
            MagicMock(),  # SET LOCAL transaction_read_only
            MagicMock(),  # SET LOCAL statement_timeout
            mock_result,
        ]

        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=None)

        columns, rows = await PostgresConnector._execute(mock_engine, "SELECT id, name FROM users")
        assert columns == ("id", "name")
        assert rows == [[1, "A"], [2, "B"]]

    async def test_execute_query_error_raises_app_error(self) -> None:
        """查询执行失败时抛 AppError。"""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = [
            MagicMock(),
            MagicMock(),
            RuntimeError("syntax error"),
        ]

        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=None)

        with pytest.raises(AppError, match="查询执行失败"):
            await PostgresConnector._execute(mock_engine, "BAD SQL")

    async def test_execute_app_error_re_raises(self) -> None:
        """AppError 异常不被包装，直接 re-raise。"""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = [
            MagicMock(),
            MagicMock(),
            AppError(code="connector_error", message="custom", retryable=True),
        ]

        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=None)

        with pytest.raises(AppError, match="custom"):
            await PostgresConnector._execute(mock_engine, "SELECT 1")


# ============================================================
# preview / read (mock create_async_engine + _execute)
# ============================================================


class TestPostgresPreviewRead:
    """PostgresConnector preview/read 测试（mock engine）。"""

    async def test_preview_success(self) -> None:
        sid = uuid4()
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="postgresql://user:pass@host/db")

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()

        columns = ("id", "name")
        rows = [[1, "A"], [2, "B"]]

        with (
            patch(
                "packages.connectors.postgres_connector.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "packages.connectors.postgres_connector.PostgresConnector._execute",
                new_callable=AsyncMock,
                return_value=(columns, rows),
            ),
        ):
            connector = PostgresConnector(mock_store)
            source = ConnectorSource(
                kind="postgres",
                config={"secret_id": str(sid), "query": "SELECT id, name FROM users"},
            )
            result = await connector.preview(source, limit=10)

        assert isinstance(result, PreviewTable)
        assert result.columns == ("id", "name")
        assert result.row_count == 2
        assert result.rows[0] == (1, "A")

    async def test_preview_validates_sql(self) -> None:
        """preview 时 SQL 校验失败抛异常。"""
        sid = uuid4()
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="postgresql://user:pass@host/db")

        connector = PostgresConnector(mock_store)
        source = ConnectorSource(
            kind="postgres",
            config={"secret_id": str(sid), "query": "DELETE FROM users"},
        )
        with pytest.raises(AppError, match="SELECT"):
            await connector.preview(source, limit=10)

    async def test_read_success(self) -> None:
        sid = uuid4()
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="postgresql://user:pass@host/db")

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()

        columns = ("id", "name")
        rows = [[1, "A"], [2, "B"]]

        with (
            patch(
                "packages.connectors.postgres_connector.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "packages.connectors.postgres_connector.PostgresConnector._execute",
                new_callable=AsyncMock,
                return_value=(columns, rows),
            ),
        ):
            connector = PostgresConnector(mock_store)
            source = ConnectorSource(
                kind="postgres",
                config={"secret_id": str(sid), "query": "SELECT id, name FROM users"},
            )
            records = []
            async for record in connector.read(source):
                records.append(record)

        assert len(records) == 2
        assert all(isinstance(r, SourceRecord) for r in records)
        assert records[0].fields["id"] == "1"
        assert records[0].fields["name"] == "A"

    async def test_read_validates_sql(self) -> None:
        """read 时 SQL 校验失败抛异常。"""
        sid = uuid4()
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="postgresql://user:pass@host/db")

        connector = PostgresConnector(mock_store)
        source = ConnectorSource(
            kind="postgres",
            config={"secret_id": str(sid), "query": "DROP TABLE users"},
        )
        with pytest.raises(AppError, match="SELECT"):
            async for _ in connector.read(source):
                pass

    async def test_preview_disposes_engine(self) -> None:
        """preview 完成后 engine 被释放。"""
        sid = uuid4()
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="postgresql://user:pass@host/db")

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()

        with (
            patch(
                "packages.connectors.postgres_connector.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "packages.connectors.postgres_connector.PostgresConnector._execute",
                new_callable=AsyncMock,
                return_value=(("id",), [[1]]),
            ),
        ):
            connector = PostgresConnector(mock_store)
            source = ConnectorSource(
                kind="postgres",
                config={"secret_id": str(sid), "query": "SELECT 1"},
            )
            await connector.preview(source, limit=10)

        mock_engine.dispose.assert_called_once()

    async def test_read_disposes_engine_on_completion(self) -> None:
        """read 完成后 engine 被释放。"""
        sid = uuid4()
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="postgresql://user:pass@host/db")

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()

        with (
            patch(
                "packages.connectors.postgres_connector.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "packages.connectors.postgres_connector.PostgresConnector._execute",
                new_callable=AsyncMock,
                return_value=(("id",), [[1]]),
            ),
        ):
            connector = PostgresConnector(mock_store)
            source = ConnectorSource(
                kind="postgres",
                config={"secret_id": str(sid), "query": "SELECT 1"},
            )
            async for _ in connector.read(source):
                pass

        mock_engine.dispose.assert_called_once()

    async def test_preview_wraps_query_with_limit(self) -> None:
        """preview 时查询被包装为 LIMIT 子查询。"""
        sid = uuid4()
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="postgresql://user:pass@host/db")

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()

        captured_sql: list[str] = []

        async def mock_execute(engine, sql):
            captured_sql.append(sql)
            return (("id",), [[1]])

        with (
            patch(
                "packages.connectors.postgres_connector.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "packages.connectors.postgres_connector.PostgresConnector._execute",
                side_effect=mock_execute,
            ),
        ):
            connector = PostgresConnector(mock_store)
            source = ConnectorSource(
                kind="postgres",
                config={"secret_id": str(sid), "query": "SELECT id FROM users"},
            )
            await connector.preview(source, limit=50)

        assert "_irip_sub" in captured_sql[0]
        assert "LIMIT 50" in captured_sql[0]

    async def test_read_does_not_wrap_query(self) -> None:
        """read 时查询不被包装（直接使用原始 query）。"""
        sid = uuid4()
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="postgresql://user:pass@host/db")

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()

        captured_sql: list[str] = []

        async def mock_execute(engine, sql):
            captured_sql.append(sql)
            return (("id",), [[1]])

        with (
            patch(
                "packages.connectors.postgres_connector.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "packages.connectors.postgres_connector.PostgresConnector._execute",
                side_effect=mock_execute,
            ),
        ):
            connector = PostgresConnector(mock_store)
            source = ConnectorSource(
                kind="postgres",
                config={"secret_id": str(sid), "query": "SELECT id FROM users"},
            )
            async for _ in connector.read(source):
                pass

        assert captured_sql[0] == "SELECT id FROM users"

    async def test_read_none_value_fills_none(self) -> None:
        """read 时 None 值保持 None。"""
        sid = uuid4()
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value="postgresql://user:pass@host/db")

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()

        columns = ("id", "name")
        rows = [[1, None]]

        with (
            patch(
                "packages.connectors.postgres_connector.create_async_engine",
                return_value=mock_engine,
            ),
            patch(
                "packages.connectors.postgres_connector.PostgresConnector._execute",
                new_callable=AsyncMock,
                return_value=(columns, rows),
            ),
        ):
            connector = PostgresConnector(mock_store)
            source = ConnectorSource(
                kind="postgres",
                config={"secret_id": str(sid), "query": "SELECT 1"},
            )
            records = []
            async for record in connector.read(source):
                records.append(record)

        assert records[0].fields["name"] is None
        assert records[0].fields["id"] == "1"
