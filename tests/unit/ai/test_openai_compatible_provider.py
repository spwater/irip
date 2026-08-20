"""单元测试：OpenAICompatibleProvider OpenAI 兼容 REST API Provider。

覆盖：
- _build_payload：system 消息拼接 + caller system 覆盖 + user/assistant/tool 消息透传
  + tool_schemas + thinking_enabled；
- _build_headers：Authorization + Content-Type；
- _parse_response：正常回答 + tool_calls 解析 + 空 choices 报错 + args JSON 解析失败；
- complete：成功路径 + 非 200 状态码 + cancel_event 取消；
- thinking_enabled 属性读写。

使用 Mock SafeHTTPClient，不发起真实 HTTP 请求。
"""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from packages.ai.openai_compatible import OpenAICompatibleProvider
from packages.ai.providers import AIRequest, AIResponse
from packages.common.errors import AppError

# ============================================================
# Helpers
# ============================================================


def _make_provider(
    api_key: str = "sk-test-key",
    base_url: str = "https://api.example.com/v1",
    model: str = "gpt-4o",
    thinking_enabled: bool = False,
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=base_url,
        model=model,
        thinking_enabled=thinking_enabled,
    )


def _make_request(
    messages: list[dict[str, Any]] | None = None,
    tools: tuple[str, ...] = (),
    user_context: dict[str, Any] | None = None,
    tool_schemas: tuple[dict[str, Any], ...] = (),
) -> AIRequest:
    return AIRequest(
        messages=tuple(messages or [{"role": "user", "content": "你好"}]),
        tools=tools,
        user_context=user_context or {},
        tool_schemas=tool_schemas,
    )


