"""AI 对话服务：AI 对话历史持久化 + 查询时截断保留最近 50 条。

AIConversationService 负责：
1. send_message: 持久化用户消息 → 加载历史 → 调用 AI → 持久化 AI 回复；
2. list_messages: 查询对话历史（最近 N 条，旧消息保留不删除）；
3. _truncate_history: 长对话截断（保留最近 50 条）。

对话消息持久化到 research_ai_conversation 表，支持重新进入恢复对话。

参照 packages/research/snapshots.py 的 ScopedSessionMixin 模式。
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.execution.models_trusted import ConversationMessage, TaskType
from packages.research.execution.repository_trusted import ResearchRepositoryTrusted

logger = logging.getLogger("research.conversation")

#: 对话历史截断条数。
MAX_HISTORY_COUNT: int = 50


class AIConversationService(ScopedSessionMixin):
    """AI 对话持久化服务。

    依赖注入 session_factory / department_id / actor_id / model_gateway。

    Attributes:
        _factory: 异步会话工厂。
        _dept_id: 当前部门 ID。
        _actor_id: 当前操作人 ID。
        _model_gateway: 模型网关。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        department_id: UUID,
        actor_id: UUID | None,
        model_gateway: Any,
    ) -> None:
        """初始化 AI 对话服务。

        Args:
            session_factory: 异步会话工厂。
            department_id: 当前部门 ID。
            actor_id: 当前操作人 ID。
            model_gateway: 模型网关。
        """
        self._factory = session_factory
        self._dept_id = department_id
        self._actor_id = actor_id
        self._model_gateway = model_gateway
        self._rls_dept_id: UUID | None = None

    def _require_actor(self) -> UUID:
        """获取当前操作人 ID，为空时抛出异常。"""
        if self._actor_id is None:
            raise AppError(
                code="forbidden",
                message="操作需要已认证用户",
                retryable=False,
                fields={},
            )
        return self._actor_id

    async def send_message(
        self,
        workspace_id: UUID,
        message: str,
        run_id: UUID | None = None,
    ) -> ConversationMessage:
        """发送对话消息并获取 AI 回复。

        流程：
        1. 持久化用户消息；
        2. 加载最近 50 条历史；
        3. 构建研究上下文（主问题 + 计划 + 已完成步骤摘要）；
        4. 调用 ModelGateway.call(CONVERSATION)；
        5. 持久化 AI 回复。

        Args:
            workspace_id: 工作空间 ID。
            message: 用户消息文本。
            run_id: 关联的 Run ID（可选）。

        Returns:
            ConversationMessage: AI 回复消息。
        """
        actor_id = self._require_actor()
        async with self._scoped_session() as session:
            # 1. 持久化用户消息
            await ResearchRepositoryTrusted.insert_conversation_message(
                session,
                workspace_id=workspace_id,
                role="user",
                content={"text": message},
                run_id=run_id,
                created_by=actor_id,
            )

            # 2. 加载最近 50 条历史
            history = await ResearchRepositoryTrusted.list_messages(
                session,
                workspace_id,
                run_id=run_id,
                limit=MAX_HISTORY_COUNT,
            )

            # 3. 构建对话上下文
            conversation_context = self._build_conversation_context(history)

            # 4. 调用 AI
            system_prompt = (
                "你是 IRIP 研究分析助手。请根据对话历史和研究上下文，"
                "帮助用户理解分析进度、解释中间结果、建议下一步方向或修复错误。"
                "回答应简洁、专业、可操作。"
            )

            try:
                response = await self._model_gateway.call(
                    task_type=TaskType.CONVERSATION,
                    system_prompt=system_prompt,
                    data_context="",
                    research_context=conversation_context,
                )
                ai_answer = response.answer if hasattr(response, "answer") else str(response)
                code_blocks = []  # type: ignore[var-annotated]
                list(response.tool_calls) if hasattr(response, "tool_calls") else []
            except Exception as exc:
                logger.warning("AI conversation call failed: %s", exc)
                ai_answer = "抱歉，AI 助手暂时无法响应。请稍后重试。"
                code_blocks = []

            # 5. 持久化 AI 回复
            ai_content: dict[str, Any] = {"text": ai_answer}
            if code_blocks:
                ai_content["code_blocks"] = code_blocks
            if run_id is not None:
                ai_content["run_ref"] = str(run_id)

            ai_msg = await ResearchRepositoryTrusted.insert_conversation_message(
                session,
                workspace_id=workspace_id,
                role="assistant",
                content=ai_content,
                run_id=run_id,
                created_by=None,
            )

            return ConversationMessage(
                message_id=ai_msg.id,
                workspace_id=workspace_id,
                role="assistant",
                content=ai_content,
                run_id=run_id,
                created_at=ai_msg.created_at,
            )

    async def list_messages(
        self,
        workspace_id: UUID,
        run_id: UUID | None = None,
        limit: int = MAX_HISTORY_COUNT,
    ) -> list[ConversationMessage]:
        """列出对话消息（最近 N 条，按时间正序返回）。

        长对话截断策略：查询时仅返回最近 N 条，旧消息保留在表中不删除。

        Args:
            workspace_id: 工作空间 ID。
            run_id: 关联 Run ID（可选过滤）。
            limit: 返回条数上限（默认 50）。

        Returns:
            list[ConversationMessage]: 消息列表（按时间正序）。
        """
        async with self._scoped_session() as session:
            messages = await ResearchRepositoryTrusted.list_messages(
                session,
                workspace_id,
                run_id=run_id,
                limit=limit,
            )
            return [
                ConversationMessage(
                    message_id=m.id,
                    workspace_id=m.workspace_id,
                    role=m.role,
                    content=dict(m.content),
                    run_id=m.run_id,
                    created_at=m.created_at,
                )
                for m in messages
            ]

    def _build_conversation_context(self, history: list[Any]) -> str:
        """构建对话上下文文本（从历史消息构建）。

        Args:
            history: 历史消息列表（ORM 实体）。

        Returns:
            str: 对话上下文文本。
        """
        if not history:
            return "（无历史对话）"

        lines: list[str] = []
        for msg in history:
            role_label = (
                "用户" if msg.role == "user" else "AI 助手" if msg.role == "assistant" else "系统"
            )
            content = msg.content if isinstance(msg.content, dict) else {}
            text = content.get("text", "")
            lines.append(f"{role_label}: {text}")

        return "\n".join(lines)

    def _truncate_history(
        self,
        messages: list[Any],
        max_count: int = MAX_HISTORY_COUNT,
    ) -> list[Any]:
        """截断历史消息，保留最近 N 条。

        旧消息保留在数据库表中不删除，仅查询时截断。

        Args:
            messages: 历史消息列表。
            max_count: 保留条数上限。

        Returns:
            list: 截断后的消息列表（最近 N 条）。
        """
        if len(messages) <= max_count:
            return list(messages)
        return list(messages[-max_count:])
