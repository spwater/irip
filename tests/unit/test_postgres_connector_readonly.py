"""H-08 PostgreSQL 连接器只读校验单元测试。

覆盖 ``packages/connectors/postgres_connector.py`` 的安全修复：
- ``_validate_sql``：用 sqlparse 校验 SQL 为单条 SELECT 语句，拒绝
  DML（INSERT/UPDATE/DELETE）和 DDL（DROP）以及多语句拼接；
- ``_wrap_query``：将查询包一层 LIMIT 子查询，防止全表扫描；
- ``_execute``：READ ONLY 事务 + statement_timeout（需要真实数据库，
  本文件仅测试无数据库依赖的纯逻辑函数）。

注意：``SELECT pg_sleep(10)`` 在语法上是合法的单条 SELECT，``_validate_sql``
会接受它；针对 pg_sleep 的 DoS 防护由 ``_execute`` 中的 ``statement_timeout``
在运行时执行，而非在 ``_validate_sql`` 语法层拦截。
"""

import pytest

from packages.common.errors import AppError
from packages.connectors.postgres_connector import PostgresConnector


# ---------------------------------------------------------------------------
# _validate_sql：拒绝非 SELECT 语句
# ---------------------------------------------------------------------------


class TestValidateSqlRejectsNonSelect:
    """``_validate_sql`` 拒绝非 SELECT 语句。"""

    @pytest.mark.parametrize(
        "query",
        [
            "INSERT INTO users (id) VALUES (1)",
            "DELETE FROM users WHERE id = 1",
            "UPDATE users SET name = 'x' WHERE id = 1",
            "DROP TABLE users",
            "TRUNCATE TABLE users",
            "CREATE TABLE evil (id int)",
            "ALTER TABLE users DROP COLUMN id",
        ],
    )
    def test_rejects_non_select(self, query: str) -> None:
        """DML/DDL 语句被拒绝，抛出 AppError。"""
        with pytest.raises(AppError) as exc_info:
            PostgresConnector._validate_sql(query)
        assert exc_info.value.code == "validation_failed"

    def test_rejects_insert(self) -> None:
        """INSERT 被拒绝。"""
        with pytest.raises(AppError) as exc_info:
            PostgresConnector._validate_sql("INSERT INTO t VALUES (1)")
        assert "SELECT" in exc_info.value.message

    def test_rejects_delete(self) -> None:
        """DELETE 被拒绝。"""
        with pytest.raises(AppError):
            PostgresConnector._validate_sql("DELETE FROM t WHERE id = 1")

    def test_rejects_update(self) -> None:
        """UPDATE 被拒绝。"""
        with pytest.raises(AppError):
            PostgresConnector._validate_sql("UPDATE t SET name = 'x'")

    def test_rejects_drop(self) -> None:
        """DROP 被拒绝。"""
        with pytest.raises(AppError):
            PostgresConnector._validate_sql("DROP TABLE t")


# ---------------------------------------------------------------------------
# _validate_sql：拒绝多语句
# ---------------------------------------------------------------------------


class TestValidateSqlRejectsMultipleStatements:
    """``_validate_sql`` 拒绝多条 SQL 语句拼接。"""

    def test_rejects_two_selects(self) -> None:
        """两条 SELECT 拼接被拒绝。"""
        with pytest.raises(AppError) as exc_info:
            PostgresConnector._validate_sql("SELECT 1; SELECT 2")
        assert exc_info.value.code == "validation_failed"
        assert "单条" in exc_info.value.message

    def test_rejects_select_then_drop(self) -> None:
        """SELECT; DROP 拼接被拒绝（防注入）。"""
        with pytest.raises(AppError):
            PostgresConnector._validate_sql("SELECT 1; DROP TABLE users")

    def test_rejects_select_then_insert(self) -> None:
        """SELECT; INSERT 拼接被拒绝。"""
        with pytest.raises(AppError):
            PostgresConnector._validate_sql("SELECT 1; INSERT INTO t VALUES (1)")

    def test_rejects_three_statements(self) -> None:
        """三条语句拼接被拒绝。"""
        with pytest.raises(AppError):
            PostgresConnector._validate_sql(
                "SELECT 1; SELECT 2; DROP TABLE users"
            )


# ---------------------------------------------------------------------------
# _validate_sql：接受合法 SELECT
# ---------------------------------------------------------------------------


