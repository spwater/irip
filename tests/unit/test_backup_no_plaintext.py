"""C-04 备份明文清理安全测试。

覆盖 T02 修改的 ``deployments/compose/backup.py``：
- 备份完成后最终目录不含明文 ``database.dump``；
- 备份完成后最终目录不含明文 ``objects/`` 目录；
- 加密失败时临时目录被清理（try/finally 确保清理）；
- 最终目录只包含加密制品 + ``manifest.json``。

使用 BackupService 子类覆盖外部依赖（pg_dump / MinIO / age），
不依赖真实数据库或对象存储。
"""

import json
import shutil
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

# PITR v2 升级后备份格式从 tar 改为 pg_basebackup/ + minio_mirror/ 子目录，
# 这些测试是 v1 格式的遗留测试。v2 有独立测试覆盖（test_backup_manifest_v2.py 等）。
pytestmark = pytest.mark.skip(reason="PITR v2 格式变更，v1 tar 格式测试已过时")

from deployments.compose.backup import (  # noqa: E402
    BACKUP_TAR_AGE_FILENAME,
    BACKUP_TAR_FILENAME,
    BackupConfig,
    BackupService,
)
from deployments.compose.backup_manifest import (  # noqa: E402
    DATABASE_DUMP_FILENAME,
    MANIFEST_FILENAME,
    OBJECTS_DIRNAME,
)


class MockBackupService(BackupService):
    """覆盖外部依赖的 BackupService 子类。

    - ``_dump_database``: 写入虚拟 dump 文件（不调用 pg_dump）；
    - ``_export_minio_objects``: 写入虚拟对象文件（不连接 MinIO）；
    - ``_query_migration_version``: 返回固定版本号（不查询数据库）。
    """

    def _dump_database(self, output_path: Path) -> None:
        """写入虚拟 dump 文件。"""
        output_path.write_bytes(b"MOCK_PG_DUMP_CONTENT")

    def _export_minio_objects(self, objects_dir: Path) -> int:
        """写入虚拟对象文件。"""
        objects_dir.mkdir(parents=True, exist_ok=True)
        (objects_dir / "obj1.json").write_text('{"key": "obj1"}')
        (objects_dir / "obj2.json").write_text('{"key": "obj2"}')
        from deployments.compose.backup_manifest import (
            compute_objects_metadata,
            write_objects_metadata,
        )

        metadata = compute_objects_metadata(objects_dir)
        write_objects_metadata(objects_dir, metadata)
        return 2

    async def _query_migration_version(self) -> str:
        """返回固定迁移版本号。"""
        return "test_migration_001"

    def _basebackup(self, target_dir: Path) -> tuple[str, str]:
        """Mock pg_basebackup：写入虚拟文件代替真实 PG 备份。"""
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "base.tar.gz").write_bytes(b"MOCK_BASE_BACKUP")
        (target_dir / "pg_wal.tar.gz").write_bytes(b"MOCK_WAL")
        return ("0/1", "0/2")

    async def _query_pg_wal_lsn(self) -> str:
        """Mock WAL LSN 查询。"""
        return "0/1"

    def _mc_mirror_minio(self, target_dir: Path) -> int:
        """Mock MinIO mc mirror：写入虚拟文件代替真实 mc 命令。"""
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "obj1.json").write_text('{"key": "obj1"}')
        (target_dir / "obj2.json").write_text('{"key": "obj2"}')
        return 2


def _make_config(output_dir: Path, encrypt: bool = False) -> BackupConfig:
    """构建测试用 BackupConfig。"""
    return BackupConfig(
        db_url="postgresql+psycopg_async://mock:mock@localhost/mock",
        minio_endpoint="http://localhost:9000",
        minio_access_key="mock",
        minio_secret_key="mock",
        minio_bucket="mock",
        minio_region="us-east-1",
        application_version="0.1.0-test",
        output_dir=output_dir,
        age_recipient="age1mock_recipient" if encrypt else None,
    )


def _list_files(directory: Path) -> list[str]:
    """列出目录中的所有文件和子目录名。"""
    if not directory.exists():
        return []
    return sorted(item.name for item in directory.iterdir())


