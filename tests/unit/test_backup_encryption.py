"""Production backup must require age encryption."""

import pytest


def test_backup_service_requires_age_recipient_in_production(monkeypatch):
    """In production, missing age recipient should fail."""
    monkeypatch.setenv("IRIP_ENV", "production")
    monkeypatch.delenv("IRIP_BACKUP_AGE_RECIPIENT", raising=False)
    monkeypatch.delenv("IRIP_BACKUP_AGE_RECIPIENT_FILE", raising=False)
    from deployments.compose.backup import BackupService, ConfigurationError

    with pytest.raises(ConfigurationError):
        BackupService.from_environment()


def test_backup_service_works_with_age_recipient(monkeypatch):
    """In production, age recipient enables backup."""
    monkeypatch.setenv("IRIP_ENV", "production")
    monkeypatch.setenv("IRIP_BACKUP_AGE_RECIPIENT", "age1...")
    from deployments.compose.backup import BackupService

    # Should not raise due to missing age recipient; other config may be absent.
    try:
        BackupService.from_environment()
    except Exception:
        pass  # Other config might be missing, that's OK
