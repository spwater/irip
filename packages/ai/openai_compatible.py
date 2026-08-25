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
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from packages.ai.providers import AIRequest, AIResponse
from packages.common.errors import AppError
from packages.common.safe_http import SafeHTTPClient

logger = logging.getLogger(__name__)


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
        timeout: float = 1200.0,
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

    @property
    def thinking_enabled(self) -> bool:
        """是否启用思考模式（公开属性，替代直接访问 _thinking_enabled）。"""
        return self._thinking_enabled

    @thinking_enabled.setter
    def thinking_enabled(self, value: bool) -> None:
        """设置思考模式开关。"""
        self._thinking_enabled = value

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
            # H-05: 使用 SafeHTTPClient（SSRF 防护 + 流式大小限制）
            async with SafeHTTPClient(timeout=self._timeout, max_size=10 * 1024 * 1024) as client:
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
            # 不在消息中暴露密钥或完整 URL，但记录详细错误供排查
            logger.error(
                "AI provider HTTPError: type=%s, msg=%s",
                type(exc).__name__,
                str(exc)[:500],
            )
            raise AppError(
                code="ai_provider_error",
                message=(f"AI 服务连接失败: {type(exc).__name__}: {str(exc)[:200]}"),
                retryable=True,
                fields={},
            ) from exc

        if resp.status_code != 200:
            # 绝不记录响应正文（可能含 prompt/分析结果等敏感内容），仅记录安全元数据。
            logger.error(
                "AI provider error: status_code=%s, content_type=%s, body_len=%s",
                resp.status_code,
                resp.headers.get("content-type", ""),
                len(resp.text),
            )
            raise AppError(
                code="ai_provider_error",
                message=f"AI 服务返回错误状态码 {resp.status_code}",
                retryable=resp.status_code >= 500,
                fields={"status_code": resp.status_code},
            )

        data: dict[str, Any] = resp.json()
        return self._parse_response(data, request)

    async def stream_complete(
        self,
        request: AIRequest,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式调用 OpenAI 兼容 API，逐 chunk 产出文本增量。

        与 ``complete()`` 不同，此方法使用 SSE 流式传输，逐行解析
        ``data: {json}`` 事件，实时 yield 文本增量。流结束后组装完整的
        tool_calls 并通过 ``done`` 事件返回。

        Args:
            request: AI 请求。
            cancel_event: 取消事件，set() 时中断流式读取。

        Yields:
            dict: 流式事件，格式为：
                - ``{"type": "chunk", "content": "文本增量"}`` — 文本块
                - ``{"type": "done", "tool_calls": [...]}`` — 流结束 + 组装的工具调用
                - ``{"type": "error", "message": "错误信息"}`` — 错误
        """
        payload = self._build_payload(request)
        payload["stream"] = True
        headers = self._build_headers()

        # 按 index 累积 tool_calls fragments
        tool_calls_fragments: dict[int, dict[str, Any]] = {}

        try:
            # H-05: 使用 SafeHTTPClient（SSRF 防护），流式模式不限制响应体大小
            async with SafeHTTPClient(timeout=self._timeout, max_size=50 * 1024 * 1024) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status_code != 200:
                        # 丢弃错误响应体（仅统计长度），绝不把正文写进日志/消息。
                        error_body = await resp.aread()
                        logger.error(
                            "AI provider stream error: status_code=%s, "
                            "content_type=%s, body_len=%s",
                            resp.status_code,
                            resp.headers.get("content-type", ""),
                            len(error_body),
                        )
                        yield {
                            "type": "error",
                            "message": f"AI 服务返回错误状态码 {resp.status_code}",
                        }
                        return

                    finish_reason: str | None = None
                    has_content: bool = False
                    has_reasoning: bool = False

                    async for line in resp.aiter_lines():
                        # 取消检查
                        if cancel_event is not None and cancel_event.is_set():
                            yield {
                                "type": "error",
                                "message": "请求已被用户取消",
                            }
                            return

                        line = line.strip()
                        if not line or not line.startswith("data: "):
                            continue

                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break

                        try:
                            chunk = json.loads(data_str)
                        except (json.JSONDecodeError, TypeError):
                            continue

                        choices = chunk.get("choices") or []
                        if not choices:
                            continue

                        delta = choices[0].get("delta") or {}

                        # 思考过程增量（reasoning_content）
                        # Qwen3 等模型在 enable_thinking=true 时先输出 reasoning_content，
                        # 再输出 content。需要透传给调用方以展示"思考中"状态。
                        reasoning_content = delta.get("reasoning_content")
                        if reasoning_content:
                            has_reasoning = True
                            yield {"type": "reasoning", "content": reasoning_content}

                        # 文本增量
                        content = delta.get("content")
                        if content:
                            has_content = True
                            yield {"type": "chunk", "content": content}

                        # tool_calls fragments（按 index 累积）
                        raw_tool_calls = delta.get("tool_calls")
                        if raw_tool_calls:
                            for tc in raw_tool_calls:
                                idx = tc.get("index", 0)
                                if idx not in tool_calls_fragments:
                                    tool_calls_fragments[idx] = {
                                        "id": "",
                                        "tool": "",
                                        "args_str": "",
                                    }
                                frag = tool_calls_fragments[idx]
                                if tc.get("id"):
                                    frag["id"] = tc["id"]
                                func = tc.get("function") or {}
                                if func.get("name"):
                                    frag["tool"] = func["name"]
                                if func.get("arguments"):
                                    frag["args_str"] += func["arguments"]

                        # 跟踪 finish_reason
                        fr = choices[0].get("finish_reason")
                        if fr:
                            finish_reason = fr

        except httpx.TimeoutException:
            yield {"type": "error", "message": "AI 服务请求超时"}
            return
        except httpx.HTTPError:
            yield {"type": "error", "message": "AI 服务连接失败"}
            return
        except AppError as exc:
            yield {"type": "error", "message": str(exc.message)}
            return

        # 思考过程耗尽 token 预算：finish_reason=length 且无 content
        if finish_reason == "length" and not has_content:
            logger.warning(
                "LLM stream finished with finish_reason=length, no content generated. "
                "has_reasoning=%s, tool_calls=%d. Thinking likely exhausted max_tokens.",
                has_reasoning,
                len(tool_calls_fragments),
            )
            yield {
                "type": "error",
                "message": "AI 思考过程过长，耗尽了 token 预算，未生成回答。"
                " 请尝试关闭思考模式、简化问题或减少数据量后重试。",
            }
            return

        # 组装完整 tool_calls
        assembled_tool_calls: list[dict[str, Any]] = []
        for idx in sorted(tool_calls_fragments.keys()):
            frag = tool_calls_fragments[idx]
            try:
                args = json.loads(frag["args_str"]) if frag["args_str"] else {}
            except (json.JSONDecodeError, TypeError):
                args = {}
            assembled_tool_calls.append(
                {
                    "id": frag["id"],
                    "tool": frag["tool"],
                    "args": args if isinstance(args, dict) else {},
                    "summary": f"调用工具 {frag['tool']}",
                }
            )

        yield {"type": "done", "tool_calls": assembled_tool_calls}

    def _build_headers(self) -> dict[str, str]:
        """构建 HTTP 请求头（含 Authorization，密钥不外泄）。"""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, request: AIRequest) -> dict[str, Any]:
        """构建 OpenAI Chat Completions 请求体。"""
        # 基础 system 消息（从 config/prompts.yaml 加载）
        from packages.ai.prompt_store import get_prompt

        system_content = get_prompt("ai_assistant.system_prompt")
        # 如果有用户传入的系统上下文（如实验数据），拼到 system 消息
        system_context = (
            request.user_context.get("system_context") if request.user_context else None
        )
        if system_context:
            system_content += "\n\n" + system_context
            # 日志：确认 system_context 传到了
            import logging

            logging.getLogger("irip.ai").info("system_context 已拼接, 长度=%d", len(system_context))

        # 如果调用方传了 system message，使用调用方的 system content（覆盖默认）
        caller_system_content = None
        for m in request.messages:
            if m.get("role") == "system":
                caller_system_content = str(m.get("content", ""))
                break
        if caller_system_content:
            system_content = caller_system_content

        # 强制约束：在 system 消息末尾追加工具调用提醒
        # 每次请求都生效，防止模型在多轮对话中"遗忘"规则
        system_content += "\n\n[强制规则] 回答中任何涉及数字运算的结果，必须通过 evaluate_expression 或 describe_series 工具获得，禁止自行心算。违反此规则的回答将被视为无效。"

        messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]
        # 加入历史消息和当前问题（不含 system role）
        # 第二轮 completion 时，messages 中可能包含 assistant 的 tool_calls
        # 和 tool 角色的结果消息，需要完整透传
        for m in request.messages:
            role = m.get("role", "user")
            if role == "system":
                continue
            msg: dict[str, Any] = {
                "role": role,
                "content": str(m.get("content", "")),
            }
            # 透传 assistant 的 tool_calls（第二轮 completion 需要）
            if role == "assistant" and "tool_calls" in m:
                msg["tool_calls"] = m["tool_calls"]
            # 透传 tool 消息的 tool_call_id（工具结果回传）
            if role == "tool" and "tool_call_id" in m:
                msg["tool_call_id"] = m["tool_call_id"]
            messages.append(msg)

        # 元数据级日志：仅记录消息数量和总字符数，不记录消息正文
        logger.info(
            "llm_request_prepared: message_count=%d, content_chars=%d, model=%s",
            len(messages),
            sum(len(str(m.get("content", ""))) for m in messages),
            self._model,
        )

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": 200000,
            "temperature": 0.0,
            "seed": 42,
        }
        # 工具调用：将 tool_schemas 转为 OpenAI tools 格式
        if request.tool_schemas:
            payload["tools"] = list(request.tool_schemas)
            payload["tool_choice"] = "auto"
        # 思考模式：不同模型族通过 chat_template_kwargs 控制思考开关
        # - Qwen3: {"enable_thinking": true/false}
        # - DeepSeek-V4: {"thinking": true/false, "reasoning_effort": "low/medium/high"}
        # LiteLLM 网关不识别这些参数，但会作为 extra_body 透传给 vLLM
        # 注意：system_context（实验数据）很大时，thinking 会大幅消耗 token 预算，
        # 可能导致 finish_reason=length 且 content 为空。此时降低 reasoning_effort。
        if self._thinking_enabled:
            if self._model.lower().startswith("deepseek"):
                # 检查 system_context 大小，大 context 时降低 reasoning_effort
                sys_ctx = request.user_context.get("system_context", "") if request.user_context else ""
                effort = "low" if len(sys_ctx) > 2000 else "high"
                payload["chat_template_kwargs"] = {
                    "thinking": True,
                    "reasoning_effort": effort,
                }
            else:
                payload["chat_template_kwargs"] = {"enable_thinking": True}
        else:
            if self._model.lower().startswith("deepseek"):
                payload["chat_template_kwargs"] = {"thinking": False}
            else:
                payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    def _parse_response(self, data: dict[str, Any], request: AIRequest) -> AIResponse:
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

        # 日志：finish_reason 和 usage（排查截断问题）
        _finish_reason = choices[0].get("finish_reason", "unknown")
        _usage = data.get("usage", {})
        if _finish_reason != "stop" or _usage:
            logger.info(
                "LLM response: finish_reason=%s, content_len=%d, usage=%s",
                _finish_reason,
                len(str((choices[0].get("message") or {}).get("content") or "")),
                _usage,
            )

        message: dict[str, Any] = choices[0].get("message") or {}
        answer: str = str(message.get("content") or "")

        # 思考模式耗尽 token 预算的诊断日志
        # Qwen3 等模型在 enable_thinking=true 时，reasoning_content 可能消耗全部
        # max_tokens，导致 content 为空、finish_reason="length"
        if _finish_reason == "length" and not answer:
            _has_reasoning = bool(message.get("reasoning_content"))
            logger.warning(
                "LLM non-stream response: finish_reason=length, content is empty. "
                "has_reasoning=%s, has_tool_calls=%s. "
                "Thinking likely exhausted max_tokens (model=%s).",
                _has_reasoning,
                bool(message.get("tool_calls")),
                self._model,
            )

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
                    "id": str(tc.get("id") or ""),
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
