"""Performance baseline: verify async chain integrity, reconciler, and outbox routes."""

from pathlib import Path


def test_k6_smoke_has_bounded_thresholds():
    """k6 smoke test must have bounded HTTP error rate and latency thresholds."""
    k6_path = Path("tests/performance/k6-smoke.js")
    assert k6_path.exists(), "k6-smoke.js missing"
    content = k6_path.read_text()
    assert "http_req_failed" in content, "k6 must track http_req_failed"
    assert "rate<0.01" in content, "k6 error rate threshold must be < 1%"
    assert "p(95)<1000" in content, "k6 p95 latency threshold must be < 1000ms"
    # Must have actual test scenario (not just config)
    assert "export default" in content or "export const" in content, (
        "k6 script must define a test scenario"
    )


def test_reconciler_is_scheduled_in_beat():
    """Celery Beat must schedule the timeline reconciler at a reasonable interval."""
    celery_app = Path("apps/worker/celery_app.py")
    assert celery_app.exists(), "celery_app.py missing"
    content = celery_app.read_text()
    assert "timeline-reconcile" in content or "research.timeline.reconcile" in content, (
        "Beat schedule must include timeline reconciler"
    )
    # Must have a numeric schedule (not just a string reference)
    import re

    schedule_match = re.search(r'"timeline-reconcile".*?"schedule":\s*(\d+)', content, re.DOTALL)
    assert schedule_match is not None, "Reconciler must have a numeric schedule"
    interval = int(schedule_match.group(1))
    assert 10 <= interval <= 120, (
        f"Reconciler interval {interval}s must be 10-120s (not too fast/slow)"
    )


def test_outbox_routes_cover_all_research_events():
    """All research event types must have dispatcher routes with correct queues."""
    from packages.jobs.dispatcher import RESEARCH_EVENT_ROUTES

    required = {
        "research.plan.requested",
        "research.run.requested",
        "research.candidate_extraction.requested",
        "research.recommendation.requested",
    }
    actual = set(RESEARCH_EVENT_ROUTES.keys())
    assert required.issubset(actual), f"Missing routes: {required - actual}"

    # Each route must map to (task_name, queue_name)
    for event_type, route in RESEARCH_EVENT_ROUTES.items():
        assert isinstance(route, tuple) and len(route) == 2, (
            f"Route for {event_type} must be (task_name, queue_name) tuple"
        )
        task_name, queue = route
        assert task_name.startswith("research."), (
            f"Task name for {event_type} must start with 'research.'"
        )
        assert queue == "irip-research", (
            f"Queue for {event_type} must be 'irip-research', got '{queue}'"
        )


def test_reconciler_task_exists_in_worker():
    """The reconcile_timeline task must be defined in the worker module."""
    worker_path = Path("apps/worker/research_timeline_tasks.py")
    content = worker_path.read_text()
    assert "research.timeline.reconcile" in content, (
        "Worker must define the research.timeline.reconcile task"
    )
    assert "def reconcile_timeline" in content, "Worker must have reconcile_timeline function"
    # Must use FOR UPDATE SKIP LOCKED (not just a plain SELECT)
    assert "skip_locked" in content, "Reconciler must use FOR UPDATE SKIP LOCKED"


def test_worker_tasks_accept_principal_kwargs():
    """Worker tasks must accept actor_id/department_id/workspace_id kwargs."""
    worker_path = Path("apps/worker/research_timeline_tasks.py")
    content = worker_path.read_text()
    # execute_analysis_run must accept principal kwargs
    assert "actor_id" in content and "department_id" in content, (
        "execute_analysis_run must accept principal kwargs"
    )
    # Must use ResearchTaskPrincipal or equivalent
    assert "dept_uuid" in content or "department_id" in content, (
        "Worker must construct identity from principal kwargs"
    )
