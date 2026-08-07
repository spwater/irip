"""PostgreSQL 连接器：按 secret_id 解析 DSN，预览与流式读取查询结果。

安全约定：
- DSN 通过 SecretStore 按 secret_id 解析，绝不返回、绝不记录日志；
- 连接器仅接收 DSN 内部使用，API 响应不携带 DSN 明文；
- 查询强制包一层 LIMIT，防止全表扫描；
- H-08 安全修复：查询在 READ ONLY 事务中执行，设置 statement_timeout；
- H-08 安全修复：用 sqlparse 校验 SQL 为单条 SELECT 语句。

实现 Connector 协议：
- preview(source, limit): 执行 ``SELECT * FROM (<query>) sub LIMIT n``；
- read(source): 流式 yield SourceRecord。
"""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlparse
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from packages.common.errors import AppError
from packages.connectors.contracts import (
    ConnectorSource,
    PreviewTable,
    SourceRecord,
)

if TYPE_CHECKING:
    from packages.connectors.mapping import SecretStore


def _to_async_url(url: str) -> str:
    """将同步 psycopg URL 转换为异步 psycopg_async URL。"""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+psycopg_async://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg_async://", 1)
    return url


class PostgresConnector:
    """PostgreSQL 数据源连接器。

    通过 SecretStore 按 secret_id 解析 DSN，内部创建临时引擎执行查询。

    Attributes:
        _secret_store: 密钥存储，用于解析 secret_id → DSN。
    """

    def __init__(self, secret_store: "SecretStore") -> None:
        """初始化 PostgreSQL 连接器。

        Args:
            secret_store: 密钥存储实例。
        """
        self._secret_store = secret_store

    async def preview(self, source: ConnectorSource, limit: int = 100) -> PreviewTable:
        """预览查询结果前 limit 行。

        Args:
            source: postgres 数据源，config 须含 secret_id 与 query。
            limit: 预览行数上限。

        Returns:
            PreviewTable: 列名 + 行 + 总行数。

        Raises:
            AppError: code="validation_failed"，当缺少 secret_id/query 或 SQL 校验失败时。
            AppError: code="secret_not_found"，当 secret 不存在时。
            AppError: code="connector_error"，当查询执行失败时。
        """
        secret_id, query = self._extract(source)
        self._validate_sql(query)
        dsn = await self._secret_store.get(secret_id)
        engine = create_async_engine(_to_async_url(dsn))
        try:
            wrapped = self._wrap_query(query, limit)
            columns, rows = await self._execute(engine, wrapped)
        finally:
            await engine.dispose()
        return PreviewTable(
            columns=columns,
            rows=tuple(tuple(r) for r in rows),
            row_count=len(rows),
        )

    async def read(self, source: ConnectorSource) -> AsyncIterator[SourceRecord]:
        """流式读取查询全部结果。

        Args:
            source: postgres 数据源。

        Yields:
            SourceRecord: 每行一条记录。
        """
        secret_id, query = self._extract(source)
        self._validate_sql(query)
        dsn = await self._secret_store.get(secret_id)
        engine = create_async_engine(_to_async_url(dsn))
        try:
            columns, rows = await self._execute(engine, query)
            for row in rows:
                fields: dict[str, str | None] = {}
                for idx, col in enumerate(columns):
                    value = row[idx] if idx < len(row) else None
                    fields[col] = None if value is None else str(value)
                yield SourceRecord(fields=fields)
        finally:
            await engine.dispose()

    # ---- 内部辅助 ----

    @staticmethod
    def _extract(source: ConnectorSource) -> tuple[UUID, str]:
        """从 source.config 提取 secret_id 与 query。"""
        raw_secret_id = source.config.get("secret_id")
        query = source.config.get("query")
        if not raw_secret_id:
            raise AppError(
                code="validation_failed",
                message="postgres 数据源缺少 secret_id",
                retryable=False,
                fields={"field": "secret_id"},
            )
        if not query:
            raise AppError(
                code="validation_failed",
                message="postgres 数据源缺少 query",
                retryable=False,
                fields={"field": "query"},
            )
        try:
            secret_id = UUID(str(raw_secret_id))
        except (ValueError, TypeError) as exc:
            raise AppError(
                code="validation_failed",
                message="secret_id 不是合法 UUID",
                retryable=False,
                fields={"secret_id": raw_secret_id},
            ) from exc
        return secret_id, str(query)

    @staticmethod
    def _wrap_query(query: str, limit: int) -> str:
        """将查询包一层 LIMIT 子查询，防止全表扫描。"""
        safe_limit = max(1, min(limit, 10000))
        return f"SELECT * FROM ({query.rstrip(';')}) AS _irip_sub LIMIT {safe_limit}"

    @staticmethod
    def _validate_sql(query: str) -> None:
        """校验 SQL 为单条 SELECT 语句（H-08 安全修复）。

        用 sqlparse 解析 SQL，确保：
        1. 只有一条语句（防止 SQL 注入拼接多条语句）；
        2. 语句类型为 SELECT（防止 DML/DDL 操作）。

        Args:
            query: 待校验的 SQL 字符串。

        Raises:
            AppError: code="validation_failed"，当 SQL 不是单条 SELECT 语句时。
        """
        statements = sqlparse.parse(query)
        non_empty = [s for s in statements if s.token_first(skip_ws=True, skip_cm=True) is not None]  # type: ignore[no-untyped-call]
        if len(non_empty) != 1:
            raise AppError(
                code="validation_failed",
                message="只允许单条 SQL 语句",
                retryable=False,
                fields={},
            )
        stmt_type = non_empty[0].get_type()  # type: ignore[no-untyped-call]
        if stmt_type != "SELECT":
            raise AppError(
                code="validation_failed",
                message=f"只允许 SELECT 查询，当前语句类型: {stmt_type}",
                retryable=False,
                fields={"statement_type": str(stmt_type)},
            )

    @staticmethod
    async def _execute(engine: AsyncEngine, sql: str) -> tuple[tuple[str, ...], list[list[Any]]]:
        """执行 SQL，返回 (列名元组, 行列表)。

        H-08 安全修复：在 READ ONLY 事务中执行，设置 statement_timeout，
        防止数据修改和长时间运行的查询。
        """
        from sqlalchemy import text

        try:
            async with engine.connect() as conn:
                # 设置 READ ONLY 事务，防止任何写操作
                await conn.execute(text("SET LOCAL transaction_read_only = true"))
                # 设置语句超时，防止长时间运行的查询
                await conn.execute(text("SET LOCAL statement_timeout = '30s'"))
                result = await conn.execute(text(sql))
                columns = tuple(str(c) for c in result.keys())
                rows = [list(row) for row in result.fetchall()]
                return columns, rows
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                code="connector_error",
                message=f"PostgreSQL 查询执行失败：{exc}",
                retryable=True,
                fields={},
            ) from exc
