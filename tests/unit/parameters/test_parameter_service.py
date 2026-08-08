"""单元测试：ParameterService 参数审批/发布/弃用流程。

覆盖：
- ParameterStatus / ParameterReviewState 枚举值；
- create_parameter 空变量代码抛 validation_failed；
- create_parameter 重复参数抛 conflict；
- approve 候选非 pending_review 抛 candidate_not_pending；
- approve 提交人自审抛 self_approval_forbidden；
- approve 推导运行未成功抛 derivation_not_succeeded；
- reject 候选非 pending 抛 candidate_not_pending；
- reject 提交人自审抛 self_approval_forbidden；
- deprecate 非 published 状态抛 invalid_transition；
- create_candidate 推导运行不存在抛 not_found。
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from packages.common.database import ScopedSessionMixin
from packages.common.errors import AppError
from packages.parameters.service import (
    ParameterReviewState,
    ParameterService,
    ParameterStatus,
)


def _make_service() -> ParameterService:
    """构造 ParameterService 实例。"""
    return ParameterService(
        session_factory=MagicMock(),
        department_id=uuid4(),
        actor_id=uuid4(),
    )


@asynccontextmanager
async def _patch_scoped_session(mock_session: AsyncMock) -> Any:
    """临时替换 ScopedSessionMixin._scoped_session。"""
    original = ScopedSessionMixin._scoped_session

    @asynccontextmanager
    async def fake_scoped_session(self: Any) -> Any:
        yield mock_session

    ScopedSessionMixin._scoped_session = fake_scoped_session  # type: ignore[method-assign]
    try:
        yield
    finally:
        ScopedSessionMixin._scoped_session = original  # type: ignore[method-assign]


class TestParameterEnums:
    """参数状态枚举测试。"""

    def test_parameter_status_values(self) -> None:
        """ParameterStatus 包含 7 种状态。"""
        assert ParameterStatus.DRAFT == "draft"
        assert ParameterStatus.PENDING_REVIEW == "pending_review"
        assert ParameterStatus.PUBLISHED == "published"
        assert ParameterStatus.REJECTED == "rejected"
        assert ParameterStatus.EXPIRED == "expired"
        assert ParameterStatus.DEPRECATED == "deprecated"
        assert len(ParameterStatus) == 6

    def test_review_state_values(self) -> None:
        """ParameterReviewState 包含 current / review_required。"""
        assert ParameterReviewState.CURRENT == "current"
        assert ParameterReviewState.REVIEW_REQUIRED == "review_required"
        assert len(ParameterReviewState) == 2


class TestCreateParameterValidation:
    """create_parameter 输入校验测试。"""

    async def test_empty_variable_code_raises(self) -> None:
        """空变量代码抛 validation_failed。"""
        service = _make_service()
        mock_session = AsyncMock()
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="变量代码不能为空"):
                await service.create_parameter("", uuid4())

    async def test_whitespace_variable_code_raises(self) -> None:
        """纯空格变量代码抛 validation_failed。"""
        service = _make_service()
        mock_session = AsyncMock()
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="变量代码不能为空"):
                await service.create_parameter("   ", uuid4())

    async def test_duplicate_parameter_raises_conflict(self) -> None:
        """重复参数抛 conflict。"""
        service = _make_service()
        mock_session = AsyncMock()
        existing = MagicMock()
        mock_session.scalar = AsyncMock(return_value=existing)
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="参数已存在"):
                await service.create_parameter("TEMP_C", uuid4())


def _make_pending_candidate(submitted_by: Any) -> MagicMock:
    """构造 pending_review 状态的候选 mock。"""
    candidate = MagicMock()
    candidate.id = uuid4()
    candidate.parameter_id = uuid4()
    candidate.derivation_run_id = uuid4()
    candidate.value = "25"
    candidate.unit = "°C"
    candidate.confidence = "high"
    candidate.confidence_interval = None
    candidate.conditions = None
    candidate.status = "pending_review"
    candidate.submitted_by = submitted_by
    return candidate


def _make_succeeded_run() -> MagicMock:
    """构造 succeeded 状态的推导运行 mock。"""
    run = MagicMock()
    run.id = uuid4()
    run.status = "succeeded"
    return run


class TestApproveValidation:
    """approve 审批校验测试。"""

    async def test_candidate_not_pending_raises(self) -> None:
        """候选非 pending_review 抛 candidate_not_pending。"""
        service = _make_service()
        mock_session = AsyncMock()
        candidate = _make_pending_candidate(uuid4())
        candidate.status = "approved"
        mock_session.scalar = AsyncMock(return_value=candidate)
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="候选不在 pending_review"):
                await service.approve(candidate.id, uuid4())

    async def test_self_approval_forbidden(self) -> None:
        """提交人审批自己的候选抛 self_approval_forbidden。"""
        service = _make_service()
        mock_session = AsyncMock()
        submitter = uuid4()
        candidate = _make_pending_candidate(submitter)
        mock_session.scalar = AsyncMock(return_value=candidate)
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="提交人不能审批"):
                await service.approve(candidate.id, submitter)

    async def test_derivation_not_succeeded_raises(self) -> None:
        """推导运行未成功抛 derivation_not_succeeded。"""
        service = _make_service()
        mock_session = AsyncMock()
        submitter = uuid4()
        reviewer = uuid4()
        candidate = _make_pending_candidate(submitter)
        failed_run = MagicMock()
        failed_run.id = candidate.derivation_run_id
        failed_run.status = "failed"

        # 第一次 scalar 返回 candidate，第二次返回 run
        mock_session.scalar = AsyncMock(side_effect=[candidate, failed_run])
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="推导运行未成功"):
                await service.approve(candidate.id, reviewer)

    async def test_derivation_run_not_found_raises(self) -> None:
        """推导运行不存在抛 not_found。"""
        service = _make_service()
        mock_session = AsyncMock()
        submitter = uuid4()
        reviewer = uuid4()
        candidate = _make_pending_candidate(submitter)
        mock_session.scalar = AsyncMock(side_effect=[candidate, None])
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="推导运行不存在"):
                await service.approve(candidate.id, reviewer)

    async def test_candidate_not_found_raises(self) -> None:
        """候选不存在抛 not_found。"""
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=None)
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="参数候选不存在"):
                await service.approve(uuid4(), uuid4())


class TestRejectValidation:
    """reject 拒绝校验测试。"""

    async def test_reject_self_approval_forbidden(self) -> None:
        """提交人拒绝自己的候选抛 self_approval_forbidden。"""
        service = _make_service()
        mock_session = AsyncMock()
        submitter = uuid4()
        candidate = _make_pending_candidate(submitter)
        mock_session.scalar = AsyncMock(return_value=candidate)
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="提交人不能审批"):
                await service.reject(candidate.id, submitter, "bad")

    async def test_reject_not_pending_raises(self) -> None:
        """拒绝非 pending 候选抛 candidate_not_pending。"""
        service = _make_service()
        mock_session = AsyncMock()
        candidate = _make_pending_candidate(uuid4())
        candidate.status = "rejected"
        mock_session.scalar = AsyncMock(return_value=candidate)
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="候选不在 pending_review"):
                await service.reject(candidate.id, uuid4(), "bad")

    async def test_reject_candidate_not_found(self) -> None:
        """候选不存在抛 not_found。"""
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=None)
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="参数候选不存在"):
                await service.reject(uuid4(), uuid4(), "comment")


class TestDeprecateValidation:
    """deprecate 弃用校验测试。"""

    async def test_deprecate_non_published_raises(self) -> None:
        """非 published 状态弃用抛 invalid_transition。"""
        service = _make_service()
        mock_session = AsyncMock()
        param = MagicMock()
        param.id = uuid4()
        param.status = "draft"
        mock_session.scalar = AsyncMock(return_value=param)
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="仅 published"):
                await service.deprecate(param.id)

    async def test_deprecate_not_found_raises(self) -> None:
        """参数不存在抛 not_found。"""
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=None)
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="参数不存在"):
                await service.deprecate(uuid4())


class TestCreateCandidateValidation:
    """create_candidate 候选创建校验测试。"""

    async def test_derivation_run_not_found_raises(self) -> None:
        """推导运行不存在抛 not_found。"""
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=None)
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="推导运行不存在"):
                await service.create_candidate(
                    parameter_id=uuid4(),
                    derivation_run_id=uuid4(),
                    value="25",
                    unit="°C",
                    confidence=None,
                )

    async def test_derivation_not_succeeded_raises(self) -> None:
        """推导运行未成功抛 derivation_not_succeeded。"""
        service = _make_service()
        mock_session = AsyncMock()
        run = MagicMock()
        run.status = "running"
        mock_session.scalar = AsyncMock(return_value=run)
        async with _patch_scoped_session(mock_session):
            with pytest.raises(AppError, match="推导运行未成功"):
                await service.create_candidate(
                    parameter_id=uuid4(),
                    derivation_run_id=run.id,
                    value="25",
                    unit="°C",
                    confidence=None,
                )
