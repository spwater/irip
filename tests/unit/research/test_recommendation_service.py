"""Tests for recommendation service: NFKC dedup, retry, parsing."""

import json as _json
from typing import Any, cast
from unittest import mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.research.timeline.contracts import (
    RecommendationOutput,
    RecommendedQuestion,
)


class TestNFKCDedup:
    """Test NFKC normalization and deduplication logic."""

    def test_fullwidth_question_normalized(self) -> None:
        import unicodedata

        q1 = "哪些批次收率偏低？"
        q2 = "哪些批次收率偏低?"
        n1 = unicodedata.normalize("NFKC", q1).strip().casefold()
        n2 = unicodedata.normalize("NFKC", q2).strip().casefold()
        assert n1 == n2

    def test_duplicate_questions_deduped(self) -> None:
        import unicodedata

        questions = ["温度影响收率？", "温度影响收率？", "温度影响收率?"]
        seen: set[str] = set()
        unique: list[str] = []
        for q in questions:
            n = unicodedata.normalize("NFKC", q).strip().casefold()
            if n not in seen:
                seen.add(n)
                unique.append(q)
        assert len(unique) == 1

    def test_distinct_questions_preserved(self) -> None:
        import unicodedata

        questions = ["温度影响？", "压力影响？", "时间影响？"]
        seen: set[str] = set()
        unique: list[str] = []
        for q in questions:
            n = unicodedata.normalize("NFKC", q).strip().casefold()
            if n not in seen:
                seen.add(n)
                unique.append(q)
        assert len(unique) == 3


class TestRecommendationOutputValidation:
    """Test Pydantic schema validation boundaries."""

    def test_one_question_accepted(self) -> None:
        output = RecommendationOutput(
            questions=[RecommendedQuestion(question="一个问题", rationale="理由")]
        )
        assert len(output.questions) == 1

    def test_four_questions_accepted(self) -> None:
        output = RecommendationOutput(
            questions=[
                RecommendedQuestion(question=f"问题{i}描述", rationale="理由") for i in range(4)
            ]
        )
        assert len(output.questions) == 4

    def test_zero_questions_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecommendationOutput(questions=[])

    def test_five_questions_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecommendationOutput(
                questions=[
                    RecommendedQuestion(question=f"问题{i}描述", rationale="理由") for i in range(5)
                ]
            )

    def test_short_question_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecommendationOutput(questions=[RecommendedQuestion(question="Q", rationale="理由")])

    def test_empty_rationale_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecommendationOutput(questions=[RecommendedQuestion(question="问题", rationale="")])


class TestResearchEventRoutes:
    """Test that the Outbox dispatcher has research event routes."""

    def test_routes_exist(self) -> None:
        from packages.jobs.dispatcher import RESEARCH_EVENT_ROUTES

        assert "research.recommendation.requested" in RESEARCH_EVENT_ROUTES
        assert "research.run.requested" in RESEARCH_EVENT_ROUTES
        assert "research.candidate_extraction.requested" in RESEARCH_EVENT_ROUTES

    def test_recommendation_route(self) -> None:
        from packages.jobs.dispatcher import RESEARCH_EVENT_ROUTES

        task, queue = RESEARCH_EVENT_ROUTES["research.recommendation.requested"]
        assert task == "research.recommendations.generate"
        assert queue == "irip-research"

    def test_extraction_route(self) -> None:
        from packages.jobs.dispatcher import RESEARCH_EVENT_ROUTES

        task, queue = RESEARCH_EVENT_ROUTES["research.candidate_extraction.requested"]
        assert task == "research.candidates.extract"
        assert queue == "irip-research"

    def test_no_arbitrary_task_injection(self) -> None:
        from packages.jobs.dispatcher import RESEARCH_EVENT_ROUTES

        for _event_type, (task_name, queue) in RESEARCH_EVENT_ROUTES.items():
            assert task_name.startswith("research.")
            assert queue == "irip-research"


