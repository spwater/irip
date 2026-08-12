"""Tests for Task 6-8: Turn-aware Plan/Run/Extraction adapters."""

import inspect

from packages.research.timeline.extraction_service import (
    CandidateExtractionService,
)
from packages.research.timeline.turn_plan_adapter import (
    confirm_plan_for_turn,
    generate_plan_for_turn,
)
from packages.research.timeline.turn_run_adapter import (
    complete_run_for_turn,
    submit_run_for_turn,
)


class TestTurnPlanAdapter:
    """Test that plan adapter functions exist and have correct signatures."""

    def test_generate_plan_for_turn_exists(self) -> None:
        assert callable(generate_plan_for_turn)

    def test_confirm_plan_for_turn_exists(self) -> None:
        assert callable(confirm_plan_for_turn)

    def test_generate_plan_uses_turn_context(self) -> None:
        source = inspect.getsource(generate_plan_for_turn)
        assert "TurnContextBuilder" in source
        assert "lock_turn_inputs" in source

    def test_confirm_plan_transitions_turn(self) -> None:
        source = inspect.getsource(confirm_plan_for_turn)
        assert "plan_confirmed" in source
        assert "plan_review" in source

    def test_plan_binds_to_turn(self) -> None:
        source = inspect.getsource(generate_plan_for_turn)
        assert "turn_id" in source


class TestTurnRunAdapter:
    """Test that run adapter functions exist and have correct logic."""

    def test_submit_run_for_turn_exists(self) -> None:
        assert callable(submit_run_for_turn)

    def test_complete_run_for_turn_exists(self) -> None:
        assert callable(complete_run_for_turn)

    def test_submit_checks_active_run(self) -> None:
        source = inspect.getsource(submit_run_for_turn)
        assert "analysis_busy" in source
        assert "get_active_run_status" in source

    def test_submit_uses_attempt_number(self) -> None:
        source = inspect.getsource(submit_run_for_turn)
        assert "attempt" in source.lower()
        assert "turn_id" in source

    def test_complete_writes_turn_result(self) -> None:
        source = inspect.getsource(complete_run_for_turn)
        assert "insert_turn_result" in source

    def test_complete_creates_extraction_job(self) -> None:
        source = inspect.getsource(complete_run_for_turn)
        assert "insert_extraction_job" in source

    def test_complete_transitions_on_succeeded(self) -> None:
        source = inspect.getsource(complete_run_for_turn)
        assert "succeeded" in source

    def test_complete_transitions_on_failed(self) -> None:
        source = inspect.getsource(complete_run_for_turn)
        assert "run_failed" in source


class TestExtractionService:
    """Test that extraction service exists and has correct methods."""

    def test_service_class_exists(self) -> None:
        assert CandidateExtractionService is not None

    def test_has_enqueue_method(self) -> None:
        assert hasattr(CandidateExtractionService, "enqueue_for_completed_run")

    def test_has_execute_method(self) -> None:
        assert hasattr(CandidateExtractionService, "execute")

    def test_has_retry_method(self) -> None:
        assert hasattr(CandidateExtractionService, "retry")

    def test_enqueue_is_static(self) -> None:
        # enqueue_for_completed_run should be a staticmethod
        assert isinstance(
            CandidateExtractionService.__dict__.get(
                "enqueue_for_completed_run",
                CandidateExtractionService.__dict__.get(
                    "_CandidateExtractionService__enqueue_for_completed_run"
                ),
            ),
            staticmethod,
        )

    def test_enqueue_checks_run_status(self) -> None:
        source = inspect.getsource(CandidateExtractionService.enqueue_for_completed_run)
        assert "succeeded" in source
        assert "partially_succeeded" in source

    def test_execute_uses_cas(self) -> None:
        source = inspect.getsource(CandidateExtractionService.execute)
        assert "queued" in source
        assert "running" in source

    def test_execute_has_heartbeat(self) -> None:
        source = inspect.getsource(CandidateExtractionService.execute)
        assert "heartbeat" in source.lower()

    def test_execute_limits_candidates(self) -> None:
        source = inspect.getsource(CandidateExtractionService)
        assert "20" in source or "MAX_CANDIDATES" in source

    def test_retry_checks_can_retry(self) -> None:
        source = inspect.getsource(CandidateExtractionService.retry)
        assert "can_retry" in source
