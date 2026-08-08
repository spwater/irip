"""单元测试：MessagePersistence 凭据脱敏 + 离线标题生成。

覆盖：
- redact_credentials 移除 Bearer token 模式；
- redact_credentials 移除 sk- 开头 API key；
- redact_credentials 保留正常文本；
- redact_credentials 处理空字符串；
- redact_credentials 处理混合内容（凭据 + 正常文本共存）；
- redact_credentials bearer 小写同样脱敏；
- auto_generate_title 离线模式回退到问题前 30 字。
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import packages.ai.persistence as persist_mod
from packages.ai.persistence import MessagePersistence
from packages.common.clock import FixedClock


class TestRedactCredentials:
    """MessagePersistence.redact_credentials 凭据脱敏测试。"""

    @pytest.fixture
    def persistence(self) -> MessagePersistence:
        """MessagePersistence 实例（provider 为 mock，不实际调用 API）。"""
        provider = MagicMock()
        return MessagePersistence(
            session_factory=None,
            clock=FixedClock(datetime.now(UTC)),
            provider=provider,
        )

    def test_redact_bearer_token(self, persistence: MessagePersistence) -> None:
        """Bearer token 被替换为 [REDACTED]。"""
        text = "The token is Bearer abc123def456ghi789jkl012mno345pqr"
        result = persistence.redact_credentials(text)
        assert "[REDACTED]" in result
        assert "abc123def456ghi789jkl012mno345pqr" not in result

    def test_redact_sk_api_key(self, persistence: MessagePersistence) -> None:
        """sk- 开头的 API key 被替换为 [REDACTED]。"""
        text = "API key: sk-abcdefghijklmnopqrstuvwxyz1234567890"
        result = persistence.redact_credentials(text)
        assert "[REDACTED]" in result
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in result

    def test_redact_preserves_normal_text(self, persistence: MessagePersistence) -> None:
        """正常文本不被修改。"""
        text = "实验结果显示温度为 25°C，符合预期。"
        assert persistence.redact_credentials(text) == text

    def test_redact_empty_string(self, persistence: MessagePersistence) -> None:
        """空字符串返回空字符串。"""
        assert persistence.redact_credentials("") == ""

    def test_redact_mixed_content(self, persistence: MessagePersistence) -> None:
        """凭据与正常文本共存时仅脱敏凭据部分。"""
        text = "配置信息：Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.xxx 实验编号 EXP-001"
        result = persistence.redact_credentials(text)
        assert "[REDACTED]" in result
        assert "EXP-001" in result

    def test_redact_bearer_case_insensitive(self, persistence: MessagePersistence) -> None:
        """bearer（小写）同样被脱敏。"""
        text = "bearer abc123def456ghi789jkl012mno345pqr"
        result = persistence.redact_credentials(text)
        assert "[REDACTED]" in result


class TestAutoGenerateTitleOffline:
    """MessagePersistence.auto_generate_title 离线模式测试。"""

    def _make_offline_provider(self) -> MagicMock:
        """构建无 API 配置的离线 provider mock。"""
        provider = MagicMock()
        provider._api_key = None
        provider._base_url = None
        provider._model = None
        return provider

    async def test_offline_title_triggers_db_update(self) -> None:
        """离线 provider 用问题前 30 字做标题并执行 UPDATE。"""
        persistence = MessagePersistence(
            session_factory=MagicMock(),
            clock=FixedClock(datetime.now(UTC)),
            provider=self._make_offline_provider(),
        )
        mock_session = AsyncMock()
        original_scoped = persist_mod.scoped_session

        @asynccontextmanager
        async def fake_scoped_session(factory, dept_id, user_id):  # noqa: ANN001
            yield mock_session

        try:
            persist_mod.scoped_session = fake_scoped_session  # type: ignore[assignment]
            question = "请帮我分析一下最新的实验数据趋势和异常点"
            await persistence.auto_generate_title(uuid4(), question, "answer text")
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_awaited_once()

    async def test_offline_empty_question_skips_title(self) -> None:
        """离线模式下空问题跳过标题更新（不执行 UPDATE）。"""
        persistence = MessagePersistence(
            session_factory=MagicMock(),
            clock=FixedClock(datetime.now(UTC)),
            provider=self._make_offline_provider(),
        )
        mock_session = AsyncMock()
        original_scoped = persist_mod.scoped_session

        @asynccontextmanager
        async def fake_scoped_session(factory, dept_id, user_id):  # noqa: ANN001
            yield mock_session

        try:
            persist_mod.scoped_session = fake_scoped_session  # type: ignore[assignment]
            await persistence.auto_generate_title(uuid4(), "   ", "answer")
        finally:
            persist_mod.scoped_session = original_scoped  # type: ignore[assignment]

        mock_session.execute.assert_not_awaited()
