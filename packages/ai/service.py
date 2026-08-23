"""AI 编排服务：对话管理、工具执行、权限检查、持久化。

AIService 是 AI 助手的业务编排层，职责：
1. **对话管理**：创建对话、追加消息、列出对话与消息；
2. **工具执行**：调用白名单工具（只读），候选工具仅记录建议不执行；
3. **权限检查**：工具执行前通过 AuthorizationService 检查用户权限
   （与 REST API 相同的授权服务）；
4. **持久化**：对话与消息持久化到 ai_conversation / ai_message 表；
5. **凭据隔离**：user_context 不包含凭据，AI 回答不泄露密钥。

核心不变量：
- tool_permission_checked: 每个工具调用前必须检查 required_permission；
- no_credential_leak: AI 回答中不包含密码、密钥、令牌等凭据信息。

向后兼容：``service.py`` 重新导出所有从子模块移出的符号
（``AIConversation``, ``AIMessage``, ``ConversationRef``, ``MessageRef``,
``CancellationRegistry``），外部 ``from packages.ai.service import X`` 不受影响。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.ai.ask_service import AskService
from packages.ai.cancellation import CancellationRegistry
from packages.ai.collaboration_entities import (
    MentionableUserRef,
    ParticipantRef,
)
from packages.ai.collaboration_service import CollaborationService
from packages.ai.conversation_service import ConversationService
from packages.ai.entities import (  # noqa: F401
    AIConversation,
    AIMessage,
    ConversationRef,
    MessageRef,
)
from packages.ai.persistence import MessagePersistence
from packages.ai.providers import AIProvider, AIResponse
from packages.ai.showcase_entities import ShowcaseItemRef
from packages.ai.showcase_service import ShowcaseService
from packages.ai.tool_executor import ToolExecutor
from packages.ai.tools import ToolRegistry
from packages.common.clock import Clock, SystemClock


class AIService:
    """AI 编排服务。

    依赖注入：
    - provider: AI Provider（OfflineProvider / OpenAICompatibleProvider）；
    - tool_registry: 工具注册表（白名单 + 候选）；
    - fact_service: 事实服务（工具 search_facts / compare_experiments 执行）；
    - parameter_service: 参数服务（工具 search_parameters 执行）；
    - model_service: 模型服务（工具 run_published_model 执行）；
    - provenance_service: 溯源服务（工具 explain_provenance 执行）；
    - auth_service: 授权服务（工具调用前权限检查，与 REST API 相同）；
    - session_factory: 异步会话工厂（对话持久化）；
    - clock: 时钟依赖。

    Attributes:
        _provider: AI Provider 实例。
        _tool_registry: 工具注册表。
        _fact_service: 事实服务。
        _parameter_service: 参数服务。
        _model_service: 模型服务。
        _provenance_service: 溯源服务。
        _auth_service: 授权服务。
        _factory: 异步会话工厂。
        _clock: 时钟依赖。
    """

    def __init__(
        self,
        provider: AIProvider,
        tool_registry: ToolRegistry,
        fact_service: Any | None = None,
        parameter_service: Any | None = None,
        model_service: Any | None = None,
        provenance_service: Any | None = None,
        auth_service: Any | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        clock: Clock | None = None,
        numeric_tools: Any | None = None,
    ) -> None:
        """初始化 AI 编排服务。

        Args:
            provider: AI Provider 实例。
            tool_registry: 工具注册表。
            fact_service: 事实服务（工具执行用，可选）。
            parameter_service: 参数服务（工具执行用，可选）。
            model_service: 模型服务（工具执行用，可选）。
            provenance_service: 溯源服务（工具执行用，可选）。
            auth_service: 授权服务（工具权限检查，与 REST API 相同，
                可选）。None 时仅检查角色级权限。
            session_factory: 异步会话工厂（对话持久化，可选）。
            clock: 时钟依赖，默认 SystemClock。
            numeric_tools: NumericToolFacade 实例（数值工具执行用，可选）。
                生产环境必须注入；测试可不注入。
        """
        self._provider = provider
        self._tool_registry = tool_registry
        self._fact_service = fact_service
        self._parameter_service = parameter_service
        self._model_service = model_service
        self._provenance_service = provenance_service
        self._auth_service = auth_service
        self._factory = session_factory
        self._clock = clock or SystemClock()
        self._cancellation = CancellationRegistry()
        self._conversation_svc = ConversationService(self._factory, self._clock)
        self._collaboration_svc = CollaborationService(self._factory, self._clock)
        self._showcase_svc = ShowcaseService(self._factory, self._clock)
        self._tool_executor = ToolExecutor(
            tool_registry=tool_registry,
            fact_service=fact_service,
            parameter_service=parameter_service,
            model_service=model_service,
            provenance_service=provenance_service,
            session_factory=session_factory,
            numeric_tools=numeric_tools,
        )
        self._persistence = MessagePersistence(self._factory, self._clock, provider)
        self._ask_svc = AskService(
            provider=provider,
            tool_registry=tool_registry,
            tool_executor=self._tool_executor,
            persistence=self._persistence,
            conversation_service=self._conversation_svc,
            cancellation_registry=self._cancellation,
            session_factory=session_factory,
            clock=self._clock,
        )

    # ---- 对话管理（委托 ConversationService）----

    async def resolve_dept_id(
        self,
        user_id: UUID,
        known_dept_id: UUID | None = None,
    ) -> UUID:
        """解析用户所属部门 ID（供路由层调用，替代 router 内直接 ORM 查询）。

        优先使用已知 department_id；缺失时查 app_user 表；
        app_user 也查不到时查 root 哨兵部门；均失败时抛 AppError(forbidden)。

        Args:
            user_id: 用户 UUID。
            known_dept_id: 已知的部门 ID（如 token 中的 department_id），可为 None。

        Returns:
            UUID: 用户所属部门 ID。

        Raises:
            AppError: code="forbidden"，当无法确定用户所属部门时。
        """
        import logging

        from packages.common.database import session_scope
        from packages.common.errors import AppError

        logger = logging.getLogger("api.assistant")

        if known_dept_id is not None:
            return known_dept_id

        # 1. 查 app_user 表获取 department_id
        import sqlalchemy as sa

        from packages.auth.entities import AppUser

        try:
            if self._factory is not None:
                async with session_scope(self._factory) as session:
                    user = await session.scalar(sa.select(AppUser).where(AppUser.id == user_id))
                    if user is not None and user.department_id is not None:
                        return user.department_id
        except Exception as exc:
            logger.warning("Failed to load AppUser.department_id for %s: %s", user_id, exc)

        # 2. 兜底：查 root 哨兵部门
        try:
            if self._factory is not None:
                async with session_scope(self._factory) as session:
                    result = await session.execute(
                        sa.text(
                            "SELECT id FROM department "
                            "WHERE code = 'root' AND parent_id IS NULL LIMIT 1"
                        )
                    )
                    row = result.scalar()
                    if row is not None:
                        return UUID(str(row))
        except Exception as exc:
            logger.warning("Failed to resolve sentinel root department: %s", exc)

        raise AppError(
            code="forbidden",
            message="无法确定用户所属部门，请先绑定部门后再使用 AI 助手",
            retryable=False,
            fields={"user_id": str(user_id)},
        )

    async def create_conversation(
        self,
        user_id: UUID,
        department_id: UUID,
        title: str = "",
        provider_mode: str = "offline",
    ) -> ConversationRef:
        """创建新对话（委托到 ConversationService）。"""
        return await self._conversation_svc.create_conversation(
            user_id, department_id, title, provider_mode
        )

    async def list_conversations(
        self,
        user_id: UUID,
        department_id: UUID,
        limit: int = 50,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> list[ConversationRef]:
        """列出用户的对话（委托到 ConversationService）。"""
        return await self._conversation_svc.list_conversations(
            user_id, department_id, limit, include_archived, archived_only
        )

    async def toggle_pin(
        self,
        conversation_id: UUID,
        user_id: UUID,
        pinned: bool | None = None,
    ) -> bool:
        """切换对话置顶状态（委托到 ConversationService）。"""
        return await self._conversation_svc.toggle_pin(conversation_id, user_id, pinned)

    async def get_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> ConversationRef | None:
        """查询单个对话（委托到 ConversationService）。"""
        return await self._conversation_svc.get_conversation(conversation_id, user_id)

    async def toggle_archive(
        self,
        conversation_id: UUID,
        user_id: UUID,
        archived: bool | None = None,
    ) -> bool:
        """切换对话归档状态（委托到 ConversationService）。"""
        return await self._conversation_svc.toggle_archive(conversation_id, user_id, archived)

    async def delete_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> None:
        """永久删除对话及其所有消息（委托到 ConversationService）。"""
        await self._conversation_svc.delete_conversation(conversation_id, user_id)

    async def list_messages(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> list[MessageRef]:
        """列出对话中的消息（委托到 ConversationService）。"""
        return await self._conversation_svc.list_messages(conversation_id, user_id)

    # ---- 对话搜索（委托 ConversationService）----

    async def search_conversations(
        self,
        user_id: UUID,
        department_id: UUID,
        keyword: str,
        include_archived: bool = False,
        archived_only: bool = False,
        limit: int = 50,
    ) -> list[ConversationRef]:
        """按关键词搜索对话（委托到 ConversationService）。"""
        return await self._conversation_svc.search_conversations(
            user_id, department_id, keyword, include_archived, archived_only, limit
        )

    # ---- irip-ai-collab: 协作管理（委托 CollaborationService）----

    async def list_conversations_with_tab(
        self,
        user_id: UUID,
        department_id: UUID,
        tab: str = "private",
        limit: int = 50,
        include_archived: bool = False,
        archived_only: bool = False,
        keyword: str | None = None,
    ) -> list[ConversationRef]:
        """按 tab 筛选列出对话（委托到 CollaborationService）。"""
        return await self._collaboration_svc.list_conversations_with_tab(
            user_id, department_id, tab, limit, include_archived, archived_only, keyword
        )

    async def add_participant(
        self,
        conversation_id: UUID,
        inviter_user_id: UUID,
        target_user_id: UUID,
    ) -> ParticipantRef:
        """邀请用户加入对话（委托到 CollaborationService）。"""
        return await self._collaboration_svc.add_participant(
            conversation_id, inviter_user_id, target_user_id
        )

    async def remove_participant(
        self,
        conversation_id: UUID,
        owner_user_id: UUID,
        target_user_id: UUID,
    ) -> None:
        """移除对话参与者（委托到 CollaborationService）。"""
        await self._collaboration_svc.remove_participant(
            conversation_id, owner_user_id, target_user_id
        )

    async def leave_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> None:
        """退出对话（委托到 CollaborationService）。"""
        await self._collaboration_svc.leave_conversation(conversation_id, user_id)

    async def list_participants(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> list[ParticipantRef]:
        """列出对话参与者（委托到 CollaborationService）。"""
        return await self._collaboration_svc.list_participants(conversation_id, user_id)

    async def list_mentionable_users(
        self,
        user_id: UUID,
        department_id: UUID,
        roles: list[str] | None = None,
    ) -> list[MentionableUserRef]:
        """列出可 @ 的用户（委托到 CollaborationService）。"""
        return await self._collaboration_svc.list_mentionable_users(user_id, department_id, roles)

    # ---- 橱窗卡片管理（委托 ShowcaseService）----

    async def _check_conversation_access(
        self,
        session: AsyncSession,
        conversation_id: UUID,
        user_id: UUID,
    ) -> bool:
        """校验用户是否有权访问对话（委托到 ShowcaseService，向后兼容）。"""
        return await self._showcase_svc._check_conversation_access(
            session, conversation_id, user_id
        )

    async def add_showcase_item(
        self,
        user_id: UUID,
        conversation_id: UUID,
        block_type: str,
        title: str,
        content_snapshot: str,
        source_message_id: UUID,
        source_block_index: int,
        data_source: dict[str, Any] | None = None,
    ) -> ShowcaseItemRef:
        """向对话橱窗添加一个内容块卡片（委托到 ShowcaseService）。"""
        return await self._showcase_svc.add_showcase_item(
            user_id,
            conversation_id,
            block_type,
            title,
            content_snapshot,
            source_message_id,
            source_block_index,
            data_source,
        )

    async def list_showcase_items(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> list[ShowcaseItemRef]:
        """列出对话橱窗的卡片（委托到 ShowcaseService）。"""
        return await self._showcase_svc.list_showcase_items(conversation_id, user_id)

    async def update_showcase_item(
        self,
        item_id: UUID,
        user_id: UUID,
        title: str | None = None,
    ) -> ShowcaseItemRef:
        """更新橱窗卡片标题（委托到 ShowcaseService）。"""
        return await self._showcase_svc.update_showcase_item(item_id, user_id, title)

    async def delete_showcase_item(
        self,
        item_id: UUID,
        user_id: UUID,
    ) -> None:
        """删除橱窗卡片（委托到 ShowcaseService）。"""
        await self._showcase_svc.delete_showcase_item(item_id, user_id)

    async def reorder_showcase_items(
        self,
        conversation_id: UUID,
        user_id: UUID,
        item_ids: list[UUID],
    ) -> None:
        """批量更新橱窗卡片排序（委托到 ShowcaseService）。"""
        await self._showcase_svc.reorder_showcase_items(conversation_id, user_id, item_ids)

    async def generate_summary(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> tuple[str, int]:
        """基于橱窗卡片生成 Markdown 分析摘要（委托到 ShowcaseService）。"""
        return await self._showcase_svc.generate_summary(conversation_id, user_id)

    # ---- 问答（委托 AskService）----

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
        """处理用户问题，返回 AI 回答（委托到 AskService）。"""
        return await self._ask_svc.ask(
            user,
            question,
            conversation_id,
            provider_name,
            thinking_enabled,
            system_context,
            mentions,
        )

    def stream_ask(
        self,
        user: Any,
        question: str,
        conversation_id: UUID | None = None,
        provider_name: str = "offline",
        thinking_enabled: bool = False,
        system_context: str | None = None,
        mentions: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式处理用户问题（委托到 AskService）。

        注意：不能用 async def，否则 return 会被包装为 coroutine，
        调用方 async for 会报 'coroutine was never awaited'。
        """
        return self._ask_svc.stream_ask(
            user,
            question,
            conversation_id,
            provider_name,
            thinking_enabled,
            system_context,
            mentions,
        )

    # ---- 内部方法（委托到 ToolExecutor，保持 _ 前缀向后兼容）----

    def _check_role_permission(self, user: Any, action: str) -> bool:
        """检查用户角色是否拥有指定权限（委托到 ToolExecutor，向后兼容）。"""
        return self._tool_executor.check_role_permission(user, action)

    def _build_tool_schemas(self) -> tuple[dict[str, Any], ...]:
        """将 ToolRegistry 工具规格转为 OpenAI tools JSON schema（委托到 ToolExecutor）。"""
        return self._tool_executor.build_tool_schemas()

    async def _execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        user: Any,
        org_id: UUID,
    ) -> dict[str, Any]:
        """执行白名单工具的真实查询（委托到 ToolExecutor，向后兼容）。"""
        return await self._tool_executor.execute_tool(tool_name, args, user, org_id)

    # ---- 持久化（委托到 MessagePersistence，保持 _ 前缀向后兼容）----

    def _redact_credentials(self, text: str) -> str:
        """凭据脱敏（委托到 MessagePersistence，向后兼容）。"""
        return self._persistence.redact_credentials(text)

    async def _persist_user_message_only(self, *args: Any, **kwargs: Any) -> None:
        """仅持久化用户消息（委托到 MessagePersistence，向后兼容）。"""
        await self._persistence.persist_user_message_only(*args, **kwargs)

    async def _persist_messages(self, *args: Any, **kwargs: Any) -> None:
        """持久化用户消息与 AI 消息（委托到 MessagePersistence，向后兼容）。"""
        await self._persistence.persist_messages(*args, **kwargs)

    async def _auto_generate_title(self, *args: Any, **kwargs: Any) -> None:
        """自动生成对话标题（委托到 MessagePersistence，向后兼容）。"""
        await self._persistence.auto_generate_title(*args, **kwargs)

    # ---- Provider 状态（委托到 AskService）----

    def cancel_request(self, conversation_id: UUID) -> bool:
        """取消正在进行的 AI 请求（委托到 AskService）。"""
        return self._ask_svc.cancel_request(conversation_id)

    async def reload_tools(self) -> None:
        """从 DB 重新加载工具注册表（委托到 AskService）。"""
        await self._ask_svc.reload_tools()

    def get_provider_status(self) -> dict[str, Any]:
        """返回当前 Provider 状态信息（委托到 AskService）。"""
        return self._ask_svc.get_provider_status()
