"""Tests for Turn, RecommendationBatch and Extraction state machines."""

import pytest

from packages.research.timeline.state_machine import (
    ExtractionStateMachine,
    InvalidBatchTransition,
    InvalidExtractionTransition,
    InvalidTurnTransition,
    RecommendationBatchStateMachine,
    TurnStateMachine,
)


class TestTurnStateMachine:
    """Turn state transitions."""

    def test_normal_flow(self) -> None:
        assert TurnStateMachine.transition("question_draft", "planning") == "planning"
        assert TurnStateMachine.transition("planning", "plan_review") == "plan_review"
        assert TurnStateMachine.transition("plan_review", "plan_confirmed") == "plan_confirmed"
        assert TurnStateMachine.transition("plan_confirmed", "queued") == "queued"
        assert TurnStateMachine.transition("queued", "running") == "running"
        assert TurnStateMachine.transition("running", "succeeded") == "succeeded"
        assert (
            TurnStateMachine.transition("succeeded", "conclusion_reviewed") == "conclusion_reviewed"
        )

    def test_confirmed_plan_can_queue(self) -> None:
        assert TurnStateMachine.transition("plan_confirmed", "queued") == "queued"

    def test_candidate_failure_does_not_fail_succeeded_turn(self) -> None:
        with pytest.raises(InvalidTurnTransition):
            TurnStateMachine.transition("succeeded", "run_failed")

    def test_planning_failed_from_question_draft(self) -> None:
        assert TurnStateMachine.transition("question_draft", "planning_failed") == "planning_failed"

    def test_planning_failed_from_planning(self) -> None:
        assert TurnStateMachine.transition("planning", "planning_failed") == "planning_failed"

    def test_retry_from_planning_failed(self) -> None:
        assert TurnStateMachine.transition("planning_failed", "planning") == "planning"

    def test_cancel_from_queued(self) -> None:
        assert TurnStateMachine.transition("queued", "cancelled") == "cancelled"

    def test_cancel_from_running(self) -> None:
        assert TurnStateMachine.transition("running", "cancelled") == "cancelled"

    def test_run_failed_from_running(self) -> None:
        assert TurnStateMachine.transition("running", "run_failed") == "run_failed"

    def test_retry_from_run_failed(self) -> None:
        assert TurnStateMachine.transition("run_failed", "queued") == "queued"

    def test_succeeded_without_saved(self) -> None:
        assert (
            TurnStateMachine.transition("succeeded", "succeeded_without_saved_conclusion")
            == "succeeded_without_saved_conclusion"
        )

    def test_invalid_transition_raises(self) -> None:
        with pytest.raises(InvalidTurnTransition):
            TurnStateMachine.transition("question_draft", "running")

    def test_invalid_from_terminal(self) -> None:
        with pytest.raises(InvalidTurnTransition):
            TurnStateMachine.transition("conclusion_reviewed", "planning")

    def test_same_status_returns_same(self) -> None:
        assert TurnStateMachine.transition("queued", "queued") == "queued"

    def test_is_active(self) -> None:
        assert TurnStateMachine.is_active("queued")
        assert TurnStateMachine.is_active("running")
        assert not TurnStateMachine.is_active("succeeded")
        assert not TurnStateMachine.is_active("question_draft")

    def test_is_terminal(self) -> None:
        assert TurnStateMachine.is_terminal("conclusion_reviewed")
        assert TurnStateMachine.is_terminal("cancelled")
        assert not TurnStateMachine.is_terminal("running")
        assert not TurnStateMachine.is_terminal("succeeded")

    def test_can_plan(self) -> None:
        assert TurnStateMachine.can_plan("question_draft")
        assert TurnStateMachine.can_plan("planning_failed")
        assert not TurnStateMachine.can_plan("plan_review")

    def test_can_run(self) -> None:
        assert TurnStateMachine.can_run("plan_confirmed")
        assert TurnStateMachine.can_run("run_failed")
        assert not TurnStateMachine.can_run("question_draft")


class TestRecommendationBatchStateMachine:
    """Recommendation batch state transitions."""

    def test_normal_flow(self) -> None:
        assert RecommendationBatchStateMachine.transition("queued", "running") == "running"
        assert RecommendationBatchStateMachine.transition("running", "succeeded") == "succeeded"

    def test_failed_flow(self) -> None:
        assert RecommendationBatchStateMachine.transition("running", "failed") == "failed"

    def test_retry_from_failed(self) -> None:
        assert RecommendationBatchStateMachine.transition("failed", "queued") == "queued"

    def test_invalid_retry_from_succeeded(self) -> None:
        with pytest.raises(InvalidBatchTransition):
            RecommendationBatchStateMachine.transition("succeeded", "queued")

    def test_is_terminal(self) -> None:
        assert RecommendationBatchStateMachine.is_terminal("succeeded")
        assert not RecommendationBatchStateMachine.is_terminal("failed")

    def test_can_retry(self) -> None:
        assert RecommendationBatchStateMachine.can_retry("failed")
        assert not RecommendationBatchStateMachine.can_retry("succeeded")


class TestExtractionStateMachine:
    """Candidate extraction job state transitions."""

    def test_normal_flow(self) -> None:
        assert ExtractionStateMachine.transition("queued", "running") == "running"
        assert ExtractionStateMachine.transition("running", "succeeded") == "succeeded"

    def test_failed_flow(self) -> None:
        assert ExtractionStateMachine.transition("running", "failed") == "failed"

    def test_task_lost_from_running(self) -> None:
        assert ExtractionStateMachine.transition("running", "task_lost") == "task_lost"

    def test_retry_from_failed(self) -> None:
        assert ExtractionStateMachine.transition("failed", "queued") == "queued"

    def test_requeue_from_task_lost(self) -> None:
        assert ExtractionStateMachine.transition("task_lost", "queued") == "queued"

    def test_invalid_from_succeeded(self) -> None:
        with pytest.raises(InvalidExtractionTransition):
            ExtractionStateMachine.transition("succeeded", "running")

    def test_is_terminal(self) -> None:
        assert ExtractionStateMachine.is_terminal("succeeded")
        assert not ExtractionStateMachine.is_terminal("failed")

    def test_can_retry(self) -> None:
        assert ExtractionStateMachine.can_retry("failed")
        assert ExtractionStateMachine.can_retry("task_lost")
        assert not ExtractionStateMachine.can_retry("succeeded")