class _FakeResponse:
    """模拟 httpx.Response。"""

    def __init__(self, status_code: int = 200, json_data: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = json.dumps(self._json_data)

    def json(self) -> dict[str, Any]:
        return self._json_data


# ============================================================
# _build_headers
# ============================================================


class TestBuildHeaders:
    """_build_headers 测试。"""

    def test_headers_contain_authorization_and_content_type(self) -> None:
        """请求头含 Authorization 和 Content-Type。"""
        provider = _make_provider(api_key="sk-secret")
        headers = provider._build_headers()
        assert headers["Authorization"] == "Bearer sk-secret"
        assert headers["Content-Type"] == "application/json"


# ============================================================
# _build_payload
# ============================================================


class TestBuildPayload:
    """_build_payload 测试。"""

    def test_basic_payload(self) -> None:
        """基础 payload 含 model / messages / max_tokens / temperature。"""
        provider = _make_provider(model="gpt-4o")
        payload = provider._build_payload(_make_request())

        assert payload["model"] == "gpt-4o"
        assert payload["max_tokens"] == 50000
        assert payload["temperature"] == 0.0
        assert payload["seed"] == 42
        assert payload["messages"][0]["role"] == "system"

    def test_user_message_appended(self) -> None:
        """user 消息被追加到 messages。"""
        provider = _make_provider()
        payload = provider._build_payload(
            _make_request(messages=[{"role": "user", "content": "计算 3+5"}])
        )
        user_msgs = [m for m in payload["messages"] if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["content"] == "计算 3+5"

    def test_system_message_from_caller_overrides_default(self) -> None:
        """调用方传入的 system 消息覆盖默认 system。"""
        provider = _make_provider()
        payload = provider._build_payload(
            _make_request(messages=[{"role": "system", "content": "自定义 system"}])
        )
        assert payload["messages"][0]["content"] == "自定义 system"

    def test_system_context_appended_to_default(self) -> None:
        """user_context 中的 system_context 被拼接到默认 system 消息。"""
        provider = _make_provider()
        payload = provider._build_payload(
            _make_request(user_context={"system_context": "实验数据上下文"})
        )
        assert "实验数据上下文" in payload["messages"][0]["content"]

    def test_assistant_tool_calls_passed_through(self) -> None:
        """assistant 消息的 tool_calls 被透传。"""
        provider = _make_provider()
        tool_calls = [{"id": "tc1", "function": {"name": "calc", "arguments": "{}"}}]
        payload = provider._build_payload(
            _make_request(
                messages=[
                    {"role": "user", "content": "算"},
                    {"role": "assistant", "content": "", "tool_calls": tool_calls},
                ]
            )
        )
        assistant_msgs = [m for m in payload["messages"] if m["role"] == "assistant"]
        assert assistant_msgs[0]["tool_calls"] == tool_calls

    def test_tool_message_with_tool_call_id(self) -> None:
        """tool 角色消息的 tool_call_id 被透传。"""
        provider = _make_provider()
        payload = provider._build_payload(
            _make_request(
                messages=[
                    {"role": "user", "content": "算"},
                    {"role": "tool", "content": "42", "tool_call_id": "tc1"},
                ]
            )
        )
        tool_msgs = [m for m in payload["messages"] if m["role"] == "tool"]
        assert tool_msgs[0]["tool_call_id"] == "tc1"

    def test_tool_schemas_added_to_payload(self) -> None:
        """tool_schemas 被转为 OpenAI tools 格式。"""
        provider = _make_provider()
        schemas = ({"type": "function", "function": {"name": "calc"}},)
        payload = provider._build_payload(_make_request(tool_schemas=schemas))
        assert "tools" in payload
        assert payload["tool_choice"] == "auto"
        assert payload["tools"][0]["function"]["name"] == "calc"

    def test_thinking_enabled_in_payload(self) -> None:
        """thinking_enabled 注入 chat_template_kwargs。"""
        provider = _make_provider(thinking_enabled=True)
        payload = provider._build_payload(_make_request())
        assert payload["chat_template_kwargs"]["enable_thinking"] is True

    def test_thinking_disabled_in_payload(self) -> None:
        """thinking_disabled 时 chat_template_kwargs 为 False。"""
        provider = _make_provider(thinking_enabled=False)
        payload = provider._build_payload(_make_request())
        assert payload["chat_template_kwargs"]["enable_thinking"] is False

    def test_base_url_trailing_slash_stripped(self) -> None:
        """base_url 尾部斜杠被去除。"""
        provider = _make_provider(base_url="https://api.example.com/v1/")
        assert provider._base_url == "https://api.example.com/v1"


# ============================================================
# _parse_response
# ============================================================


class TestParseResponse:
    """_parse_response 测试。"""

    def test_parse_text_response(self) -> None:
        """解析纯文本回答。"""
        provider = _make_provider()
        data = {"choices": [{"message": {"content": "答案是 42"}}]}
        resp = provider._parse_response(data, _make_request())
        assert resp.answer == "答案是 42"
        assert resp.tool_calls == ()
        assert resp.provider_mode == "openai_compatible"

    def test_parse_tool_calls(self) -> None:
        """解析工具调用。"""
        provider = _make_provider()
        data = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "tc-1",
                                "function": {
                                    "name": "evaluate_expression",
                                    "arguments": '{"expr": "3+5"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
        resp = provider._parse_response(data, _make_request())
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0]["tool"] == "evaluate_expression"
        assert resp.tool_calls[0]["args"] == {"expr": "3+5"}
        assert resp.tool_calls[0]["id"] == "tc-1"

    def test_parse_tool_calls_invalid_json_args(self) -> None:
        """工具调用 args 非法 JSON 时回退为空 dict。"""
        provider = _make_provider()
        data = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "tc-2",
                                "function": {"name": "calc", "arguments": "not-json"},
                            }
                        ],
                    }
                }
            ]
        }
        resp = provider._parse_response(data, _make_request())
        assert resp.tool_calls[0]["args"] == {}

    def test_parse_tool_calls_dict_args(self) -> None:
        """工具调用 args 为 dict 时直接使用。"""
        provider = _make_provider()
        data = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "tc-3",
                                "function": {"name": "calc", "arguments": {"x": 1}},
                            }
                        ],
                    }
                }
            ]
        }
        resp = provider._parse_response(data, _make_request())
        assert resp.tool_calls[0]["args"] == {"x": 1}

    def test_parse_empty_choices_raises(self) -> None:
        """空 choices 抛 ai_provider_error。"""
        provider = _make_provider()
        with pytest.raises(AppError) as exc_info:
            provider._parse_response({"choices": []}, _make_request())
        assert exc_info.value.code == "ai_provider_error"

    def test_parse_no_choices_key_raises(self) -> None:
        """无 choices 键抛 ai_provider_error。"""
        provider = _make_provider()
        with pytest.raises(AppError) as exc_info:
            provider._parse_response({}, _make_request())
        assert exc_info.value.code == "ai_provider_error"

    def test_parse_empty_content(self) -> None:
        """content 为 None 时 answer 为空字符串。"""
        provider = _make_provider()
        data = {"choices": [{"message": {}}]}
        resp = provider._parse_response(data, _make_request())
        assert resp.answer == ""


