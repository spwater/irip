"""AI Provider 协议与请求/响应值对象。

定义平台与外部 LLM 之间的稳定抽象边界：
- ``AIProvider`` (Protocol): 所有 Provider 实现的统一接口 ``complete``；
- ``AIRequest`` (frozen dataclass): 向 Provider 发出的请求（消息、工具、上下文、模式）；
- ``AIResponse`` (frozen dataclass): Provider 返回的回答（文本、工具调用、引用、不确定性）。

设计原则：
1. 不可变值对象：请求与响应均为 frozen dataclass，便于审计与并发安全；
2. Protocol 而非 ABC：使用结构化子类型（Protocol），不强制继承；
3. provider_mode 透传：请求中的 provider_mode 会被原样回填到响应中，
   供调用方区分离线模拟 / OpenAI 兼容等模式。
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class AIRequest:
    """向 AI Provider 发出的请求（不可变值对象）。

    Attributes:
        messages: 对话消息元组，每条消息为 ``{"role": str, "content": str}`` 字典。
        tools: 允许调用的工具名称元组（来自 ToolRegistry 白名单）。
        user_context: 用户上下文字典（organization_id、user_id、roles 等），
            供 Provider 进行权限感知的回答（但不传递凭据）。
        provider_mode: Provider 模式标识（如 ``"offline"``、``"openai_compatible"``）。
    """

    messages: tuple[dict[str, Any], ...]
    tools: tuple[str, ...]
    user_context: dict[str, Any] = field(default_factory=dict)
    provider_mode: str = "offline"
    tool_schemas: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AIResponse:
    """AI Provider 返回的回答（不可变值对象）。

    Attributes:
        answer: 面向用户的自然语言回答文本。
        tool_calls: 工具调用摘要元组，每项为 ``{"tool": str, "args": dict,
            "summary": str}`` 字典，记录 AI 调用了哪些工具及结果摘要。
        citations: 引用元组（``Citation`` 对象），指向回答所依据的平台对象。
        uncertainty: 不确定性说明（如 ``"数据覆盖范围有限"``），无不确定性时为 None。
        provider_mode: Provider 模式标识，与请求中的 provider_mode 一致。
    """

    answer: str
    tool_calls: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    citations: tuple[Any, ...] = field(default_factory=tuple)
    uncertainty: str | None = None
    provider_mode: str = "offline"


@runtime_checkable
class AIProvider(Protocol):
    """AI Provider 协议：所有 Provider 实现的统一接口。

    实现方需提供 ``async def complete(self, request: AIRequest) -> AIResponse``。
    平台通过此协议与不同 LLM 后端解耦：
    - ``OfflineProvider``: 确定性模拟，不调用外部 API；
    - ``OpenAICompatibleProvider``: 调用 OpenAI 兼容 REST API。
    """

    async def complete(self, request: AIRequest) -> AIResponse:
        """处理 AI 请求并返回回答。

        Args:
            request: AI 请求（消息、工具、上下文、模式）。

        Returns:
            AIResponse: 回答（文本、工具调用、引用、不确定性）。
        """
        ...