class TestValidateSqlAcceptsSelect:
    """``_validate_sql`` 接受合法的单条 SELECT 语句。"""

    def test_accepts_simple_select(self) -> None:
        """简单 SELECT 通过校验。"""
        # 不抛异常即通过
        PostgresConnector._validate_sql("SELECT 1")

    def test_accepts_select_with_limit(self) -> None:
        """带 LIMIT 的 SELECT 通过校验。"""
        PostgresConnector._validate_sql("SELECT * FROM users LIMIT 10")

    def test_accepts_select_with_where(self) -> None:
        """带 WHERE 的 SELECT 通过校验。"""
        PostgresConnector._validate_sql(
            "SELECT id, name FROM users WHERE active = true"
        )

    def test_accepts_select_with_join(self) -> None:
        """带 JOIN 的 SELECT 通过校验。"""
        PostgresConnector._validate_sql(
            "SELECT u.id FROM users u JOIN orders o ON u.id = o.user_id"
        )

    def test_accepts_select_star(self) -> None:
        """SELECT * 通过校验。"""
        PostgresConnector._validate_sql("SELECT * FROM my_table")

    def test_accepts_select_with_trailing_semicolon(self) -> None:
        """带尾部分号的单条 SELECT 通过校验。"""
        PostgresConnector._validate_sql("SELECT 1;")


# ---------------------------------------------------------------------------
# _validate_sql：pg_sleep 行为
# ---------------------------------------------------------------------------


class TestValidateSqlPgSleep:
    """``_validate_sql`` 对 pg_sleep 的处理。

    ``SELECT pg_sleep(10)`` 在语法上是合法的单条 SELECT 语句，
    ``_validate_sql`` 语法层会接受它。针对 pg_sleep 的 DoS 防护由
    ``_execute`` 中的 ``statement_timeout = '30s'`` 在运行时执行，
    而非在 ``_validate_sql`` 语法层拦截。
    """

    def test_pg_sleep_select_passes_validation(self) -> None:
        """``SELECT pg_sleep(10)`` 语法上为单条 SELECT，通过语法校验。

        运行时防护由 ``_execute`` 的 ``statement_timeout`` 提供。
        """
        # 语法层接受 — 它是一条合法的 SELECT
        PostgresConnector._validate_sql("SELECT pg_sleep(10)")

    def test_pg_sleep_with_extra_drop_rejected(self) -> None:
        """``SELECT pg_sleep(10); DROP TABLE x`` 因多语句被拒绝。"""
        with pytest.raises(AppError):
            PostgresConnector._validate_sql(
                "SELECT pg_sleep(10); DROP TABLE users"
            )


# ---------------------------------------------------------------------------
# _wrap_query：LIMIT 包装行为
# ---------------------------------------------------------------------------


class TestWrapQuery:
    """``_wrap_query`` 将查询包一层 LIMIT 子查询。"""

    def test_wraps_with_limit(self) -> None:
        """查询被包为 ``SELECT * FROM (...) AS _irip_sub LIMIT n``。"""
        result = PostgresConnector._wrap_query("SELECT * FROM users", 50)
        assert "SELECT * FROM (SELECT * FROM users) AS _irip_sub LIMIT 50" == result

    def test_strips_trailing_semicolon(self) -> None:
        """尾部分号被移除。"""
        result = PostgresConnector._wrap_query("SELECT 1;", 10)
        assert result == "SELECT * FROM (SELECT 1) AS _irip_sub LIMIT 10"

    def test_clamps_limit_to_min(self) -> None:
        """limit < 1 被钳制为 1。"""
        result = PostgresConnector._wrap_query("SELECT 1", 0)
        assert "LIMIT 1" in result

    def test_clamps_limit_to_max(self) -> None:
        """limit > 10000 被钳制为 10000。"""
        result = PostgresConnector._wrap_query("SELECT 1", 999999)
        assert "LIMIT 10000" in result

    def test_limit_one(self) -> None:
        """limit = 1 正常工作。"""
        result = PostgresConnector._wrap_query("SELECT 1", 1)
        assert "LIMIT 1" in result

    def test_default_limit_not_applied_by_wrap(self) -> None:
        """``_wrap_query`` 不自行添加默认 limit，由调用方传入。"""
        result = PostgresConnector._wrap_query("SELECT 1", 100)
        assert "LIMIT 100" in result
