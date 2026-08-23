"""AI 对话服务单元测试。

覆盖 packages/research/conversation_service.py：
- _require_actor: 无 actor → forbidden；
- _build_conversation_context: 空历史、user/assistant/system 角色、非 dict content；
- _truncate_history: 短列表、刚好超限、远超上限；
- send_message: mock DB + mock model_gateway，正常和异常路径；
- list_messages: mock DB 返回消息列表。
"""

import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from packages.common.errors import AppError
from packages.research.conversation_service import (
    MAX_HISTORY_COUNT,
    AIConversationService,
)
from packages.research.execution.models_trusted import ConversationMessage

# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_service(
    actor_id: UUID | None = None,
    model_gateway: Any = None,
) -> AIConversationService:
    """构建 AIConversationService 实例。"""
    factory = MagicMock()
    gw = model_gateway or MagicMock()
    return AIConversationService(
        session_factory=factory,
        department_id=uuid4(),
        actor_id=actor_id,
        model_gateway=gw,
    )


def _patch_scoped_session(service: AIConversationService, session: AsyncMock) -> Any:
    """Patch _scoped_session 返回固定 mock session。"""

    @contextlib.asynccontextmanager
    async def _ctx(_self: Any):
        yield session

    return patch.object(type(service), "_scoped_session", _ctx)


def _make_orm_message(
    role: str = "user",
    content: dict[str, Any] | None = None,
    run_id: UUID | None = None,
) -> MagicMock:
    """构建模拟的 ORM 消息对象。"""
    msg = MagicMock()
    msg.id = uuid4()
    msg.workspace_id = uuid4()
    msg.role = role
    msg.content = content or {"text": "hello"}
    msg.run_id = run_id
    msg.created_at = MagicMock()
    return msg


# ---------------------------------------------------------------------------
# _require_actor
# ---------------------------------------------------------------------------


class TestRequireActor:
    """_require_actor 方法。"""

    def test_with_actor(self) -> None:
        """有 actor → 返回 actor_id。"""
        actor = uuid4()
        service = _make_service(actor_id=actor)
        assert service._require_actor() == actor

    def test_without_actor(self) -> None:
        """无 actor → forbidden。"""
        service = _make_service(actor_id=None)
        with pytest.raises(AppError) as exc_info:
            service._require_actor()
        assert exc_info.value.code == "forbidden"


# ---------------------------------------------------------------------------
# _build_conversation_context
# ---------------------------------------------------------------------------


class TestBuildConversationContext:
    """_build_conversation_context 方法。"""

    def test_empty_history(self) -> None:
        """空历史 → "（无历史对话）"。"""
        service = _make_service(actor_id=uuid4())
        result = service._build_conversation_context([])
        assert result == "（无历史对话）"

    def test_user_message(self) -> None:
        """user 消息 → "用户: ..."。"""
        service = _make_service(actor_id=uuid4())
        msg = _make_orm_message(role="user", content={"text": "你好"})
        result = service._build_conversation_context([msg])
        assert "用户: 你好" in result

    def test_assistant_message(self) -> None:
        """assistant 消息 → "AI 助手: ..."。"""
        service = _make_service(actor_id=uuid4())
        msg = _make_orm_message(role="assistant", content={"text": "回复"})
        result = service._build_conversation_context([msg])
        assert "AI 助手: 回复" in result

    def test_system_message(self) -> None:
        """system 消息 → "系统: ..."。"""
        service = _make_service(actor_id=uuid4())
        msg = _make_orm_message(role="system", content={"text": "通知"})
        result = service._build_conversation_context([msg])
        assert "系统: 通知" in result

    def test_multiple_messages(self) -> None:
        """多条消息 → 逐行拼接。"""
        service = _make_service(actor_id=uuid4())
        msgs = [
            _make_orm_message(role="user", content={"text": "问题"}),
            _make_orm_message(role="assistant", content={"text": "回答"}),
        ]
        result = service._build_conversation_context(msgs)
        lines = result.split("\n")
        assert len(lines) == 2
        assert "用户: 问题" in lines[0]
        assert "AI 助手: 回答" in lines[1]

    def test_non_dict_content(self) -> None:
        """content 为非 dict → 空文本。"""
        service = _make_service(actor_id=uuid4())
        msg = _make_orm_message(role="user", content="not-a-dict")
        result = service._build_conversation_context([msg])
        assert "用户: " in result

    def test_content_without_text_key(self) -> None:
        """content dict 无 text 键 → 空文本。"""
        service = _make_service(actor_id=uuid4())
        msg = _make_orm_message(role="user", content={"code": "print(1)"})
        result = service._build_conversation_context([msg])
        assert "用户: " in result