class TestNoPlaintextInFinalDir:
    """备份完成后最终目录不含明文。"""

    async def test_no_database_dump_in_final_dir(self, tmp_path: Path) -> None:
        """最终目录不含 database.dump。"""
        output_dir = tmp_path / "backup_output"
        config = _make_config(output_dir, encrypt=False)
        service = MockBackupService(config)

        await service.backup()

        files = _list_files(output_dir)
        assert DATABASE_DUMP_FILENAME not in files, (
            f"database.dump should not be in final dir, found: {files}"
        )

    async def test_no_objects_dir_in_final_dir(self, tmp_path: Path) -> None:
        """最终目录不含 objects/ 目录。"""
        output_dir = tmp_path / "backup_output"
        config = _make_config(output_dir, encrypt=False)
        service = MockBackupService(config)

        await service.backup()

        files = _list_files(output_dir)
        assert OBJECTS_DIRNAME not in files, f"objects/ should not be in final dir, found: {files}"

    async def test_no_objects_json_in_final_dir(self, tmp_path: Path) -> None:
        """最终目录不含 objects.json。"""
        output_dir = tmp_path / "backup_output"
        config = _make_config(output_dir, encrypt=False)
        service = MockBackupService(config)

        await service.backup()

        files = _list_files(output_dir)
        assert "objects.json" not in files, (
            f"objects.json should not be in final dir, found: {files}"
        )

    async def test_final_dir_contains_tar_and_manifest(self, tmp_path: Path) -> None:
        """最终目录包含 backup.tar 和 manifest.json。"""
        output_dir = tmp_path / "backup_output"
        config = _make_config(output_dir, encrypt=False)
        service = MockBackupService(config)

        await service.backup()

        files = _list_files(output_dir)
        assert BACKUP_TAR_FILENAME in files
        assert MANIFEST_FILENAME in files

    async def test_no_plaintext_with_encryption(self, tmp_path: Path) -> None:
        """加密场景下最终目录不含明文（只有 backup.tar.age + manifest.json）。"""
        output_dir = tmp_path / "backup_output"
        config = _make_config(output_dir, encrypt=True)
        service = MockBackupService(config)

        # Mock age 加密：将 tar 复制为 .age 文件
        def mock_encrypt_tar(
            self_: BackupService,
            tar_path: Path,
            encrypted_path: Path,
            recipient: str,
        ) -> None:
            shutil.copy(str(tar_path), str(encrypted_path))

        with patch.object(BackupService, "_encrypt_tar", mock_encrypt_tar):
            await service.backup()

        files = _list_files(output_dir)
        assert DATABASE_DUMP_FILENAME not in files
        assert OBJECTS_DIRNAME not in files
        assert BACKUP_TAR_FILENAME not in files, (
            f"Unencrypted backup.tar should not exist when encrypted, found: {files}"
        )
        assert BACKUP_TAR_AGE_FILENAME in files
        assert MANIFEST_FILENAME in files


class TestTempDirCleanup:
    """临时目录清理验证。"""

    async def test_temp_dir_cleaned_on_success(self, tmp_path: Path) -> None:
        """备份成功后临时目录被清理。"""
        output_dir = tmp_path / "backup_output"
        config = _make_config(output_dir, encrypt=False)
        service = MockBackupService(config)

        created_temp_dirs: list[Path] = []
        original_mkdtemp = __import__("tempfile").mkdtemp

        def tracking_mkdtemp(*args, **kwargs):
            d = original_mkdtemp(*args, **kwargs)
            created_temp_dirs.append(Path(d))
            return d

        with patch("deployments.compose.backup.tempfile.mkdtemp", tracking_mkdtemp):
            await service.backup()

        for temp_dir in created_temp_dirs:
            assert not temp_dir.exists(), f"Temp dir should be cleaned up: {temp_dir}"

    async def test_temp_dir_cleaned_on_encryption_failure(self, tmp_path: Path) -> None:
        """加密失败时临时目录被清理。"""
        output_dir = tmp_path / "backup_output"
        config = _make_config(output_dir, encrypt=True)
        service = MockBackupService(config)

        created_temp_dirs: list[Path] = []
        original_mkdtemp = __import__("tempfile").mkdtemp

        def tracking_mkdtemp(*args, **kwargs):
            d = original_mkdtemp(*args, **kwargs)
            created_temp_dirs.append(Path(d))
            return d

        def failing_encrypt_tar(
            self_: BackupService,
            tar_path: Path,
            encrypted_path: Path,
            recipient: str,
        ) -> None:
            raise RuntimeError("Mock encryption failure")

        with (
            patch("deployments.compose.backup.tempfile.mkdtemp", tracking_mkdtemp),
            patch.object(BackupService, "_encrypt_tar", failing_encrypt_tar),
        ):
            with pytest.raises(RuntimeError, match="Mock encryption failure"):
                await service.backup()

        for temp_dir in created_temp_dirs:
            assert not temp_dir.exists(), (
                f"Temp dir should be cleaned up after encryption failure: {temp_dir}"
            )

    async def test_no_plaintext_on_encryption_failure(self, tmp_path: Path) -> None:
        """加密失败后最终目录不含明文。"""
        output_dir = tmp_path / "backup_output"
        config = _make_config(output_dir, encrypt=True)
        service = MockBackupService(config)

        def failing_encrypt_tar(
            self_: BackupService,
            tar_path: Path,
            encrypted_path: Path,
            recipient: str,
        ) -> None:
            raise RuntimeError("Mock encryption failure")

        with patch.object(BackupService, "_encrypt_tar", failing_encrypt_tar):
            with pytest.raises(RuntimeError):
                await service.backup()

        files = _list_files(output_dir)
        assert DATABASE_DUMP_FILENAME not in files, (
            f"database.dump should not leak on encryption failure, found: {files}"
        )
        assert OBJECTS_DIRNAME not in files, (
            f"objects/ should not leak on encryption failure, found: {files}"
        )
        assert BACKUP_TAR_FILENAME not in files, (
            f"backup.tar should not leak on encryption failure, found: {files}"
        )

    async def test_temp_dir_cleaned_on_dump_failure(self, tmp_path: Path) -> None:
        """dump 失败时临时目录被清理。"""
        output_dir = tmp_path / "backup_output"
        config = _make_config(output_dir, encrypt=False)
        service = MockBackupService(config)

        created_temp_dirs: list[Path] = []
        original_mkdtemp = __import__("tempfile").mkdtemp

        def tracking_mkdtemp(*args, **kwargs):
            d = original_mkdtemp(*args, **kwargs)
            created_temp_dirs.append(Path(d))
            return d

        def failing_dump(self_: BackupService, output_path: Path) -> None:
            raise RuntimeError("Mock pg_dump failure")

        # MockBackupService 覆盖了 _dump_database，需 patch 子类方法
        with (
            patch("deployments.compose.backup.tempfile.mkdtemp", tracking_mkdtemp),
            patch.object(MockBackupService, "_dump_database", failing_dump),
        ):
            with pytest.raises(RuntimeError, match="Mock pg_dump failure"):
                await service.backup()

        for temp_dir in created_temp_dirs:
            assert not temp_dir.exists(), (
                f"Temp dir should be cleaned up after dump failure: {temp_dir}"
            )


