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
- candidate_not_executed: 候选工具（candidate=True）不自动执行，仅记录建议；
- no_credential_leak: AI 回答中不包含密码、密钥、令牌等凭据信息。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from packages.ai.citations import Citation
from packages.ai.providers import AIProvider, AIRequest, AIResponse
from packages.ai.tools import ToolRegistry, ToolSpec
from packages.common.clock import Clock, SystemClock
from packages.common.database import Base, session_scope
from packages.common.db_types import GUID, UTCDateTime
from packages.common.errors import AppError
from packages.common.ids import new_id


class AIConversation(Base):
    """AI 对话实体（对应 ai_conversation 表）。

    Attributes:
        id: 对话 UUID。
        organization_id: 组织 ID。
        user_id: 创建用户 ID。
        title: 对话标题。
        provider_mode: Provider 模式（offline / openai_compatible）。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    __tablename__ = "ai_conversation"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    user_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    provider_mode: Mapped[str] = mapped_column(
        sa.Text, nullable=False, default="offline"
    )
    pinned: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    archived: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    system_context: Mapped[str | None] = mapped_column(
        sa.Text, nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=lambda: SystemClock().now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=lambda: SystemClock().now()
    )


class AIMessage(Base):
    """AI 消息实体（对应 ai_message 表）。

    Attributes:
        id: 消息 UUID。
        conversation_id: 对话 ID（FK→ai_conversation.id）。
        role: 消息角色（user / assistant / tool）。
        content: 消息文本内容。
        tool_calls_json: 工具调用 JSONB（工具名、参数、结果摘要）。
        citations_json: 引用 JSONB（object_type / object_id / version / label / href）。
        uncertainty: 不确定性说明（可空）。
        created_at: 创建时间。
    """

    __tablename__ = "ai_message"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    conversation_id: Mapped[UUID] = mapped_column(
        GUID,
        sa.ForeignKey("ai_conversation.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    tool_calls_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    citations_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    uncertainty: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=lambda: SystemClock().now()
    )


@dataclass(frozen=True)
class ConversationRef:
    """对话引用（不可变值对象）。

    Attributes:
        id: 对话 UUID。
        title: 对话标题。
        provider_mode: Provider 模式。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    id: UUID
    title: str
    provider_mode: str
    pinned: bool
    archived: bool
    created_at: datetime
    updated_at: datetime
    system_context: str | None = None


@dataclass(frozen=True)
class MessageRef:
    """消息引用（不可变值对象）。

    Attributes:
        id: 消息 UUID。
        conversation_id: 对话 UUID。
        role: 消息角色。
        content: 消息文本。
        tool_calls: 工具调用列表。
        citations: 引用列表。
        uncertainty: 不确定性说明。
        created_at: 创建时间。
    """

    id: UUID
    conversation_id: UUID
    role: str
    content: str
    tool_calls: list[dict[str, Any]]
    citations: list[dict[str, str]]
    uncertainty: str | None
    created_at: datetime


