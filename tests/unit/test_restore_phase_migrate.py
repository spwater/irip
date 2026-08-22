"""RestoreService.phase_migrate 单元测试（阶段2 database/migrate 拆分）。

验证「需活 PG」的逻辑恢复（v1 pg_restore）与前向迁移从 phase_database 拆出后，
phase_migrate 的 v1/v2 分支与 skip_migrations 语义：
- v1：无条件执行 _restore_database（pg_restore，非"迁移"，skip 不跳过）；
- v1/v2：_apply_forward_migrations 仅在未 skip_migrations 时执行。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deployments.compose.restore import VALID_PHASES, RestoreConfig, RestoreService


def _make_service(skip_migrations: bool = False) -> RestoreService:
    """构建最小 RestoreService（不触发网络/S3 连接）。"""
    config = RestoreConfig(
        backup_dir=Path("/backups/test"),
        db_url="postgresql+psycopg://irip:pass@postgres:5432/irip",
        minio_endpoint="http://localhost:9000",
        minio_access_key="irip",
        minio_secret_key="pass",
        minio_bucket="irip-artifacts",
        minio_region="us-east-1",
        skip_migrations=skip_migrations,
    )
    return RestoreService(config)


def _make_manifest(format_version: int) -> MagicMock:
    manifest = MagicMock()
    manifest.backup_id = "backup-id"
    manifest.format_version = format_version
    manifest.migration_version = "0061"
    return manifest


class TestValidatePhasesOrder:
    def test_migrate_present_and_ordered(self) -> None:
        """VALID_PHASES 含 migrate，且顺序为 database → migrate → objects。"""
        assert "migrate" in VALID_PHASES
        assert VALID_PHASES.index("migrate") == VALID_PHASES.index("database") + 1
        assert VALID_PHASES.index("migrate") < VALID_PHASES.index("objects")


class TestPhaseMigrate:
    @pytest.mark.asyncio
    async def test_v1_restores_database_and_migrates(self) -> None:
        """v1 + 未 skip：pg_restore 无条件 + alembic 迁移。"""
        service = _make_service(skip_migrations=False)
        manifest = _make_manifest(1)
        with patch.object(service, "_load_and_cache_manifest", return_value=manifest):
            with patch.object(service, "_restore_database") as restore:
                with patch.object(service, "_apply_forward_migrations") as migrate:
                    await service.phase_migrate()
        restore.assert_called_once()
        migrate.assert_called_once_with("0061")

    @pytest.mark.asyncio
    async def test_v1_skip_migrations_still_restores(self) -> None:
        """v1 + skip：仍执行 pg_restore，跳过 alembic。"""
        service = _make_service(skip_migrations=True)
        manifest = _make_manifest(1)
        with patch.object(service, "_load_and_cache_manifest", return_value=manifest):
            with patch.object(service, "_restore_database") as restore:
                with patch.object(service, "_apply_forward_migrations") as migrate:
                    await service.phase_migrate()
        restore.assert_called_once()
        migrate.assert_not_called()

    @pytest.mark.asyncio
    async def test_v2_migrates_only(self) -> None:
        """v2 + 未 skip：不碰 pg_restore，仅 alembic 迁移。"""
        service = _make_service(skip_migrations=False)
        manifest = _make_manifest(2)
        with patch.object(service, "_load_and_cache_manifest", return_value=manifest):
            with patch.object(service, "_restore_database") as restore:
                with patch.object(service, "_apply_forward_migrations") as migrate:
                    await service.phase_migrate()
        restore.assert_not_called()
        migrate.assert_called_once_with("0061")

    @pytest.mark.asyncio
    async def test_v2_skip_migrations_is_noop(self) -> None:
        """v2 + skip：既不 pg_restore 也不迁移。"""
        service = _make_service(skip_migrations=True)
        manifest = _make_manifest(2)
        with patch.object(service, "_load_and_cache_manifest", return_value=manifest):
            with patch.object(service, "_restore_database") as restore:
                with patch.object(service, "_apply_forward_migrations") as migrate:
                    await service.phase_migrate()
        restore.assert_not_called()
        migrate.assert_not_called()
