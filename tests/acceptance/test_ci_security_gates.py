"""CI security gates must be blocking."""
from pathlib import Path


def test_no_continue_on_error_in_security_jobs():
    workflow = Path(".github/workflows/ci.yml")
    if not workflow.exists():
        return
    text = workflow.read_text()
    assert "continue-on-error: true" not in text


def test_no_pipe_true_in_security_commands():
    workflow = Path(".github/workflows/ci.yml")
    if not workflow.exists():
        return
    text = workflow.read_text()
    assert "|| true" not in text


def test_semgrep_rules_exist():
    assert Path("security/semgrep.yml").exists()
    content = Path("security/semgrep.yml").read_text()
    assert "no-pickle-loads" in content
    assert "no-joblib-load" in content
    assert "no-build-session-factory-in-routers" in content
    assert "no-hardcoded-admin" in content
