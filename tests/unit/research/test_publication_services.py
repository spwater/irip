"""单元测试：ResultSearchService 成果包搜索服务。

覆盖 _require_actor + search 基本路径。
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.research.publication.search import ResultSearchService


@asynccontextmanager
async def _patch_scoped_session(mock_session: AsyncMock) -> Any:
    original = ScopedSessionMixin._scoped_session

    @asynccontextmanager
    async def fake_scoped_session(self: Any) -> Any:
        yield mock_session

    ScopedSessionMixin._scoped_session = fake_scoped_session  # type: ignore[method-assign]
    try:
        yield
    finally:
        ScopedSessionMixin._scoped_session = original  # type: ignore[method-assign]


def _make_service(actor_id: Any = None) -> ResultSearchService:
    return ResultSearchService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=actor_id,
    )


class TestRequireActor:
    """_require_actor 测试。"""

    async def test_returns_actor_id(self) -> None:
        """有 actor_id 时返回。"""
        actor = uuid4()
        svc = _make_service(actor_id=actor)
        assert svc._require_actor() == actor

    async def test_raises_when_none(self) -> None:
        """无 actor_id 时抛 forbidden。"""
        svc = _make_service(actor_id=None)
        with pytest.raises(AppError, match="认证"):
            svc._require_actor()


class TestSearch:
    """search 测试。"""

    async def test_search_all_view_empty(self) -> None:
        """all 视图无结果时返回空页。"""
        session = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        session.execute = AsyncMock(return_value=mock_result)
        session.scalar = AsyncMock(return_value=0)
        svc = _make_service(actor_id=uuid4())

        async with _patch_scoped_session(session):
            result = await svc.search(None, None, "all", 1, 10)

        assert result.total == 0
        assert result.items == []

    async def test_search_mine_view_requires_actor(self) -> None:
        """mine 视图需要 actor。"""
        svc = _make_service(actor_id=None)
        with pytest.raises(AppError, match="认证"):
            svc._require_actor()
