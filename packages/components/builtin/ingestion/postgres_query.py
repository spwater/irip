"""PostgreSQL 查询组件。

仅允许 SELECT 查询，使用 sqlparse 解析拦截非 SELECT 语句。
凭据通过 context.secrets 注入，不出现在输出/日志中。

参数：
- query: SQL 查询语句（必填，必须为 SELECT）。
- limit: 最大返回行数（可选，默认 10000）。

安全要求：
- 用 sqlparse 解析 SQL，仅允许 SELECT 语句；
- 拦截 DROP/DELETE/UPDATE/INSERT/ALTER/TRUNCATE 等；
- context.secrets 中的凭据值不出现在输出、错误信息或日志中。
"""

from typing import Any

import sqlparse
from packages.common.errors import AppError

from packages.components.builtin.types import ObservationTable
from packages.components.sdk import ComponentContext, ComponentResult

#: 禁止的 SQL 语句关键字。
_FORBIDDEN_KEYWORDS: frozenset[str] = frozenset({
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER",
    "TRUNCATE", "CREATE", "GRANT", "REVOKE", "MERGE",
    "CALL", "EXEC", "EXECUTE", "COPY",
})

#: 最大返回行数默认值。
_DEFAULT_LIMIT: int = 10000


def _validate_select_only(query: str) -> None:
    """校验 SQL 仅包含 SELECT 语句。

    Args:
        query: SQL 查询字符串。

    Raises:
        AppError: code="forbidden_query"，当包含非 SELECT 语句。
    """
    parsed = sqlparse.parse(query)
    if not parsed:
        raise AppError(
            code="forbidden_query",
            message="SQL 解析为空",
            retryable=False,
            fields={},
        )

    for stmt in parsed:
        stmt_type = stmt.get_type()
        if stmt_type != "SELECT":
            raise AppError(
                code="forbidden_query",
                message="仅允许 SELECT 查询",
                retryable=False,
                fields={"statement_type": stmt_type or "UNKNOWN"},
            )
        # 二次检查：扫描所有 token 关键字
        for token in stmt.flatten():
            if token.ttype in sqlparse.tokens.Keyword:
                kw = str(token).upper()
                if kw in _FORBIDDEN_KEYWORDS:
                    raise AppError(
                        code="forbidden_query",
                        message="仅允许 SELECT 查询",
                        retryable=False,
                        fields={"forbidden_keyword": kw},
                    )


class PostgresQuery:
    """PostgreSQL 只读查询组件。"""

    async def execute(
        self,
        context: ComponentContext,
        params: dict[str, Any],
    ) -> ComponentResult:
        """执行 SELECT 查询并输出 ObservationTable。"""
        query: str = params["query"]
        limit: int = int(params.get("limit", _DEFAULT_LIMIT))

        # 安全校验：仅允许 SELECT
        _validate_select_only(query)

        # 从 secrets 获取连接凭据（不出现在输出中）
        secrets = context.secrets
        dsn = (
            f"postgresql://{secrets.get('user', '')}:"
            f"{secrets.get('password', '')}@"
            f"{secrets.get('host', 'localhost')}:"
            f"{secrets.get('port', '5432')}/"
            f"{secrets.get('database', '')}"
        )

        import asyncio

        def _run_query() -> tuple[tuple[str, ...], list[list[Any]]]:
            import psycopg

            with psycopg.connect(dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(query)
                    col_names: tuple[str, ...] = tuple(
                        desc[0] for desc in cur.description
                    ) if cur.description else ()
                    rows_data: list[list[Any]] = cur.fetchmany(limit)
                    return col_names, rows_data

        columns, raw_rows = await asyncio.to_thread(_run_query)

        data_rows: list[dict[str, Any]] = []
        for row in raw_rows:
            record: dict[str, Any] = {}
            for col_name, val in zip(columns, row):
                record[col_name] = val
            data_rows.append(record)

        table = ObservationTable(
            columns=columns,
            rows=tuple(data_rows),
            source_locations=(),
        )
        return ComponentResult(
            outputs={"observations": table},
            summary=f"查询返回 {table.row_count()} 行",
            metadata={
                "row_count": table.row_count(),
                "column_count": table.column_count(),
            },
        )
