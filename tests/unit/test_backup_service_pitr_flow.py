"""BackupService PITR 联合备份流程单元测试。

验证 deployments/compose/backup.py 的 PITR 备份流程：
- BackupConfig 新增 mc/pitr 配置字段；
- backup() 方法生成联合时间戳并调用 _basebackup + _mc_mirror_minio；
- _basebackup 调用 pg_basebackup 并记录 WAL LSN；
- _mc_mirror_minio 调用 mc mirror；
- compute_manifest_v2 生成 v2 manifest；
- build_backup_config_from_env 读取新环境变量。

所有外部命令（pg_basebackup / mc / SQL 查询）均 mock，不依赖真实 PG/MinIO。
对应 docs/arch-db-backup-pitr-upgrade.md §1.5。
"""

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deployments.compose.backup import (
    BackupConfig,
    BackupService,
    build_backup_config_from_env,
)


# ============================================================
# BackupConfig 新增字段
# ============================================================


class TestBackupConfigPitrFields:
    """BackupConfig 新增 PITR/mc 配置字段测试。"""

    def test_minio_mc_alias_default(self) -> None:
        """minio_mc_alias 默认值为 'irip'。"""
        config = BackupConfig(
            db_url="postgresql://irip:pass@localhost/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            application_version="0.1.0",
            output_dir=Path("/tmp/backups"),
        )
        assert config.minio_mc_alias == "irip"

    def test_minio_mirror_exclude_default_none(self) -> None:
        """minio_mirror_exclude 默认为 None。"""
        config = BackupConfig(
            db_url="postgresql://irip:pass@localhost/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            application_version="0.1.0",
            output_dir=Path("/tmp/backups"),
        )
        assert config.minio_mirror_exclude is None

    def test_pg_replication_slot_default_none(self) -> None:
        """pg_replication_slot 默认为 None。"""
        config = BackupConfig(
            db_url="postgresql://irip:pass@localhost/irip",
            minio_endpoint="http://localhost:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            application_version="0.1.0",
            output_dir=Path("/tmp/backups"),
        )
        assert config.pg_replication_slot is None

    def test_custom_pitr_config(self) -> None:
        """可设置自定义 PITR 配置。"""
        config = BackupConfig(
            db_url="postgresql://irip:pass@localhost/irip",
            minio_endpoint="http://minio:9000",
            minio_access_key="key",
            minio_secret_key="secret",
            minio_bucket="bucket",
            minio_region="us-east-1",
            application_version="0.1.0",
            output_dir=Path("/backups"),
            minio_mc_alias="custom-alias",
            minio_mirror_exclude="tmp/*",
            pg_replication_slot="irip_backup_slot",
        )
        assert config.minio_mc_alias == "custom-alias"
        assert config.minio_mirror_exclude == "tmp/*"
        assert config.pg_replication_slot == "irip_backup_slot"


# ============================================================
# build_backup_config_from_env
# ============================================================