class TestPromptConstants:
    """Test that prompt version constants exist and are stable."""

    def test_recommendation_prompt_version(self) -> None:
        from packages.research.timeline.prompts import (
            RECOMMENDATION_PROMPT_VERSION,
        )

        assert RECOMMENDATION_PROMPT_VERSION == "research-recommendation-v2"

    def test_recommendation_schema_version(self) -> None:
        from packages.research.timeline.prompts import (
            RECOMMENDATION_OUTPUT_SCHEMA_VERSION,
        )

        assert RECOMMENDATION_OUTPUT_SCHEMA_VERSION == "recommendation-output-v1"

    def test_synthesis_prompt_version(self) -> None:
        from packages.research.timeline.prompts import SYNTHESIS_PROMPT_VERSION

        assert SYNTHESIS_PROMPT_VERSION == "research-synthesis-v1"

    def test_candidate_extraction_prompt_version(self) -> None:
        from packages.research.timeline.prompts import (
            CANDIDATE_EXTRACTION_PROMPT_VERSION,
        )

        assert CANDIDATE_EXTRACTION_PROMPT_VERSION == "research-candidate-extraction-v1"

    def test_recommendation_system_prompt_has_quality_rules(self) -> None:
        from packages.research.timeline.prompts import (
            RECOMMENDATION_SYSTEM_PROMPT,
        )

        assert "2" in RECOMMENDATION_SYSTEM_PROMPT
        assert "1-4" in RECOMMENDATION_SYSTEM_PROMPT
        # v2 prompt focuses on data-analysis questions, not "低价值"
        assert "数据分析" in RECOMMENDATION_SYSTEM_PROMPT
        assert "禁止" in RECOMMENDATION_SYSTEM_PROMPT

    def test_synthesis_prompt_has_no_placeholder_rule(self) -> None:
        from packages.research.timeline.prompts import SYNTHESIS_SYSTEM_PROMPT

        assert "not_applicable" in SYNTHESIS_SYSTEM_PROMPT
        assert "占位" not in SYNTHESIS_SYSTEM_PROMPT or "噪音" in SYNTHESIS_SYSTEM_PROMPT

    def test_candidate_extraction_prompt_has_zero_allowed(self) -> None:
        from packages.research.timeline.prompts import (
            CANDIDATE_EXTRACTION_SYSTEM_PROMPT,
        )

        assert "0" in CANDIDATE_EXTRACTION_SYSTEM_PROMPT
        assert "成功" in CANDIDATE_EXTRACTION_SYSTEM_PROMPT


# ============================================================
# RecommendationService 核心方法测试（mock session + repository）
# 此前测试仅覆盖 NFKC/schema/prompt 等边缘逻辑，
# 从未真正调用 service 方法，导致 recommendation_service.py 覆盖率长期为 0%。
# ============================================================


class _FakeBatch:
    """替身：execute_batch/retry_batch 只读取以下属性。"""

    def __init__(self, bid: object, wid: object, status: str) -> None:
        self.id = bid
        self.workspace_id = wid
        self.status = status
        self.mode = "initial"
        self.attempt = 0


class _FakeSession:
    """支持 async with 的假 session。"""

    def __init__(self) -> None:
        self.commit = mock.AsyncMock()
        self.flush = mock.AsyncMock()
        self.execute = mock.AsyncMock(return_value=mock.MagicMock())

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeSessionFactory:
    """调用即返回 _FakeSession。"""

    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def __call__(self) -> _FakeSession:
        return self._session


def _patch_repo(method_name: str, return_value: object) -> Any:
    """patch TimelineRepository 的静态方法。"""
    from packages.research.timeline.repository import TimelineRepository

    return mock.patch.object(
        TimelineRepository,
        method_name,
        new=mock.AsyncMock(return_value=return_value),
    )


def _patch_snapshot(return_value: object | None) -> Any:
    """patch ResearchRepository.get_latest_snapshot。"""
    from packages.research.repository import ResearchRepository

    return mock.patch.object(
        ResearchRepository,
        "get_latest_snapshot",
        new=mock.AsyncMock(return_value=return_value),
    )


def _make_gateway(raw_content: object) -> mock.AsyncMock:
    gateway = mock.AsyncMock()
    gateway.call = mock.AsyncMock(return_value=raw_content)
    return gateway


def _valid_json_output(questions: list[dict[str, object]]) -> str:
    return _json.dumps({"questions": questions}, ensure_ascii=False)


