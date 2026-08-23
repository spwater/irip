"""单元测试：REST API 连接器。

覆盖 ``packages/connectors/rest_connector.py``：
- _extract：从 source.config 提取 secret_id / path / method
- _parse_secret：解析 secret value JSON
- _fetch：HTTP 请求与 JSON 数组解析
- preview：预览 REST 端点响应
- read：流式读取记录
- 异常路径：缺少字段、无效 UUID、非法 JSON、非数组响应
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
import respx

from packages.common.errors import AppError
from packages.connectors.contracts import ConnectorSource, PreviewTable, SourceRecord
from packages.connectors.rest_connector import RestConnector


@pytest.fixture(autouse=True)
def _allow_private_network():
    """绕过 SafeHTTPClient 的 SSRF DNS 校验，使 respx 能拦截请求。"""
    with patch.dict("os.environ", {"IRIP_ALLOW_PRIVATE_NETWORK": "1"}):
        yield


# ============================================================
# _extract
# ============================================================


class TestRestExtract:
    """RestConnector._extract 测试。"""

    def test_extract_valid(self) -> None:
        sid = uuid4()
        source = ConnectorSource(
            kind="rest",
            config={"secret_id": str(sid), "path": "/api/data", "method": "GET"},
        )
        result = RestConnector._extract(source)
        assert result[0] == sid
        assert result[1] == "/api/data"
        assert result[2] == "GET"

    def test_extract_default_method_is_get(self) -> None:
        sid = uuid4()
        source = ConnectorSource(
            kind="rest",
            config={"secret_id": str(sid), "path": "/api/data"},
        )
        result = RestConnector._extract(source)
        assert result[2] == "GET"

    def test_extract_post_uppercased(self) -> None:
        sid = uuid4()
        source = ConnectorSource(
            kind="rest",
            config={"secret_id": str(sid), "path": "/api", "method": "post"},
        )
        result = RestConnector._extract(source)
        assert result[2] == "POST"

    def test_extract_missing_secret_id_raises(self) -> None:
        source = ConnectorSource(kind="rest", config={"path": "/api"})
        with pytest.raises(AppError, match="缺少 secret_id"):
            RestConnector._extract(source)

    def test_extract_missing_path_raises(self) -> None:
        source = ConnectorSource(
            kind="rest",
            config={"secret_id": str(uuid4())},
        )
        with pytest.raises(AppError, match="缺少 path"):
            RestConnector._extract(source)

    def test_extract_invalid_method_raises(self) -> None:
        source = ConnectorSource(
            kind="rest",
            config={"secret_id": str(uuid4()), "path": "/api", "method": "DELETE"},
        )
        with pytest.raises(AppError, match="GET / POST"):
            RestConnector._extract(source)

    def test_extract_invalid_uuid_raises(self) -> None:
        source = ConnectorSource(
            kind="rest",
            config={"secret_id": "not-a-uuid", "path": "/api"},
        )
        with pytest.raises(AppError, match="合法 UUID"):
            RestConnector._extract(source)


# ============================================================
# _parse_secret
# ============================================================


class TestRestParseSecret:
    """RestConnector._parse_secret 测试。"""

    def test_parse_valid_secret(self) -> None:
        secret_value = json.dumps(
            {
                "base_url": "https://api.example.com",
                "token": "my-token",
                "headers": {"X-Custom": "val"},
            }
        )
        base_url, headers = RestConnector._parse_secret(secret_value)
        assert base_url == "https://api.example.com"
        assert headers["Authorization"] == "Bearer my-token"
        assert headers["X-Custom"] == "val"

    def test_parse_secret_without_token(self) -> None:
        secret_value = json.dumps({"base_url": "https://api.example.com"})
        base_url, headers = RestConnector._parse_secret(secret_value)
        assert base_url == "https://api.example.com"
        assert "Authorization" not in headers

    def test_parse_secret_without_headers(self) -> None:
        secret_value = json.dumps({"base_url": "https://api.example.com", "token": "tk"})
        base_url, headers = RestConnector._parse_secret(secret_value)
        assert headers == {"Authorization": "Bearer tk"}

    def test_parse_invalid_json_raises(self) -> None:
        with pytest.raises(AppError, match="不是合法 JSON"):
            RestConnector._parse_secret("not json")

    def test_parse_missing_base_url_raises(self) -> None:
        with pytest.raises(AppError, match="缺少 base_url"):
            RestConnector._parse_secret(json.dumps({"token": "x"}))

    def test_parse_non_dict_payload_raises(self) -> None:
        with pytest.raises(AppError, match="缺少 base_url"):
            RestConnector._parse_secret(json.dumps([1, 2, 3]))

    def test_parse_headers_override_auth(self) -> None:
        """自定义 Authorization 头优先（setdefault 不覆盖已有）。"""
        secret_value = json.dumps(
            {
                "base_url": "https://api.example.com",
                "token": "token-val",
                "headers": {"Authorization": "Custom Auth"},
            }
        )
        _, headers = RestConnector._parse_secret(secret_value)
        assert headers["Authorization"] == "Custom Auth"


# ============================================================
# _fetch
# ============================================================


class TestRestFetch:
    """RestConnector._fetch 测试。"""

    @respx.mock
    async def test_fetch_get_success(self) -> None:
        url = "https://api.example.com/data"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
            )
        )

        columns, rows = await RestConnector._fetch(
            "https://api.example.com", "/data", "GET", {}, 100
        )
        assert columns == ("id", "name")
        assert len(rows) == 2
        assert rows[0] == [1, "A"]

    @respx.mock
    async def test_fetch_post_success(self) -> None:
        url = "https://api.example.com/data"
        respx.post(url).mock(
            return_value=httpx.Response(
                200,
                json=[{"x": 1}],
            )
        )

        columns, rows = await RestConnector._fetch(
            "https://api.example.com", "/data", "POST", {}, 100
        )
        assert columns == ("x",)
        assert rows == [[1]]

    @respx.mock
    async def test_fetch_limit_applied(self) -> None:
        url = "https://api.example.com/data"
        data = [{"id": i} for i in range(50)]
        respx.get(url).mock(return_value=httpx.Response(200, json=data))

        _, rows = await RestConnector._fetch("https://api.example.com", "/data", "GET", {}, 10)
        assert len(rows) == 10

    @respx.mock
    async def test_fetch_sparse_keys(self) -> None:
        """不同对象的键集不同时列名为并集。"""
        url = "https://api.example.com/data"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json=[{"a": 1, "b": 2}, {"a": 3, "c": 4}],
            )
        )

        columns, rows = await RestConnector._fetch(
            "https://api.example.com", "/data", "GET", {}, 100
        )
        assert "a" in columns
        assert "b" in columns
        assert "c" in columns
        assert len(rows) == 2

    @respx.mock
    async def test_fetch_non_array_response_raises(self) -> None:
        url = "https://api.example.com/data"
        respx.get(url).mock(
            return_value=httpx.Response(200, json={"key": "val"}),
        )

        with pytest.raises(AppError, match="JSON 对象数组"):
            await RestConnector._fetch("https://api.example.com", "/data", "GET", {}, 100)

    @respx.mock
    async def test_fetch_http_error_raises(self) -> None:
        url = "https://api.example.com/data"
        respx.get(url).mock(return_value=httpx.Response(500, text="err"))

        with pytest.raises(AppError, match="REST 请求失败"):
            await RestConnector._fetch("https://api.example.com", "/data", "GET", {}, 100)

    @respx.mock
    async def test_fetch_url_construction(self) -> None:
        """URL 正确拼接（base_url 尾部斜杠 + path 前导斜杠）。"""
        url = "https://api.example.com/api/data"
        route = respx.get(url).mock(return_value=httpx.Response(200, json=[]))

        await RestConnector._fetch("https://api.example.com/", "/api/data", "GET", {}, 100)
        assert route.called

    @respx.mock
    async def test_fetch_with_headers(self) -> None:
        """自定义请求头被发送。"""
        url = "https://api.example.com/data"
        route = respx.get(url).mock(return_value=httpx.Response(200, json=[]))

        await RestConnector._fetch(
            "https://api.example.com",
            "/data",
            "GET",
            {"Authorization": "Bearer token123"},
            100,
        )
        assert route.called
        assert route.calls[0].request.headers["Authorization"] == "Bearer token123"

    @respx.mock
    async def test_fetch_empty_array(self) -> None:
        url = "https://api.example.com/data"
        respx.get(url).mock(return_value=httpx.Response(200, json=[]))

        columns, rows = await RestConnector._fetch(
            "https://api.example.com", "/data", "GET", {}, 100
        )
        assert columns == ()
        assert rows == []

    @respx.mock
    async def test_fetch_non_dict_items_skipped(self) -> None:
        """非 dict 元素在列名和行收集中被跳过。"""
        url = "https://api.example.com/data"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json=[{"a": 1}, "not a dict", {"b": 2}],
            )
        )

        columns, rows = await RestConnector._fetch(
            "https://api.example.com", "/data", "GET", {}, 100
        )
        assert "a" in columns
        assert "b" in columns
        assert len(rows) == 2  # 非 dict 行被跳过

    @respx.mock
    async def test_fetch_missing_key_fills_none(self) -> None:
        """后续行缺失的键填充 None。"""
        url = "https://api.example.com/data"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json=[{"a": 1, "b": 2}, {"a": 3}],
            )
        )

        columns, rows = await RestConnector._fetch(
            "https://api.example.com", "/data", "GET", {}, 100
        )
        assert rows[1][columns.index("b")] is None


# ============================================================
# preview
# ============================================================


class TestRestPreview:
    """RestConnector.preview 测试。"""

    @respx.mock
    async def test_preview_success(self) -> None:
        sid = uuid4()
        secret_value = json.dumps({"base_url": "https://api.example.com", "token": "tk"})
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value=secret_value)

        url = "https://api.example.com/data"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
            )
        )

        connector = RestConnector(mock_store)
        source = ConnectorSource(
            kind="rest",
            config={"secret_id": str(sid), "path": "/data", "method": "GET"},
        )
        result = await connector.preview(source, limit=10)

        assert isinstance(result, PreviewTable)
        assert result.columns == ("id", "name")
        assert result.row_count == 2
        assert result.rows[0] == (1, "A")

    @respx.mock
    async def test_preview_limit(self) -> None:
        sid = uuid4()
        secret_value = json.dumps({"base_url": "https://api.example.com"})
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value=secret_value)

        url = "https://api.example.com/data"
        data = [{"id": i} for i in range(50)]
        respx.get(url).mock(return_value=httpx.Response(200, json=data))

        connector = RestConnector(mock_store)
        source = ConnectorSource(
            kind="rest",
            config={"secret_id": str(sid), "path": "/data", "method": "GET"},
        )
        result = await connector.preview(source, limit=5)
        assert result.row_count == 5


# ============================================================
# read
# ============================================================


class TestRestRead:
    """RestConnector.read 测试。"""

    @respx.mock
    async def test_read_success(self) -> None:
        sid = uuid4()
        secret_value = json.dumps({"base_url": "https://api.example.com", "token": "tk"})
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value=secret_value)

        url = "https://api.example.com/data"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json=[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
            )
        )

        connector = RestConnector(mock_store)
        source = ConnectorSource(
            kind="rest",
            config={"secret_id": str(sid), "path": "/data", "method": "GET"},
        )
        records = []
        async for record in connector.read(source):
            records.append(record)

        assert len(records) == 2
        assert all(isinstance(r, SourceRecord) for r in records)
        assert records[0].fields["id"] == "1"
        assert records[0].fields["name"] == "A"

    @respx.mock
    async def test_read_missing_key_fills_none(self) -> None:
        sid = uuid4()
        secret_value = json.dumps({"base_url": "https://api.example.com"})
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value=secret_value)

        url = "https://api.example.com/data"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json=[{"a": 1, "b": 2}, {"a": 3}],
            )
        )

        connector = RestConnector(mock_store)
        source = ConnectorSource(
            kind="rest",
            config={"secret_id": str(sid), "path": "/data", "method": "GET"},
        )
        records = []
        async for record in connector.read(source):
            records.append(record)

        assert records[1].fields["b"] is None
        assert records[1].fields["a"] == "3"
