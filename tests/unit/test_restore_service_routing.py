"""RestoreService 版本路由 + 引用完整性校验单元测试（PITR 升级）。

验证 deployments/compose/restore.py 的 PITR 恢复功能：
- RestoreConfig 新增 recovery_target_time + minio_mc_alias 字段；
- restore() 根据 manifest.format_version 分流到 _restore_v1 / _restore_v2；
- _restore_v2 按 MinIO→PG 顺序恢复；
- _validate_referential_integrity 检查 MinIO 对象存在性；
- run_restore 传递 recovery_target_time。

所有外部依赖（docker compose / mc / PG / MinIO）均 mock。
对应 docs/arch-db-backup-pitr-upgrade.md §1.6 / §1.7。
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deployments.compose.backup_manifest import (
    BASE_TAR_GZ_FILENAME,
    MINIO_MIRROR_DIRNAME,
    PG_BASEBACKUP_DIRNAME,
    PG_WAL_TAR_GZ_FILENAME,
    compute_manifest_v2,
    save_manifest,
)
from deployments.compose.restore import RestoreConfig, RestoreService, run_restore

# ============================================================
# RestoreConfig 新增字段
# ============================================================


class TestRestoreConfigPitrFields:
    """RestoreConfig 新增 PITR 配置字段测试。"""

    def test_recovery_target_time_default_none(self) -> None:
        """recovery_target_time 默认为 None。"""
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
        """minio_mc_alias 默认为 'irip'。"""
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
        """可设置自定义 recovery_target_time。"""
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


# ============================================================
# 版本路由测试
# ============================================================


class TestRestoreVersionRouting:
    """RestoreService.restore() 版本路由测试。"""

    def _make_v1_backup_dir(self, tmp_path: Path) -> Path:
        """构造 v1 格式备份目录（含 manifest.json format_version=1）。"""
        backup_dir = tmp_path / "v1-backup"
        backup_dir.mkdir()
        manifest = {
            "format_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "application_version": "0.1.0",
            "migration_version": "0060",
            "database_sha256": "abc123",
            "object_count": 0,
            "objects_sha256": "def456",
            "encrypted": False,
            "backup_id": "v1-backup-id",
            "extra": {},
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        return backup_dir

    def _make_v2_backup_dir(self, tmp_path: Path) -> Path:
        """构造 v2 格式备份目录（含 manifest.json format_version=2）。"""
        backup_dir = tmp_path / "v2-backup"
        backup_dir.mkdir()
        pg_dir = backup_dir / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir()
        (pg_dir / BASE_TAR_GZ_FILENAME).write_bytes(b"base-content")
        (pg_dir / PG_WAL_TAR_GZ_FILENAME).write_bytes(b"wal-content")
        mirror_dir = backup_dir / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()

        manifest = compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
            backup_id="v2-backup-id",
            backup_timestamp="2026-08-16T02:00:00.000+00:00",
            wal_start_lsn="0/2000000",
            wal_end_lsn="0/2000123",
        )
        save_manifest(manifest, backup_dir)
        return backup_dir

    def _make_service(self, backup_dir: Path, **kwargs) -> RestoreService:
        """构造 RestoreService 实例。"""
        config = RestoreConfig(
            backup_dir=backup_dir,
            db_url="postgresql+psycopg://irip:pass@localhost/5432/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            skip_migrations=True,  # 跳过迁移避免实际 alembic 调用
            **kwargs,
        )
        return RestoreService(config)

    @pytest.mark.asyncio
    async def test_v1_routes_to_restore_v1(self, tmp_path: Path) -> None:
        """format_version=1 走 _restore_v1 路径。"""
        backup_dir = self._make_v1_backup_dir(tmp_path)
        service = self._make_service(backup_dir)

        with patch.object(service, "_restore_v1", new_callable=AsyncMock) as mock_v1:
            mock_v1.return_value = MagicMock(format_version=1)
            await service.restore()

        mock_v1.assert_called_once()

    @pytest.mark.asyncio
    async def test_v2_routes_to_restore_v2(self, tmp_path: Path) -> None:
        """format_version=2 走 _restore_v2 路径。"""
        backup_dir = self._make_v2_backup_dir(tmp_path)
        service = self._make_service(backup_dir)

        with patch.object(service, "_restore_v2", new_callable=AsyncMock) as mock_v2:
            # mock v2 内部的实际恢复步骤
            mock_v2.return_value = MagicMock(
                format_version=2, extra={"backup_timestamp": "2026-08-16T02:00:00.000+00:00"}
            )
            # 但 _restore_v2 内部会调用 validate，需要 mock 验证器
            with patch.object(service._validator, "validate", return_value=True):
                await service.restore()

        mock_v2.assert_called_once()

    @pytest.mark.asyncio
    async def test_unsupported_version_raises(self, tmp_path: Path) -> None:
        """不支持的 manifest 版本抛出 RuntimeError。"""
        backup_dir = tmp_path / "bad-backup"
        backup_dir.mkdir()
        manifest = {
            "format_version": 99,
            "created_at": datetime.now(UTC).isoformat(),
            "application_version": "0.1.0",
            "migration_version": "0061",
            "database_sha256": "",
            "object_count": 0,
            "objects_sha256": "",
            "encrypted": False,
            "backup_id": "",
            "extra": {},
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

        service = self._make_service(backup_dir)

        with pytest.raises(RuntimeError, match="不支持的 manifest 版本"):
            await service.restore()

    @pytest.mark.asyncio
    async def test_missing_backup_dir_raises(self, tmp_path: Path) -> None:
        """备份目录不存在时抛出 FileNotFoundError。"""
        service = self._make_service(tmp_path / "nonexistent")

        with pytest.raises(FileNotFoundError):
            await service.restore()


# ============================================================
# _restore_v2 恢复顺序测试（MinIO 先 → PG 后）
# ============================================================


class TestRestoreV2Order:
    """_restore_v2 恢复顺序测试：MinIO 先 → PG 后。"""

    def _make_v2_backup_dir(self, tmp_path: Path) -> Path:
        """构造 v2 格式备份目录。"""
        backup_dir = tmp_path / "v2-backup"
        backup_dir.mkdir()
        pg_dir = backup_dir / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir()
        (pg_dir / BASE_TAR_GZ_FILENAME).write_bytes(b"base-content")
        (pg_dir / PG_WAL_TAR_GZ_FILENAME).write_bytes(b"wal-content")
        mirror_dir = backup_dir / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()

        manifest = compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
            backup_id="v2-backup-id",
            backup_timestamp="2026-08-16T02:00:00.000+00:00",
        )
        save_manifest(manifest, backup_dir)
        return backup_dir

    @pytest.mark.asyncio
    async def test_minio_restored_before_pg(self, tmp_path: Path) -> None:
        """_restore_v2 先恢复 MinIO 再恢复 PG。"""
        backup_dir = self._make_v2_backup_dir(tmp_path)
        config = RestoreConfig(
            backup_dir=backup_dir,
            db_url="postgresql+psycopg://irip:pass@localhost/5432/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            skip_migrations=True,
        )
        service = RestoreService(config)

        call_order: list[str] = []

        def track_mc_restore(minio_dir: Path) -> None:
            call_order.append("mc_restore")

        def track_pitr_restore(basebackup_dir: Path, recovery_target_time: str) -> None:
            call_order.append("pitr_restore")

        async def fake_smoke() -> dict[str, int]:
            call_order.append("smoke")
            return {"app_user": 1, "alembic_version": 1}

        async def fake_referential() -> None:
            call_order.append("referential")

        with patch.object(service, "_mc_restore_minio", side_effect=track_mc_restore):
            with patch.object(service, "_pitr_restore", side_effect=track_pitr_restore):
                with patch.object(service, "_run_smoke_queries", side_effect=fake_smoke):
                    with patch.object(
                        service, "_validate_referential_integrity", side_effect=fake_referential
                    ):
                        with patch.object(service._validator, "validate", return_value=True):
                            await service.restore()

        # MinIO 恢复应在 PG 恢复之前
        assert call_order.index("mc_restore") < call_order.index("pitr_restore")
        # 冒烟查询在 PG 恢复之后
        assert call_order.index("pitr_restore") < call_order.index("smoke")
        # 引用完整性校验在冒烟查询之后
        assert call_order.index("smoke") < call_order.index("referential")

    @pytest.mark.asyncio
    async def test_v2_uses_config_recovery_target_time(self, tmp_path: Path) -> None:
        """_restore_v2 优先使用配置中的 recovery_target_time。"""
        backup_dir = self._make_v2_backup_dir(tmp_path)
        config = RestoreConfig(
            backup_dir=backup_dir,
            db_url="postgresql+psycopg://irip:pass@localhost:5432/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            skip_migrations=True,
            recovery_target_time="2026-08-16T10:30:00+00:00",
        )
        service = RestoreService(config)

        captured_target_time: list[str] = []

        def track_pitr(basebackup_dir: Path, recovery_target_time: str) -> None:
            captured_target_time.append(recovery_target_time)

        async def fake_smoke() -> dict[str, int]:
            return {"app_user": 1, "alembic_version": 1}

        async def fake_referential() -> None:
            pass

        with patch.object(service, "_mc_restore_minio"):
            with patch.object(service, "_pitr_restore", side_effect=track_pitr):
                with patch.object(service, "_run_smoke_queries", side_effect=fake_smoke):
                    with patch.object(
                        service, "_validate_referential_integrity", side_effect=fake_referential
                    ):
                        with patch.object(service._validator, "validate", return_value=True):
                            await service.restore()

        assert captured_target_time[0] == "2026-08-16T10:30:00+00:00"

    @pytest.mark.asyncio
    async def test_v2_falls_back_to_backup_timestamp(self, tmp_path: Path) -> None:
        """未配置 recovery_target_time 时回退到 manifest 中的 backup_timestamp。"""
        backup_dir = self._make_v2_backup_dir(tmp_path)
        config = RestoreConfig(
            backup_dir=backup_dir,
            db_url="postgresql+psycopg://irip:pass@localhost:5432/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            skip_migrations=True,
            # 不设置 recovery_target_time
        )
        service = RestoreService(config)

        captured_target_time: list[str] = []

        def track_pitr(basebackup_dir: Path, recovery_target_time: str) -> None:
            captured_target_time.append(recovery_target_time)

        async def fake_smoke() -> dict[str, int]:
            return {"app_user": 1, "alembic_version": 1}

        async def fake_referential() -> None:
            pass

        with patch.object(service, "_mc_restore_minio"):
            with patch.object(service, "_pitr_restore", side_effect=track_pitr):
                with patch.object(service, "_run_smoke_queries", side_effect=fake_smoke):
                    with patch.object(
                        service, "_validate_referential_integrity", side_effect=fake_referential
                    ):
                        with patch.object(service._validator, "validate", return_value=True):
                            await service.restore()

        # 应回退到 backup_timestamp
        assert captured_target_time[0] == "2026-08-16T02:00:00.000+00:00"


# ============================================================
# 引用完整性校验测试
# ============================================================


class TestReferentialIntegrity:
    """_validate_referential_integrity() 引用完整性校验测试。"""

    def _make_service(self, tmp_path: Path) -> RestoreService:
        """构造 RestoreService 实例。"""
        config = RestoreConfig(
            backup_dir=tmp_path,
            db_url="postgresql+psycopg://irip:pass@localhost:5432/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
        )
        return RestoreService(config)

    @pytest.mark.asyncio
    async def test_all_objects_exist_passes(self, tmp_path: Path) -> None:
        """所有 storage_key 对应的 MinIO 对象存在时校验通过。"""
        service = self._make_service(tmp_path)

        # mock DB 查询返回 storage_keys
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("key1",), ("key2",)]
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch("sqlalchemy.create_engine", return_value=mock_engine):
            # mock S3 head_object 不抛异常（对象存在）
            service._s3.head_object = MagicMock()
            await service._validate_referential_integrity()

        service._s3.head_object.assert_any_call("key1")
        service._s3.head_object.assert_any_call("key2")

    @pytest.mark.asyncio
    async def test_missing_object_raises(self, tmp_path: Path) -> None:
        """任一 storage_key 对应的 MinIO 对象缺失时抛出 RuntimeError。"""
        service = self._make_service(tmp_path)

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("key1",), ("missing_key",)]
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        def fake_head(key: str) -> None:
            if key == "missing_key":
                raise Exception("404 Not Found")

        with patch("sqlalchemy.create_engine", return_value=mock_engine):
            service._s3.head_object = MagicMock(side_effect=fake_head)
            with pytest.raises(RuntimeError, match="引用完整性校验失败"):
                await service._validate_referential_integrity()

    @pytest.mark.asyncio
    async def test_no_storage_keys_skips(self, tmp_path: Path) -> None:
        """无 storage_key 时跳过校验。"""
        service = self._make_service(tmp_path)

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch("sqlalchemy.create_engine", return_value=mock_engine):
            service._s3.head_object = MagicMock()
            await service._validate_referential_integrity()

        # 无 storage_key 时不应调用 head_object
        service._s3.head_object.assert_not_called()


# ============================================================
# run_restore 传递 recovery_target_time 测试
# ============================================================


class TestRunRestorePassesRecoveryTargetTime:
    """run_restore() 传递 recovery_target_time 测试。"""

    @pytest.mark.asyncio
    async def test_run_restore_passes_recovery_target_time(self, tmp_path: Path) -> None:
        """run_restore 将 recovery_target_time 传入 RestoreConfig。"""
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        manifest = {
            "format_version": 2,
            "created_at": datetime.now(UTC).isoformat(),
            "application_version": "0.1.0",
            "migration_version": "0061",
            "database_sha256": "",
            "object_count": 0,
            "objects_sha256": "",
            "encrypted": False,
            "backup_id": "",
            "extra": {"backup_timestamp": "2026-08-16T02:00:00.000+00:00"},
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

        target_time = "2026-08-16T10:30:00+00:00"

        with patch.dict(
            os.environ,
            {
                "IRIP_DATABASE_URL": "postgresql+psycopg://irip:pass@localhost:5432/irip",
                "IRIP_MINIO_ENDPOINT": "http://localhost:9000",
                "IRIP_MINIO_ACCESS_KEY": "irip",
                "IRIP_MINIO_SECRET_KEY": "pass",
                "IRIP_MINIO_BUCKET": "irip-artifacts",
                "IRIP_MINIO_REGION": "us-east-1",
            },
        ):
            with patch("deployments.compose.restore.RestoreService") as mock_service_cls:
                mock_service = MagicMock()
                mock_service.restore = AsyncMock()
                mock_service_cls.return_value = mock_service

                await run_restore(backup_dir, recovery_target_time=target_time)

                # 验证 RestoreService 初始化时 config 含 recovery_target_time
                config = mock_service_cls.call_args[0][0]
                assert config.recovery_target_time == target_time

    @pytest.mark.asyncio
    async def test_run_restore_none_recovery_target_time(self, tmp_path: Path) -> None:
        """run_restore 不传 recovery_target_time 时 config.recovery_target_time 为 None。"""
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        manifest = {
            "format_version": 2,
            "created_at": datetime.now(UTC).isoformat(),
            "application_version": "0.1.0",
            "migration_version": "0061",
            "database_sha256": "",
            "object_count": 0,
            "objects_sha256": "",
            "encrypted": False,
            "backup_id": "",
            "extra": {},
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

        with patch.dict(
            os.environ,
            {
                "IRIP_DATABASE_URL": "postgresql+psycopg://irip:pass@localhost:5432/irip",
                "IRIP_MINIO_ENDPOINT": "http://localhost:9000",
                "IRIP_MINIO_ACCESS_KEY": "irip",
                "IRIP_MINIO_SECRET_KEY": "pass",
                "IRIP_MINIO_BUCKET": "irip-artifacts",
                "IRIP_MINIO_REGION": "us-east-1",
            },
        ):
            with patch("deployments.compose.restore.RestoreService") as mock_service_cls:
                mock_service = MagicMock()
                mock_service.restore = AsyncMock()
                mock_service_cls.return_value = mock_service

                await run_restore(backup_dir)

                config = mock_service_cls.call_args[0][0]
                assert config.recovery_target_time is None
