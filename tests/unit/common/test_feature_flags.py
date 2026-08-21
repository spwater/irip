"""Unit tests for feature flags: high-risk entry defaults and guard behavior.

Validates that RESEARCH_ANALYSIS_ENABLED and LEGACY_MODEL_EXECUTION_ENABLED
default to False (fail-closed / safe-by-default), and that
require_feature_enabled() raises AppError(code=feature_disabled) when a
flag is off.
"""

import importlib

import pytest


@pytest.fixture(autouse=True)
def _restore_feature_flags(monkeypatch):
    """Restore feature_flags module to default state after each test.

    Reloading feature_flags in tests mutates the module object in-place.
    This fixture ensures that after each test the module is reloaded with
    the original environment (monkeypatch auto-cleans env vars), so the
    default values are restored for subsequent tests in the same session.
    """
    yield
    import packages.common.feature_flags as ff

    importlib.reload(ff)


def test_high_risk_flags_default_disabled(monkeypatch):
    """RESEARCH_ANALYSIS_ENABLED and LEGACY_MODEL_EXECUTION_ENABLED default to False."""
    monkeypatch.delenv("RESEARCH_ANALYSIS_ENABLED", raising=False)
    monkeypatch.delenv("LEGACY_MODEL_EXECUTION_ENABLED", raising=False)
    from packages.common import feature_flags

    module = importlib.reload(feature_flags)
    assert module.RESEARCH_ANALYSIS_ENABLED is False
    assert module.LEGACY_MODEL_EXECUTION_ENABLED is False


def test_high_risk_flags_enabled_via_env(monkeypatch):
    """Setting env vars to 'true' enables the high-risk flags."""
    monkeypatch.setenv("RESEARCH_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("LEGACY_MODEL_EXECUTION_ENABLED", "true")
    from packages.common import feature_flags

    module = importlib.reload(feature_flags)
    assert module.RESEARCH_ANALYSIS_ENABLED is True
    assert module.LEGACY_MODEL_EXECUTION_ENABLED is True


def test_require_feature_enabled_raises_when_disabled():
    """require_feature_enabled raises AppError(feature_disabled) when flag is False."""
    from packages.common.errors import AppError
    from packages.common.feature_flags import require_feature_enabled

    with pytest.raises(AppError) as exc_info:
        require_feature_enabled(False, "research_analysis")

    assert exc_info.value.code == "feature_disabled"
    assert exc_info.value.retryable is True
    assert "research_analysis" in exc_info.value.message


def test_require_feature_enabled_passes_when_enabled():
    """require_feature_enabled does not raise when flag is True."""
    from packages.common.feature_flags import require_feature_enabled

    # Should not raise
    require_feature_enabled(True, "research_analysis")