# ============================================================
# thinking_enabled property
# ============================================================


class TestThinkingEnabled:
    """thinking_enabled 属性测试。"""

    def test_default_false(self) -> None:
        """默认未启用思考模式。"""
        provider = _make_provider()
        assert provider.thinking_enabled is False

    def test_set_to_true(self) -> None:
        """可设置为 True。"""
        provider = _make_provider()
        provider.thinking_enabled = True
        assert provider.thinking_enabled is True

    def test_provider_mode_constant(self) -> None:
        """provider_mode 固定为 openai_compatible。"""
        provider = _make_provider()
        assert provider.provider_mode == "openai_compatible"


# ============================================================
# complete
# ============================================================


class TestComplete:
    """complete 方法测试。"""

    async def test_complete_success(self) -> None:
        """成功调用返回 AIResponse。"""
        provider = _make_provider()
        fake_resp = _FakeResponse(
            status_code=200,
            json_data={"choices": [{"message": {"content": "ok"}}]},
        )

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=fake_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("packages.ai.openai_compatible.SafeHTTPClient", return_value=mock_client):
            resp = await provider.complete(_make_request())

        assert isinstance(resp, AIResponse)
        assert resp.answer == "ok"
        assert resp.provider_mode == "openai_compatible"

    async def test_complete_non_200_raises(self) -> None:
        """非 200 状态码抛 ai_provider_error。"""
        provider = _make_provider()
        fake_resp = _FakeResponse(status_code=401, json_data={"error": "unauthorized"})

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=fake_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("packages.ai.openai_compatible.SafeHTTPClient", return_value=mock_client):
            with pytest.raises(AppError) as exc_info:
                await provider.complete(_make_request())
        assert exc_info.value.code == "ai_provider_error"
        assert exc_info.value.retryable is False  # 401 < 500

    async def test_complete_500_retryable(self) -> None:
        """5xx 状态码 retryable=True。"""
        provider = _make_provider()
        fake_resp = _FakeResponse(status_code=503, json_data={"error": "unavailable"})

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=fake_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("packages.ai.openai_compatible.SafeHTTPClient", return_value=mock_client):
            with pytest.raises(AppError) as exc_info:
                await provider.complete(_make_request())
        assert exc_info.value.retryable is True

    async def test_complete_timeout_raises(self) -> None:
        """超时抛 ai_provider_error（retryable=True）。"""
        provider = _make_provider()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("packages.ai.openai_compatible.SafeHTTPClient", return_value=mock_client):
            with pytest.raises(AppError) as exc_info:
                await provider.complete(_make_request())
        assert exc_info.value.code == "ai_provider_error"
        assert exc_info.value.retryable is True

    async def test_complete_http_error_raises(self) -> None:
        """HTTP 连接错误抛 ai_provider_error。"""
        provider = _make_provider()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=httpx.HTTPError("conn failed"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("packages.ai.openai_compatible.SafeHTTPClient", return_value=mock_client):
            with pytest.raises(AppError) as exc_info:
                await provider.complete(_make_request())
        assert exc_info.value.code == "ai_provider_error"

    async def test_complete_cancelled(self) -> None:
        """cancel_event 被 set 时抛 ai_cancelled。"""
        provider = _make_provider()
        cancel_event = asyncio.Event()
        cancel_event.set()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=_FakeResponse(200, {"choices": []}))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("packages.ai.openai_compatible.SafeHTTPClient", return_value=mock_client):
            with pytest.raises(AppError) as exc_info:
                await provider.complete(_make_request(), cancel_event=cancel_event)
        assert exc_info.value.code == "ai_cancelled"
