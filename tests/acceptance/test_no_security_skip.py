"""Recovery tests must not be skipped in CI or release gate."""

import pytest


def test_required_recovery_mode_fails_when_tool_missing(monkeypatch):
    """When IRIP_REQUIRE_RECOVERY_TESTS=1, missing tools should fail not skip."""
    import shutil

    monkeypatch.setenv("IRIP_REQUIRE_RECOVERY_TESTS", "1")
    monkeypatch.setattr(shutil, "which", lambda _: None)

    # Import the conftest validation function
    from tests.recovery.conftest import validate_recovery_prerequisites

    with pytest.raises(pytest.UsageError):
        validate_recovery_prerequisites()


def test_developer_mode_may_skip(monkeypatch):
    """Without IRIP_REQUIRE_RECOVERY_TESTS, missing tools may skip with a reason."""
    import shutil

    monkeypatch.delenv("IRIP_REQUIRE_RECOVERY_TESTS", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)

    from tests.recovery.conftest import validate_recovery_prerequisites

    # Should not raise, but return False (skip allowed)
    result = validate_recovery_prerequisites()
    assert result is False
