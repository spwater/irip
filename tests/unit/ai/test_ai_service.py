"""单元测试：AIService 编排服务委托逻辑。

覆盖：
- 构造函数正确初始化子服务（ConversationService / CollaborationService /
  ShowcaseService / ToolExecutor / MessagePersistence / AskService）；
- 对话管理委托方法（create_conversation / list_conversations / toggle_pin /
  get_conversation / toggle_archive / delete_conversation / list_messages /
  search_conversations）；
- 协作管理委托方法（add_participant / remove_participant / leave_conversation /
  list_participants / list_mentionable_users）；
- 橱窗管理委托方法（add_showcase_item / list_showcase_items /
  update_showcase_item / delete_showcase_item / reorder_showcase_items /
  generate_summary）；
- 工具执行委托方法（_check_role_permission / _build_tool_schemas /
  _execute_tool）；
- 持久化委托方法（_redact_credentials / _persist_user_message_only /
  _persist_messages / _auto_generate_title）；
- Provider 状态委托方法（cancel_request / reload_tools / get_provider_status）；
- ask / stream_ask 委托；
- resolve_dept_id 使用已知 dept_id。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from packages.ai.entities import ConversationRef, MessageRef
from packages.ai.providers import AIResponse
from packages.ai.service import AIService
from packages.ai.tools import ToolRegistry
from packages.common.clock import FixedClock


def _make_ai_service(
    provider: Any = None,
    tool_registry: Any = None,
    factory: Any = None,
) -> tuple[AIService, dict[str, Any]]:
    """Create an AIService with mocked sub-services.

    Returns the AIService and a dict of its sub-service mocks for assertion.
    """
    provider = provider or MagicMock()
    tool_registry = tool_registry or ToolRegistry()
    clock = FixedClock(datetime.now(UTC))

    svc = AIService(
        provider=provider,
        tool_registry=tool_registry,
        session_factory=factory,
        clock=clock,
    )

    # Replace sub-services with mocks for delegation testing
    mocks: dict[str, Any] = {
        "conversation_svc": AsyncMock(),
        "collaboration_svc": AsyncMock(),
        "showcase_svc": AsyncMock(),
        "tool_executor": AsyncMock(),
        "persistence": AsyncMock(),
        "ask_svc": AsyncMock(),
    }
    svc._conversation_svc = mocks["conversation_svc"]
    svc._collaboration_svc = mocks["collaboration_svc"]
    svc._showcase_svc = mocks["showcase_svc"]
    svc._tool_executor = mocks["tool_executor"]
    svc._persistence = mocks["persistence"]
    svc._ask_svc = mocks["ask_svc"]
    return svc, mocks


# ============================================================
# Constructor
# ============================================================


class TestAServiceConstructor:
    """AIService 构造函数测试。"""

    def test_constructor_initializes_sub_services(self) -> None:
        """构造函数正确初始化所有子服务。"""
        provider = MagicMock()
        tool_registry = ToolRegistry()
        clock = FixedClock(datetime.now(UTC))

        svc = AIService(
            provider=provider,
            tool_registry=tool_registry,
            clock=clock,
        )

        assert svc._provider is provider
        assert svc._tool_registry is tool_registry
        assert svc._clock is clock
        assert svc._cancellation is not None
        assert svc._conversation_svc is not None
        assert svc._collaboration_svc is not None
        assert svc._showcase_svc is not None
        assert svc._tool_executor is not None
        assert svc._persistence is not None
        assert svc._ask_svc is not None

    def test_constructor_defaults_clock(self) -> None:
        """未传入 clock 时使用 SystemClock。"""
        svc = AIService(
            provider=MagicMock(),
            tool_registry=ToolRegistry(),
        )
        assert svc._clock is not None


# ============================================================
# Conversation management delegation
# ============================================================


class TestConversationDelegation:
    """对话管理委托测试。"""

    async def test_create_conversation_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        uid, did = uuid4(), uuid4()
        expected = MagicMock(spec=ConversationRef)
        mocks["conversation_svc"].create_conversation = AsyncMock(return_value=expected)

        result = await svc.create_conversation(uid, did, "title", "offline")

        mocks["conversation_svc"].create_conversation.assert_awaited_once_with(
            uid, did, "title", "offline"
        )
        assert result is expected

    async def test_list_conversations_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        uid, did = uuid4(), uuid4()
        expected = [MagicMock(spec=ConversationRef)]
        mocks["conversation_svc"].list_conversations = AsyncMock(return_value=expected)

        result = await svc.list_conversations(uid, did, limit=10, include_archived=True)

        mocks["conversation_svc"].list_conversations.assert_awaited_once_with(
            uid, did, 10, True, False
        )
        assert result is expected

    async def test_toggle_pin_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        cid, uid = uuid4(), uuid4()
        mocks["conversation_svc"].toggle_pin = AsyncMock(return_value=True)

        result = await svc.toggle_pin(cid, uid, pinned=True)

        mocks["conversation_svc"].toggle_pin.assert_awaited_once_with(cid, uid, True)
        assert result is True

    async def test_get_conversation_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        cid, uid = uuid4(), uuid4()
        expected = MagicMock(spec=ConversationRef)
        mocks["conversation_svc"].get_conversation = AsyncMock(return_value=expected)

        result = await svc.get_conversation(cid, uid)

        mocks["conversation_svc"].get_conversation.assert_awaited_once_with(cid, uid)
        assert result is expected

    async def test_toggle_archive_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        cid, uid = uuid4(), uuid4()
        mocks["conversation_svc"].toggle_archive = AsyncMock(return_value=False)

        result = await svc.toggle_archive(cid, uid, archived=False)

        mocks["conversation_svc"].toggle_archive.assert_awaited_once_with(cid, uid, False)
        assert result is False

    async def test_delete_conversation_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        cid, uid = uuid4(), uuid4()
        mocks["conversation_svc"].delete_conversation = AsyncMock()

        await svc.delete_conversation(cid, uid)

        mocks["conversation_svc"].delete_conversation.assert_awaited_once_with(cid, uid)

    async def test_list_messages_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        cid, uid = uuid4(), uuid4()
        expected = [MagicMock(spec=MessageRef)]
        mocks["conversation_svc"].list_messages = AsyncMock(return_value=expected)

        result = await svc.list_messages(cid, uid)

        mocks["conversation_svc"].list_messages.assert_awaited_once_with(cid, uid)
        assert result is expected

    async def test_search_conversations_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        uid, did = uuid4(), uuid4()
        expected = [MagicMock(spec=ConversationRef)]
        mocks["conversation_svc"].search_conversations = AsyncMock(return_value=expected)

        result = await svc.search_conversations(
            uid, did, "keyword", include_archived=True, archived_only=False, limit=5
        )

        mocks["conversation_svc"].search_conversations.assert_awaited_once_with(
            uid, did, "keyword", True, False, 5
        )
        assert result is expected


# ============================================================
# Collaboration delegation
# ============================================================


class TestCollaborationDelegation:
    """协作管理委托测试。"""

    async def test_list_conversations_with_tab_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        uid, did = uuid4(), uuid4()
        expected = [MagicMock(spec=ConversationRef)]
        mocks["collaboration_svc"].list_conversations_with_tab = AsyncMock(return_value=expected)

        result = await svc.list_conversations_with_tab(
            uid, did, tab="private", limit=10, keyword="x"
        )

        mocks["collaboration_svc"].list_conversations_with_tab.assert_awaited_once_with(
            uid, did, "private", 10, False, False, "x"
        )
        assert result is expected

    async def test_add_participant_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        cid, inviter, target = uuid4(), uuid4(), uuid4()
        expected = MagicMock()
        mocks["collaboration_svc"].add_participant = AsyncMock(return_value=expected)

        result = await svc.add_participant(cid, inviter, target)

        mocks["collaboration_svc"].add_participant.assert_awaited_once_with(cid, inviter, target)
        assert result is expected

    async def test_remove_participant_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        cid, owner, target = uuid4(), uuid4(), uuid4()
        mocks["collaboration_svc"].remove_participant = AsyncMock()

        await svc.remove_participant(cid, owner, target)

        mocks["collaboration_svc"].remove_participant.assert_awaited_once_with(cid, owner, target)

    async def test_leave_conversation_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        cid, uid = uuid4(), uuid4()
        mocks["collaboration_svc"].leave_conversation = AsyncMock()

        await svc.leave_conversation(cid, uid)

        mocks["collaboration_svc"].leave_conversation.assert_awaited_once_with(cid, uid)

    async def test_list_participants_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        cid, uid = uuid4(), uuid4()
        expected = [MagicMock()]
        mocks["collaboration_svc"].list_participants = AsyncMock(return_value=expected)

        result = await svc.list_participants(cid, uid)

        mocks["collaboration_svc"].list_participants.assert_awaited_once_with(cid, uid)
        assert result is expected

    async def test_list_mentionable_users_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        uid, did = uuid4(), uuid4()
        expected = [MagicMock()]
        mocks["collaboration_svc"].list_mentionable_users = AsyncMock(return_value=expected)

        result = await svc.list_mentionable_users(uid, did, roles=["lab_member"])

        mocks["collaboration_svc"].list_mentionable_users.assert_awaited_once_with(
            uid, did, ["lab_member"]
        )
        assert result is expected


# ============================================================
# Showcase delegation
# ============================================================


class TestShowcaseDelegation:
    """橱窗管理委托测试。"""

    async def test_add_showcase_item_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        uid, cid, mid = uuid4(), uuid4(), uuid4()
        expected = MagicMock()
        mocks["showcase_svc"].add_showcase_item = AsyncMock(return_value=expected)

        result = await svc.add_showcase_item(
            uid, cid, "echarts", "title", "snapshot", mid, 0, {"k": "v"}
        )

        mocks["showcase_svc"].add_showcase_item.assert_awaited_once_with(
            uid, cid, "echarts", "title", "snapshot", mid, 0, {"k": "v"}
        )
        assert result is expected

    async def test_list_showcase_items_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        cid, uid = uuid4(), uuid4()
        expected = [MagicMock()]
        mocks["showcase_svc"].list_showcase_items = AsyncMock(return_value=expected)

        result = await svc.list_showcase_items(cid, uid)

        mocks["showcase_svc"].list_showcase_items.assert_awaited_once_with(cid, uid)
        assert result is expected

    async def test_update_showcase_item_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        iid, uid = uuid4(), uuid4()
        expected = MagicMock()
        mocks["showcase_svc"].update_showcase_item = AsyncMock(return_value=expected)

        result = await svc.update_showcase_item(iid, uid, title="new")

        mocks["showcase_svc"].update_showcase_item.assert_awaited_once_with(iid, uid, "new")
        assert result is expected

    async def test_delete_showcase_item_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        iid, uid = uuid4(), uuid4()
        mocks["showcase_svc"].delete_showcase_item = AsyncMock()

        await svc.delete_showcase_item(iid, uid)

        mocks["showcase_svc"].delete_showcase_item.assert_awaited_once_with(iid, uid)

    async def test_reorder_showcase_items_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        cid, uid = uuid4(), uuid4()
        items = [uuid4(), uuid4()]
        mocks["showcase_svc"].reorder_showcase_items = AsyncMock()

        await svc.reorder_showcase_items(cid, uid, items)

        mocks["showcase_svc"].reorder_showcase_items.assert_awaited_once_with(cid, uid, items)

    async def test_generate_summary_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        cid, uid = uuid4(), uuid4()
        expected = ("# Summary", 3)
        mocks["showcase_svc"].generate_summary = AsyncMock(return_value=expected)

        result = await svc.generate_summary(cid, uid)

        mocks["showcase_svc"].generate_summary.assert_awaited_once_with(cid, uid)
        assert result is expected


# ============================================================
# Tool executor delegation
# ============================================================


class TestToolExecutorDelegation:
    """工具执行委托测试。"""

    def test_check_role_permission_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        user = MagicMock()
        mocks["tool_executor"].check_role_permission = MagicMock(return_value=True)

        result = svc._check_role_permission(user, "fact:read")

        mocks["tool_executor"].check_role_permission.assert_called_once_with(user, "fact:read")
        assert result is True

    def test_build_tool_schemas_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        expected = ({"type": "function", "function": {"name": "x"}},)
        mocks["tool_executor"].build_tool_schemas = MagicMock(return_value=expected)

        result = svc._build_tool_schemas()

        mocks["tool_executor"].build_tool_schemas.assert_called_once()
        assert result is expected

    async def test_execute_tool_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        user = MagicMock()
        org_id = uuid4()
        expected = {"summary": "ok", "data": {}}
        mocks["tool_executor"].execute_tool = AsyncMock(return_value=expected)

        result = await svc._execute_tool("search_facts", {"query": "x"}, user, org_id)

        mocks["tool_executor"].execute_tool.assert_awaited_once_with(
            "search_facts", {"query": "x"}, user, org_id
        )
        assert result is expected


# ============================================================
# Persistence delegation
# ============================================================


class TestPersistenceDelegation:
    """持久化委托测试。"""

    def test_redact_credentials_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        mocks["persistence"].redact_credentials = MagicMock(return_value="clean")

        result = svc._redact_credentials("Bearer xxx")

        mocks["persistence"].redact_credentials.assert_called_once_with("Bearer xxx")
        assert result == "clean"

    async def test_persist_user_message_only_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        mocks["persistence"].persist_user_message_only = AsyncMock()

        await svc._persist_user_message_only(uuid4(), uuid4(), "question")

        mocks["persistence"].persist_user_message_only.assert_awaited_once()

    async def test_persist_messages_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        mocks["persistence"].persist_messages = AsyncMock()
        response = AIResponse(answer="test")

        await svc._persist_messages(uuid4(), uuid4(), "q", response)

        mocks["persistence"].persist_messages.assert_awaited_once()

    async def test_auto_generate_title_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        mocks["persistence"].auto_generate_title = AsyncMock()

        await svc._auto_generate_title(uuid4(), "question", "answer")

        mocks["persistence"].auto_generate_title.assert_awaited_once()


# ============================================================
# Provider status delegation
# ============================================================


class TestProviderStatusDelegation:
    """Provider 状态委托测试。"""

    def test_cancel_request_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        mocks["ask_svc"].cancel_request = MagicMock(return_value=True)

        result = svc.cancel_request(uuid4())

        mocks["ask_svc"].cancel_request.assert_called_once()
        assert result is True

    async def test_reload_tools_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        mocks["ask_svc"].reload_tools = AsyncMock()

        await svc.reload_tools()

        mocks["ask_svc"].reload_tools.assert_awaited_once()

    def test_get_provider_status_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        expected = {"provider_mode": "offline"}
        mocks["ask_svc"].get_provider_status = MagicMock(return_value=expected)

        result = svc.get_provider_status()

        mocks["ask_svc"].get_provider_status.assert_called_once()
        assert result is expected


# ============================================================
# Ask / stream_ask delegation
# ============================================================


class TestAskDelegation:
    """问答委托测试。"""

    async def test_ask_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        user = MagicMock()
        expected = AIResponse(answer="reply")
        mocks["ask_svc"].ask = AsyncMock(return_value=expected)

        result = await svc.ask(user, "question")

        mocks["ask_svc"].ask.assert_awaited_once()
        assert result is expected

    def test_stream_ask_delegates(self) -> None:
        svc, mocks = _make_ai_service()
        user = MagicMock()
        expected = MagicMock()
        mocks["ask_svc"].stream_ask = MagicMock(return_value=expected)

        result = svc.stream_ask(user, "question")

        mocks["ask_svc"].stream_ask.assert_called_once()
        assert result is expected


# ============================================================
# resolve_dept_id
# ============================================================


class TestResolveDeptId:
    """resolve_dept_id 测试。"""

    async def test_resolve_dept_id_with_known_dept(self) -> None:
        """传入 known_dept_id 时直接返回，不查数据库。"""
        svc, _ = _make_ai_service()
        uid = uuid4()
        did = uuid4()

        result = await svc.resolve_dept_id(uid, known_dept_id=did)

        assert result == did
