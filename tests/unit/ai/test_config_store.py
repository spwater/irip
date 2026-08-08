"""单元测试：config_store AI 大模型配置存储层。

覆盖：
- get_config_row：查询配置行 / None；
- upsert_config：insert（existing=None）/ update（existing 存在）；
- upsert_meta_prompt：insert / update；
- get_active_ai_config：未配置 / 未启用 / 已启用（解密 API key）。

使用 mock session_scope + mock session。
"""

from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import packages.ai.config_store as config_store

# ============================================================
# Helpers
# ============================================================


@contextmanager
def _patch_session_scope(mock_session: AsyncMock) -> Any:
    """临时替换 session_scope 为返回 mock_session 的上下文。"""
    from packages.common import database as db_mod

    original = db_mod.session_scope

    @asynccontextmanager
    async def fake_session_scope(factory: Any, **kwargs: Any) -> Any:
        yield mock_session

    db_mod.session_scope = fake_session_scope  # type: ignore[assignment]
    try:
        yield
    finally:
        db_mod.session_scope = original  # type: ignore[assignment]


def _make_row(enabled: bool = True, api_key: str = "enc-key") -> dict[str, Any]:
    return {
        "id": 1,
        "base_url": "https://api.openai.com/v1",
        "api_key": api_key,
        "model_name": "gpt-4o",
        "assistant_model_name": "gpt-4o-mini",
        "research_model_name": "gpt-4o",
        "enabled": enabled,
        "meta_prompt": "你是一个助手",
        "model_thinking_enabled": False,
        "assistant_thinking_enabled": False,
        "research_thinking_enabled": False,
    }


# ============================================================
# get_config_row
# ============================================================


class TestGetConfigRow:
    """get_config_row 测试。"""

    async def test_returns_dict(self) -> None:
        """查询到配置行返回 dict。"""
        row_mock = MagicMock()
        row_mock._mapping = _make_row()
        result_mock = MagicMock()
        result_mock.fetchone.return_value = row_mock
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)

        result = await config_store.get_config_row(session)
        assert result is not None
        assert result["model_name"] == "gpt-4o"

    async def test_returns_none(self) -> None:
        """无配置行返回 None。"""
        result_mock = MagicMock()
        result_mock.fetchone.return_value = None
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)

        result = await config_store.get_config_row(session)
        assert result is None


# ============================================================
# upsert_config
# ============================================================


class TestUpsertConfig:
    """upsert_config 测试。"""

    async def test_insert_when_no_existing(self) -> None:
        """无已有配置时 insert。"""
        session = AsyncMock()
        with (
            patch(
                "packages.ai.config_store.get_config_row",
                new_callable=AsyncMock,
                return_value=None,
            ),
            _patch_session_scope(session),
        ):
            result = await config_store.upsert_config(
                MagicMock(),
                base_url="https://api.openai.com/v1",
                api_key="enc-key",
                model_name="gpt-4o",
                updated_at=datetime.now(UTC),
            )

        assert result is None
        session.execute.assert_awaited_once()

    async def test_update_when_existing(self) -> None:
        """已有配置时 update。"""
        session = AsyncMock()
        existing = _make_row()
        with (
            patch(
                "packages.ai.config_store.get_config_row",
                new_callable=AsyncMock,
                return_value=existing,
            ),
            _patch_session_scope(session),
        ):
            result = await config_store.upsert_config(
                MagicMock(),
                base_url="https://api.openai.com/v1",
                api_key="new-enc-key",
                model_name="gpt-4o-mini",
                updated_at=datetime.now(UTC),
            )

        assert result == existing
        session.execute.assert_awaited_once()


# ============================================================
# upsert_meta_prompt
# ============================================================


class TestUpsertMetaPrompt:
    """upsert_meta_prompt 测试。"""

    async def test_insert_when_no_existing(self) -> None:
        """无已有配置时 insert。"""
        session = AsyncMock()
        with (
            patch(
                "packages.ai.config_store.get_config_row",
                new_callable=AsyncMock,
                return_value=None,
            ),
            _patch_session_scope(session),
        ):
            await config_store.upsert_meta_prompt(
                MagicMock(),
                meta_prompt="新提示词",
                updated_at=datetime.now(UTC),
            )

        session.execute.assert_awaited_once()

    async def test_update_when_existing(self) -> None:
        """已有配置时 update。"""
        session = AsyncMock()
        with (
            patch(
                "packages.ai.config_store.get_config_row",
                new_callable=AsyncMock,
                return_value=_make_row(),
            ),
            _patch_session_scope(session),
        ):
            await config_store.upsert_meta_prompt(
                MagicMock(),
                meta_prompt="更新提示词",
                updated_at=datetime.now(UTC),
            )

        session.execute.assert_awaited_once()


# ============================================================
# get_active_ai_config
# ============================================================


class TestGetActiveAiConfig:
    """get_active_ai_config 测试。"""

    async def test_returns_none_when_no_config(self) -> None:
        """无配置返回 None。"""
        session = AsyncMock()
        with (
            patch(
                "packages.ai.config_store.get_config_row",
                new_callable=AsyncMock,
                return_value=None,
            ),
            _patch_session_scope(session),
        ):
            result = await config_store.get_active_ai_config(MagicMock())

        assert result is None

    async def test_returns_none_when_disabled(self) -> None:
        """未启用返回 None。"""
        session = AsyncMock()
        with (
            patch(
                "packages.ai.config_store.get_config_row",
                new_callable=AsyncMock,
                return_value=_make_row(enabled=False),
            ),
            _patch_session_scope(session),
        ):
            result = await config_store.get_active_ai_config(MagicMock())

        assert result is None

    async def test_returns_config_when_enabled(self) -> None:
        """已启用返回解密后的配置。"""
        session = AsyncMock()
        mock_crypto = MagicMock()
        mock_crypto.decrypt.return_value = "sk-real-key"
        with (
            patch(
                "packages.ai.config_store.get_config_row",
                new_callable=AsyncMock,
                return_value=_make_row(enabled=True, api_key="enc-key"),
            ),
            patch(
                "packages.ai.config_store.EnvelopeCrypto.from_env",
                return_value=mock_crypto,
            ),
            _patch_session_scope(session),
        ):
            result = await config_store.get_active_ai_config(MagicMock())

        assert result is not None
        assert result["api_key"] == "sk-real-key"
        assert result["base_url"] == "https://api.openai.com/v1"
        assert result["model_name"] == "gpt-4o"
        assert result["assistant_model_name"] == "gpt-4o-mini"
        assert result["meta_prompt"] == "你是一个助手"

    async def test_falls_back_to_model_name(self) -> None:
        """assistant/research model 为空时回退到 model_name。"""
        session = AsyncMock()
        mock_crypto = MagicMock()
        mock_crypto.decrypt.return_value = "key"
        row = _make_row(enabled=True)
        row["assistant_model_name"] = ""
        row["research_model_name"] = ""
        with (
            patch(
                "packages.ai.config_store.get_config_row",
                new_callable=AsyncMock,
                return_value=row,
            ),
            patch(
                "packages.ai.config_store.EnvelopeCrypto.from_env",
                return_value=mock_crypto,
            ),
            _patch_session_scope(session),
        ):
            result = await config_store.get_active_ai_config(MagicMock())

        assert result is not None
        assert result["assistant_model_name"] == "gpt-4o"
        assert result["research_model_name"] == "gpt-4o"
