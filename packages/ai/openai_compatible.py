"""OpenAI 兼容 REST API Provider。

OpenAICompatibleProvider 通过 httpx 调用 OpenAI 兼容的 Chat Completions
REST API（如 OpenAI 官方、Azure OpenAI、本地 vLLM、Ollama 等）。

安全约定：
- **密钥不记录日志**：api_key 仅用于 HTTP 请求头，不出现在日志、错误消息中；
- 工具调用通过 OpenAI function calling 格式传递，返回结果由 AIService 执行；
- 超时与重试由 httpx 管理，网络错误转为 AppError。

请求格式（OpenAI Chat Completions）：
    POST {base_url}/chat/completions
    Authorization: Bearer {api_key}
    {
      "model": "...",
      "messages": [...],
      "tools": [...],
      "tool_choice": "auto"
    }
"""

import asyncio
import json
from typing import Any

import httpx
import sqlalchemy as sa

from packages.ai.citations import Citation
from packages.ai.providers import AIProvider, AIRequest, AIResponse
from packages.common.errors import AppError


class OpenAICompatibleProvider:
    """OpenAI 兼容 REST API Provider，实现 AIProvider 协议。

    通过 httpx 调用 ``{base_url}/chat/completions`` 端点。

    Attributes:
        _api_key: API 密钥（不记录日志）。
        _base_url: API 基础 URL（如 ``"https://api.openai.com/v1"``）。
        _model: 模型名称（如 ``"gpt-4o"``）。
        provider_mode: 固定为 ``"openai_compatible"``。
    """

    provider_mode: str = "openai_compatible"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 30.0,
        thinking_enabled: bool = False,
    ) -> None:
        """初始化 OpenAI 兼容 Provider。

        Args:
            api_key: API 密钥（仅用于请求头，不记录日志）。
            base_url: API 基础 URL（不含 /chat/completions 后缀）。
            model: 模型名称。
            timeout: HTTP 请求超时秒数。
            thinking_enabled: 是否启用思考模式（如 Qwen3 的 enable_thinking）。
        """
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._thinking_enabled = thinking_enabled

    async def complete(
        self,
        request: AIRequest,
        cancel_event: asyncio.Event | None = None,
    ) -> AIResponse:
        """调用 OpenAI 兼容 API 处理请求。

        Args:
            request: AI 请求。
            cancel_event: 取消事件，set() 时中断请求。

        Returns:
            AIResponse: 解析后的回答。

        Raises:
            AppError: code="ai_cancelled"，当请求被取消时。
            AppError: code="ai_provider_error"，当 API 调用失败时。
        """
        payload = self._build_payload(request)
        headers = self._build_headers()

        try:
            async with httpx.AsyncClient(timeout=self._timeout, proxy=None) as client:
                if cancel_event is not None:
                    # 竞速：请求 vs 取消信号
                    request_task = asyncio.create_task(
                        client.post(
                            f"{self._base_url}/chat/completions",
                            json=payload,
                            headers=headers,
                        )
                    )
                    cancel_task = asyncio.create_task(cancel_event.wait())
                    done, pending = await asyncio.wait(
                        {request_task, cancel_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    # 取消未完成的任务
                    for t in pending:
                        t.cancel()
                    if cancel_task in done:
                        # 被取消
                        raise AppError(
                            code="ai_cancelled",
                            message="请求已被用户取消",
                            retryable=False,
                            fields={},
                        )
                    resp = request_task.result()
                else:
                    resp = await client.post(
                        f"{self._base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
        except httpx.TimeoutException as exc:
            raise AppError(
                code="ai_provider_error",
                message="AI 服务请求超时",
                retryable=True,
                fields={},
            ) from exc
        except httpx.HTTPError as exc:
            # 不在消息中暴露密钥或完整 URL
            raise AppError(
                code="ai_provider_error",
                message="AI 服务连接失败",
                retryable=True,
                fields={},
            ) from exc

        if resp.status_code != 200:
            import logging
            logging.getLogger(__name__).error(f"AI provider error {resp.status_code}: {resp.text[:500]}")
            raise AppError(
                code="ai_provider_error",
                message=f"AI 服务返回错误状态码 {resp.status_code}: {resp.text[:200]}",
                retryable=resp.status_code >= 500,
                fields={},
            )

        data: dict[str, Any] = resp.json()
        return self._parse_response(data, request)

    def _build_headers(self) -> dict[str, str]:
        """构建 HTTP 请求头（含 Authorization，密钥不外泄）。"""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, request: AIRequest) -> dict[str, Any]:
        """构建 OpenAI Chat Completions 请求体。"""
        # 基础 system 消息
        system_content = (
            "你是 IRIP 工业研发智能平台的 AI 助手。"
            "你可以回答关于工业研究、材料科学、数据分析的问题。"
            "回答使用中文。"
        )
        # 如果有用户传入的系统上下文（如实验数据），拼到 system 消息
        system_context = request.user_context.get("system_context") if request.user_context else None
        if system_context:
            system_content += "\n\n" + system_context

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_content}
        ]
        # 加入历史消息和当前问题（不含 system role）
        messages.extend(
            {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
            for m in request.messages
            if m.get("role") != "system"
        )

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        # 思考模式：Qwen3 vLLM 通过 chat_template_kwargs 控制思考开关
        # 顶层 enable_thinking 参数无效，只有 chat_template_kwargs 生效
        payload["chat_template_kwargs"] = {"enable_thinking": self._thinking_enabled}
        return payload

    def _parse_response(
        self, data: dict[str, Any], request: AIRequest
    ) -> AIResponse:
        """解析 OpenAI API 响应为 AIResponse。

        Args:
            data: OpenAI API 返回的 JSON。
            request: 原始请求（用于透传 provider_mode）。

        Returns:
            AIResponse: 解析后的回答。
        """
        choices = data.get("choices") or []
        if not choices:
            raise AppError(
                code="ai_provider_error",
                message="AI 服务返回空响应",
                retryable=True,
                fields={},
            )

        message: dict[str, Any] = choices[0].get("message") or {}
        answer: str = str(message.get("content") or "")

        # 解析工具调用
        tool_calls: list[dict[str, Any]] = []
        raw_tool_calls = message.get("tool_calls") or []
        for tc in raw_tool_calls:
            func = tc.get("function") or {}
            args_str = func.get("arguments") or "{}"
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append(
                {
                    "tool": str(func.get("name") or ""),
                    "args": args if isinstance(args, dict) else {},
                    "summary": f"调用工具 {func.get('name', 'unknown')}",
                }
            )

        # OpenAI 响应不直接包含引用，引用由 AIService 在工具执行后附加
        return AIResponse(
            answer=answer,
            tool_calls=tuple(tool_calls),
            citations=(),
            uncertainty=None,
            provider_mode=self.provider_mode,
        )
