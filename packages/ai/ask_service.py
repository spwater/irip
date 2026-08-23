"""AI 问答编排核心服务。

从 ``service.py`` 提取的问答编排逻辑（ask / stream_ask）。
职责：前置准备（加载对话、构建请求）、AI 调用、工具执行编排、持久化、取消管理。

依赖注入：
- provider: AI Provider
- tool_registry: 工具注册表
- tool_executor: ToolExecutor 实例
- persistence: MessagePersistence 实例
- conversation_service: ConversationService 实例
- cancellation_registry: CancellationRegistry 实例
- session_factory: 异步会话工厂
- clock: 时钟依赖

关键设计：
- ``_AskContext`` 定义在本模块内部（仅为 AskService 使用）。
- ``_provider.thinking_enabled`` 通过公开属性访问（P2-C9 修复）。
- ``AppUser`` 延迟 import 不涉及此模块。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.ai.cancellation import CancellationRegistry
from packages.ai.citation import CitationGenerator
from packages.ai.citation_builder import (
    _build_nav_citation,
)
from packages.ai.collaboration_entities import ConversationParticipant
from packages.ai.conversation_service import ConversationService
from packages.ai.entities import AIConversation
from packages.ai.persistence import MessagePersistence
from packages.ai.providers import AIProvider, AIRequest, AIResponse
from packages.ai.tool_executor import ToolExecutor
from packages.ai.tools import ToolRegistry
from packages.common.clock import Clock
from packages.common.database import scoped_session
from packages.common.errors import AppError

logger = logging.getLogger(__name__)


@dataclass
class _AskContext:
    """ask / stream_ask 共享的前置准备上下文（内部使用）。

    由 ``_prepare_ask()`` 构建并返回，供 ``ask()`` 和 ``stream_ask()``
    复用同一套设置逻辑（加载历史、构建请求、创建取消事件等）。
    """

    user_id: UUID
    org_id: UUID
    conversation_id: UUID
    question: str
    history_messages: list[dict[str, Any]]
    msg_list: list[dict[str, Any]]
    user_context: dict[str, Any]
    tool_names: tuple[str, ...]
    tool_schemas: tuple[dict[str, Any], ...]
    ai_request: AIRequest
    cancel_event: asyncio.Event
    mentions: list[str]
    thinking_enabled: bool
    provider_name: str
    mention_only: bool
    config_thinking_enabled: bool = False


class AskService:
    """AI 问答编排核心服务。

    Attributes:
        _provider: AI Provider 实例。
        _tool_registry: 工具注册表。
        _tool_executor: 工具执行器。
        _persistence: 消息持久化服务。
        _conversation_svc: 对话管理服务。
        _cancellation: 取消注册表。
        _factory: 异步会话工厂。
        _clock: 时钟依赖。
    """

    def __init__(
        self,
        provider: AIProvider,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        persistence: MessagePersistence,
        conversation_service: ConversationService,
        cancellation_registry: CancellationRegistry,
        session_factory: async_sessionmaker[AsyncSession] | None,
        clock: Clock,
    ) -> None:
        """初始化问答编排服务。

        Args:
            provider: AI Provider 实例。
            tool_registry: 工具注册表。
            tool_executor: 工具执行器。
            persistence: 消息持久化服务。
            conversation_service: 对话管理服务。
            cancellation_registry: 取消注册表。
            session_factory: 异步会话工厂。
            clock: 时钟依赖。
        """
        self._provider = provider
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._persistence = persistence
        self._conversation_svc = conversation_service
        self._cancellation = cancellation_registry
        self._factory = session_factory
        self._clock = clock
        self._tools_cache_ts: float = 0.0
        self._tools_cache_ttl: float = 30.0

    async def _prepare_ask(
        self,
        user: Any,
        question: str,
        conversation_id: UUID | None,
        provider_name: str,
        thinking_enabled: bool,
        system_context: str | None,
        mentions: list[str] | None,
    ) -> _AskContext:
        """ask / stream_ask 共享的前置准备逻辑。

        包含：加载/创建对话、判断 mention-only、构建 AIRequest、
        创建取消事件并注册到取消注册表。

        Args:
            user: 当前用户（需有 user_id, email, roles 属性）。
            question: 用户问题文本。
            conversation_id: 对话 ID（None 时自动创建新对话）。
            provider_name: Provider 名称。
            thinking_enabled: 是否启用思考模式。
            system_context: 系统上下文（如实验数据 JSON）。
            mentions: @ 人的 user_id 数组。

        Returns:
            _AskContext: 共享上下文，包含所有后续步骤所需的变量。
        """
        user_id: UUID = user.user_id
        org_id: UUID | None = getattr(user, "department_id", None)
        if org_id is None:
            raise AppError(
                code="forbidden",
                message="无法确定用户所属部门，请先绑定部门后再使用 AI 助手",
                retryable=False,
                fields={"user_id": str(user_id)},
            )

        # 热更新：从 DB 重新加载工具声明层（带 30s TTL 缓存）
        if self._factory is not None:
            now = time.monotonic()
            if now - self._tools_cache_ts > self._tools_cache_ttl:
                async with scoped_session(self._factory, None, user_id) as session:
                    await self._tool_registry.reload_from_db(session)
                self._tools_cache_ts = now

        # 加载或创建对话
        if conversation_id is None:
            conv_ref = await self._conversation_svc.create_conversation(
                user_id=user_id,
                department_id=org_id,
                title=question[:60],
                provider_mode=provider_name,
            )
            conversation_id = conv_ref.id
            history_messages: list[dict[str, Any]] = []
        else:
            msgs = await self._conversation_svc.list_messages(conversation_id, user_id)
            history_messages = [{"role": m.role, "content": m.content} for m in msgs]

        # 合并 session：participant count + system_context 读写
        mentions_list: list[str] = mentions or []
        participant_count: int | None = None
        if self._factory is not None:
            async with scoped_session(self._factory, None, user_id) as session:
                # 查 participant count（判断 mention_only）
                participant_count = await session.scalar(
                    sa.select(sa.func.count())
                    .select_from(ConversationParticipant)
                    .where(ConversationParticipant.conversation_id == conversation_id)
                )
                # system_context 恢复（前端没传时从对话记录读）+ 写回
                conv_obj: AIConversation | None = None
                if not system_context:
                    conv_obj = await session.scalar(
                        sa.select(AIConversation).where(AIConversation.id == conversation_id)
                    )
                    if conv_obj and conv_obj.system_context:
                        system_context = conv_obj.system_context
                # system_context 写回（如果前端传了新值）
                if system_context:
                    if conv_obj is None:
                        conv_obj = await session.scalar(
                            sa.select(AIConversation).where(AIConversation.id == conversation_id)
                        )
                    if conv_obj:
                        conv_obj.system_context = system_context
        is_private: bool = participant_count is None or participant_count <= 1
        mention_only: bool = not is_private and "ai" not in mentions_list

        # 构建 user_context（不含凭据）
        user_context: dict[str, Any] = {
            "user_id": str(user_id),
            "department_id": str(org_id),
            "roles": list(user.roles),
        }

        # 构建消息列表（历史 + 当前问题，跳过 system role）
        msg_list: list[dict[str, Any]] = []
        for m in history_messages:
            if m.get("role") == "system":
                continue
            msg_list.append(m)
        msg_list.append({"role": "user", "content": question})
        messages: tuple[dict[str, Any], ...] = tuple(msg_list)

        if system_context:
            user_context["system_context"] = system_context

        # 构建工具名称和 schema
        tool_names: tuple[str, ...] = self._tool_registry.enabled_names()
        tool_schemas: tuple[dict[str, Any], ...] = self._tool_executor.build_tool_schemas()

        # 构建 AIRequest
        ai_request = AIRequest(
            messages=messages,
            tools=tool_names,
            tool_schemas=tool_schemas,
            user_context=user_context,
            provider_mode=provider_name,
        )

        # 思考模式：前端开关 AND 配置层面开关，两者都为 True 时才启用
        # P2-C9: 使用公开属性 thinking_enabled 替代直接访问 _thinking_enabled
        config_thinking = False
        if hasattr(self._provider, "thinking_enabled"):
            config_thinking = getattr(self._provider, "thinking_enabled", False)
            self._provider.thinking_enabled = thinking_enabled and config_thinking

        # 创建取消事件并注册（仅对非 mention-only 消息注册）
        if not mention_only:
            cancel_event = self._cancellation.register(conversation_id)
        else:
            cancel_event = asyncio.Event()

        return _AskContext(
            user_id=user_id,
            org_id=org_id,
            conversation_id=conversation_id,
            question=question,
            history_messages=history_messages,
            msg_list=msg_list,
            user_context=user_context,
            tool_names=tool_names,
            tool_schemas=tool_schemas,
            ai_request=ai_request,
            cancel_event=cancel_event,
            mentions=mentions_list,
            thinking_enabled=thinking_enabled,
            provider_name=provider_name,
            mention_only=mention_only,
            config_thinking_enabled=config_thinking,
        )

    async def _execute_and_finalize(
        self,
        response: AIResponse,
        ctx: _AskContext,
        user: Any,
    ) -> AIResponse:
        """ask / stream_ask 共享的后置处理逻辑。

        包含：工具调用执行（权限检查 + 白名单执行）、第二轮 completion
        （如有工具被调用）、构建最终 AIResponse、持久化消息、自动生成标题。

        Args:
            response: 第一轮 completion 的 AIResponse。
            ctx: 由 _prepare_ask 构建的共享上下文。
            user: 当前用户。

        Returns:
            AIResponse: 最终回答（含工具调用结果、引用、不确定性）。
        """
        # 执行工具调用（权限检查 + 白名单工具真实执行）
        executed_tool_calls: list[dict[str, Any]] = []
        tool_result_messages: list[dict[str, Any]] = []
        all_citations: list[Any] = []

        for tc in response.tool_calls:
            tool_name = str(tc.get("tool", ""))
            tool_args = tc.get("args", {})
            if not isinstance(tool_args, dict):
                tool_args = {}
            tool_call_id = str(tc.get("id", "")) or f"call_{tool_name}_{len(executed_tool_calls)}"

            # 验证工具在白名单中
            try:
                spec = self._tool_registry.validate(tool_name)
            except AppError:
                executed_tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "summary": f"拒绝执行：未知工具 '{tool_name}'",
                        "status": "rejected",
                    }
                )
                tool_result_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(
                            {"error": f"未知工具: {tool_name}"},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                )
                continue

            # 检查用户权限
            has_perm = self._tool_executor.check_role_permission(user, spec.required_permission)
            if not has_perm:
                executed_tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "summary": (f"拒绝执行：缺少权限 '{spec.required_permission}'"),
                        "status": "forbidden",
                    }
                )
                tool_result_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(
                            {"error": f"权限不足: 需要 {spec.required_permission}"},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                )
                continue

            # 白名单工具真实执行
            try:
                _t_tool_start = time.monotonic()
                tool_result = await self._tool_executor.execute_tool(
                    tool_name, tool_args, user, ctx.org_id
                )
                _t_tool_end = time.monotonic()
                logger.debug(
                    "[TIMING] tool_exec: %s  duration=%.1fms",
                    tool_name,
                    (_t_tool_end - _t_tool_start) * 1000,
                )
                result_summary = str(tool_result.get("summary", ""))

                # 工具结果归一化：检测数值工具的三路分流
                has_numeric_audit = (
                    isinstance(tool_result, dict)
                    and "audit" in tool_result
                    and "citation_params" in tool_result
                )
                if has_numeric_audit:
                    llm_payload = tool_result.get("data", {})
                    persisted_result = tool_result.get("audit", {})
                    citation_payload = tool_result.get("citation_params", tool_args)
                else:
                    llm_payload = tool_result.get("data", tool_result)
                    persisted_result = tool_result.get("data")
                    citation_payload = tool_args

                executed_tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "summary": result_summary or f"已执行 {spec.display_name}",
                        "status": "executed",
                        "result": persisted_result,
                    }
                )
                tool_result_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(
                            llm_payload,
                            ensure_ascii=False,
                            default=str,
                            separators=(",", ":"),
                        ),
                    }
                )
                # 生成结构化 citation（服务端签名，不可伪造）
                citation_gen = CitationGenerator()
                signed_citation = citation_gen.generate(
                    tool_name=tool_name,
                    query_params=citation_payload,
                    result_summary=result_summary or "工具执行完成",
                )
                all_citations.append(signed_citation)

                # 生成前端导航 citation（带 href 路由路径）
                nav_citation = _build_nav_citation(
                    tool_name, tool_args, tool_result, spec.display_name
                )
                if nav_citation is not None:
                    all_citations.append(nav_citation)
            except Exception as exc:
                error_msg = f"工具执行失败: {exc}"
                executed_tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "summary": error_msg,
                        "status": "error",
                    }
                )
                tool_result_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(
                            {"error": error_msg},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                )

        # 如果有工具被执行，进行第二轮 completion 获取最终回答
        if tool_result_messages:
            assistant_tool_calls: list[dict[str, Any]] = []
            for tc in response.tool_calls:
                tc_id = (
                    str(tc.get("id", ""))
                    or f"call_{tc.get('tool', 'unknown')}_{len(assistant_tool_calls)}"
                )
                assistant_tool_calls.append(
                    {
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": str(tc.get("tool", "")),
                            "arguments": json.dumps(
                                tc.get("args", {}),
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }
                )

            second_messages: list[dict[str, Any]] = list(ctx.msg_list)
            second_messages.append(
                {
                    "role": "assistant",
                    "content": response.answer,
                    "tool_calls": assistant_tool_calls,
                }
            )
            second_messages.extend(tool_result_messages)

            second_request = AIRequest(
                messages=tuple(second_messages),
                tools=ctx.tool_names,
                tool_schemas=ctx.tool_schemas,
                user_context=ctx.user_context,
                provider_mode=ctx.provider_name,
            )

            if hasattr(self._provider, "thinking_enabled"):
                self._provider.thinking_enabled = (
                    ctx.thinking_enabled and ctx.config_thinking_enabled
                )

            try:
                _t_r2_start = time.monotonic()
                second_response: AIResponse = await self._provider.complete(  # type: ignore[call-arg]
                    second_request, cancel_event=ctx.cancel_event
                )
                _t_r2_end = time.monotonic()
                logger.debug("[TIMING] llm_round2=%.0fms", (_t_r2_end - _t_r2_start) * 1000)
                final_answer = self._persistence.redact_credentials(second_response.answer)
                final_uncertainty = second_response.uncertainty
            except Exception:
                # 第二轮失败时使用第一轮回答 + 工具结果摘要
                tool_summaries = "\n".join(
                    f"- {tc['tool']}: {tc.get('summary', '')}"
                    for tc in executed_tool_calls
                    if tc.get("status") == "executed"
                )
                final_answer = self._persistence.redact_credentials(
                    response.answer
                    + (f"\n\n工具执行结果：\n{tool_summaries}" if tool_summaries else "")
                )
                final_uncertainty = response.uncertainty
        else:
            final_answer = self._persistence.redact_credentials(response.answer)
            final_uncertainty = response.uncertainty

        # 构建最终响应
        final_response = AIResponse(
            answer=final_answer,
            tool_calls=tuple(executed_tool_calls),
            citations=tuple(all_citations),
            uncertainty=final_uncertainty,
            provider_mode=response.provider_mode,
        )

        # 持久化消息
        await self._persistence.persist_messages(
            conversation_id=ctx.conversation_id,
            user_id=ctx.user_id,
            question=ctx.question,
            response=final_response,
            mentions=ctx.mentions,
            sender_display_name=getattr(user, "email", None),
            sender_avatar_url=None,
        )

        # 首次对话后自动生成标题
        if not ctx.history_messages:
            try:
                await self._persistence.auto_generate_title(
                    conversation_id=ctx.conversation_id,
                    question=ctx.question,
                    answer=final_response.answer,
                )
            except Exception:
                logging.getLogger(__name__).warning("unexpected error", exc_info=True)

        return final_response

    async def ask(
        self,
        user: Any,
        question: str,
        conversation_id: UUID | None = None,
        provider_name: str = "offline",
        thinking_enabled: bool = False,
        system_context: str | None = None,
        mentions: list[str] | None = None,
    ) -> AIResponse:
        """处理用户问题，返回 AI 回答。

        流程：
        1. 验证用户拥有 assistant:use 权限（由路由层 require_permission 保证）；
        2. 构建对话上下文（加载历史消息或创建新对话）；
        3. 构建 AIRequest（消息、工具、用户上下文）；
        4. 调用 Provider.complete 获取回答；
        5. 对回答中的工具调用执行权限检查与工具执行；
        6. 持久化用户消息与 AI 消息；
        7. 返回 AIResponse。

        Args:
            user: 当前用户（需有 user_id, email, roles 属性）。
            question: 用户问题文本。
            conversation_id: 对话 ID（None 时自动创建新对话）。
            provider_name: Provider 名称（用于选择 provider，当前仅支持 offline）。

        Returns:
            AIResponse: AI 回答。

        Raises:
            AppError: code="forbidden"，当用户缺少工具所需权限时。
        """
        ctx = await self._prepare_ask(
            user=user,
            question=question,
            conversation_id=conversation_id,
            provider_name=provider_name,
            thinking_enabled=thinking_enabled,
            system_context=system_context,
            mentions=mentions,
        )

        # 协作对话中：mentions 不含 "ai" 标识 → 仅持久化用户消息，不调 AI
        if ctx.mention_only:
            mention_only_response = AIResponse(
                answer="",
                tool_calls=(),
                citations=(),
                uncertainty=None,
                provider_mode=provider_name,
            )
            await self._persistence.persist_user_message_only(
                conversation_id=ctx.conversation_id,
                user_id=ctx.user_id,
                question=question,
                mentions=ctx.mentions,
                sender_display_name=getattr(user, "email", None),
                sender_avatar_url=None,
            )
            if not ctx.history_messages:
                try:
                    await self._persistence.auto_generate_title(
                        conversation_id=ctx.conversation_id,
                        question=question,
                        answer=question[:60],
                    )
                except Exception:
                    logging.getLogger(__name__).warning("unexpected error", exc_info=True)
            return mention_only_response

        try:
            # 调用 Provider（支持取消）
            _t0 = time.monotonic()
            _t1 = time.monotonic()
            response: AIResponse = await self._provider.complete(  # type: ignore[call-arg]
                ctx.ai_request, cancel_event=ctx.cancel_event
            )
            _t2 = time.monotonic()
            logger.debug(
                "[TIMING] prepare=%.0fms  llm_round1=%.0fms  tools=%d  thinking=%s",
                (_t1 - _t0) * 1000,
                (_t2 - _t1) * 1000,
                len(response.tool_calls),
                thinking_enabled and ctx.config_thinking_enabled,
            )
        except AppError as exc:
            if exc.code == "ai_cancelled":
                await self._persistence.persist_messages(
                    conversation_id=ctx.conversation_id,
                    user_id=ctx.user_id,
                    question=question,
                    response=AIResponse(
                        answer="[已取消]",
                        tool_calls=(),
                        citations=(),
                        uncertainty=None,
                        provider_mode=provider_name,
                    ),
                    mentions=ctx.mentions,
                    sender_display_name=getattr(user, "email", None),
                    sender_avatar_url=None,
                )
            raise
        finally:
            self._cancellation.unregister(ctx.conversation_id)

        final_response = await self._execute_and_finalize(response, ctx, user)
        return final_response

    async def stream_ask(
        self,
        user: Any,
        question: str,
        conversation_id: UUID | None = None,
        provider_name: str = "offline",
        thinking_enabled: bool = False,
        system_context: str | None = None,
        mentions: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式处理用户问题，逐 chunk 产出文本增量。

        与 ``ask()`` 共享前置准备（``_prepare_ask``）和后置处理
        （``_execute_and_finalize``）逻辑，中间用 Provider 的流式接口
        逐 chunk 产出文本。

        Args:
            user: 当前用户（需有 user_id, email, roles 属性）。
            question: 用户问题文本。
            conversation_id: 对话 ID（None 时自动创建新对话）。
            provider_name: Provider 名称。
            thinking_enabled: 是否启用思考模式。
            system_context: 系统上下文。
            mentions: @ 人的 user_id 数组。

        Yields:
            dict: 流式事件，格式为：
                - ``{"type": "chunk", "content": "文本增量"}`` — 文本块
                - ``{"type": "done", "answer": "...", "tool_calls": [...],
                  "citations": [...], "uncertainty": ...}`` — 完成
                - ``{"type": "error", "message": "错误信息"}`` — 错误
        """
        ctx = await self._prepare_ask(
            user=user,
            question=question,
            conversation_id=conversation_id,
            provider_name=provider_name,
            thinking_enabled=thinking_enabled,
            system_context=system_context,
            mentions=mentions,
        )
        _t0 = time.monotonic()
        logger.debug("[TIMING] stream_ask entered, question_len=%d", len(question))
        _t1 = time.monotonic()
        logger.debug("[TIMING] prepare=%.0fms", (_t1 - _t0) * 1000)

        # 协作对话中仅 @人（不 @AI）：仅持久化用户消息，不调 AI
        if ctx.mention_only:
            await self._persistence.persist_user_message_only(
                conversation_id=ctx.conversation_id,
                user_id=ctx.user_id,
                question=question,
                mentions=ctx.mentions,
                sender_display_name=getattr(user, "email", None),
                sender_avatar_url=None,
            )
            if not ctx.history_messages:
                try:
                    await self._persistence.auto_generate_title(
                        conversation_id=ctx.conversation_id,
                        question=question,
                        answer=question[:60],
                    )
                except Exception:
                    logging.getLogger(__name__).warning("unexpected error", exc_info=True)
            yield {
                "type": "done",
                "answer": "",
                "tool_calls": [],
                "citations": [],
                "uncertainty": None,
            }
            return

        try:
            # 检查 Provider 是否支持流式
            has_stream = callable(getattr(self._provider, "stream_complete", None))

            if not has_stream:
                # OfflineProvider 等不支持流式：一次性获取完整回答，作为单个 chunk yield
                _t1 = time.monotonic()
                response: AIResponse = await self._provider.complete(  # type: ignore[call-arg]
                    ctx.ai_request, cancel_event=ctx.cancel_event
                )
                _t2 = time.monotonic()
                logger.debug(
                    "[TIMING] stream_prepare=%.0fms  llm_round1=%.0fms  tools=%d  thinking=%s",
                    (_t1 - _t0) * 1000,
                    (_t2 - _t1) * 1000,
                    len(response.tool_calls),
                    thinking_enabled and ctx.config_thinking_enabled,
                )
                answer_text = self._persistence.redact_credentials(response.answer)
                if answer_text:
                    yield {"type": "chunk", "content": answer_text}

                # 后置处理（工具执行 + 持久化）
                final_response = await self._execute_and_finalize(response, ctx, user)
            else:
                # 流式 Provider：逐 chunk 产出文本增量
                _t1 = time.monotonic()
                full_text = ""
                streamed_tool_calls: list[dict[str, Any]] = []
                stream_error = False

                async for event in self._provider.stream_complete(  # type: ignore[attr-defined]
                    ctx.ai_request, cancel_event=ctx.cancel_event
                ):
                    event_type = event.get("type", "")
                    if event_type == "chunk":
                        content = event.get("content", "")
                        full_text += content
                        yield {"type": "chunk", "content": content}
                    elif event_type == "done":
                        streamed_tool_calls = event.get("tool_calls", [])
                        _t2 = time.monotonic()
                        logger.debug(
                            "[TIMING] stream_prepare=%.0fms"
                            "  llm_round1=%.0fms  tools=%d  thinking=%s",
                            (_t1 - _t0) * 1000,
                            (_t2 - _t1) * 1000,
                            len(streamed_tool_calls),
                            thinking_enabled and ctx.config_thinking_enabled,
                        )
                    elif event_type == "error":
                        stream_error = True
                        yield event
                        # 持久化取消/错误消息
                        await self._persistence.persist_messages(
                            conversation_id=ctx.conversation_id,
                            user_id=ctx.user_id,
                            question=question,
                            response=AIResponse(
                                answer="[已取消]",
                                tool_calls=(),
                                citations=(),
                                uncertainty=None,
                                provider_mode=provider_name,
                            ),
                            mentions=ctx.mentions,
                            sender_display_name=getattr(user, "email", None),
                            sender_avatar_url=None,
                        )
                        return

                if stream_error:
                    return

                # 构建第一轮 AIResponse（流式文本 + 组装的工具调用）
                first_response = AIResponse(
                    answer=full_text,
                    tool_calls=tuple(streamed_tool_calls),
                    citations=(),
                    uncertainty=None,
                    provider_mode=provider_name,
                )

                # 后置处理（工具执行 + 第二轮 + 持久化）
                final_response = await self._execute_and_finalize(first_response, ctx, user)

            # 序列化 done 事件
            yield {
                "type": "done",
                "answer": final_response.answer,
                "tool_calls": [
                    {
                        "tool": str(tc.get("tool", "")),
                        "args": tc.get("args", {}) if isinstance(tc.get("args"), dict) else {},
                        "summary": str(tc.get("summary", "")),
                        "status": str(tc.get("status", "")),
                    }
                    for tc in final_response.tool_calls
                ],
                "citations": [
                    ct.to_dict() if hasattr(ct, "to_dict") else ct
                    for ct in final_response.citations
                ],
                "uncertainty": final_response.uncertainty,
            }

        except AppError as exc:
            if exc.code == "ai_cancelled":
                await self._persistence.persist_messages(
                    conversation_id=ctx.conversation_id,
                    user_id=ctx.user_id,
                    question=question,
                    response=AIResponse(
                        answer="[已取消]",
                        tool_calls=(),
                        citations=(),
                        uncertainty=None,
                        provider_mode=provider_name,
                    ),
                    mentions=ctx.mentions,
                    sender_display_name=getattr(user, "email", None),
                    sender_avatar_url=None,
                )
            yield {"type": "error", "message": str(exc.message)}
        finally:
            self._cancellation.unregister(ctx.conversation_id)

    def cancel_request(self, conversation_id: UUID) -> bool:
        """取消正在进行的 AI 请求。

        Args:
            conversation_id: 对话 ID。

        Returns:
            bool: 是否成功取消（False 表示没有正在进行的请求）。
        """
        return self._cancellation.cancel(conversation_id)

    async def reload_tools(self) -> None:
        """从 DB 重新加载工具注册表（供 provider-status 等端点调用）。

        确保管理页面的启用/禁用变更能立即反映到状态查询中，
        而不仅是在 ask 时才 reload。
        """
        if self._factory is not None:
            async with scoped_session(self._factory, None, None) as session:
                await self._tool_registry.reload_from_db(session)

    def get_provider_status(self) -> dict[str, Any]:
        """返回当前 Provider 状态信息。

        Returns:
            dict: 包含 provider_mode、可用工具列表（仅已启用工具）。
        """
        # 仅展示已启用的 ai_tool 分类工具（ingestion 类不展示给 AI 对话）
        enabled = [s for s in self._tool_registry.list_enabled_tools() if s.category == "ai_tool"]
        return {
            "provider_mode": getattr(self._provider, "provider_mode", "unknown"),
            "whitelist_tools": [
                {
                    "name": s.name,
                    "display_name": s.display_name,
                    "description": s.description,
                    "required_permission": s.required_permission,
                }
                for s in enabled
            ],
            "candidate_tools": [],
        }
