"""Unit tests for ContextRouter — pure logic, no DB needed.

Tests mode selection, budget calculation, chunking strategies, and coverage
declaration computation.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.research.execution.models_trusted import (
    AnalysisMode,
    Chunk,
    ChunkStrategy,
    DataProfile,
    PlanStep,
)
from packages.research.planning.context_router import (
    DATA_BUDGET_HARD_LIMIT,
    DEFAULT_MODEL_CONTEXT_LIMIT,
    DEFAULT_OUTPUT_TOKENS,
    DEFAULT_SAFETY_MARGIN,
    DEFAULT_SYSTEM_TOKENS,
    ContextRouter,
    _estimate_tokens,
)

# ============================================================
# Helpers
# ============================================================


def _profile(**overrides: object) -> DataProfile:
    defaults: dict[str, object] = {
        "snapshot_id": uuid4(),
        "total_records": 100,
        "total_tokens_estimate": 10000,
        "field_manifest": {"fact1": ["field_a", "field_b"]},
        "source_count": 1,
        "data_summary": "",
    }
    defaults.update(overrides)
    return DataProfile(**defaults)  # type: ignore[arg-type]


def _step(**overrides: object) -> PlanStep:
    defaults: dict[str, object] = {
        "step_key": "step1",
        "question": "分析收率",
        "requires_full": True,
        "per_record_semantic": False,
        "cross_record_reasoning": False,
        "allows_sampling": False,
        "estimated_tokens": 1000,
        "method": "python",
    }
    defaults.update(overrides)
    return PlanStep(**defaults)  # type: ignore[arg-type]


# ============================================================
# _estimate_tokens
# ============================================================


class TestEstimateTokens:
    def test_empty_string(self) -> None:
        assert _estimate_tokens("") == 0

    def test_non_empty(self) -> None:
        assert _estimate_tokens("hello world") > 0

    def test_min_one_token(self) -> None:
        assert _estimate_tokens("a") >= 1


# ============================================================
# calculate_budget
# ============================================================


class TestCalculateBudget:
    def test_default_budget(self) -> None:
        router = ContextRouter()
        budget = router.calculate_budget()
        expected = (
            DEFAULT_MODEL_CONTEXT_LIMIT
            - DEFAULT_SYSTEM_TOKENS
            - 0
            - DEFAULT_OUTPUT_TOKENS
            - DEFAULT_SAFETY_MARGIN
        )
        assert budget == min(DATA_BUDGET_HARD_LIMIT, expected)

    def test_budget_with_research_context(self) -> None:
        router = ContextRouter()
        budget = router.calculate_budget(research_context_tokens=10000)
        expected = (
            DEFAULT_MODEL_CONTEXT_LIMIT
            - DEFAULT_SYSTEM_TOKENS
            - 10000
            - DEFAULT_OUTPUT_TOKENS
            - DEFAULT_SAFETY_MARGIN
        )
        assert budget == min(DATA_BUDGET_HARD_LIMIT, expected)

    def test_budget_capped_at_hard_limit(self) -> None:
        router = ContextRouter()
        budget = router.calculate_budget(model_context_limit=10_000_000)
        assert budget == DATA_BUDGET_HARD_LIMIT

    def test_budget_floor_zero(self) -> None:
        router = ContextRouter()
        budget = router.calculate_budget(model_context_limit=100)
        assert budget == 0

    def test_budget_with_custom_safety_margin(self) -> None:
        router = ContextRouter()
        budget = router.calculate_budget(safety_margin=20000)
        expected = (
            DEFAULT_MODEL_CONTEXT_LIMIT - DEFAULT_SYSTEM_TOKENS - 0 - DEFAULT_OUTPUT_TOKENS - 20000
        )
        assert budget == min(DATA_BUDGET_HARD_LIMIT, expected)


# ============================================================
# analyze_step
# ============================================================


class TestAnalyzeStep:
    def test_full_compute_no_semantic(self) -> None:
        router = ContextRouter()
        step = _step(requires_full=True, per_record_semantic=False, cross_record_reasoning=False)
        profile = _profile()
        mode, reason = router.analyze_step(step, profile)
        assert mode == AnalysisMode.FULL_COMPUTE.value

    def test_per_record_semantic_fits_budget(self) -> None:
        router = ContextRouter()
        step = _step(requires_full=True, per_record_semantic=True)
        profile = _profile(total_tokens_estimate=100)  # small → fits
        mode, reason = router.analyze_step(step, profile)
        assert mode == AnalysisMode.DIRECT_FULL_CONTEXT.value

    def test_per_record_semantic_exceeds_budget(self) -> None:
        router = ContextRouter()
        step = _step(requires_full=True, per_record_semantic=True)
        profile = _profile(total_tokens_estimate=10_000_000)  # large → exceeds
        mode, reason = router.analyze_step(step, profile)
        assert mode == AnalysisMode.CHUNKED_FULL_SCAN.value

    def test_cross_record_fits_budget(self) -> None:
        router = ContextRouter()
        step = _step(requires_full=True, cross_record_reasoning=True, per_record_semantic=False)
        profile = _profile(total_tokens_estimate=100)
        mode, reason = router.analyze_step(step, profile)
        assert mode == AnalysisMode.DIRECT_FULL_CONTEXT.value

    def test_cross_record_exceeds_no_sampling(self) -> None:
        router = ContextRouter()
        step = _step(
            requires_full=True,
            cross_record_reasoning=True,
            per_record_semantic=False,
            allows_sampling=False,
        )
        profile = _profile(total_tokens_estimate=10_000_000)
        mode, reason = router.analyze_step(step, profile)
        assert mode == AnalysisMode.CHUNKED_FULL_SCAN.value

    def test_cross_record_exceeds_allows_sampling(self) -> None:
        router = ContextRouter()
        step = _step(
            requires_full=True,
            cross_record_reasoning=True,
            per_record_semantic=False,
            allows_sampling=True,
        )
        profile = _profile(total_tokens_estimate=10_000_000)
        mode, reason = router.analyze_step(step, profile)
        assert mode == AnalysisMode.RETRIEVAL.value

    def test_mixed_method(self) -> None:
        router = ContextRouter()
        step = _step(requires_full=False, method="mixed")
        profile = _profile()
        mode, reason = router.analyze_step(step, profile)
        assert mode == AnalysisMode.MIXED.value

    def test_default_retrieval(self) -> None:
        router = ContextRouter()
        step = _step(requires_full=False, method="python")
        profile = _profile()
        mode, reason = router.analyze_step(step, profile)
        assert mode == AnalysisMode.RETRIEVAL.value


# ============================================================
# chunk_data
# ============================================================


class TestChunkData:
    def test_empty_data(self) -> None:
        router = ContextRouter()
        chunks = router.chunk_data("", 1000)
        assert chunks == []

    def test_token_budget_strategy(self) -> None:
        router = ContextRouter()
        data = "x" * 10000
        chunks = router.chunk_data(data, 1000, ChunkStrategy.TOKEN_BUDGET)
        assert len(chunks) > 0
        assert all(isinstance(c, Chunk) for c in chunks)

    def test_token_budget_single_chunk(self) -> None:
        router = ContextRouter()
        data = "short"
        chunks = router.chunk_data(data, 10000, ChunkStrategy.TOKEN_BUDGET)
        assert len(chunks) == 1

    def test_record_count_strategy(self) -> None:
        router = ContextRouter()
        data = "line1\nline2\nline3\nline4\nline5"
        chunks = router.chunk_data(data, 100, ChunkStrategy.RECORD_COUNT)
        assert len(chunks) > 0

    def test_record_count_strategy_empty(self) -> None:
        router = ContextRouter()
        chunks = router.chunk_data("", 100, ChunkStrategy.RECORD_COUNT)
        assert chunks == []

    def test_business_logic_strategy(self) -> None:
        router = ContextRouter()
        data = "x" * 1000
        chunks = router.chunk_data(data, 100, ChunkStrategy.BUSINESS_LOGIC)
        assert len(chunks) > 0

    def test_default_strategy_is_token_budget(self) -> None:
        router = ContextRouter()
        data = "x" * 10000
        chunks = router.chunk_data(data, 1000)
        assert len(chunks) > 0

    def test_chunk_indices_sequential(self) -> None:
        router = ContextRouter()
        data = "x" * 10000
        chunks = router.chunk_data(data, 100, ChunkStrategy.TOKEN_BUDGET)
        for i, chunk in enumerate(chunks):
            assert chunk.index == i

    def test_zero_budget_token_budget(self) -> None:
        router = ContextRouter()
        chunks = router.chunk_data("data", 0, ChunkStrategy.TOKEN_BUDGET)
        assert len(chunks) > 0

    def test_record_count_multiple_chunks(self) -> None:
        router = ContextRouter()
        # Create many lines that exceed budget
        lines = [f"line_{i} " * 20 for i in range(50)]
        data = "\n".join(lines)
        chunks = router.chunk_data(data, 10, ChunkStrategy.RECORD_COUNT)
        assert len(chunks) > 1


# ============================================================
# compute_coverage
# ============================================================


class TestComputeCoverage:
    def test_full_compute_coverage(self) -> None:
        router = ContextRouter()
        step = _step()
        cov = router.compute_coverage(step, None, 100, AnalysisMode.FULL_COMPUTE.value)
        assert cov.data_coverage_rate == 1.0
        assert cov.llm_read_rate == 0.0
        assert cov.is_sampled is False

    def test_direct_full_context_coverage(self) -> None:
        router = ContextRouter()
        step = _step()
        cov = router.compute_coverage(step, None, 100, AnalysisMode.DIRECT_FULL_CONTEXT.value)
        assert cov.data_coverage_rate == 1.0
        assert cov.llm_read_rate == 1.0

    def test_chunked_full_scan_coverage(self) -> None:
        router = ContextRouter()
        step = _step()
        chunks = [
            Chunk(index=0, content="a"),
            Chunk(index=1, content="b"),
            Chunk(index=2, content="c"),
        ]
        cov = router.compute_coverage(
            step, chunks, 100, AnalysisMode.CHUNKED_FULL_SCAN.value, successful_chunks=2
        )
        assert cov.data_coverage_rate == pytest.approx(2 / 3)
        assert cov.llm_read_rate == pytest.approx(2 / 3)
        assert cov.batch_count == 3
        assert cov.batch_progress == 2

    def test_chunked_full_scan_no_chunks(self) -> None:
        router = ContextRouter()
        step = _step()
        cov = router.compute_coverage(step, None, 100, AnalysisMode.CHUNKED_FULL_SCAN.value)
        assert cov.data_coverage_rate == 0.0
        assert cov.batch_count is None

    def test_chunked_full_scan_empty_chunks(self) -> None:
        router = ContextRouter()
        step = _step()
        cov = router.compute_coverage(step, [], 100, AnalysisMode.CHUNKED_FULL_SCAN.value)
        assert cov.data_coverage_rate == 0.0
        assert cov.batch_count is None

    def test_retrieval_coverage_sampled(self) -> None:
        router = ContextRouter()
        step = _step(allows_sampling=True)
        cov = router.compute_coverage(
            step, None, 100, AnalysisMode.RETRIEVAL.value, successful_chunks=10
        )
        assert cov.is_sampled is True
        assert cov.data_coverage_rate > 0

    def test_retrieval_coverage_not_sampled(self) -> None:
        router = ContextRouter()
        step = _step(allows_sampling=False)
        cov = router.compute_coverage(
            step, None, 100, AnalysisMode.RETRIEVAL.value, successful_chunks=10
        )
        assert cov.is_sampled is False

    def test_mixed_coverage(self) -> None:
        router = ContextRouter()
        step = _step()
        chunks = [Chunk(index=0, content="a"), Chunk(index=1, content="b")]
        cov = router.compute_coverage(
            step, chunks, 100, AnalysisMode.MIXED.value, successful_chunks=1
        )
        assert cov.data_coverage_rate == 1.0
        assert cov.llm_read_rate == 0.75
        assert cov.batch_count == 2
        assert cov.batch_progress == 1

    def test_mixed_coverage_no_chunks(self) -> None:
        router = ContextRouter()
        step = _step()
        cov = router.compute_coverage(
            step, None, 100, AnalysisMode.MIXED.value, successful_chunks=0
        )
        assert cov.data_coverage_rate == 1.0
        assert cov.batch_count is None
        assert cov.batch_progress is None


# ============================================================
# build_data_profile_summary
# ============================================================


class TestBuildDataProfileSummary:
    def test_summary_contains_key_info(self) -> None:
        router = ContextRouter()
        profile = _profile(
            source_count=3,
            total_records=500,
            total_tokens_estimate=20000,
            field_manifest={"fact1": ["field_a", "field_b"], "fact2": ["field_c"]},
        )
        summary = router.build_data_profile_summary(profile)
        assert "数据源数量: 3" in summary
        assert "总记录数: 500" in summary
        assert "预估 token 数: 20000" in summary
        assert "field_a" in summary
        assert "field_c" in summary

    def test_summary_empty_field_manifest(self) -> None:
        router = ContextRouter()
        profile = _profile(field_manifest={})
        summary = router.build_data_profile_summary(profile)
        assert "字段清单:" in summary