# ---------------------------------------------------------------------------
# _truncate_history
# ---------------------------------------------------------------------------


class TestTruncateHistory:
    """_truncate_history 方法。"""

    def test_short_list(self) -> None:
        """短于上限 → 原样返回（副本）。"""
        service = _make_service(actor_id=uuid4())
        msgs = [MagicMock() for _ in range(10)]
        result = service._truncate_history(msgs)
        assert len(result) == 10
        assert result is not msgs  # 返回副本

    def test_exact_limit(self) -> None:
        """刚好等于上限 → 原样返回。"""
        service = _make_service(actor_id=uuid4())
        msgs = [MagicMock() for _ in range(MAX_HISTORY_COUNT)]
        result = service._truncate_history(msgs)
        assert len(result) == MAX_HISTORY_COUNT

    def test_exceeds_limit(self) -> None:
        """超过上限 → 截断到上限。"""
        service = _make_service(actor_id=uuid4())
        msgs = [MagicMock() for _ in range(MAX_HISTORY_COUNT + 20)]
        result = service._truncate_history(msgs)
        assert len(result) == MAX_HISTORY_COUNT

    def test_empty_list(self) -> None:
        """空列表 → 空列表。"""
        service = _make_service(actor_id=uuid4())
        result = service._truncate_history([])
        assert result == []

    def test_custom_max_count(self) -> None:
        """自定义 max_count。"""
        service = _make_service(actor_id=uuid4())
        msgs = [MagicMock() for _ in range(100)]
        result = service._truncate_history(msgs, max_count=5)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    """send_message 方法。"""

    @pytest.mark.asyncio
    async def test_requires_actor(self) -> None:
        """无 actor → forbidden。"""
        service = _make_service(actor_id=None)
        with pytest.raises(AppError) as exc_info:
            await service.send_message(uuid4(), "hello")
        assert exc_info.value.code == "forbidden"

    @pytest.mark.asyncio
    async def test_send_message_success(self) -> None:
        """正常发送 → 返回 assistant 消息。"""
        actor = uuid4()
        workspace_id = uuid4()
        run_id = uuid4()

        gw = MagicMock()
        gw.call = AsyncMock(return_value=MagicMock(answer="AI 回复", tool_calls=[]))

        service = _make_service(actor_id=actor, model_gateway=gw)

        session = AsyncMock()
        user_orm_msg = _make_orm_message(role="user", content={"text": "hello"})
        ai_orm_msg = _make_orm_message(role="assistant", content={"text": "AI 回复"})

        with (
            patch(
                "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.insert_conversation_message",
                AsyncMock(side_effect=[user_orm_msg, ai_orm_msg]),
            ),
            patch(
                "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.list_messages",
                AsyncMock(return_value=[]),
            ),
            patch("packages.ai.prompt_store.get_prompt", return_value="system prompt"),
            _patch_scoped_session(service, session),
        ):
            result = await service.send_message(workspace_id, "hello", run_id=run_id)

        assert isinstance(result, ConversationMessage)
        assert result.role == "assistant"
        gw.call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_message_ai_failure(self) -> None:
        """AI 调用失败 → 返回错误提示消息。"""
        actor = uuid4()
        workspace_id = uuid4()

        gw = MagicMock()
        gw.call = AsyncMock(side_effect=RuntimeError("model down"))

        service = _make_service(actor_id=actor, model_gateway=gw)

        session = AsyncMock()
        user_orm_msg = _make_orm_message(role="user")
        ai_orm_msg = _make_orm_message(role="assistant")

        with (
            patch(
                "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.insert_conversation_message",
                AsyncMock(side_effect=[user_orm_msg, ai_orm_msg]),
            ),
            patch(
                "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.list_messages",
                AsyncMock(return_value=[]),
            ),
            patch("packages.ai.prompt_store.get_prompt", return_value="system prompt"),
            _patch_scoped_session(service, session),
        ):
            result = await service.send_message(workspace_id, "hello")

        assert result.role == "assistant"
        assert "暂时无法响应" in result.content.get("text", "")

    @pytest.mark.asyncio
    async def test_send_message_without_run_id(self) -> None:
        """无 run_id → ai_content 不含 run_ref。"""
        actor = uuid4()
        workspace_id = uuid4()

        gw = MagicMock()
        gw.call = AsyncMock(return_value=MagicMock(answer="ok", tool_calls=[]))

        service = _make_service(actor_id=actor, model_gateway=gw)

        session = AsyncMock()
        user_orm_msg = _make_orm_message(role="user")
        ai_orm_msg = _make_orm_message(role="assistant")

        with (
            patch(
                "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.insert_conversation_message",
                AsyncMock(side_effect=[user_orm_msg, ai_orm_msg]),
            ),
            patch(
                "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.list_messages",
                AsyncMock(return_value=[]),
            ),
            patch("packages.ai.prompt_store.get_prompt", return_value="system prompt"),
            _patch_scoped_session(service, session),
        ):
            result = await service.send_message(workspace_id, "hello")

        assert "run_ref" not in result.content


