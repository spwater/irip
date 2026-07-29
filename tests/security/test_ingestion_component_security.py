"""摄入组件安全测试。

测试覆盖：
- SQL 注入防护：postgres_query 拦截非 SELECT 语句；
- SSRF 防护：rest_fetch 拦截内网/环回地址；
- 凭据泄露防护：secrets 值不出现在输出/错误/日志中。
"""

import pytest

from packages.common.errors import AppError
from packages.components.builtin.ingestion.postgres_query import (
    PostgresQuery,
    _validate_select_only,
)
from packages.components.builtin.ingestion.rest_fetch import (
    RESTFetch,
    _check_ip_allowed,
    _resolve_and_check,
)
from tests.unit.components.conftest import make_test_context

# ===== SQL 注入防护 =====


class TestSQLInjection:
    """SQL 注入防护测试。"""

    def test_select_passes(self):
        """SELECT 语句通过。"""
        _validate_select_only("SELECT * FROM users")
        _validate_select_only("  SELECT id FROM users WHERE id = 1  ")

    def test_drop_blocked(self):
        """DROP 被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("DROP TABLE users")

    def test_delete_blocked(self):
        """DELETE 被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("DELETE FROM users WHERE id = 1")

    def test_update_blocked(self):
        """UPDATE 被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("UPDATE users SET name = 'hack'")

    def test_insert_blocked(self):
        """INSERT 被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("INSERT INTO users VALUES (1, 'hack')")

    def test_truncate_blocked(self):
        """TRUNCATE 被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("TRUNCATE TABLE users")

    def test_alter_blocked(self):
        """ALTER 被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("ALTER TABLE users DROP COLUMN name")

    def test_create_blocked(self):
        """CREATE 被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("CREATE TABLE hack (id int)")

    def test_grant_blocked(self):
        """GRANT 被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("GRANT ALL ON users TO public")

    def test_multiple_statements_blocked(self):
        """多语句中包含非 SELECT 被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("SELECT * FROM users; DROP TABLE users")

    def test_empty_sql_blocked(self):
        """空 SQL 被拦截。"""
        with pytest.raises(AppError, match="SQL 解析为空"):
            _validate_select_only("")

    def test_non_sql_blocked(self):
        """非 SQL 文本被拦截。"""
        with pytest.raises(AppError, match="仅允许 SELECT"):
            _validate_select_only("hello world")


# ===== SSRF 防护 =====


class TestSSRFProtection:
    """SSRF 防护测试。"""

    def test_loopback_127_blocked(self):
        """127.0.0.1 被拦截。"""
        with pytest.raises(AppError, match="禁止访问"):
            _check_ip_allowed("127.0.0.1")

    def test_private_10_blocked(self):
        """10.x.x.x 被拦截。"""
        with pytest.raises(AppError, match="禁止访问"):
            _check_ip_allowed("10.0.0.1")

    def test_private_172_blocked(self):
        """172.16.x.x 被拦截。"""
        with pytest.raises(AppError, match="禁止访问"):
            _check_ip_allowed("172.16.0.1")

    def test_private_192_blocked(self):
        """192.168.x.x 被拦截。"""
        with pytest.raises(AppError, match="禁止访问"):
            _check_ip_allowed("192.168.1.1")

    def test_link_local_blocked(self):
        """169.254.x.x 被拦截。"""
        with pytest.raises(AppError, match="禁止访问"):
            _check_ip_allowed("169.254.1.1")

    def test_ipv6_loopback_blocked(self):
        """::1 被拦截。"""
        with pytest.raises(AppError, match="禁止访问"):
            _check_ip_allowed("::1")

    def test_ipv6_ula_blocked(self):
        """fc00:: 被拦截。"""
        with pytest.raises(AppError, match="禁止访问"):
            _check_ip_allowed("fc00::1")

    def test_public_ip_allowed(self):
        """公网 IP 通过。"""
        _check_ip_allowed("8.8.8.8")  # 不抛异常即通过
        _check_ip_allowed("1.1.1.1")

    def test_resolve_loopback_blocked(self):
        """解析主机名为环回地址时被拦截。"""
        from unittest.mock import patch

        mock_addrinfo = [(0, 0, 0, 0, ("127.0.0.1", 0))]
        with patch("socket.getaddrinfo", return_value=mock_addrinfo):
            with pytest.raises(AppError, match="禁止访问"):
                _resolve_and_check("localhost")

    def test_resolve_public_allowed(self):
        """解析主机名为公网 IP 时通过。"""
        from unittest.mock import patch

        mock_addrinfo = [(0, 0, 0, 0, ("93.184.216.34", 0))]
        with patch("socket.getaddrinfo", return_value=mock_addrinfo):
            _resolve_and_check("example.com")  # 不抛异常即通过

    async def test_http_rejected_by_default(self):
        """HTTP 默认被 HTTPS 限制拦截。"""
        reader = RESTFetch()
        ctx = make_test_context()
        with pytest.raises(AppError, match="仅允许 HTTPS"):
            await reader.execute(ctx, {"url": "http://example.com/data"})

    async def test_non_http_scheme_rejected(self):
        """非 HTTP/HTTPS 协议被拦截。"""
        reader = RESTFetch()
        ctx = make_test_context()
        with pytest.raises(AppError, match="仅支持 HTTP"):
            await reader.execute(ctx, {"url": "ftp://example.com/file"})

    async def test_file_scheme_rejected(self):
        """file:// 协议被拦截。"""
        reader = RESTFetch()
        ctx = make_test_context()
        with pytest.raises(AppError, match="仅支持 HTTP"):
            await reader.execute(ctx, {"url": "file:///etc/passwd"})


