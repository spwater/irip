"""Timeline metrics and database invariant tests."""

from packages.research.timeline.metrics import (
    get_metrics_snapshot,
    record_run_completion,
    record_turn_status,
)


def test_metrics_snapshot_contains_all_names():
    snapshot = get_metrics_snapshot()
    assert "research_turns_by_status" in snapshot
    assert "research_run_completion_seconds_count" in snapshot
    assert "research_run_completion_seconds_p95" in snapshot


def test_record_turn_status_increments_counter():
    record_turn_status("succeeded")
    record_turn_status("succeeded")
    snapshot = get_metrics_snapshot()
    assert snapshot["research_turns_by_status"].get("succeeded", 0) >= 2


def test_record_run_completion_tracks_duration():
    record_run_completion(1.5)
    record_run_completion(2.5)
    snapshot = get_metrics_snapshot()
    assert snapshot["research_run_completion_seconds_count"] >= 2


def test_database_invariant_no_duplicate_results():
    """Verify TurnResult has unique constraint on run_id."""
    from packages.research.timeline.entities import ResearchTurnResult

    column = ResearchTurnResult.__table__.c.run_id
    assert column.unique is True
    assert column.nullable is False


def test_database_invariant_no_null_run_id():
    """Verify run_id is NOT NULL at ORM level."""
    from packages.research.timeline.entities import ResearchTurnResult

    assert ResearchTurnResult.__table__.c.run_id.nullable is False
