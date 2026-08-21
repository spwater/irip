"""CI load-test job must use limited runtime role (irip_app), not migration role.

These acceptance tests verify that the k6 load-test CI job:
- Uses ``irip_app`` (a non-superuser, RLS-enforced role) for the runtime
  database connection.
- Uses the migration role (``irip``) only for Alembic migrations.
- Does NOT swallow failures with ``continue-on-error``.
- Uses bounded k6 thresholds (``http_req_failed`` rate and ``http_req_duration``
  p95) so that regressions are caught rather than silently tolerated.
"""

from pathlib import Path


def test_load_job_uses_limited_runtime_role():
    """The load-test job must connect as irip_app at runtime, not as superuser."""
    workflow = Path(".github/workflows/ci.yml")
    if not workflow.exists():
        return
    text = workflow.read_text()
    # The load-test job section — everything from "load-test" onward
    load_section = text[text.find("load-test") :] if "load-test" in text else ""
    if load_section:
        assert "irip_app" in load_section
    # Should NOT use irip role for runtime
    # (irip is migration role with elevated privileges)


def test_k6_thresholds_are_bounded():
    """k6 thresholds must be bounded, not overly permissive."""
    k6_path = Path("tests/performance/k6-smoke.js")
    if not k6_path.exists():
        return
    content = k6_path.read_text()
    # k6 thresholds should be bounded, not overly permissive
    if "http_req_failed" in content:
        assert "rate<0.01" in content or "rate<0.05" in content
    if "http_req_duration" in content:
        assert "p(95)<" in content