# ===== 凭据泄露防护 =====


class TestCredentialLeakage:
    """凭据泄露防护测试。"""

    async def test_secrets_not_in_output(self):
        """凭据值不出现在组件输出中。"""
        from unittest.mock import MagicMock, patch

        secret_password = "super_secret_12345"

        mock_cursor = MagicMock()
        mock_cursor.description = [("id",)]
        mock_cursor.fetchmany.return_value = [(1,)]
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("psycopg.connect", return_value=mock_conn):
            reader = PostgresQuery()
            ctx = make_test_context(
                secrets={
                    "host": "localhost",
                    "port": "5432",
                    "database": "test",
                    "user": "admin",
                    "password": secret_password,
                }
            )
            result = await reader.execute(ctx, {"query": "SELECT id FROM users"})

        # 检查输出中不包含密码
        result_str = str(result.outputs) + str(result.metadata) + str(result.summary)
        assert secret_password not in result_str
        assert "super_secret_12345" not in result_str

    async def test_secrets_not_in_error(self):
        """凭据值不出现在错误信息中。"""
        reader = PostgresQuery()
        ctx = make_test_context(
            secrets={
                "host": "localhost",
                "port": "5432",
                "database": "test",
                "user": "admin",
                "password": "my_secret_password",
            }
        )
        try:
            await reader.execute(ctx, {"query": "DROP TABLE users"})
        except AppError as exc:
            error_str = str(exc.message) + str(exc.fields)
            assert "my_secret_password" not in error_str
        except Exception:
            pass  # 其他异常类型也可，只要不含密码

    async def test_auth_token_not_in_output(self):
        """认证 token 不出现在输出中。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}

        async def _mock_aiter_bytes(chunk_size: int = 8192):
            yield b'[{"x": 1}]'

        mock_response.aiter_bytes = _mock_aiter_bytes

        mock_client = AsyncMock()
        mock_client.request.return_value = mock_response
        # async with ... as client 需要 __aenter__ 返回 mock_client 自身
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch(
            "packages.components.builtin.ingestion.rest_fetch.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with patch("packages.components.builtin.ingestion.rest_fetch._resolve_and_check"):
                reader = RESTFetch()
                ctx = make_test_context(secrets={"api_token": "secret_token_xyz_789"})
                result = await reader.execute(
                    ctx,
                    {
                        "url": "https://api.example.com/data",
                        "auth_header_secret": "api_token",
                    },
                )

        result_str = str(result.outputs) + str(result.metadata) + str(result.summary)
        assert "secret_token_xyz_789" not in result_str

    async def test_secrets_not_in_diagnostics(self):
        """凭据值不出现在 diagnostics 中。"""
        from unittest.mock import MagicMock, patch

        secret_password = "leak_test_password_456"

        mock_cursor = MagicMock()
        mock_cursor.description = [("id",)]
        mock_cursor.fetchmany.return_value = [(1,)]
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("psycopg.connect", return_value=mock_conn):
            reader = PostgresQuery()
            ctx = make_test_context(secrets={"password": secret_password})
            result = await reader.execute(ctx, {"query": "SELECT id FROM users"})

        if result.diagnostics:
            diag_str = str(result.diagnostics)
            assert secret_password not in diag_str
