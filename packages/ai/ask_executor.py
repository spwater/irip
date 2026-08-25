"""AskService 执行阶段辅助函数。

从 ``ask_service.py`` 的 ``_execute_and_finalize`` 方法中提取的模块级辅助逻辑，
负责工具调用处理、第二轮 completion、最终响应组装和持久化。

设计要点：
- 各函数接收所需数据作为参数（非共享状态），保持无副作用；
- ``_process_tool_calls`` 处理工具调用循环（权限检查 + 执行 + citation 生成）；
- ``_build_final_response`` 执行第二轮 completion（如有工具被调用）并组装最终响应；
- ``_persist_ask_result`` 负责消息持久化和自动标题生成。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from packages.ai.citation import CitationGenerator
from packages.ai.citation_builder import _build_nav_citation
from packages.ai.persistence import MessagePersistence
from packages.ai.providers import AIProvider, AIRequest, AIResponse
from packages.ai.tool_executor import ToolExecutor
from packages.ai.tools import ToolRegistry
from packages.common.errors import AppError

logger = logging.getLogger(__name__)


async def _process_tool_calls(
    response: AIResponse,
    user: Any,
    org_id: Any,
    tool_registry: ToolRegistry,
    tool_executor: ToolExecutor,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Any]]:
    """处理第一轮响应中的工具调用。

    遍历 ``response.tool_calls``，对每个工具调用执行：
    1. 白名单验证（未知工具 → rejected）；
    2. 用户权限检查（权限不足 → forbidden）；
    3. 白名单工具真实执行（异常 → error）；
    4. 生成结构化 citation 和前端导航 citation。

    Args:
        response: 第一轮 completion 的 AIResponse。
        user: 当前用户（需有 user_id, email, roles 属性）。
        org_id: 用户所属部门 ID。
        tool_registry: 工具注册表。
        tool_executor: 工具执行器。

    Returns:
        tuple: (executed_tool_calls, tool_result_messages, all_citations)
            - executed_tool_calls: 工具调用记录列表（含状态和结果）。
            - tool_result_messages: 第二轮 completion 的 tool role 消息列表。
            - all_citations: 结构化 citation 列表。
    """
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
            spec = tool_registry.validate(tool_name)
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
        has_perm = tool_executor.check_role_permission(user, spec.required_permission)
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
            tool_result = await tool_executor.execute_tool(tool_name, tool_args, user, org_id)
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
            nav_citation = _build_nav_citation(tool_name, tool_args, tool_result, spec.display_name)
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

    return executed_tool_calls, tool_result_messages, all_citations


async def _build_final_response(
    response: AIResponse,
    ctx: Any,
    provider: AIProvider,
    tool_result_messages: list[dict[str, Any]],
    executed_tool_calls: list[dict[str, Any]],
    all_citations: list[Any],
    persistence: MessagePersistence,
    user: Any = None,
    tool_registry: Any = None,
    tool_executor: Any = None,
) -> AIResponse:
    """构建最终 AIResponse（含第二轮 completion 如有工具被调用）。

    如果有工具被执行，组装第二轮消息并调用 Provider 获取最终回答；
    第二轮失败时回退到第一轮回答 + 工具结果摘要。

    Args:
        response: 第一轮 completion 的 AIResponse。
        ctx: _AskContext 共享上下文。
        provider: AI Provider 实例。
        tool_result_messages: 工具执行结果消息列表。
        executed_tool_calls: 工具调用记录列表。
        all_citations: citation 列表。
        persistence: 消息持久化服务。

    Returns:
        AIResponse: 最终回答（含工具调用结果、引用、不确定性）。
    """
    if tool_result_messages:
        # 构建第一轮 assistant + tool 结果消息
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

        # 累积消息列表：历史 + 第一轮 assistant + tool 结果
        round_messages: list[dict[str, Any]] = list(ctx.msg_list)
        round_messages.append(
            {
                "role": "assistant",
                "content": response.answer,
                "tool_calls": assistant_tool_calls,
            }
        )
        round_messages.extend(tool_result_messages)

        if hasattr(provider, "thinking_enabled"):
            provider.thinking_enabled = False

        # 循环执行：AI 产出 tool_calls → 执行工具 → 再 completion，直到无 tool_calls
        # 安全上限 10 轮，防止死循环
        MAX_ROUNDS = 10
        current_response = response
        final_answer = persistence.redact_credentials(response.answer)
        final_uncertainty = response.uncertainty

        for round_num in range(2, MAX_ROUNDS + 2):
            round_request = AIRequest(
                messages=tuple(round_messages),
                tools=ctx.tool_names,
                tool_schemas=ctx.tool_schemas,
                user_context=ctx.user_context,
                provider_mode=ctx.provider_name,
            )
            try:
                _t_start = time.monotonic()
                round_response = await provider.complete(  # type: ignore[call-arg]
                    round_request, cancel_event=ctx.cancel_event
                )
                _t_end = time.monotonic()
                logger.debug("[TIMING] llm_round%d=%.0fms", round_num, (_t_end - _t_start) * 1000)
            except Exception:
                # completion 失败，用已有回答 + 工具摘要兜底
                tool_summaries = "\n".join(
                    f"- {tc['tool']}: {tc.get('summary', '')}"
                    for tc in executed_tool_calls
                    if tc.get("status") == "executed"
                )
                final_answer = persistence.redact_credentials(
                    current_response.answer
                    + (f"\n\n工具执行结果：\n{tool_summaries}" if tool_summaries else "")
                )
                final_uncertainty = current_response.uncertainty
                break

            final_answer = persistence.redact_credentials(round_response.answer)
            final_uncertainty = round_response.uncertainty
            current_response = round_response

            if not round_response.tool_calls:
                # AI 不再需要调工具，结束循环
                break

            if round_num > MAX_ROUNDS:
                logger.warning("工具调用循环达到上限 %d 轮，强制停止", MAX_ROUNDS)
                break

            # AI 又产生了 tool_calls，执行它们
            logger.info(
                "第 %d 轮产生 %d 个 tool_calls，继续执行",
                round_num,
                len(round_response.tool_calls),
            )
            r_executed, r_tool_msgs, r_citations = await _process_tool_calls(
                response=round_response,
                user=user,
                org_id=ctx.org_id,
                tool_registry=tool_registry,
                tool_executor=tool_executor,
            )
            executed_tool_calls.extend(r_executed)
            all_citations.extend(r_citations)

            if not r_tool_msgs:
                break

            # 追加 assistant + tool 结果到消息列表
            r_assistant_calls = []
            for tc in round_response.tool_calls:
                tc_id = str(tc.get("id", "")) or f"call_r{round_num}_{len(r_assistant_calls)}"
                r_assistant_calls.append({
                    "id": tc_id,
                    "type": "function",
                    "function": {
                        "name": str(tc.get("tool", "")),
                        "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False, separators=(",", ":")),
                    },
                })
            round_messages.append(
                {"role": "assistant", "content": round_response.answer, "tool_calls": r_assistant_calls}
            )
            round_messages.extend(r_tool_msgs)
    else:
        final_answer = persistence.redact_credentials(response.answer)
        final_uncertainty = response.uncertainty
        # 后置校验：回答含数值结果但未调用工具（心算检测）
        if response.answer and not tool_result_messages:
            import re

            has_numeric_result = bool(
                re.search(r"=\s*\*{0,2}\s*\d+\.?\d*", response.answer)
            )
            if has_numeric_result:
                logger.warning(
                    "AI 回答包含数值结果但未调用任何工具（心算），"
                    "answer_len=%d, model=%s",
                    len(response.answer),
                    response.provider_mode,
                )

    return AIResponse(
        answer=final_answer,
        tool_calls=tuple(executed_tool_calls),
        citations=tuple(all_citations),
        uncertainty=final_uncertainty,
        provider_mode=response.provider_mode,
    )


async def _persist_ask_result(
    final_response: AIResponse,
    ctx: Any,
    user: Any,
    persistence: MessagePersistence,
) -> None:
    """持久化 ask 结果消息，首次对话后自动生成标题。

    Args:
        final_response: 最终 AIResponse。
        ctx: _AskContext 共享上下文。
        user: 当前用户。
        persistence: 消息持久化服务。
    """
    await persistence.persist_messages(
        conversation_id=ctx.conversation_id,
        user_id=ctx.user_id,
        question=ctx.question,
        response=final_response,
        mentions=ctx.mentions,
        sender_display_name=getattr(user, "email", None),
        sender_avatar_url=None,
    )

    # 前 3 轮每轮都尝试生成/更新标题（第 3 轮后停止，避免重复调用）
    # history_messages 包含之前的 user+assistant 消息，2 条 = 1 轮，6 条 = 3 轮
    if len(ctx.history_messages) < 6:
        try:
            await persistence.auto_generate_title(
                conversation_id=ctx.conversation_id,
                question=ctx.question,
                answer=final_response.answer,
                user_id=ctx.user_id,
                dept_id=ctx.org_id,
            )
        except Exception:
            logging.getLogger(__name__).warning("unexpected error", exc_info=True)
