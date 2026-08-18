"""Tests for recommendation service: NFKC dedup, retry, parsing."""

import pytest
from pydantic import ValidationError

from packages.research.timeline.contracts import RecommendationOutput


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
        output = RecommendationOutput(questions=[{"question": "一个问题", "rationale": "理由"}])
        assert len(output.questions) == 1

    def test_four_questions_accepted(self) -> None:
        output = RecommendationOutput(
            questions=[{"question": f"问题{i}描述", "rationale": "理由"} for i in range(4)]
        )
        assert len(output.questions) == 4

    def test_zero_questions_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecommendationOutput(questions=[])

    def test_five_questions_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecommendationOutput(
                questions=[{"question": f"问题{i}描述", "rationale": "理由"} for i in range(5)]
            )

    def test_short_question_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecommendationOutput(questions=[{"question": "Q", "rationale": "理由"}])

    def test_empty_rationale_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RecommendationOutput(questions=[{"question": "问题", "rationale": ""}])


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
