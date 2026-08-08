"""单元测试：ParameterService 参数业务编排服务。

覆盖 cursor 编解码 + 枚举 + create_parameter / list_parameters 等方法。
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from packages.common.database import ScopedSessionMixin
from packages.parameters.service import (
    ParameterReviewState,
    ParameterService,
    ParameterStatus,
    ParameterVersionRef,
)


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


def _make_service() -> ParameterService:
    return ParameterService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=uuid4(),
    )


class TestEnums:
    """枚举测试。"""

    def test_parameter_status_values(self) -> None:
        assert ParameterStatus.DRAFT == "draft"
        assert ParameterStatus.PUBLISHED == "published"
        assert ParameterStatus.DEPRECATED == "deprecated"

    def test_review_state_values(self) -> None:
        assert ParameterReviewState.CURRENT == "current"
        assert ParameterReviewState.REVIEW_REQUIRED == "review_required"


class TestCreateParameter:
    """create_parameter 测试。"""


class TestListParameters:
    """list_parameters 测试。"""

    async def test_list_success(self) -> None:
        params = [MagicMock(), MagicMock()]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = params
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        svc = _make_service()
        async with _patch_scoped_session(session):
            result = await svc.list_parameters()
        assert len(result) == 2


class TestParameterVersionRef:
    """ParameterVersionRef 值对象测试。"""

    def test_creation(self) -> None:
        ref = ParameterVersionRef(
            parameter_id=uuid4(),
            version=1,
            version_id=uuid4(),
            variable_code="temp",
            value="100",
            unit="℃",
            confidence=None,
            status="published",
            conditions=None,
            published_at=datetime.now(UTC),
        )
        assert ref.version == 1
        assert ref.value == "100"
        assert ref.status == "published"