# ---------------------------------------------------------------------------
# list_messages
# ---------------------------------------------------------------------------


class TestListMessages:
    """list_messages 方法。"""

    @pytest.mark.asyncio
    async def test_list_messages(self) -> None:
        """返回消息列表。"""
        actor = uuid4()
        workspace_id = uuid4()
        service = _make_service(actor_id=actor)

        session = AsyncMock()
        orm_msgs = [
            _make_orm_message(role="user", content={"text": "q1"}),
            _make_orm_message(role="assistant", content={"text": "a1"}),
        ]

        with (
            patch(
                "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.list_messages",
                AsyncMock(return_value=orm_msgs),
            ),
            _patch_scoped_session(service, session),
        ):
            result = await service.list_messages(workspace_id)

        assert len(result) == 2
        assert isinstance(result[0], ConversationMessage)
        assert result[0].role == "user"
        assert result[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_list_messages_empty(self) -> None:
        """无消息 → 空列表。"""
        actor = uuid4()
        workspace_id = uuid4()
        service = _make_service(actor_id=actor)

        session = AsyncMock()

        with (
            patch(
                "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.list_messages",
                AsyncMock(return_value=[]),
            ),
            _patch_scoped_session(service, session),
        ):
            result = await service.list_messages(workspace_id)

        assert result == []

    @pytest.mark.asyncio
    async def test_list_messages_with_run_id(self) -> None:
        """带 run_id 过滤。"""
        actor = uuid4()
        workspace_id = uuid4()
        run_id = uuid4()
        service = _make_service(actor_id=actor)

        session = AsyncMock()
        orm_msgs = [_make_orm_message(role="user", run_id=run_id)]

        with (
            patch(
                "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.list_messages",
                AsyncMock(return_value=orm_msgs),
            ),
            _patch_scoped_session(service, session),
        ):
            result = await service.list_messages(workspace_id, run_id=run_id)

        assert len(result) == 1
        assert result[0].run_id == run_id

    @pytest.mark.asyncio
    async def test_list_messages_custom_limit(self) -> None:
        """自定义 limit。"""
        actor = uuid4()
        workspace_id = uuid4()
        service = _make_service(actor_id=actor)

        session = AsyncMock()
        orm_msgs = [_make_orm_message() for _ in range(5)]

        mock_list = AsyncMock(return_value=orm_msgs)

        with (
            patch(
                "packages.research.execution.repository_trusted.ResearchRepositoryTrusted.list_messages",
                mock_list,
            ),
            _patch_scoped_session(service, session),
        ):
            result = await service.list_messages(workspace_id, limit=5)

        assert len(result) == 5
        # 验证 limit 参数传递
        call_args = mock_list.call_args
        assert call_args.kwargs.get("limit") == 5
