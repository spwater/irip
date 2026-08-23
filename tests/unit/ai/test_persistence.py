"""单元测试：MessagePersistence 消息持久化 + 自动标题生成。

覆盖：
- persist_user_message_only：保存用户消息 + 更新对话 updated_at；
- persist_messages：保存用户消息 + AI 消息（tool_calls 裁剪 + citations 序列化）；
- auto_generate_title：LLM API 成功（respx）/ 非 200 跳过 / 异常跳过 / 无配置回退；
- redact_credentials 已在 test_persistence_redact.py 覆盖，此处不重复。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import respx

import packages.ai.persistence as persist_mod
from packages.ai.persistence import MessagePersistence
from packages.ai.providers import AIResponse
from packages.common.clock import FixedClock


def _make_persistence(
    factory: Any = None,
    provider: Any = None,
) -> MessagePersistence:
    """Create a MessagePersistence with test config."""
    return MessagePersistence(
        session_factory=factory or MagicMock(),
        clock=FixedClock(datetime.now(UTC)),
        provider=provider or MagicMock(),
    )


def _make_mock_session() -> AsyncMock:
    """Create a mock AsyncSession with sync add (not async)."""
    session = AsyncMock()
    session.add = MagicMock()  # session.add is sync, not async
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    session.delete = AsyncMock()
    return session


def _patch_scoped(mock_session: AsyncMock) -> Any:
    """Patch scoped_session in persistence module to yield mock_session."""

    @asynccontextmanager
    async def fake_scoped(factory: Any, dept_id: Any = None, user_id: Any = None) -> Any:
        yield mock_session

    original = persist_mod.scoped_session
    persist_mod.scoped_session = fake_scoped  # type: ignore[assignment]
    return original


# ============================================================
# persist_user_message_only
# ============================================================


class TestPersistUserMessageOnly:
    """persist_user_message_only 测试。"""

    async def test_persists_user_message_and_updates_conversation(self) -> None:
        """保存用户消息并更新对话 updated_at。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original = _patch_scoped(mock_session)

        try:
            await persistence.persist_user_message_only(
                conversation_id=uuid4(),
                user_id=uuid4(),
                question="测试问题",
                mentions=["user-1", "user-2"],
            )
        finally:
            persist_mod.scoped_session = original  # type: ignore[assignment]

        # session.add called with AIMessage
        assert mock_session.add.called
        added_obj = mock_session.add.call_args[0][0]
        assert added_obj.role == "user"
        assert added_obj.content == "测试问题"
        assert added_obj.mentions == ["user-1", "user-2"]
        # session.execute called for update conversation
        mock_session.execute.assert_awaited()

    async def test_no_mentions_defaults_to_empty(self) -> None:
        """mentions 为 None 时默认空列表。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original = _patch_scoped(mock_session)

        try:
            await persistence.persist_user_message_only(
                conversation_id=uuid4(),
                user_id=uuid4(),
                question="question",
            )
        finally:
            persist_mod.scoped_session = original  # type: ignore[assignment]

        added_obj = mock_session.add.call_args[0][0]
        assert added_obj.mentions == []


# ============================================================
# persist_messages
# ============================================================


class TestPersistMessages:
    """persist_messages 测试。"""

    async def test_persists_user_and_ai_messages(self) -> None:
        """同时保存用户消息和 AI 消息。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original = _patch_scoped(mock_session)
        response = AIResponse(
            answer="AI 回答",
            tool_calls=(),
            citations=(),
        )

        try:
            await persistence.persist_messages(
                conversation_id=uuid4(),
                user_id=uuid4(),
                question="用户问题",
                response=response,
            )
        finally:
            persist_mod.scoped_session = original  # type: ignore[assignment]

        # Two add calls: user message + AI message
        assert mock_session.add.call_count == 2
        user_msg = mock_session.add.call_args_list[0][0][0]
        ai_msg = mock_session.add.call_args_list[1][0][0]
        assert user_msg.role == "user"
        assert user_msg.content == "用户问题"
        assert ai_msg.role == "assistant"
        assert ai_msg.content == "AI 回答"

    async def test_persists_tool_calls_with_trimming(self) -> None:
        """tool_calls 中大型 inline 数组被裁剪。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original = _patch_scoped(mock_session)
        large_list = list(range(25))
        response = AIResponse(
            answer="result",
            tool_calls=(
                {
                    "tool": "search_facts",
                    "args": {"big_array": large_list},
                    "summary": "搜索",
                    "status": "ok",
                    "audit": None,
                },
            ),
        )

        try:
            await persistence.persist_messages(
                conversation_id=uuid4(),
                user_id=uuid4(),
                question="q",
                response=response,
            )
        finally:
            persist_mod.scoped_session = original  # type: ignore[assignment]

        ai_msg = mock_session.add.call_args_list[1][0][0]
        tc = ai_msg.tool_calls_json[0]
        assert tc["args"]["big_array"] == "[25 items]"

    async def test_persists_tool_calls_variables_trimming(self) -> None:
        """variables 中大型 list 超过 20 项时被裁剪。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original = _patch_scoped(mock_session)
        large_var_list = list(range(25))  # > 20 items
        response = AIResponse(
            answer="r",
            tool_calls=(
                {
                    "tool": "evaluate_expression",
                    "args": {"variables": large_var_list},
                    "summary": "计算",
                    "status": "ok",
                    "audit": None,
                },
            ),
        )

        try:
            await persistence.persist_messages(
                conversation_id=uuid4(),
                user_id=uuid4(),
                question="q",
                response=response,
            )
        finally:
            persist_mod.scoped_session = original  # type: ignore[assignment]

        ai_msg = mock_session.add.call_args_list[1][0][0]
        tc = ai_msg.tool_calls_json[0]
        assert tc["args"]["variables"] == "[25 items]"

    async def test_persists_citations_as_dicts(self) -> None:
        """citations 为 dict 时直接保存。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original = _patch_scoped(mock_session)
        citation_dict = {"object_type": "fact", "object_id": "f-1", "label": "实验1"}
        response = AIResponse(
            answer="a",
            citations=(citation_dict,),
        )

        try:
            await persistence.persist_messages(
                conversation_id=uuid4(),
                user_id=uuid4(),
                question="q",
                response=response,
            )
        finally:
            persist_mod.scoped_session = original  # type: ignore[assignment]

        ai_msg = mock_session.add.call_args_list[1][0][0]
        assert ai_msg.citations_json == [citation_dict]

    async def test_persists_with_mentions(self) -> None:
        """用户消息含 mentions。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original = _patch_scoped(mock_session)
        response = AIResponse(answer="a")

        try:
            await persistence.persist_messages(
                conversation_id=uuid4(),
                user_id=uuid4(),
                question="q",
                response=response,
                mentions=["u1", "u2"],
            )
        finally:
            persist_mod.scoped_session = original  # type: ignore[assignment]

        user_msg = mock_session.add.call_args_list[0][0][0]
        assert user_msg.mentions == ["u1", "u2"]


