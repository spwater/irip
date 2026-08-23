"""Unit tests for packages.ai.ask_service — AskService orchestration.

Tests _prepare_ask, _execute_and_finalize, ask(), stream_ask(),
cancel_request(), reload_tools(), get_provider_status()
with mocked provider, tool_registry, persistence, conversation_service.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from packages.ai.ask_service import AskService
from packages.ai.providers import AIResponse


@pytest.fixture
def mock_provider() -> MagicMock:
    """Mock AI Provider."""
    provider = MagicMock()
    provider.thinking_enabled = False
    provider.provider_mode = "offline"
    provider.complete = AsyncMock(return_value=AIResponse(answer="test answer"))
    return provider


@pytest.fixture
def mock_tool_registry() -> MagicMock:
    """Mock ToolRegistry."""
    registry = MagicMock()
    registry.enabled_names.return_value = ("tool1",)
    registry.list_enabled_tools.return_value = []
    registry.validate.return_value = MagicMock(
        name="tool1",
        display_name="Tool 1",
        description="A tool",
        required_permission="assistant:use",
    )
    registry.reload_from_db = AsyncMock()
    return registry


@pytest.fixture
def mock_tool_executor() -> MagicMock:
    """Mock ToolExecutor."""
    executor = MagicMock()
    executor.build_tool_schemas.return_value = ()
    executor.check_role_permission.return_value = True
    executor.execute_tool = AsyncMock(return_value={"summary": "ok", "data": {}})
    return executor


@pytest.fixture
def mock_persistence() -> MagicMock:
    """Mock MessagePersistence."""
    persistence = MagicMock()
    persistence.persist_messages = AsyncMock()
    persistence.persist_user_message_only = AsyncMock()
    persistence.auto_generate_title = AsyncMock()
    persistence.redact_credentials = lambda s: s
    return persistence


@pytest.fixture
def mock_conversation_svc() -> MagicMock:
    """Mock ConversationService."""
    svc = MagicMock()
    conv_ref = MagicMock()
    conv_ref.id = UUID("00000000-0000-0000-0000-000000000001")
    svc.create_conversation = AsyncMock(return_value=conv_ref)
    svc.list_messages = AsyncMock(return_value=[])
    return svc


@pytest.fixture
def mock_cancellation() -> MagicMock:
    """Mock CancellationRegistry."""
    reg = MagicMock()
    reg.register.return_value = asyncio.Event()
    reg.unregister = MagicMock()
    reg.cancel.return_value = True
    return reg


@pytest.fixture
def mock_factory() -> MagicMock:
    """Mock session factory (None to skip DB-dependent code paths)."""
    return None


@pytest.fixture
def mock_clock() -> MagicMock:
    """Mock clock."""
    from packages.common.clock import SystemClock

    return SystemClock()


@pytest.fixture
def ask_service(
    mock_provider: MagicMock,
    mock_tool_registry: MagicMock,
    mock_tool_executor: MagicMock,
    mock_persistence: MagicMock,
    mock_conversation_svc: MagicMock,
    mock_cancellation: MagicMock,
    mock_factory: MagicMock,
    mock_clock: MagicMock,
) -> AskService:
    """AskService with all mocked dependencies."""
    return AskService(
        provider=mock_provider,
        tool_registry=mock_tool_registry,
        tool_executor=mock_tool_executor,
        persistence=mock_persistence,
        conversation_service=mock_conversation_svc,
        cancellation_registry=mock_cancellation,
        session_factory=mock_factory,
        clock=mock_clock,
    )


@pytest.fixture
def mock_user() -> MagicMock:
    """Mock user object."""
    user = MagicMock()
    user.user_id = UUID("00000000-0000-0000-0000-000000000010")
    user.department_id = UUID("00000000-0000-0000-0000-000000000020")
    user.email = "user@irip.local"
    user.roles = ["lab_member"]
    return user


class TestAsk:
    """Tests for AskService.ask()."""

    async def test_basic_ask_no_tools(
        self,
        ask_service: AskService,
        mock_provider: MagicMock,
        mock_persistence: MagicMock,
        mock_user: MagicMock,
    ) -> None:
        mock_provider.complete.return_value = AIResponse(
            answer="Hello!",
            tool_calls=(),
            citations=(),
            uncertainty=None,
            provider_mode="offline",
        )
        result = await ask_service.ask(mock_user, "Hi")

        assert result.answer == "Hello!"
        mock_persistence.persist_messages.assert_called_once()

    async def test_ask_with_tool_execution(
        self,
        ask_service: AskService,
        mock_provider: MagicMock,
        mock_tool_executor: MagicMock,
        mock_persistence: MagicMock,
        mock_user: MagicMock,
    ) -> None:
        """First-round response with tool_calls triggers tool execution + second round."""
        first_response = AIResponse(
            answer="Let me check.",
            tool_calls=({"tool": "tool1", "args": {"q": "test"}, "id": "call_1"},),
            citations=(),
            uncertainty=None,
            provider_mode="offline",
        )
        second_response = AIResponse(
            answer="Based on the data, the result is 42.",
            tool_calls=(),
            citations=(),
            uncertainty=None,
            provider_mode="offline",
        )
        mock_provider.complete.side_effect = [first_response, second_response]

        result = await ask_service.ask(mock_user, "What is the value?")

        assert result.answer == "Based on the data, the result is 42."
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["status"] == "executed"
        mock_tool_executor.execute_tool.assert_called_once()
        assert mock_provider.complete.call_count == 2

    async def test_ask_tool_unknown_rejected(
        self,
        ask_service: AskService,
        mock_provider: MagicMock,
        mock_tool_registry: MagicMock,
        mock_persistence: MagicMock,
        mock_user: MagicMock,
    ) -> None:
        from packages.common.errors import AppError

        mock_tool_registry.validate.side_effect = AppError(
            code="not_found", message="unknown tool", retryable=False
        )
        first_response = AIResponse(
            answer="I'll use an unknown tool.",
            tool_calls=({"tool": "unknown_tool", "args": {}, "id": "c1"},),
            citations=(),
            uncertainty=None,
            provider_mode="offline",
        )
        second_response = AIResponse(
            answer="Sorry, that tool is not available.",
            tool_calls=(),
            citations=(),
            uncertainty=None,
            provider_mode="offline",
        )
        mock_provider.complete.side_effect = [first_response, second_response]

        result = await ask_service.ask(mock_user, "test")
        assert result.tool_calls[0]["status"] == "rejected"

    async def test_ask_tool_forbidden(
        self,
        ask_service: AskService,
        mock_provider: MagicMock,
        mock_tool_executor: MagicMock,
        mock_persistence: MagicMock,
        mock_user: MagicMock,
    ) -> None:
        mock_tool_executor.check_role_permission.return_value = False
        first_response = AIResponse(
            answer="I'll use a tool.",
            tool_calls=({"tool": "tool1", "args": {}, "id": "c1"},),
            citations=(),
            uncertainty=None,
            provider_mode="offline",
        )
        second_response = AIResponse(
            answer="Sorry, no permission.",
            tool_calls=(),
            citations=(),
            uncertainty=None,
            provider_mode="offline",
        )
        mock_provider.complete.side_effect = [first_response, second_response]

        result = await ask_service.ask(mock_user, "test")
        assert result.tool_calls[0]["status"] == "forbidden"

    async def test_ask_tool_execution_error(
        self,
        ask_service: AskService,
        mock_provider: MagicMock,
        mock_tool_executor: MagicMock,
        mock_persistence: MagicMock,
        mock_user: MagicMock,
    ) -> None:
        mock_tool_executor.execute_tool.side_effect = RuntimeError("tool crashed")
        first_response = AIResponse(
            answer="Using tool.",
            tool_calls=({"tool": "tool1", "args": {}, "id": "c1"},),
            citations=(),
            uncertainty=None,
            provider_mode="offline",
        )
        second_response = AIResponse(
            answer="Tool failed.",
            tool_calls=(),
            citations=(),
            uncertainty=None,
            provider_mode="offline",
        )
        mock_provider.complete.side_effect = [first_response, second_response]

        result = await ask_service.ask(mock_user, "test")
        assert result.tool_calls[0]["status"] == "error"

    async def test_ask_no_department_raises(
        self,
        ask_service: AskService,
        mock_user: MagicMock,
    ) -> None:
        mock_user.department_id = None
        from packages.common.errors import AppError

        with pytest.raises(AppError, match="无法确定用户所属部门"):
            await ask_service.ask(mock_user, "test")

    async def test_ask_cancelled(
        self,
        ask_service: AskService,
        mock_provider: MagicMock,
        mock_persistence: MagicMock,
        mock_user: MagicMock,
    ) -> None:
        from packages.common.errors import AppError

        cancelled = AppError(code="ai_cancelled", message="cancelled", retryable=False)
        mock_provider.complete.side_effect = cancelled

        with pytest.raises(AppError, match="cancelled"):
            await ask_service.ask(mock_user, "test")
        # Should persist a cancelled message
        mock_persistence.persist_messages.assert_called_once()


class TestStreamAsk:
    """Tests for AskService.stream_ask()."""

    async def test_stream_basic(
        self,
        ask_service: AskService,
        mock_provider: MagicMock,
        mock_persistence: MagicMock,
        mock_user: MagicMock,
    ) -> None:
        mock_provider.complete.return_value = AIResponse(
            answer="Streamed answer.",
            tool_calls=(),
            citations=(),
            uncertainty=None,
            provider_mode="offline",
        )
        # stream_complete is not available (OfflineProvider path)
        mock_provider.stream_complete = None

        events = []
        async for event in ask_service.stream_ask(mock_user, "Hi"):
            events.append(event)

        assert any(e["type"] == "chunk" for e in events)
        assert events[-1]["type"] == "done"
        assert "Streamed answer." in events[-1]["answer"]

    async def test_stream_with_streaming_provider(
        self,
        ask_service: AskService,
        mock_provider: MagicMock,
        mock_user: MagicMock,
    ) -> None:
        async def fake_stream(_req, cancel_event=None):
            yield {"type": "chunk", "content": "Hello "}
            yield {"type": "chunk", "content": "world"}
            yield {"type": "done", "tool_calls": []}

        mock_provider.stream_complete = fake_stream

        events = []
        async for event in ask_service.stream_ask(mock_user, "Hi"):
            events.append(event)

        chunks = [e for e in events if e["type"] == "chunk"]
        assert chunks[0]["content"] == "Hello "
        assert chunks[1]["content"] == "world"
        assert events[-1]["type"] == "done"

    async def test_stream_error_event(
        self,
        ask_service: AskService,
        mock_provider: MagicMock,
        mock_persistence: MagicMock,
        mock_user: MagicMock,
    ) -> None:
        async def fake_stream(_req, cancel_event=None):
            yield {"type": "error", "message": "stream error"}
            return

        mock_provider.stream_complete = fake_stream

        events = []
        async for event in ask_service.stream_ask(mock_user, "Hi"):
            events.append(event)

        assert events[0]["type"] == "error"
        mock_persistence.persist_messages.assert_called_once()

    async def test_stream_apperror_cancelled(
        self,
        ask_service: AskService,
        mock_provider: MagicMock,
        mock_persistence: MagicMock,
        mock_user: MagicMock,
    ) -> None:
        from packages.common.errors import AppError

        async def fake_stream(_req, cancel_event=None):
            raise AppError(code="ai_cancelled", message="cancelled", retryable=False)
            yield  # make it an async generator

        mock_provider.stream_complete = fake_stream

        events = []
        async for event in ask_service.stream_ask(mock_user, "Hi"):
            events.append(event)

        assert events[-1]["type"] == "error"
        mock_persistence.persist_messages.assert_called_once()


class TestCancelRequest:
    """Tests for cancel_request()."""

    def test_cancel_delegates_to_registry(
        self, ask_service: AskService, mock_cancellation: MagicMock
    ) -> None:
        conv_id = UUID("00000000-0000-0000-0000-000000000100")
        result = ask_service.cancel_request(conv_id)
        mock_cancellation.cancel.assert_called_once_with(conv_id)
        assert result is True


class TestReloadTools:
    """Tests for reload_tools()."""

    async def test_no_factory_noop(self, ask_service: AskService) -> None:
        # factory is None, should be a no-op
        await ask_service.reload_tools()

    async def test_with_factory_calls_reload(
        self,
        mock_provider: MagicMock,
        mock_tool_registry: MagicMock,
        mock_tool_executor: MagicMock,
        mock_persistence: MagicMock,
        mock_conversation_svc: MagicMock,
        mock_cancellation: MagicMock,
        mock_clock: MagicMock,
    ) -> None:
        mock_session = AsyncMock()
        mock_factory = MagicMock()

        from packages.common.clock import SystemClock

        svc = AskService(
            provider=mock_provider,
            tool_registry=mock_tool_registry,
            tool_executor=mock_tool_executor,
            persistence=mock_persistence,
            conversation_service=mock_conversation_svc,
            cancellation_registry=mock_cancellation,
            session_factory=mock_factory,
            clock=SystemClock(),
        )

        with patch("packages.ai.ask_service.scoped_session", _fake_scoped_session(mock_session)):
            await svc.reload_tools()
        mock_tool_registry.reload_from_db.assert_called_once_with(mock_session)


class TestGetProviderStatus:
    """Tests for get_provider_status()."""

    def test_returns_status(
        self,
        ask_service: AskService,
        mock_provider: MagicMock,
        mock_tool_registry: MagicMock,
    ) -> None:
        tool_spec = MagicMock()
        tool_spec.name = "tool1"
        tool_spec.display_name = "Tool 1"
        tool_spec.description = "A tool"
        tool_spec.required_permission = "assistant:use"
        tool_spec.category = "ai_tool"
        mock_tool_registry.list_enabled_tools.return_value = [tool_spec]

        result = ask_service.get_provider_status()
        assert result["provider_mode"] == "offline"
        assert len(result["whitelist_tools"]) == 1
        assert result["whitelist_tools"][0]["name"] == "tool1"
        assert result["candidate_tools"] == []

    def test_filters_non_ai_tool_category(
        self,
        ask_service: AskService,
        mock_tool_registry: MagicMock,
    ) -> None:
        ai_tool = MagicMock()
        ai_tool.category = "ai_tool"
        ai_tool.name = "t1"
        ai_tool.display_name = "T1"
        ai_tool.description = "D1"
        ai_tool.required_permission = "p1"

        ingestion_tool = MagicMock()
        ingestion_tool.category = "ingestion"

        mock_tool_registry.list_enabled_tools.return_value = [ai_tool, ingestion_tool]

        result = ask_service.get_provider_status()
        assert len(result["whitelist_tools"]) == 1


class TestAskContextAndFinalize:
    """Tests for internal _prepare_ask and _execute_and_finalize."""

    async def test_prepare_ask_creates_conversation(
        self,
        ask_service: AskService,
        mock_conversation_svc: MagicMock,
        mock_user: MagicMock,
    ) -> None:
        ctx = await ask_service._prepare_ask(
            user=mock_user,
            question="test question",
            conversation_id=None,  # new conversation
            provider_name="offline",
            thinking_enabled=False,
            system_context=None,
            mentions=None,
        )
        assert ctx.conversation_id == UUID("00000000-0000-0000-0000-000000000001")
        assert ctx.question == "test question"
        assert ctx.mention_only is False  # single participant -> private -> not mention_only
        mock_conversation_svc.create_conversation.assert_called_once()

    async def test_prepare_ask_existing_conversation(
        self,
        ask_service: AskService,
        mock_conversation_svc: MagicMock,
        mock_user: MagicMock,
    ) -> None:

        msg = MagicMock()
        msg.role = "user"
        msg.content = "previous"
        mock_conversation_svc.list_messages.return_value = [msg]

        ctx = await ask_service._prepare_ask(
            user=mock_user,
            question="follow up",
            conversation_id=UUID("00000000-0000-0000-0000-000000000099"),
            provider_name="offline",
            thinking_enabled=False,
            system_context=None,
            mentions=None,
        )
        assert len(ctx.history_messages) == 1
        assert ctx.history_messages[0]["role"] == "user"

    async def test_prepare_ask_no_department_raises(
        self,
        ask_service: AskService,
        mock_user: MagicMock,
    ) -> None:
        mock_user.department_id = None
        from packages.common.errors import AppError

        with pytest.raises(AppError, match="无法确定用户所属部门"):
            await ask_service._prepare_ask(
                user=mock_user,
                question="test",
                conversation_id=None,
                provider_name="offline",
                thinking_enabled=False,
                system_context=None,
                mentions=None,
            )


def _fake_scoped_session(mock_session):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake(factory, dept_id=None, user_id=None):
        yield mock_session

    return _fake
