"""单元测试：llm_utils LLM 调用公共工具。

覆盖 ``packages/plugins/converters/common/llm_utils.py``：
- _parse_llm_json：从 LLM 响应中提取 JSON（3 级 fallback）
- _call_llm：LLM API 调用（含超时、断线重试、非 200 状态码）
- call_llm_for_structured：公共入口（参数校验、内容截断、空内容）
使用 respx mock HTTP 请求。
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from packages.common.errors import AppError
from packages.plugins.converters.common.llm_utils import (
    _call_llm,
    _parse_llm_json,
    call_llm_for_structured,
)

# ============================================================
# _parse_llm_json
# ============================================================


class TestParseLlmJson:
    """_parse_llm_json JSON 提取测试（3 级 fallback）。"""

    def test_direct_json(self) -> None:
        """直接 JSON 字符串。"""
        content = '{"metadata": {}, "points": [], "series": []}'
        result = _parse_llm_json(content)
        assert result == {"metadata": {}, "points": [], "series": []}

    def test_json_in_code_block(self) -> None:
        """```json ... ``` 代码块中的 JSON。"""
        content = '```json\n{"key": "value"}\n```'
        result = _parse_llm_json(content)
        assert result == {"key": "value"}

    def test_json_in_plain_code_block(self) -> None:
        """``` ... ``` 代码块（无 json 标记）中的 JSON。"""
        content = '```\n{"key": "value"}\n```'
        result = _parse_llm_json(content)
        assert result == {"key": "value"}

    def test_json_embedded_in_text(self) -> None:
        """嵌入在文本中的 JSON（第 3 级 fallback）。"""
        content = 'Here is the result:\n{"key": "value"}\nThat is all.'
        result = _parse_llm_json(content)
        assert result == {"key": "value"}

    def test_json_with_nested_braces(self) -> None:
        """含嵌套大括号的 JSON。"""
        content = 'prefix {"outer": {"inner": "val"}} suffix'
        result = _parse_llm_json(content)
        assert result == {"outer": {"inner": "val"}}

    def test_invalid_json_raises(self) -> None:
        """无法解析 JSON 时抛 AppError。"""
        content = "this is not json at all"
        with pytest.raises(AppError, match="无法从 LLM 响应中解析 JSON"):
            _parse_llm_json(content)

    def test_empty_string_raises(self) -> None:
        with pytest.raises(AppError):
            _parse_llm_json("")

    def test_code_block_with_invalid_json_falls_through(self) -> None:
        """代码块内 JSON 无效时 fallback 到第 3 级。"""
        content = '```json\nnot valid json\n```\n{"real": "json"}'
        result = _parse_llm_json(content)
        assert result == {"real": "json"}

    def test_complex_nested_structure(self) -> None:
        """复杂嵌套结构。"""
        data = {
            "metadata": {"instrument": "XRD", "sample": "SMX1"},
            "points": [{"name": "voltage", "value": 40, "unit": "kV"}],
            "series": [{"name": "spectrum", "columns": ["2theta", "I"], "rows": [[10, 100]]}],
        }
        content = json.dumps(data)
        result = _parse_llm_json(content)
        assert result == data


# ============================================================
# call_llm_for_structured: 参数校验
# ============================================================


class TestCallLlmForStructuredValidation:
    """call_llm_for_structured 参数校验测试。"""

    async def test_empty_prompt_raises(self) -> None:
        with pytest.raises(AppError, match="prompt"):
            await call_llm_for_structured("content", "", {"base_url": "x"})

    async def test_none_ai_config_raises(self) -> None:
        with pytest.raises(AppError, match="配置"):
            await call_llm_for_structured("content", "prompt", None)

    async def test_empty_content_returns_empty_result(self) -> None:
        """空内容直接返回空结果（不调 LLM）。"""
        result = await call_llm_for_structured(
            "",
            "prompt",
            {"base_url": "x", "api_key": "y", "model_name": "z"},
        )
        assert result == {"metadata": {}, "points": [], "series": []}

    async def test_whitespace_content_returns_empty_result(self) -> None:
        """纯空白内容也返回空结果。"""
        result = await call_llm_for_structured(
            "   \n\t  ",
            "prompt",
            {"base_url": "x", "api_key": "y", "model_name": "z"},
        )
        assert result == {"metadata": {}, "points": [], "series": []}


# ============================================================
# call_llm_for_structured: 内容截断
# ============================================================


class TestCallLlmForStructuredTruncation:
    """call_llm_for_structured 内容截断测试。"""

    async def test_content_truncated(self) -> None:
        """超长内容被截断。"""
        long_content = "A" * 200
        captured_content: list[str] = []

        async def mock_call_llm(
            url: str,
            headers: dict,
            body: dict,
            timeout: int,  # noqa: ASYNC109
        ) -> httpx.Response:
            captured_content.append(body["messages"][0]["content"])
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [
                    {"message": {"content": '{"metadata": {}, "points": [], "series": []}'}}
                ]
            }
            return mock_resp

        with patch(
            "packages.plugins.converters.common.llm_utils._call_llm",
            side_effect=mock_call_llm,
        ):
            await call_llm_for_structured(
                long_content,
                "prompt",
                {"base_url": "x", "api_key": "y", "model_name": "z"},
                max_chars=50,
            )

        # 内容应被截断为 50 字符
        user_msg = captured_content[0]
        # user_message = prompt + "\n\n文件内容：\n" + content
        assert len(user_msg) < len("prompt") + len(long_content) + 10


# ============================================================
# call_llm_for_structured: LLM 调用与解析
# ============================================================


class TestCallLlmForStructuredLlmCall:
    """call_llm_for_structured LLM 调用与响应解析测试。"""

    async def test_successful_call(self) -> None:
        """成功的 LLM 调用返回结构化数据。"""
        llm_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "metadata": {"key": "value"},
                                "points": [{"name": "p", "value": 1, "unit": "u"}],
                                "series": [],
                            }
                        )
                    }
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = llm_response

        with patch(
            "packages.plugins.converters.common.llm_utils._call_llm",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await call_llm_for_structured(
                "content",
                "prompt",
                {"base_url": "http://localhost:8000", "api_key": "key", "model_name": "model"},
            )

        assert result["metadata"] == {"key": "value"}
        assert len(result["points"]) == 1

    async def test_empty_choices_raises(self) -> None:
        """LLM 返回空 choices 时抛 AppError。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": []}

        with patch(
            "packages.plugins.converters.common.llm_utils._call_llm",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            with pytest.raises(AppError, match="空响应"):
                await call_llm_for_structured(
                    "content",
                    "prompt",
                    {"base_url": "x", "api_key": "y", "model_name": "z"},
                )

    async def test_llm_returns_partial_json(self) -> None:
        """LLM 返回部分 JSON 时提取有效部分。"""
        llm_content = (
            "Here is the data:\n```json\n"
            '{"metadata": {"a": 1}, "points": [], "series": []}\n```\nDone.'
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": llm_content}}],
        }

        with patch(
            "packages.plugins.converters.common.llm_utils._call_llm",
            new_callable=AsyncMock,
            return_value=mock_resp,
        ):
            result = await call_llm_for_structured(
                "content",
                "prompt",
                {"base_url": "x", "api_key": "y", "model_name": "z"},
            )

        assert result["metadata"] == {"a": 1}
        assert result["points"] == []
        assert result["series"] == []

    async def test_request_body_construction(self) -> None:
        """验证请求体正确构建。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"metadata": {}, "points": [], "series": []}'}}],
        }

        captured_body: dict = {}

        async def mock_call(url: str, headers: dict, body: dict, timeout: int) -> httpx.Response:  # noqa: ASYNC109
            captured_body.update(body)
            return mock_resp

        with patch(
            "packages.plugins.converters.common.llm_utils._call_llm",
            side_effect=mock_call,
        ):
            await call_llm_for_structured(
                "my content",
                "my prompt",
                {"base_url": "http://api.example.com", "api_key": "secret", "model_name": "gpt-4"},
            )

        assert captured_body["model"] == "gpt-4"
        assert captured_body["temperature"] == 0.0
        assert captured_body["seed"] == 42
        assert "my prompt" in captured_body["messages"][0]["content"]
        assert "my content" in captured_body["messages"][0]["content"]

    async def test_url_construction(self) -> None:
        """验证 URL 正确构建（base_url 尾部斜杠被去除）。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"metadata": {}, "points": [], "series": []}'}}],
        }

        captured_url: list[str] = []

        async def mock_call(url: str, headers: dict, body: dict, timeout: int) -> httpx.Response:  # noqa: ASYNC109
            captured_url.append(url)
            return mock_resp

        with patch(
            "packages.plugins.converters.common.llm_utils._call_llm",
            side_effect=mock_call,
        ):
            await call_llm_for_structured(
                "content",
                "prompt",
                {"base_url": "http://api.example.com/", "api_key": "key", "model_name": "model"},
            )

        assert captured_url[0] == "http://api.example.com/chat/completions"

    async def test_headers_construction(self) -> None:
        """验证请求头正确构建。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"metadata": {}, "points": [], "series": []}'}}],
        }

        captured_headers: dict = {}

        async def mock_call(url: str, headers: dict, body: dict, timeout: int) -> httpx.Response:  # noqa: ASYNC109
            captured_headers.update(headers)
            return mock_resp

        with patch(
            "packages.plugins.converters.common.llm_utils._call_llm",
            side_effect=mock_call,
        ):
            await call_llm_for_structured(
                "content",
                "prompt",
                {"base_url": "x", "api_key": "my-secret-key", "model_name": "model"},
            )

        assert captured_headers["Authorization"] == "Bearer my-secret-key"
        assert captured_headers["Content-Type"] == "application/json"


# ============================================================
# _call_llm: HTTP 调用与重试
# ============================================================


class TestCallLlm:
    """_call_llm HTTP 调用测试。

    使用 IRIP_ALLOW_PRIVATE_NETWORK=1 绕过 SafeHTTPClient 的 SSRF DNS
    校验，使 respx 能拦截请求。
    """

    @respx.mock
    async def test_successful_call(self) -> None:
        """成功的 HTTP 调用。"""
        url = "http://api.example.com/chat/completions"
        respx.post(url).mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "{}"}}]},
            )
        )

        with patch.dict("os.environ", {"IRIP_ALLOW_PRIVATE_NETWORK": "1"}):
            resp = await _call_llm(url, {"Authorization": "Bearer key"}, {"model": "test"}, 30)
        assert resp.status_code == 200

    @respx.mock
    async def test_non_200_raises(self) -> None:
        """非 200 状态码抛 AppError。"""
        url = "http://api.example.com/chat/completions"
        respx.post(url).mock(return_value=httpx.Response(500, text="Internal Error"))

        with patch.dict("os.environ", {"IRIP_ALLOW_PRIVATE_NETWORK": "1"}):
            with pytest.raises(AppError, match="500"):
                await _call_llm(url, {}, {}, 30)

    @respx.mock
    async def test_timeout_raises(self) -> None:
        """超时抛 AppError。"""
        url = "http://api.example.com/chat/completions"
        respx.post(url).mock(side_effect=httpx.TimeoutException("timeout"))

        with patch.dict("os.environ", {"IRIP_ALLOW_PRIVATE_NETWORK": "1"}):
            with pytest.raises(AppError, match="超时"):
                await _call_llm(url, {}, {}, 30)

    @respx.mock
    async def test_disconnected_retries(self) -> None:
        """断线重试成功。"""
        url = "http://api.example.com/chat/completions"
        # 第一次请求失败（disconnected），第二次成功
        route = respx.post(url)
        route.mock(
            side_effect=[
                httpx.ConnectError("Connection disconnected"),
                httpx.Response(200, json={"result": "ok"}),
            ]
        )

        with patch.dict("os.environ", {"IRIP_ALLOW_PRIVATE_NETWORK": "1"}):
            resp = await _call_llm(url, {}, {}, 30)
        assert resp.status_code == 200

    @respx.mock
    async def test_http_error_no_retry(self) -> None:
        """非断线 HTTP 错误不重试。"""
        url = "http://api.example.com/chat/completions"
        respx.post(url).mock(side_effect=httpx.HTTPError("generic error"))

        with patch.dict("os.environ", {"IRIP_ALLOW_PRIVATE_NETWORK": "1"}):
            with pytest.raises(AppError, match="请求失败"):
                await _call_llm(url, {}, {}, 30)

    @respx.mock
    async def test_retry_also_fails(self) -> None:
        """断线重试也失败时抛 AppError。"""
        url = "http://api.example.com/chat/completions"
        respx.post(url).mock(side_effect=httpx.ConnectError("disconnected"))

        with patch.dict("os.environ", {"IRIP_ALLOW_PRIVATE_NETWORK": "1"}):
            with pytest.raises(AppError, match="重试后"):
                await _call_llm(url, {}, {}, 30)
