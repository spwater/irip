"""Research timeline observation metrics (low-cardinality counters).

These metrics are designed for Prometheus-style scraping with low cardinality.
They track research turn/run lifecycle states without exposing user data.
"""

from __future__ import annotations

import logging
from collections import Counter

logger = logging.getLogger("research.metrics")

# In-memory counters (production would use prometheus_client)
_turn_status_counter: Counter[str] = Counter()
_run_completion_seconds: list[float] = []

METRIC_NAMES = frozenset(
    {
        "research_turns_by_status",
        "research_run_completion_seconds",
        "research_extraction_by_status",
        "research_reconciler_requeued_total",
        "research_reconciler_marked_lost_total",
    }
)


def record_turn_status(status: str) -> None:
    """Record a turn status transition."""
    _turn_status_counter[status] += 1


def record_run_completion(duration_seconds: float) -> None:
    """Record run completion duration."""
    _run_completion_seconds.append(duration_seconds)
    # Keep only last 1000 samples
    if len(_run_completion_seconds) > 1000:
        del _run_completion_seconds[:-1000]


def get_metrics_snapshot() -> dict[str, object]:
    """Get a snapshot of all metrics for monitoring."""
    return {
        "research_turns_by_status": dict(_turn_status_counter),
        "research_run_completion_seconds_count": len(_run_completion_seconds),
        "research_run_completion_seconds_p95": (
            sorted(_run_completion_seconds)[int(len(_run_completion_seconds) * 0.95)]
            if _run_completion_seconds
            else 0
        ),
    }