class TestRecommendationServiceMethods:
    """通过 mock 直接调用 RecommendationService 的核心 async 方法。"""

    async def test_enqueue_initial_creates_batch(self) -> None:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        session = _FakeSession()
        wid = uuid4()
        sid = uuid4()
        new_batch = _FakeBatch(uuid4(), wid, "queued")

        with (
            _patch_repo("get_batch_by_idempotency", None),
            _patch_repo("insert_batch", new_batch),
        ):
            ref = await RecommendationService.enqueue_initial(cast(AsyncSession, session), wid, sid)
            assert ref.batch_id == new_batch.id
            assert ref.status == "queued"
            assert ref.item_count == 0

    async def test_enqueue_initial_idempotent_reuses_existing(self) -> None:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        session = _FakeSession()
        wid = uuid4()
        sid = uuid4()
        existing = _FakeBatch(uuid4(), wid, "succeeded")

        with (
            _patch_repo("get_batch_by_idempotency", existing),
            _patch_repo("list_recommendation_items", [1, 2, 3]),
        ):
            ref = await RecommendationService.enqueue_initial(cast(AsyncSession, session), wid, sid)
            assert ref.batch_id == existing.id
            assert ref.status == "succeeded"
            assert ref.item_count == 3

    async def test_execute_batch_not_found_raises(self) -> None:
        from packages.common.errors import AppError
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
        )
        bid = uuid4()

        with _patch_repo("get_batch", None):
            with pytest.raises(AppError) as exc_info:
                await svc.execute_batch(bid)
            assert exc_info.value.code == "not_found"

    async def test_execute_batch_terminal_short_circuits(self) -> None:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        wid = uuid4()
        bid = uuid4()
        batch = _FakeBatch(bid, wid, "succeeded")
        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
        )

        with (
            _patch_repo("get_batch", batch),
            _patch_repo("list_recommendation_items", [1, 2]),
        ):
            ref = await svc.execute_batch(bid)
            assert ref.status == "succeeded"
            assert ref.item_count == 2

    async def test_execute_batch_success_path(self) -> None:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        wid = uuid4()
        bid = uuid4()
        batch = _FakeBatch(bid, wid, "queued")
        gateway = _make_gateway(
            _valid_json_output(
                [
                    {"question": "哪些批次收率偏低？", "rationale": "分析"},
                    {"question": "温度如何影响收率？", "rationale": "分析"},
                ]
            )
        )
        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
            model_gateway=gateway,
        )

        with (
            _patch_repo("get_batch", batch),
            _patch_repo("update_batch_status", True),
            _patch_repo("insert_recommendation_items", None),
            _patch_snapshot(None),
        ):
            ref = await svc.execute_batch(bid)
            assert ref.status == "succeeded"
            assert ref.item_count == 2

    async def test_execute_batch_parses_code_fenced_json(self) -> None:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        wid = uuid4()
        bid = uuid4()
        batch = _FakeBatch(bid, wid, "queued")
        inner = _valid_json_output([{"question": "哪些批次收率偏低？", "rationale": "分析"}])
        gateway = _make_gateway("```json\n" + inner + "\n```")
        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
            model_gateway=gateway,
        )

        with (
            _patch_repo("get_batch", batch),
            _patch_repo("update_batch_status", True),
            _patch_repo("insert_recommendation_items", None),
            _patch_snapshot(None),
        ):
            ref = await svc.execute_batch(bid)
            assert ref.status == "succeeded"
            assert ref.item_count == 1

    async def test_execute_batch_dedup_normalization(self) -> None:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        wid = uuid4()
        bid = uuid4()
        batch = _FakeBatch(bid, wid, "queued")
        # 全角问号 vs 半角问号：NFKC 归一后视为重复
        gateway = _make_gateway(
            _valid_json_output(
                [
                    {"question": "哪些批次收率偏低？", "rationale": "a"},
                    {"question": "哪些批次收率偏低?", "rationale": "b"},
                ]
            )
        )
        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
            model_gateway=gateway,
        )

        with (
            _patch_repo("get_batch", batch),
            _patch_repo("update_batch_status", True),
            _patch_repo("insert_recommendation_items", None),
            _patch_snapshot(None),
        ):
            ref = await svc.execute_batch(bid)
            assert ref.status == "succeeded"
            assert ref.item_count == 1  # 归一化去重后仅剩 1 个

    async def test_execute_batch_gateway_not_configured_fails(self) -> None:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        wid = uuid4()
        bid = uuid4()
        batch = _FakeBatch(bid, wid, "queued")
        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
            model_gateway=None,
        )

        with (
            _patch_repo("get_batch", batch),
            _patch_repo("update_batch_status", True),
            _patch_snapshot(None),
        ):
            ref = await svc.execute_batch(bid)
            assert ref.status == "failed"
            assert ref.item_count == 0

    async def test_retry_batch_only_failed_status(self) -> None:
        from packages.common.errors import AppError
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        wid = uuid4()
        bid = uuid4()
        batch = _FakeBatch(bid, wid, "succeeded")
        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
        )

        with _patch_repo("get_batch", batch):
            with pytest.raises(AppError) as exc_info:
                await svc.retry_batch(bid)
            assert exc_info.value.code == "state_conflict"

    async def test_retry_batch_failed_to_queued(self) -> None:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        wid = uuid4()
        bid = uuid4()
        batch = _FakeBatch(bid, wid, "failed")
        batch.attempt = 1
        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
        )

        with (
            _patch_repo("get_batch", batch),
            _patch_repo("update_batch_status", True),
        ):
            ref = await svc.retry_batch(bid)
            assert ref.status == "queued"

    async def test_get_active_no_batch_returns_none(self) -> None:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        wid = uuid4()
        session = _FakeSession()
        result = mock.MagicMock()
        result.scalar_one_or_none = mock.MagicMock(return_value=None)
        session.execute = mock.AsyncMock(return_value=result)

        svc = RecommendationService(
            _FakeSessionFactory(session),
            department_id=uuid4(),
            actor_id=uuid4(),
        )
        out = await svc.get_active(wid)
        assert out["status"] == "none"
        assert out["items"] == []

    async def test_request_followup_creates_batch(self) -> None:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        wid = uuid4()
        sid = uuid4()
        new_batch = _FakeBatch(uuid4(), wid, "queued")
        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
        )
        with (
            _patch_repo("get_batch_by_idempotency", None),
            _patch_repo("insert_batch", new_batch),
        ):
            ref = await svc.request_followup(wid, sid, (), "fk")
        assert ref.batch_id == new_batch.id
        assert ref.status == "queued"

    async def test_request_followup_idempotent(self) -> None:
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        wid = uuid4()
        existing = _FakeBatch(uuid4(), wid, "succeeded")
        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
        )
        with (
            _patch_repo("get_batch_by_idempotency", existing),
            _patch_repo("list_recommendation_items", [1, 2]),
        ):
            ref = await svc.request_followup(wid, uuid4(), (), "fk")
        assert ref.status == "succeeded"
        assert ref.item_count == 2

    async def test_retry_batch_not_found(self) -> None:
        from packages.common.errors import AppError
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
        )
        with _patch_repo("get_batch", None):
            with pytest.raises(AppError) as exc_info:
                await svc.retry_batch(uuid4())
            assert exc_info.value.code == "not_found"

    async def test_execute_batch_snapshot_not_none(self) -> None:
        from types import SimpleNamespace

        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )

        wid = uuid4()
        bid = uuid4()
        batch = _FakeBatch(bid, wid, "queued")
        gateway = _make_gateway(
            _valid_json_output([{"question": "温度如何影响收率？", "rationale": "分析"}])
        )
        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
            model_gateway=gateway,
        )
        snapshot = SimpleNamespace(
            field_manifest=["field"],
            source_refs=[{"id": "1"}, {"id": "2"}],
            snapshot_number=7,
        )
        with (
            _patch_repo("get_batch", batch),
            _patch_repo("update_batch_status", True),
            _patch_repo("insert_recommendation_items", None),
            _patch_snapshot(snapshot),
        ):
            ref = await svc.execute_batch(bid)
        assert ref.status == "succeeded"

    async def test_execute_batch_cas_conflict_returns_current(self) -> None:
        from packages.common.errors import AppError
        from packages.research.timeline.recommendation_service import (
            RecommendationService,
        )
        from packages.research.timeline.repository import TimelineRepository

        wid = uuid4()
        bid = uuid4()
        batch = _FakeBatch(bid, wid, "queued")
        svc = RecommendationService(
            _FakeSessionFactory(_FakeSession()),
            department_id=uuid4(),
            actor_id=uuid4(),
        )
        with (
            _patch_repo("get_batch", batch),
            mock.patch.object(
                TimelineRepository,
                "update_batch_status",
                new=mock.AsyncMock(
                    side_effect=AppError(code="state_conflict", message="conflict")
                ),
            ),
        ):
            ref = await svc.execute_batch(bid)
        assert ref.status == "queued"