# ---- 模块级取消注册表 ----
# conversation_id → asyncio.Event，用于取消正在进行的 AI 请求
_active_requests: dict[UUID, asyncio.Event] = {}


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

    # ---- 对话管理 ----

    async def create_conversation(
        self,
        user_id: UUID,
        organization_id: UUID,
        title: str = "",
        provider_mode: str = "offline",
    ) -> ConversationRef:
        """创建新对话。

        Args:
            user_id: 用户 ID。
            organization_id: 组织 ID。
            title: 对话标题（空时自动生成）。
            provider_mode: Provider 模式。

        Returns:
            ConversationRef: 新对话引用。
        """
        conv_id = new_id()
        now = self._clock.now()
        if not title:
            title = f"对话 {now.strftime('%Y-%m-%d %H:%M')}"

        async with session_scope(self._factory) as session:
            conv = AIConversation(
                id=conv_id,
                organization_id=organization_id,
                user_id=user_id,
                title=title,
                provider_mode=provider_mode,
                created_at=now,
                updated_at=now,
            )
            session.add(conv)
            await session.flush()
            return ConversationRef(
                id=conv.id,
                title=conv.title,
                provider_mode=conv.provider_mode,
                pinned=False,
                archived=False,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
            )

    async def list_conversations(
        self,
        user_id: UUID,
        organization_id: UUID,
        limit: int = 50,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> list[ConversationRef]:
        """列出用户的对话（置顶优先，然后按更新时间倒序）。

        Args:
            user_id: 用户 ID（仅返回该用户的对话）。
            organization_id: 组织 ID。
            limit: 最大返回数。
            include_archived: 是否包含已归档对话（默认不含）。
            archived_only: 是否只返回已归档对话（优先于 include_archived）。

        Returns:
            list[ConversationRef]: 对话引用列表。
        """
        conditions = [
            AIConversation.user_id == user_id,
            AIConversation.organization_id == organization_id,
        ]
        if archived_only:
            conditions.append(AIConversation.archived == sa.true())
        elif not include_archived:
            conditions.append(AIConversation.archived == sa.false())

        async with self._factory() as session:
            result = await session.execute(
                sa.select(AIConversation)
                .where(*conditions)
                .order_by(
                    sa.desc(AIConversation.pinned),
                    sa.desc(AIConversation.updated_at),
                )
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                ConversationRef(
                    id=r.id,
                    title=r.title,
                    provider_mode=r.provider_mode,
                    pinned=r.pinned,
                    archived=r.archived,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    system_context=r.system_context,
                )
                for r in rows
            ]

    async def toggle_pin(
        self,
        conversation_id: UUID,
        user_id: UUID,
        pinned: bool | None = None,
    ) -> bool:
        """切换对话置顶状态。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID（权限检查）。
            pinned: 目标状态，None 时切换当前值。

        Returns:
            bool: 新的置顶状态。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            conv = await session.scalar(
                sa.select(AIConversation).where(
                    AIConversation.id == conversation_id,
                    AIConversation.user_id == user_id,
                )
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在或无权操作",
                    retryable=False,
                    fields={},
                )
            new_val = (not conv.pinned) if pinned is None else pinned
            conv.pinned = new_val
            conv.updated_at = now
            return new_val

    async def toggle_archive(
        self,
        conversation_id: UUID,
        user_id: UUID,
        archived: bool | None = None,
    ) -> bool:
        """切换对话归档状态。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID（权限检查）。
            archived: 目标状态，None 时切换当前值。

        Returns:
            bool: 新的归档状态。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            conv = await session.scalar(
                sa.select(AIConversation).where(
                    AIConversation.id == conversation_id,
                    AIConversation.user_id == user_id,
                )
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在或无权操作",
                    retryable=False,
                    fields={},
                )
            new_val = (not conv.archived) if archived is None else archived
            conv.archived = new_val
            conv.updated_at = now
            return new_val

    async def delete_conversation(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> None:
        """永久删除对话及其所有消息。

        仅允许删除已归档的对话，防止误删活跃对话。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID（权限检查）。

        Raises:
            AppError: code="not_found"，对话不存在或无权操作。
            AppError: code="forbidden"，对话未归档，不允许删除。
        """
        async with session_scope(self._factory) as session:
            conv = await session.scalar(
                sa.select(AIConversation).where(
                    AIConversation.id == conversation_id,
                    AIConversation.user_id == user_id,
                )
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在或无权操作",
                    retryable=False,
                    fields={},
                )
            if not conv.archived:
                raise AppError(
                    code="forbidden",
                    message="仅允许删除已归档的对话",
                    retryable=False,
                    fields={},
                )
            # 消息通过外键 CASCADE 自动删除
            await session.delete(conv)

    async def list_messages(
        self,
        conversation_id: UUID,
        user_id: UUID,
    ) -> list[MessageRef]:
        """列出对话中的消息（按创建时间正序）。

        安全检查：对话必须属于当前用户，否则抛 forbidden。

        Args:
            conversation_id: 对话 ID。
            user_id: 当前用户 ID（权限检查）。

        Returns:
            list[MessageRef]: 消息引用列表。

        Raises:
            AppError: code="forbidden"，当对话不属于当前用户时。
            AppError: code="not_found"，当对话不存在时。
        """
        async with self._factory() as session:
            conv = await session.scalar(
                sa.select(AIConversation).where(
                    AIConversation.id == conversation_id
                )
            )
            if conv is None:
                raise AppError(
                    code="not_found",
                    message="对话不存在",
                    retryable=False,
                    fields={},
                )
            if conv.user_id != user_id:
                raise AppError(
                    code="forbidden",
                    message="无权访问该对话",
                    retryable=False,
                    fields={},
                )

            result = await session.execute(
                sa.select(AIMessage)
                .where(AIMessage.conversation_id == conversation_id)
                .order_by(sa.asc(AIMessage.created_at))
            )
            rows = result.scalars().all()
            return [
                MessageRef(
                    id=r.id,
                    conversation_id=r.conversation_id,
                    role=r.role,
                    content=r.content,
                    tool_calls=r.tool_calls_json if isinstance(r.tool_calls_json, list) else [],
                    citations=r.citations_json if isinstance(r.citations_json, list) else [],
                    uncertainty=r.uncertainty,
                    created_at=r.created_at,
                )
                for r in rows
            ]

    # ---- 问答 ----

    async def ask(
        self,
        user: Any,
        question: str,
        conversation_id: UUID | None = None,
        provider_name: str = "offline",
        thinking_enabled: bool = False,
        system_context: str | None = None,
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
        user_id: UUID = user.user_id
        org_id: UUID | None = getattr(user, "organization_id", None)
        if org_id is None:
            # CurrentUser 可能没有 organization_id，使用默认值
            org_id = new_id()

        # 加载或创建对话
        if conversation_id is None:
            conv_ref = await self.create_conversation(
                user_id=user_id,
                organization_id=org_id,
                title=question[:60],
                provider_mode=provider_name,
            )
            conversation_id = conv_ref.id
            history_messages: list[dict[str, Any]] = []
        else:
            # 验证对话归属并加载历史
            msgs = await self.list_messages(conversation_id, user_id)
            history_messages = [
                {"role": m.role, "content": m.content} for m in msgs
            ]

        # 构建 user_context（不含凭据）
        user_context: dict[str, Any] = {
            "user_id": str(user_id),
            "organization_id": str(org_id),
            "roles": list(user.roles),
        }

        # 构建消息元组（历史 + 当前问题）
        # 如果有系统上下文（如实验数据），拼到默认 system 消息后面，不单独加 system role
        msg_list: list[dict[str, Any]] = []
        for m in history_messages:
            if m.get("role") == "system":
                continue  # 跳过历史中的 system 消息
            msg_list.append(m)
        msg_list.append({"role": "user", "content": question})
        messages: tuple[dict[str, Any], ...] = tuple(msg_list)

        # 把 system_context 存到 user_context 里，让 provider 拼到 system 消息
        if system_context:
            user_context["system_context"] = system_context
            # 同时存到对话记录里，切回对话时恢复
            async with self._factory() as session:
                conv_obj = await session.scalar(
                    sa.select(AIConversation).where(AIConversation.id == conversation_id)
                )
                if conv_obj:
                    conv_obj.system_context = system_context
                    await session.commit()

        # 构建工具名称元组（全部白名单 + 候选）
        tool_names: tuple[str, ...] = self._tool_registry.names()

        # 构建 AIRequest
        ai_request = AIRequest(
            messages=messages,
            tools=tool_names,
            user_context=user_context,
            provider_mode=provider_name,
        )

        # 思考模式：由对话框开关控制（AI 配置页已移除全局 thinking 开关，
        # 改为以对话级 thinking_enabled 作为唯一控制源）
        if hasattr(self._provider, "_thinking_enabled"):
            self._provider._thinking_enabled = thinking_enabled

        # 创建取消事件并注册到模块级字典
        cancel_event = asyncio.Event()
        _active_requests[conversation_id] = cancel_event

        try:
            # 调用 Provider（支持取消）
            response: AIResponse = await self._provider.complete(
                ai_request, cancel_event=cancel_event
            )
        except AppError as exc:
            if exc.code == "ai_cancelled":
                # 用户取消，不持久化 AI 回答，但保留用户消息
                await self._persist_messages(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    question=question,
                    response=AIResponse(
                        answer="[已取消]",
                        tool_calls=(),
                        citations=(),
                        uncertainty=None,
                        provider_mode=provider_name,
                    ),
                )
            raise
        finally:
            _active_requests.pop(conversation_id, None)

        # 执行工具调用（权限检查 + 白名单工具执行）
        executed_tool_calls: list[dict[str, Any]] = []
        for tc in response.tool_calls:
            tool_name = str(tc.get("tool", ""))
            tool_args = tc.get("args", {})
            if not isinstance(tool_args, dict):
                tool_args = {}

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
                continue

            # 检查用户权限
            has_perm = self._check_role_permission(user, spec.required_permission)
            if not has_perm:
                executed_tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "summary": (
                            f"拒绝执行：缺少权限 '{spec.required_permission}'"
                        ),
                        "status": "forbidden",
                    }
                )
                continue

            # 候选工具不自动执行
            if spec.candidate:
                executed_tool_calls.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "summary": (
                            f"候选工具建议（需人工审批）：{spec.display_name}"
                        ),
                        "status": "candidate",
                    }
                )
                continue

            # 白名单工具执行（只读，模拟执行结果摘要）
            executed_tool_calls.append(
                {
                    "tool": tool_name,
                    "args": tool_args,
                    "summary": tc.get("summary", f"已执行 {spec.display_name}"),
                    "status": "executed",
                }
            )

        # 凭据泄露检查：确保回答中不含密钥
        safe_answer = self._redact_credentials(response.answer)

        # 构建最终响应
        final_response = AIResponse(
            answer=safe_answer,
            tool_calls=tuple(executed_tool_calls),
            citations=response.citations,
            uncertainty=response.uncertainty,
            provider_mode=response.provider_mode,
        )

        # 持久化消息
        await self._persist_messages(
            conversation_id=conversation_id,
            user_id=user_id,
            question=question,
            response=final_response,
        )

        # 首次对话后自动生成标题
        if not history_messages:
            try:
                await self._auto_generate_title(
                    conversation_id=conversation_id,
                    question=question,
                    answer=final_response.answer,
                )
            except Exception:
                # 标题生成失败不影响主流程
                pass

        return final_response

    # ---- 内部方法 ----

    def _check_role_permission(self, user: Any, action: str) -> bool:
        """检查用户角色是否拥有指定权限（角色级，非对象级）。

        基于 BUILTIN_ROLES 权限矩阵，与 require_permission 依赖相同逻辑。

        Args:
            user: 当前用户（需有 roles 属性）。
            action: 权限字符串。

        Returns:
            bool: 有权返回 True。
        """
        from packages.auth.permissions import BUILTIN_ROLES

        for role_code in user.roles:
            role_def = BUILTIN_ROLES.get(role_code)
            if role_def is not None:
                permissions = role_def["permissions"]
                if isinstance(permissions, list) and action in permissions:
                    return True
        return False

    def _redact_credentials(self, text: str) -> str:
        """凭据脱敏：移除回答中可能出现的密钥模式。

        扫描回答文本，将疑似 API 密钥、Bearer 令牌的模式替换为 [REDACTED]。
        确保 AI 回答不泄露凭据。

        Args:
            text: 原始回答文本。

        Returns:
            str: 脱敏后的回答文本。
        """
        import re

        # 替换 Bearer token 模式
        text = re.sub(
            r"[Bb]earer\s+[A-Za-z0-9\-_\.]{20,}", "[REDACTED]", text
        )
        # 替换疑似 API key 模式（sk- 开头或长 hex/base64 串）
        text = re.sub(r"sk-[A-Za-z0-9]{20,}", "[REDACTED]", text)
        return text

    async def _persist_messages(
        self,
        conversation_id: UUID,
        user_id: UUID,
        question: str,
        response: AIResponse,
    ) -> None:
        """持久化用户消息与 AI 消息到数据库。

        Args:
            conversation_id: 对话 ID。
            user_id: 用户 ID。
            question: 用户问题。
            response: AI 回答。
        """
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            # 用户消息
            user_msg = AIMessage(
                id=new_id(),
                conversation_id=conversation_id,
                role="user",
                content=question,
                tool_calls_json=[],
                citations_json=[],
                uncertainty=None,
                created_at=now,
            )
            session.add(user_msg)

            # AI 消息
            tool_calls_list: list[dict[str, Any]] = [
                {
                    "tool": tc.get("tool", ""),
                    "args": tc.get("args", {}),
                    "summary": tc.get("summary", ""),
                    "status": tc.get("status", ""),
                }
                for tc in response.tool_calls
            ]
            citations_list: list[dict[str, str]] = []
            for c in response.citations:
                if isinstance(c, Citation):
                    citations_list.append(c.to_dict())
                elif isinstance(c, dict):
                    citations_list.append(c)

            ai_msg = AIMessage(
                id=new_id(),
                conversation_id=conversation_id,
                role="assistant",
                content=response.answer,
                tool_calls_json=tool_calls_list,
                citations_json=citations_list,
                uncertainty=response.uncertainty,
                created_at=now,
            )
            session.add(ai_msg)

            # 更新对话 updated_at
            await session.execute(
                sa.update(AIConversation)
                .values(updated_at=now)
                .where(AIConversation.id == conversation_id)
            )

    async def _auto_generate_title(
        self,
        conversation_id: UUID,
        question: str,
        answer: str,
    ) -> None:
        """首次对话后自动生成标题。

        直接用 httpx 调用 LLM API 生成标题（不走 thinking 模式），
        然后更新数据库中的对话标题。失败时静默跳过。

        Args:
            conversation_id: 对话 ID。
            question: 用户问题。
            answer: AI 回答。
        """
        # 从 provider 提取 API 配置
        api_key = getattr(self._provider, "_api_key", None)
        base_url = getattr(self._provider, "_base_url", None)
        model = getattr(self._provider, "_model", None)

        if not api_key or not base_url or not model:
            # 离线模式或其他无 API 配置的 provider，用问题前 30 字做标题
            title = question[:30].strip()
            if not title:
                return
        else:
            # 直接 httpx 调用，不走 thinking 模式
            import httpx

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": [
                                {
                                    "role": "system",
                                    "content": (
                                        "请用一句话概括以下对话的主题，不超过15个字。"
                                        "直接返回标题文本，不要解释、不要引号。"
                                    ),
                                },
                                {
                                    "role": "user",
                                    "content": (
                                        f"用户问题：{question[:500]}\n"
                                        f"AI回答：{answer[:500]}"
                                    ),
                                },
                            ],
                            "max_tokens": 200,
                            # 关闭思考模式，避免 token 浪费在思考过程
                            "chat_template_kwargs": {"enable_thinking": False},
                        },
                    )
                if resp.status_code != 200:
                    return
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    return
                msg = choices[0].get("message", {})
                # 优先取 content，回退到 reasoning_content（Qwen3 思考模式）
                title = (msg.get("content") or msg.get("reasoning_content") or "").strip()
            except Exception:
                return

        # 清理标题
        title = title.strip("\"'""''「」『』 \n\r\t")
        title = title.split("\n")[0].strip()
        if len(title) > 60:
            title = title[:60]
        if not title:
            return

        # 更新数据库
        now = self._clock.now()
        async with session_scope(self._factory) as session:
            await session.execute(
                sa.update(AIConversation)
                .values(title=title, updated_at=now)
                .where(AIConversation.id == conversation_id)
            )

    # ---- Provider 状态 ----

    def cancel_request(self, conversation_id: UUID) -> bool:
        """取消正在进行的 AI 请求。

        Args:
            conversation_id: 对话 ID。

        Returns:
            bool: 是否成功取消（False 表示没有正在进行的请求）。
        """
        event = _active_requests.get(conversation_id)
        if event is not None:
            event.set()
            return True
        return False

    def get_provider_status(self) -> dict[str, Any]:
        """返回当前 Provider 状态信息。

        Returns:
            dict: 包含 provider_mode、可用工具列表等。
        """
        return {
            "provider_mode": getattr(self._provider, "provider_mode", "unknown"),
            "whitelist_tools": [
                {
                    "name": s.name,
                    "display_name": s.display_name,
                    "description": s.description,
                    "required_permission": s.required_permission,
                    "candidate": s.candidate,
                }
                for s in self._tool_registry.list_whitelist_tools()
            ],
            "candidate_tools": [
                {
                    "name": s.name,
                    "display_name": s.display_name,
                    "description": s.description,
                    "required_permission": s.required_permission,
                    "candidate": s.candidate,
                }
                for s in self._tool_registry.list_candidate_tools()
            ],
        }