class TestBuildBackupConfigFromEnv:
    """build_backup_config_from_env 读取 PITR 环境变量测试。"""

    def test_reads_minio_mc_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """从 IRIP_MINIO_MC_ALIAS 读取 mc alias。"""
        monkeypatch.setenv("IRIP_DATABASE_URL", "postgresql://irip:pass@localhost/irip")
        monkeypatch.setenv("IRIP_MINIO_MC_ALIAS", "my-alias")
        config = build_backup_config_from_env()
        assert config.minio_mc_alias == "my-alias"

    def test_reads_pg_replication_slot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """从 IRIP_PG_REPLICATION_SLOT 读取复制槽名。"""
        monkeypatch.setenv("IRIP_DATABASE_URL", "postgresql://irip:pass@localhost/irip")
        monkeypatch.setenv("IRIP_PG_REPLICATION_SLOT", "backup_slot")
        config = build_backup_config_from_env()
        assert config.pg_replication_slot == "backup_slot"

    def test_reads_minio_mirror_exclude(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """从 IRIP_MINIO_MIRROR_EXCLUDE 读取排除规则。"""
        monkeypatch.setenv("IRIP_DATABASE_URL", "postgresql://irip:pass@localhost/irip")
        monkeypatch.setenv("IRIP_MINIO_MIRROR_EXCLUDE", "tmp/*")
        config = build_backup_config_from_env()
        assert config.minio_mirror_exclude == "tmp/*"

    def test_empty_env_vars_default_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """空环境变量默认为 None。"""
        monkeypatch.setenv("IRIP_DATABASE_URL", "postgresql://irip:pass@localhost/irip")
        monkeypatch.setenv("IRIP_MINIO_MIRROR_EXCLUDE", "")
        monkeypatch.setenv("IRIP_PG_REPLICATION_SLOT", "")
        config = build_backup_config_from_env()
        assert config.minio_mirror_exclude is None
        assert config.pg_replication_slot is None


# ============================================================
# _basebackup 方法测试（mock subprocess）
# ============================================================


class TestBasebackup:
    """_basebackup() 方法测试（mock pg_basebackup 命令）。"""

    def _make_service(self) -> BackupService:
        """构造 BackupService 实例。"""
        config = BackupConfig(
            db_url="postgresql+psycopg://irip:pass@postgres:5432/irip",
            minio_endpoint="http://minio:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            application_version="0.1.0",
            output_dir=Path("/tmp/backups"),
        )
        return BackupService(config)

    def test_basebackup_returns_wal_lsn_tuple(self, tmp_path: Path) -> None:
        """_basebackup 返回 (wal_start_lsn, wal_end_lsn) 元组。"""
        service = self._make_service()

        # mock _query_wal_lsn 返回不同 LSN
        with patch.object(service, "_query_wal_lsn", side_effect=["0/2000000", "0/2000123"]):
            # mock subprocess.run 模拟 pg_basebackup 成功
            def fake_run(cmd, **kwargs):
                # 模拟产出 base.tar.gz
                target_dir = Path(cmd[cmd.index("-D") + 1])
                (target_dir / "base.tar.gz").write_bytes(b"base-content")
                result = MagicMock()
                result.returncode = 0
                result.stderr = b""
                return result

            with patch("deployments.compose.backup.subprocess.run", side_effect=fake_run):
                wal_start, wal_end = service._basebackup(tmp_path)

        assert wal_start == "0/2000000"
        assert wal_end == "0/2000123"

    def test_basebackup_creates_base_tar_gz(self, tmp_path: Path) -> None:
        """_basebackup 后 target_dir 含 base.tar.gz。"""
        service = self._make_service()

        with patch.object(service, "_query_wal_lsn", return_value="0/1000000"):
            def fake_run(cmd, **kwargs):
                target_dir = Path(cmd[cmd.index("-D") + 1])
                (target_dir / "base.tar.gz").write_bytes(b"base")
                result = MagicMock()
                result.returncode = 0
                result.stderr = b""
                return result

            with patch("deployments.compose.backup.subprocess.run", side_effect=fake_run):
                service._basebackup(tmp_path)

        assert (tmp_path / "base.tar.gz").exists()

    def test_basebackup_failure_raises(self, tmp_path: Path) -> None:
        """pg_basebackup 失败时抛出 RuntimeError。"""
        service = self._make_service()

        with patch.object(service, "_query_wal_lsn", return_value="0/1000000"):
            result = MagicMock()
            result.returncode = 1
            result.stderr = b"connection refused"

            with patch("deployments.compose.backup.subprocess.run", return_value=result):
                with pytest.raises(RuntimeError, match="pg_basebackup failed"):
                    service._basebackup(tmp_path)

    def test_basebackup_with_replication_slot(self, tmp_path: Path) -> None:
        """配置了 pg_replication_slot 时命令含 -C -S 参数。"""
        config = BackupConfig(
            db_url="postgresql+psycopg://irip:pass@postgres:5432/irip",
            minio_endpoint="http://minio:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            application_version="0.1.0",
            output_dir=Path("/tmp/backups"),
            pg_replication_slot="my_slot",
        )
        service = BackupService(config)

        captured_cmd = []

        with patch.object(service, "_query_wal_lsn", return_value="0/1000000"):
            def fake_run(cmd, **kwargs):
                captured_cmd.extend(cmd)
                target_dir = Path(cmd[cmd.index("-D") + 1])
                (target_dir / "base.tar.gz").write_bytes(b"base")
                result = MagicMock()
                result.returncode = 0
                result.stderr = b""
                return result

            with patch("deployments.compose.backup.subprocess.run", side_effect=fake_run):
                service._basebackup(tmp_path)

        assert "-C" in captured_cmd
        assert "-S" in captured_cmd
        slot_idx = captured_cmd.index("-S")
        assert captured_cmd[slot_idx + 1] == "my_slot"

    def test_basebackup_without_slot_no_cs_flags(self, tmp_path: Path) -> None:
        """未配置复制槽时命令不含 -C -S 参数。"""
        service = self._make_service()
        captured_cmd = []

        with patch.object(service, "_query_wal_lsn", return_value="0/1000000"):
            def fake_run(cmd, **kwargs):
                captured_cmd.extend(cmd)
                target_dir = Path(cmd[cmd.index("-D") + 1])
                (target_dir / "base.tar.gz").write_bytes(b"base")
                result = MagicMock()
                result.returncode = 0
                result.stderr = b""
                return result

            with patch("deployments.compose.backup.subprocess.run", side_effect=fake_run):
                service._basebackup(tmp_path)

        assert "-C" not in captured_cmd
        assert "-S" not in captured_cmd


# ============================================================
# _mc_mirror_minio 方法测试（mock mc 命令）
# ============================================================


class TestMcMirrorMinio:
    """_mc_mirror_minio() 方法测试（mock mc mirror 命令）。"""

    def _make_service(self) -> BackupService:
        """构造 BackupService 实例。"""
        config = BackupConfig(
            db_url="postgresql://irip:pass@postgres:5432/irip",
            minio_endpoint="http://minio:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            application_version="0.1.0",
            output_dir=Path("/tmp/backups"),
        )
        return BackupService(config)

    def test_mc_mirror_returns_object_count(self, tmp_path: Path) -> None:
        """_mc_mirror_minio 返回镜像的对象数。"""
        service = self._make_service()

        with patch.object(service, "_setup_mc_alias"):
            # 预创建文件模拟 mc mirror 产出，mc mirror 结束后目录中有文件
            (tmp_path / "obj1").write_bytes(b"a")
            (tmp_path / "obj2").write_bytes(b"b")

            result = MagicMock()
            result.returncode = 0
            result.stderr = b""

            with patch("deployments.compose.backup.subprocess.run", return_value=result):
                count = service._mc_mirror_minio(tmp_path)

        assert count == 2

    def test_mc_mirror_failure_raises(self, tmp_path: Path) -> None:
        """mc mirror 失败时抛出 RuntimeError。"""
        service = self._make_service()

        with patch.object(service, "_setup_mc_alias"):
            result = MagicMock()
            result.returncode = 1
            result.stderr = b"mc error"

            with patch("deployments.compose.backup.subprocess.run", return_value=result):
                with pytest.raises(RuntimeError, match="mc mirror failed"):
                    service._mc_mirror_minio(tmp_path)

    def test_mc_mirror_with_exclude(self, tmp_path: Path) -> None:
        """配置了 minio_mirror_exclude 时命令含 --exclude 参数。"""
        config = BackupConfig(
            db_url="postgresql://irip:pass@postgres:5432/irip",
            minio_endpoint="http://minio:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            application_version="0.1.0",
            output_dir=Path("/tmp/backups"),
            minio_mirror_exclude="tmp/*",
        )
        service = BackupService(config)
        captured_cmd = []

        with patch.object(service, "_setup_mc_alias"):
            def fake_run(cmd, **kwargs):
                captured_cmd.extend(cmd)
                r = MagicMock()
                r.returncode = 0
                r.stderr = b""
                return r

            with patch("deployments.compose.backup.subprocess.run", side_effect=fake_run):
                service._mc_mirror_minio(tmp_path)

        assert "--exclude" in captured_cmd
        exclude_idx = captured_cmd.index("--exclude")
        assert captured_cmd[exclude_idx + 1] == "tmp/*"


# ============================================================
# backup() 联合备份流程测试（mock 全部外部依赖）
# ============================================================


class TestBackupPitrFlow:
    """backup() 联合备份流程测试（mock pg_basebackup + mc mirror + DB 查询）。"""

    def _make_service(self, tmp_path: Path) -> BackupService:
        """构造 BackupService 实例。"""
        config = BackupConfig(
            db_url="postgresql+psycopg://irip:pass@postgres:5432/irip",
            minio_endpoint="http://minio:9000",
            minio_access_key="irip",
            minio_secret_key="pass",
            minio_bucket="irip-artifacts",
            minio_region="us-east-1",
            application_version="0.1.0",
            output_dir=tmp_path,
        )
        return BackupService(config)

    @pytest.mark.asyncio
    async def test_backup_produces_v2_manifest(self, tmp_path: Path) -> None:
        """backup() 生成 format_version=2 的 manifest。"""
        service = self._make_service(tmp_path)

        # mock _basebackup: 返回 WAL LSN 并创建 base.tar.gz
        def fake_basebackup(target_dir: Path) -> tuple[str, str]:
            (target_dir / "base.tar.gz").write_bytes(b"base-content")
            return "0/2000000", "0/2000123"

        # mock _mc_mirror_minio: 创建 mirror 文件并返回计数
        def fake_mc_mirror(target_dir: Path) -> int:
            (target_dir / "obj1").write_bytes(b"data-1")
            return 1

        # mock _query_migration_version
        async def fake_query_migration() -> str:
            return "0061"

        with patch.object(service, "_basebackup", side_effect=fake_basebackup):
            with patch.object(service, "_mc_mirror_minio", side_effect=fake_mc_mirror):
                with patch.object(service, "_query_migration_version", side_effect=fake_query_migration):
                    manifest = await service.backup()

        assert manifest.format_version == 2
        assert manifest.extra["backup_method"] == "pitr"
        assert manifest.extra["wal_start_lsn"] == "0/2000000"
        assert manifest.extra["wal_end_lsn"] == "0/2000123"
        assert manifest.extra["minio_mirror_object_count"] == 1

    @pytest.mark.asyncio
    async def test_backup_generates_backup_timestamp(self, tmp_path: Path) -> None:
        """backup() 生成联合时间戳 backup_timestamp。"""
        service = self._make_service(tmp_path)

        def fake_basebackup(target_dir: Path) -> tuple[str, str]:
            (target_dir / "base.tar.gz").write_bytes(b"base")
            return "0/1000000", "0/2000000"

        def fake_mc_mirror(target_dir: Path) -> int:
            return 0

        async def fake_query_migration() -> str:
            return "0061"

        with patch.object(service, "_basebackup", side_effect=fake_basebackup):
            with patch.object(service, "_mc_mirror_minio", side_effect=fake_mc_mirror):
                with patch.object(service, "_query_migration_version", side_effect=fake_query_migration):
                    manifest = await service.backup()

        ts = manifest.extra["backup_timestamp"]
        assert ts  # 非空
        # 验证为 ISO 8601 格式
        parsed = datetime.fromisoformat(ts)
        assert parsed is not None

    @pytest.mark.asyncio
    async def test_backup_writes_manifest_json(self, tmp_path: Path) -> None:
        """backup() 写入 manifest.json 到备份目录。"""
        service = self._make_service(tmp_path)

        def fake_basebackup(target_dir: Path) -> tuple[str, str]:
            (target_dir / "base.tar.gz").write_bytes(b"base")
            return "0/1000000", "0/2000000"

        def fake_mc_mirror(target_dir: Path) -> int:
            return 0

        async def fake_query_migration() -> str:
            return "0061"

        with patch.object(service, "_basebackup", side_effect=fake_basebackup):
            with patch.object(service, "_mc_mirror_minio", side_effect=fake_mc_mirror):
                with patch.object(service, "_query_migration_version", side_effect=fake_query_migration):
                    manifest = await service.backup()

        # manifest.json 应在 backup_id 子目录中
        backup_dir = tmp_path / manifest.backup_id
        manifest_path = backup_dir / "manifest.json"
        assert manifest_path.exists()

        # 验证写入的 manifest 格式正确
        import json
        saved = json.loads(manifest_path.read_text())
        assert saved["format_version"] == 2
        assert saved["extra"]["backup_method"] == "pitr"

    @pytest.mark.asyncio
    async def test_backup_creates_subdirectories(self, tmp_path: Path) -> None:
        """backup() 创建 pg_basebackup/ 和 minio_mirror/ 子目录。"""
        from deployments.compose.backup_manifest import (
            MINIO_MIRROR_DIRNAME,
            PG_BASEBACKUP_DIRNAME,
        )

        service = self._make_service(tmp_path)

        def fake_basebackup(target_dir: Path) -> tuple[str, str]:
            assert target_dir.name == PG_BASEBACKUP_DIRNAME
            (target_dir / "base.tar.gz").write_bytes(b"base")
            return "0/1000000", "0/2000000"

        def fake_mc_mirror(target_dir: Path) -> int:
            assert target_dir.name == MINIO_MIRROR_DIRNAME
            return 0

        async def fake_query_migration() -> str:
            return "0061"

        with patch.object(service, "_basebackup", side_effect=fake_basebackup):
            with patch.object(service, "_mc_mirror_minio", side_effect=fake_mc_mirror):
                with patch.object(service, "_query_migration_version", side_effect=fake_query_migration):
                    manifest = await service.backup()

        backup_dir = tmp_path / manifest.backup_id
        assert (backup_dir / PG_BASEBACKUP_DIRNAME).exists()
        assert (backup_dir / MINIO_MIRROR_DIRNAME).exists()
