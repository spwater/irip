"""RestoreService config field tests.

RestoreService was refactored to pure-phase functions (P1-Deploy-T6).
The old restore() / _restore_v1 / _restore_v2 methods no longer exist.
These tests verify RestoreConfig fields only.
"""

from pathlib import Path

from deployments.compose.restore import RestoreConfig

# ============================================================
# RestoreConfig PITR fields
# ============================================================


class TestRestoreConfigPitrFields:
    """RestoreConfig PITR config field tests."""

    def test_recovery_target_time_default_none(self) -> None:
        """recovery_target_time defaults to None."""
        config = RestoreConfig(
            backup_dir=Path("/backups/test"),
            db_url="postgresql://irip:pass@localhost/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
        )
        assert config.recovery_target_time is None

    def test_minio_mc_alias_default_irip(self) -> None:
        """minio_mc_alias defaults to 'irip'."""
        config = RestoreConfig(
            backup_dir=Path("/backups/test"),
            db_url="postgresql://irip:pass@localhost/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
        )
        assert config.minio_mc_alias == "irip"

    def test_custom_recovery_target_time(self) -> None:
        """Can set custom recovery_target_time."""
        config = RestoreConfig(
            backup_dir=Path("/backups/test"),
            db_url="postgresql://irip:pass@localhost/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            recovery_target_time="2026-08-16T10:30:00+00:00",
        )
        assert config.recovery_target_time == "2026-08-16T10:30:00+00:00"

    def test_age_identity_default_none(self) -> None:
        """age_identity defaults to None."""
        config = RestoreConfig(
            backup_dir=Path("/backups/test"),
            db_url="postgresql://irip:pass@localhost/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
        )
        assert config.age_identity is None

    def test_skip_migrations_default_false(self) -> None:
        """skip_migrations defaults to False."""
        config = RestoreConfig(
            backup_dir=Path("/backups/test"),
            db_url="postgresql://irip:pass@localhost/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
        )
        assert config.skip_migrations is False

    def test_can_set_skip_migrations(self) -> None:
        """Can set skip_migrations to True."""
        config = RestoreConfig(
            backup_dir=Path("/backups/test"),
            db_url="postgresql://irip:pass@localhost/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            skip_migrations=True,
        )
        assert config.skip_migrations is True
