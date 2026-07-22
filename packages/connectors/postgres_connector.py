"""PostgreSQL 连接器：按 secret_id 解析 DSN，预览与流式读取查询结果。

安全约定：
- DSN 通过 SecretStore 按 secret_id 解析，绝不返回、绝不记录日志；
- 连接器仅接收 DSN 内部使用，API 响应不携带 DSN 明文；
- 查询强制包一层 LIMIT，防止全表扫描。

实现 Connector 协议：
- preview(source, limit): 执行 ``SELECT * FROM (<query>) sub LIMIT n``；
- read(source): 流式 yield SourceRecord。
"""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import UUID

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
        return url.replace(
            "postgresql+psycopg://", "postgresql+psycopg_async://", 1
        )
    if url.startswith("postgresql://"):
        return url.replace(
            "postgresql://", "postgresql+psycopg_async://", 1
        )
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

    async def preview(
        self, source: ConnectorSource, limit: int = 100
    ) -> PreviewTable:
        """预览查询结果前 limit 行。

        Args:
            source: postgres 数据源，config 须含 secret_id 与 query。
            limit: 预览行数上限。

        Returns:
            PreviewTable: 列名 + 行 + 总行数。

        Raises:
            AppError: code="validation_failed"，当缺少 secret_id/query 时。
            AppError: code="secret_not_found"，当 secret 不存在时。
            AppError: code="connector_error"，当查询执行失败时。
        """
        secret_id, query = self._extract(source)
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

    async def read(
        self, source: ConnectorSource
    ) -> AsyncIterator[SourceRecord]:
        """流式读取查询全部结果。

        Args:
            source: postgres 数据源。

        Yields:
            SourceRecord: 每行一条记录。
        """
        secret_id, query = self._extract(source)
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
    async def _execute(
        engine: AsyncEngine, sql: str
    ) -> tuple[tuple[str, ...], list[list]]:
        """执行 SQL，返回 (列名元组, 行列表)。"""
        from sqlalchemy import text

        try:
            async with engine.connect() as conn:
                result = await conn.execute(text(sql))
                columns = tuple(str(c) for c in result.keys())
                rows = [list(row) for row in result.fetchall()]
                return columns, rows
        except Exception as exc:
            raise AppError(
                code="connector_error",
                message=f"PostgreSQL 查询执行失败：{exc}",
                retryable=True,
                fields={},
            ) from exc