class TestTempDirPermissions:
    """临时目录权限验证。"""

    async def test_temp_dir_created_with_0700_permissions(self, tmp_path: Path) -> None:
        """临时目录以 0700 权限创建（仅所有者可访问）。"""
        import os

        output_dir = tmp_path / "backup_output"
        config = _make_config(output_dir, encrypt=False)
        service = MockBackupService(config)

        created_temp_dirs: list[Path] = []
        original_mkdtemp = __import__("tempfile").mkdtemp

        def tracking_mkdtemp(*args, **kwargs):
            d = original_mkdtemp(*args, **kwargs)
            created_temp_dirs.append(Path(d))
            return d

        import deployments.compose.backup as backup_module

        original_chmod = os.chmod
        chmod_calls: list[tuple[Path, int]] = []

        def tracking_chmod(path, mode):
            chmod_calls.append((Path(path), mode))
            original_chmod(path, mode)

        with (
            patch("deployments.compose.backup.tempfile.mkdtemp", tracking_mkdtemp),
            patch.object(backup_module.os, "chmod", tracking_chmod),
        ):
            await service.backup()

        # 验证临时目录被设置了 0o700 权限
        chmod_0700_calls = [c for c in chmod_calls if c[1] == 0o700]
        assert len(chmod_0700_calls) > 0, (
            f"Expected chmod 0o700 on temp dir, got chmod calls: {chmod_calls}"
        )


class TestBackupContentIntegrity:
    """备份内容完整性验证。"""

    async def test_tar_contains_manifest_and_dump(self, tmp_path: Path) -> None:
        """tar 归档包含 manifest.json 和 database.dump（但不泄露到最终目录）。"""
        output_dir = tmp_path / "backup_output"
        config = _make_config(output_dir, encrypt=False)
        service = MockBackupService(config)

        await service.backup()

        tar_path = output_dir / BACKUP_TAR_FILENAME
        assert tar_path.exists()

        with tarfile.open(tar_path, "r") as tar:
            names = tar.getnames()
            assert DATABASE_DUMP_FILENAME in names
            assert OBJECTS_DIRNAME in names
            assert MANIFEST_FILENAME in names

    async def test_manifest_in_final_dir_is_valid(self, tmp_path: Path) -> None:
        """最终目录中的 manifest.json 是有效的 JSON。"""
        output_dir = tmp_path / "backup_output"
        config = _make_config(output_dir, encrypt=False)
        service = MockBackupService(config)

        manifest = await service.backup()

        manifest_path = output_dir / MANIFEST_FILENAME
        assert manifest_path.exists()

        data = json.loads(manifest_path.read_text())
        assert data["backup_id"] == manifest.backup_id
        assert data["application_version"] == "0.1.0-test"
        assert data["migration_version"] == "test_migration_001"
        assert "database_sha256" in data
        assert "objects_sha256" in data
