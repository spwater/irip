"""Explicit state machines for Turn, RecommendationBatch and CandidateExtractionJob.

Each state machine is a pure-function transition table.  The repository
layer uses compare-and-set (``WHERE status = :expected``) to guarantee
that only valid transitions succeed; an invalid transition raises
``InvalidTurnTransition`` (or the batch/extraction equivalents).
"""

from __future__ import annotations


class InvalidTurnTransition(Exception):
    """Raised when a Turn state transition is not allowed."""


class InvalidBatchTransition(Exception):
    """Raised when a RecommendationBatch state transition is not allowed."""


class InvalidExtractionTransition(Exception):
    """Raised when a CandidateExtractionJob state transition is not allowed."""


# ============================================================
# Turn state machine
# ============================================================

# Normal flow:
#   question_draft -> planning -> plan_review -> plan_confirmed
#   -> queued -> running -> succeeded -> conclusion_reviewed
#
# Exceptional:
#   question_draft/planning -> planning_failed
#   queued/running -> cancelled
#   running -> run_failed
#   succeeded -> succeeded_without_saved_conclusion

_TURN_TRANSITIONS: dict[str, set[str]] = {
    "question_draft": {"planning", "planning_failed", "cancelled"},
    "planning": {"plan_review", "planning_failed", "cancelled"},
    "planning_failed": {"planning", "cancelled"},
    "plan_review": {"plan_confirmed", "planning_failed", "cancelled"},
    "plan_confirmed": {"queued", "cancelled"},
    "queued": {"running", "cancelled"},
    "running": {"succeeded", "run_failed", "cancelled"},
    "run_failed": {"queued", "cancelled"},
    "succeeded": {"conclusion_reviewed", "succeeded_without_saved_conclusion"},
    "conclusion_reviewed": set(),  # terminal
    "succeeded_without_saved_conclusion": set(),  # terminal
    "cancelled": set(),  # terminal
}

# Statuses that count as "active" for workspace concurrency control
ACTIVE_TURN_STATUSES = {"queued", "running"}

# Statuses where the Turn has a confirmed plan and can be submitted to run
RUNNABLE_TURN_STATUSES = {"plan_confirmed", "run_failed"}


class TurnStateMachine:
    """Turn state transition validator."""

    @staticmethod
    def transition(current: str, target: str) -> str:
        """Validate a state transition and return the new status.

        Args:
            current: Current turn status.
            target: Desired next status.

        Returns:
            The validated target status.

        Raises:
            InvalidTurnTransition: If the transition is not allowed.
        """
        if current == target:
            return target
        allowed = _TURN_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidTurnTransition(f"Cannot transition turn from '{current}' to '{target}'")
        return target

    @staticmethod
    def can_plan(turn_status: str) -> bool:
        """Whether a plan can be generated from this status."""
        return turn_status in {"question_draft", "planning_failed"}

    @staticmethod
    def can_run(turn_status: str) -> bool:
        """Whether a run can be submitted from this status."""
        return turn_status in RUNNABLE_TURN_STATUSES

    @staticmethod
    def is_active(turn_status: str) -> bool:
        """Whether this turn status counts as an active run."""
        return turn_status in ACTIVE_TURN_STATUSES

    @staticmethod
    def is_terminal(turn_status: str) -> bool:
        """Whether this is a terminal status (no further transitions)."""
        return len(_TURN_TRANSITIONS.get(turn_status, set())) == 0


# ============================================================
# Recommendation batch state machine
# ============================================================

# queued -> running -> succeeded | failed
# queued -> cancelled (if workspace deleted)

_BATCH_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled"},
    "running": {"succeeded", "failed"},
    "succeeded": set(),  # terminal
    "failed": {"queued"},  # retry allowed
    "cancelled": set(),  # terminal
}


class RecommendationBatchStateMachine:
    """Recommendation batch state transition validator."""

    @staticmethod
    def transition(current: str, target: str) -> str:
        if current == target:
            return target
        allowed = _BATCH_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidBatchTransition(f"Cannot transition batch from '{current}' to '{target}'")
        return target

    @staticmethod
    def is_terminal(status: str) -> bool:
        return len(_BATCH_TRANSITIONS.get(status, set())) == 0

    @staticmethod
    def can_retry(status: str) -> bool:
        return status == "failed"


# ============================================================
# Candidate extraction job state machine
# ============================================================

# queued -> running -> succeeded | failed | task_lost
# failed -> queued (retry)

_EXTRACTION_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled"},
    "running": {"succeeded", "failed", "task_lost"},
    "succeeded": set(),  # terminal
    "failed": {"queued"},  # retry allowed
    "task_lost": {"queued"},  # requeue by reconciler
    "cancelled": set(),  # terminal
}


class ExtractionStateMachine:
    """Candidate extraction job state transition validator."""

    @staticmethod
    def transition(current: str, target: str) -> str:
        if current == target:
            return target
        allowed = _EXTRACTION_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidExtractionTransition(
                f"Cannot transition extraction from '{current}' to '{target}'"
            )
        return target

    @staticmethod
    def is_terminal(status: str) -> bool:
        return len(_EXTRACTION_TRANSITIONS.get(status, set())) == 0

    @staticmethod
    def can_retry(status: str) -> bool:
        return status in {"failed", "task_lost"}
