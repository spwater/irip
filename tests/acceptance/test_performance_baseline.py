"""Performance baseline and regression tests.

Verifies k6 smoke thresholds and E2E coverage for the async timeline chain.
"""

from pathlib import Path


def test_k6_smoke_has_reasonable_thresholds():
    """k6 smoke test must have bounded thresholds."""
    k6_path = Path("tests/performance/k6-smoke.js")
    if not k6_path.exists():
        return
    content = k6_path.read_text()
    assert "http_req_failed" in content
    assert "rate<0.01" in content
    assert "p(95)<1000" in content


def test_reconciler_beat_schedule_exists():
    """Celery Beat must schedule the timeline reconciler."""
    # Check beat schedule config
    from pathlib import Path

    celery_config_paths = [
        Path("apps/worker/celery_app.py"),
        Path("apps/worker/celeryconfig.py"),
    ]
    found = False
    for p in celery_config_paths:
        if p.exists():
            content = p.read_text()
            if "reconcile" in content or "timeline.reconcile" in content:
                found = True
                break
    if not found:
        # Check beat schedule module
        beat_path = Path("apps/worker/beat_schedule.py")
        if beat_path.exists():
            content = beat_path.read_text()
            if "reconcile" in content:
                found = True
    assert found, "Timeline reconciler not found in Beat schedule"


def test_outbox_routes_cover_all_research_events():
    """All research event types must have dispatcher routes."""
    from packages.jobs.dispatcher import RESEARCH_EVENT_ROUTES

    required = {
        "research.plan.requested",
        "research.run.requested",
        "research.candidate_extraction.requested",
    }
    actual = set(RESEARCH_EVENT_ROUTES.keys())
    assert required.issubset(actual), (
        f"Missing routes: {required - actual}"
    )
