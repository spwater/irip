"""单元测试：AskService 问答编排核心逻辑。

覆盖：
- cancel_request 委托到 CancellationRegistry；
- get_provider_status 返回 provider_mode + 已启用 ai_tool 工具列表；
- _prepare_ask 用户无部门时抛 forbidden；
- _prepare_ask mention_only 判定（协作对话且 mentions 不含 ai）；
- ask mention-only 路径仅持久化用户消息不调 AI；
- ask 正常路径调用 provider 并返回 AIResponse。
"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.ai.ask_service import AskService
from packages.ai.cancellation import CancellationRegistry
from packages.ai.providers import AIRequest, AIResponse
from packages.ai.tools import ToolRegistry
from packages.common.clock import FixedClock
from packages.common.errors import AppError

_SENTINEL: Any = object()


def _make_user(department_id: Any = _SENTINEL, roles: list[str] | None = None) -> MagicMock:
    """构造带 user_id / department_id / email / roles 的 user mock。"""
    user = MagicMock()
    user.user_id = uuid4()
    user.department_id = uuid4() if department_id is _SENTINEL else department_id
    user.email = "tester@irip.local"
    user.roles = roles or ["lab_member"]
    return user


def _make_ask_service(
    provider: Any = None,
    persistence: Any = None,
    conversation_svc: Any = None,
    cancellation: CancellationRegistry | None = None,
    tool_executor: Any = None,
) -> AskService:
    """构造 AskService 实例（全部依赖 mock）。"""
    if provider is None:
        provider = MagicMock()
        provider.thinking_enabled = False
        provider.provider_mode = "offline"
        provider.complete = AsyncMock(
            return_value=AIResponse(
                answer="ok", tool_calls=(), citations=(), provider_mode="offline"
            )
        )
    if persistence is None:
        persistence = MagicMock()
        persistence.redact_credentials = lambda x: x
        persistence.persist_messages = AsyncMock()
        persistence.persist_user_message_only = AsyncMock()
        persistence.auto_generate_title = AsyncMock()
    if conversation_svc is None:
        conversation_svc = MagicMock()
        conversation_svc.create_conversation = AsyncMock()
        conversation_svc.list_messages = AsyncMock(return_value=[])
    if cancellation is None:
        cancellation = CancellationRegistry()
    if tool_executor is None:
        tool_executor = MagicMock()
        tool_executor.build_tool_schemas = MagicMock(return_value=())
        tool_executor.check_role_permission = MagicMock(return_value=True)
        tool_executor.execute_tool = AsyncMock(return_value={"summary": "done", "data": {}})

    return AskService(
        provider=provider,
        tool_registry=ToolRegistry(),
        tool_executor=tool_executor,
        persistence=persistence,
        conversation_service=conversation_svc,
        cancellation_registry=cancellation,
        session_factory=None,
        clock=FixedClock(datetime.now(UTC)),
    )


class TestCancelRequest:
    """AskService.cancel_request 测试。"""

    def test_cancel_delegates_to_registry(self) -> None:
        """cancel_request 委托到 CancellationRegistry.cancel。"""
        cancellation = CancellationRegistry()
        service = _make_ask_service(cancellation=cancellation)
        conv_id = uuid4()
        cancellation.register(conv_id)

        assert service.cancel_request(conv_id) is True
        assert service.cancel_request(uuid4()) is False


class TestGetProviderStatus:
    """AskService.get_provider_status 测试。"""

    def test_returns_provider_mode_and_tools(self) -> None:
        """get_provider_status 返回 provider_mode 和已启用 ai_tool 工具列表。"""
        service = _make_ask_service()
        status = service.get_provider_status()
        assert status["provider_mode"] == "offline"
        assert "whitelist_tools" in status
        assert isinstance(status["whitelist_tools"], list)
        # 仅含 ai_tool 类别工具（不含 ingestion 类）
        tool_names = {t["name"] for t in status["whitelist_tools"]}
        assert "search_facts" in tool_names
        assert "xrd_converter" not in tool_names

    def test_whitelist_tools_have_required_fields(self) -> None:
        """whitelist_tools 中每项含 name / display_name / description / required_permission。"""
        service = _make_ask_service()
        status = service.get_provider_status()
        for t in status["whitelist_tools"]:
            assert "name" in t
            assert "display_name" in t
            assert "description" in t
            assert "required_permission" in t


class TestPrepareAskForbidden:
    """AskService._prepare_ask 无部门时 forbidden 测试。"""

    async def test_user_without_department_raises_forbidden(self) -> None:
        """用户无 department_id 时 _prepare_ask 抛 AppError(forbidden)。"""
        service = _make_ask_service()
        user = _make_user(department_id=None)
        with pytest.raises(AppError) as exc_info:
            await service._prepare_ask(
                user=user,
                question="test",
                conversation_id=None,
                provider_name="offline",
                thinking_enabled=False,
                system_context=None,
                mentions=None,
            )
        assert exc_info.value.code == "forbidden"


class TestAskMentionOnly:
    """AskService.ask mention-only 路径测试。"""

    async def test_mention_only_persists_user_message_only(self) -> None:
        """mention-only 消息仅持久化用户消息，不调用 AI provider。"""
        persistence = MagicMock()
        persistence.redact_credentials = lambda x: x
        persistence.persist_user_message_only = AsyncMock()
        persistence.persist_messages = AsyncMock()
        persistence.auto_generate_title = AsyncMock()

        provider = MagicMock()
        provider.thinking_enabled = False
        provider.provider_mode = "offline"
        provider.complete = AsyncMock()

        service = _make_ask_service(provider=provider, persistence=persistence)

        # 通过 patch _prepare_ask 返回 mention_only=True 的上下文
        from packages.ai.ask_service import _AskContext

        conv_id = uuid4()
        user_id = uuid4()
        dept_id = uuid4()
        ctx = _AskContext(
            user_id=user_id,
            org_id=dept_id,
            conversation_id=conv_id,
            question="hi @张三",
            history_messages=[],
            msg_list=[],
            user_context={},
            tool_names=(),
            tool_schemas=(),
            ai_request=AIRequest(messages=(), tools=()),
            cancel_event=asyncio.Event(),
            mentions=["user-zhang"],
            thinking_enabled=False,
            provider_name="offline",
            mention_only=True,
        )

        async def fake_prepare(*args: Any, **kwargs: Any) -> _AskContext:
            return ctx

        service._prepare_ask = fake_prepare  # type: ignore[assignment]

        user = _make_user()
        response = await service.ask(
            user=user, question="hi @张三", conversation_id=conv_id, mentions=["user-zhang"]
        )

        assert response.answer == ""
        persistence.persist_user_message_only.assert_awaited_once()
        provider.complete.assert_not_awaited()
        persistence.persist_messages.assert_not_awaited()


class TestAskNormalPath:
    """AskService.ask 正常路径测试。"""

    async def test_normal_path_calls_provider_and_persists(self) -> None:
        """正常路径调用 provider.complete 并持久化消息。"""
        persistence = MagicMock()
        persistence.redact_credentials = lambda x: x
        persistence.persist_messages = AsyncMock()
        persistence.persist_user_message_only = AsyncMock()
        persistence.auto_generate_title = AsyncMock()

        ai_response = AIResponse(
            answer="答案是 42", tool_calls=(), citations=(), provider_mode="offline"
        )
        provider = MagicMock()
        provider.thinking_enabled = False
        provider.provider_mode = "offline"
        provider.complete = AsyncMock(return_value=ai_response)

        service = _make_ask_service(provider=provider, persistence=persistence)

        from packages.ai.ask_service import _AskContext

        conv_id = uuid4()
        user_id = uuid4()
        dept_id = uuid4()
        ctx = _AskContext(
            user_id=user_id,
            org_id=dept_id,
            conversation_id=conv_id,
            question="什么是实验事实？",
            history_messages=[],
            msg_list=[{"role": "user", "content": "什么是实验事实？"}],
            user_context={"user_id": str(user_id)},
            tool_names=(),
            tool_schemas=(),
            ai_request=AIRequest(messages=(), tools=()),
            cancel_event=asyncio.Event(),
            mentions=[],
            thinking_enabled=False,
            provider_name="offline",
            mention_only=False,
        )

        async def fake_prepare(*args: Any, **kwargs: Any) -> _AskContext:
            return ctx

        service._prepare_ask = fake_prepare  # type: ignore[assignment]

        user = _make_user()
        response = await service.ask(user=user, question="什么是实验事实？")

        provider.complete.assert_awaited_once()
        persistence.persist_messages.assert_awaited_once()
        assert response.answer == "答案是 42"