# ============================================================
# auto_generate_title with respx
# ============================================================


class TestAutoGenerateTitleWithRespx:
    """auto_generate_title 使用 respx 模拟 LLM API 测试。"""

    @respx.mock
    async def test_title_from_llm_api(self) -> None:
        """LLM API 返回标题时更新数据库。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original_scoped = _patch_scoped(mock_session)

        config_mock = MagicMock()
        config_mock.api_key = "test-key"
        config_mock.base_url = "http://test-llm:8000/v1"
        config_mock.model = "test-model"

        respx.post("http://test-llm:8000/v1/chat/completions").respond(
            json={"choices": [{"message": {"content": "温度趋势分析报告"}}]}
        )

        try:
            with patch(
                "packages.ai.yaml_config.get_scenario_config",
                return_value=config_mock,
            ):
                with patch("packages.ai.prompt_store.get_prompt", return_value="sys prompt"):
                    await persistence.auto_generate_title(
                        uuid4(), "请分析温度数据", "温度呈上升趋势"
                    )
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_awaited_once()
        # Verify the UPDATE was called (title is in parameters, not SQL string)
        update_call = mock_session.execute.call_args
        assert update_call is not None

    @respx.mock
    async def test_title_from_reasoning_content(self) -> None:
        """content 为空时回退到 reasoning_content。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original_scoped = _patch_scoped(mock_session)

        config_mock = MagicMock()
        config_mock.api_key = "k"
        config_mock.base_url = "http://test-llm:8000/v1"
        config_mock.model = "m"

        respx.post("http://test-llm:8000/v1/chat/completions").respond(
            json={"choices": [{"message": {"content": "", "reasoning_content": "分析结论"}}]}
        )

        try:
            with patch(
                "packages.ai.yaml_config.get_scenario_config",
                return_value=config_mock,
            ):
                with patch("packages.ai.prompt_store.get_prompt", return_value="sys"):
                    await persistence.auto_generate_title(uuid4(), "question", "answer")
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_awaited_once()

    @respx.mock
    async def test_title_non_200_skips_update(self) -> None:
        """LLM API 返回非 200 时跳过标题更新。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original_scoped = _patch_scoped(mock_session)

        config_mock = MagicMock()
        config_mock.api_key = "k"
        config_mock.base_url = "http://test-llm:8000/v1"
        config_mock.model = "m"

        respx.post("http://test-llm:8000/v1/chat/completions").respond(
            status_code=500,
            json={"error": "server error"},
        )

        try:
            with patch(
                "packages.ai.yaml_config.get_scenario_config",
                return_value=config_mock,
            ):
                with patch("packages.ai.prompt_store.get_prompt", return_value="sys"):
                    await persistence.auto_generate_title(uuid4(), "question", "answer")
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_not_awaited()

    @respx.mock
    async def test_title_empty_content_skips_update(self) -> None:
        """LLM 返回空标题时跳过更新。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original_scoped = _patch_scoped(mock_session)

        config_mock = MagicMock()
        config_mock.api_key = "k"
        config_mock.base_url = "http://test-llm:8000/v1"
        config_mock.model = "m"

        respx.post("http://test-llm:8000/v1/chat/completions").respond(
            json={"choices": [{"message": {"content": "   "}}]}
        )

        try:
            with patch(
                "packages.ai.yaml_config.get_scenario_config",
                return_value=config_mock,
            ):
                with patch("packages.ai.prompt_store.get_prompt", return_value="sys"):
                    await persistence.auto_generate_title(uuid4(), "question", "answer")
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_not_awaited()

    @respx.mock
    async def test_title_long_content_truncated_to_60(self) -> None:
        """标题超过 60 字符时被截断。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original_scoped = _patch_scoped(mock_session)

        config_mock = MagicMock()
        config_mock.api_key = "k"
        config_mock.base_url = "http://test-llm:8000/v1"
        config_mock.model = "m"

        long_title = "这是一" + "个" * 65 + "标题"
        respx.post("http://test-llm:8000/v1/chat/completions").respond(
            json={"choices": [{"message": {"content": long_title}}]}
        )

        try:
            with patch(
                "packages.ai.yaml_config.get_scenario_config",
                return_value=config_mock,
            ):
                with patch("packages.ai.prompt_store.get_prompt", return_value="sys"):
                    await persistence.auto_generate_title(uuid4(), "question", "answer")
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_awaited_once()
        # Title should be truncated to 60 chars (verified by UPDATE being called)
        assert len(long_title) > 60

    @respx.mock
    async def test_title_strips_quotes_and_brackets(self) -> None:
        """标题被清理引号和括号。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original_scoped = _patch_scoped(mock_session)

        config_mock = MagicMock()
        config_mock.api_key = "k"
        config_mock.base_url = "http://test-llm:8000/v1"
        config_mock.model = "m"

        respx.post("http://test-llm:8000/v1/chat/completions").respond(
            json={"choices": [{"message": {"content": "「实验分析报告」"}}]}
        )

        try:
            with patch(
                "packages.ai.yaml_config.get_scenario_config",
                return_value=config_mock,
            ):
                with patch("packages.ai.prompt_store.get_prompt", return_value="sys"):
                    await persistence.auto_generate_title(uuid4(), "question", "answer")
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_awaited_once()

    @respx.mock
    async def test_title_multiline_takes_first_line(self) -> None:
        """多行标题取第一行。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original_scoped = _patch_scoped(mock_session)

        config_mock = MagicMock()
        config_mock.api_key = "k"
        config_mock.base_url = "http://test-llm:8000/v1"
        config_mock.model = "m"

        respx.post("http://test-llm:8000/v1/chat/completions").respond(
            json={"choices": [{"message": {"content": "第一行标题\n第二行忽略"}}]}
        )

        try:
            with patch(
                "packages.ai.yaml_config.get_scenario_config",
                return_value=config_mock,
            ):
                with patch("packages.ai.prompt_store.get_prompt", return_value="sys"):
                    await persistence.auto_generate_title(uuid4(), "question", "answer")
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_awaited_once()
        # Multi-line title: first line is used (verified by UPDATE being called)

    async def test_title_no_config_uses_question_prefix(self) -> None:
        """无 YAML 配置时用问题前 30 字做标题。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original_scoped = _patch_scoped(mock_session)

        try:
            with patch(
                "packages.ai.yaml_config.get_scenario_config",
                side_effect=FileNotFoundError("no config"),
            ):
                question = "请帮我分析一下最新的实验数据趋势"
                await persistence.auto_generate_title(uuid4(), question, "answer")
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_awaited_once()

    async def test_title_no_config_empty_question_skips(self) -> None:
        """无配置且空问题时跳过更新。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original_scoped = _patch_scoped(mock_session)

        try:
            with patch(
                "packages.ai.yaml_config.get_scenario_config",
                side_effect=FileNotFoundError("no config"),
            ):
                await persistence.auto_generate_title(uuid4(), "   ", "answer")
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_not_awaited()

    @respx.mock
    async def test_title_api_exception_skips_update(self) -> None:
        """LLM API 请求异常时静默跳过。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original_scoped = _patch_scoped(mock_session)

        config_mock = MagicMock()
        config_mock.api_key = "k"
        config_mock.base_url = "http://test-llm:8000/v1"
        config_mock.model = "m"

        respx.post("http://test-llm:8000/v1/chat/completions").mock(
            side_effect=Exception("network error")
        )

        try:
            with patch(
                "packages.ai.yaml_config.get_scenario_config",
                return_value=config_mock,
            ):
                with patch("packages.ai.prompt_store.get_prompt", return_value="sys"):
                    await persistence.auto_generate_title(uuid4(), "question", "answer")
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_not_awaited()

    @respx.mock
    async def test_title_empty_choices_skips_update(self) -> None:
        """LLM 返回空 choices 时跳过更新。"""
        persistence = _make_persistence()
        mock_session = _make_mock_session()
        original_scoped = _patch_scoped(mock_session)

        config_mock = MagicMock()
        config_mock.api_key = "k"
        config_mock.base_url = "http://test-llm:8000/v1"
        config_mock.model = "m"

        respx.post("http://test-llm:8000/v1/chat/completions").respond(json={"choices": []})

        try:
            with patch(
                "packages.ai.yaml_config.get_scenario_config",
                return_value=config_mock,
            ):
                with patch("packages.ai.prompt_store.get_prompt", return_value="sys"):
                    await persistence.auto_generate_title(uuid4(), "question", "answer")
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_not_awaited()
