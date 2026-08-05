"""BackupManifest v2 单元测试（PITR 升级）。

验证 deployments/compose/backup_manifest.py 的 v2 功能：
- MANIFEST_FORMAT_VERSION = 2；
- compute_manifest_v2() 生成正确的 v2 manifest（extra dict 含 PITR 元数据）；
- BackupManifestValidator v1/v2 验证分流；
- v1 manifest 仍可通过验证（向后兼容）；
- v2 manifest 完整性校验（base.tar.gz + pg_wal.tar.gz + minio_mirror 聚合 SHA-256）；
- v2 校验失败时抛出 ManifestValidationError。

对应 docs/arch-db-backup-pitr-upgrade.md §3.2 / §1.7。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from deployments.compose.backup_manifest import (
    BASE_TAR_GZ_FILENAME,
    MANIFEST_FORMAT_VERSION,
    MINIO_MIRROR_DIRNAME,
    PG_BASEBACKUP_DIRNAME,
    PG_WAL_TAR_GZ_FILENAME,
    BackupManifest,
    BackupManifestValidator,
    ManifestValidationError,
    compute_manifest_v2,
    load_manifest,
    save_manifest,
)

# ============================================================
# 常量验证
# ============================================================


class TestManifestFormatVersion:
    """MANIFEST_FORMAT_VERSION 升级至 2。"""

    def test_format_version_is_2(self) -> None:
        """MANIFEST_FORMAT_VERSION 应为 2。"""
        assert MANIFEST_FORMAT_VERSION == 2


# ============================================================
# compute_manifest_v2() 测试
# ============================================================


class TestComputeManifestV2:
    """compute_manifest_v2() 生成 v2 manifest 测试。"""

    def test_produces_format_version_2(self, tmp_path: Path) -> None:
        """compute_manifest_v2 生成 format_version=2 的 manifest。"""
        pg_dir = tmp_path / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir()
        (pg_dir / BASE_TAR_GZ_FILENAME).write_bytes(b"base-tar-content")
        (pg_dir / PG_WAL_TAR_GZ_FILENAME).write_bytes(b"wal-tar-content")

        mirror_dir = tmp_path / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()
        (mirror_dir / "obj1").write_bytes(b"object-1")
        (mirror_dir / "obj2").write_bytes(b"object-2")

        manifest = compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
            backup_id="test-backup-id",
            backup_timestamp="2026-08-16T02:00:00.000+00:00",
            wal_start_lsn="0/2000000",
            wal_end_lsn="0/2000123",
        )

        assert manifest.format_version == 2

    def test_extra_contains_pitr_metadata(self, tmp_path: Path) -> None:
        """v2 manifest 的 extra dict 包含全部 PITR 元数据字段。"""
        pg_dir = tmp_path / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir()
        (pg_dir / BASE_TAR_GZ_FILENAME).write_bytes(b"base")
        (pg_dir / PG_WAL_TAR_GZ_FILENAME).write_bytes(b"wal")

        mirror_dir = tmp_path / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()
        (mirror_dir / "obj1").write_bytes(b"data")

        manifest = compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
            backup_id="bid-001",
            backup_timestamp="2026-08-16T02:00:00.000+00:00",
            wal_start_lsn="0/2000000",
            wal_end_lsn="0/2000123",
        )

        extra = manifest.extra
        assert extra["backup_timestamp"] == "2026-08-16T02:00:00.000+00:00"
        assert extra["backup_method"] == "pitr"
        assert extra["wal_start_lsn"] == "0/2000000"
        assert extra["wal_end_lsn"] == "0/2000123"
        assert "pg_basebackup_sha256" in extra
        assert "pg_wal_sha256" in extra
        assert "minio_mirror_sha256" in extra
        assert extra["minio_mirror_object_count"] == 1

    def test_database_sha256_equals_base_tar_sha256(self, tmp_path: Path) -> None:
        """v2 manifest 的 database_sha256 字段复用为 base.tar.gz 的 SHA-256。"""
        import hashlib

        pg_dir = tmp_path / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir()
        base_content = b"base-tar-content"
        (pg_dir / BASE_TAR_GZ_FILENAME).write_bytes(base_content)
        (pg_dir / PG_WAL_TAR_GZ_FILENAME).write_bytes(b"wal")

        mirror_dir = tmp_path / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()

        manifest = compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
        )

        expected_base_sha = hashlib.sha256(base_content).hexdigest()
        assert manifest.database_sha256 == expected_base_sha
        assert manifest.extra["pg_basebackup_sha256"] == expected_base_sha

    def test_object_count_equals_mirror_count(self, tmp_path: Path) -> None:
        """v2 manifest 的 object_count 字段复用为 minio_mirror 对象数。"""
        pg_dir = tmp_path / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir()
        (pg_dir / BASE_TAR_GZ_FILENAME).write_bytes(b"base")

        mirror_dir = tmp_path / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()
        for i in range(5):
            (mirror_dir / f"obj{i}").write_bytes(b"data")

        manifest = compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
        )

        assert manifest.object_count == 5
        assert manifest.extra["minio_mirror_object_count"] == 5

    def test_empty_mirror_dir(self, tmp_path: Path) -> None:
        """空 minio_mirror 目录时 object_count=0。"""
        pg_dir = tmp_path / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir()
        (pg_dir / BASE_TAR_GZ_FILENAME).write_bytes(b"base")

        mirror_dir = tmp_path / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()

        manifest = compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
        )

        assert manifest.object_count == 0
        assert manifest.extra["minio_mirror_object_count"] == 0

    def test_missing_base_tar_gz(self, tmp_path: Path) -> None:
        """base.tar.gz 不存在时 pg_basebackup_sha256 为空字符串。"""
        pg_dir = tmp_path / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir()

        mirror_dir = tmp_path / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()

        manifest = compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
        )

        assert manifest.extra["pg_basebackup_sha256"] == ""

    def test_not_encrypted(self, tmp_path: Path) -> None:
        """v2 manifest 不加密（encrypted=False）。"""
        pg_dir = tmp_path / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir()
        (pg_dir / BASE_TAR_GZ_FILENAME).write_bytes(b"base")

        mirror_dir = tmp_path / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()

        manifest = compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
        )

        assert manifest.encrypted is False


# ============================================================
# v1/v2 验证分流
# ============================================================


class TestValidatorVersionRouting:
    """BackupManifestValidator v1/v2 验证分流测试。"""

    def test_v1_routes_to_validate_v1(self, tmp_path: Path) -> None:
        """format_version=1 走 _validate_v1 路径。"""
        # 构造 v1 manifest 和目录
        from deployments.compose.backup_manifest import (
            DATABASE_DUMP_FILENAME,
            OBJECTS_DIRNAME,
            compute_manifest,
        )

        dump_path = tmp_path / DATABASE_DUMP_FILENAME
        dump_path.write_bytes(b"database-dump")
        objects_dir = tmp_path / OBJECTS_DIRNAME
        objects_dir.mkdir()
        (objects_dir / "obj1").write_bytes(b"obj-1")

        manifest = compute_manifest(
            database_dump_path=dump_path,
            objects_dir=objects_dir,
            application_version="0.1.0",
            migration_version="0060",
        )
        # compute_manifest 使用 MANIFEST_FORMAT_VERSION=2，手动改为 1
        manifest_v1 = BackupManifest(
            format_version=1,
            created_at=manifest.created_at,
            application_version=manifest.application_version,
            migration_version=manifest.migration_version,
            database_sha256=manifest.database_sha256,
            object_count=manifest.object_count,
            objects_sha256=manifest.objects_sha256,
            encrypted=False,
            backup_id="",
            extra={},
        )

        validator = BackupManifestValidator()
        assert validator.validate(manifest_v1, tmp_path) is True

    def test_v2_routes_to_validate_v2(self, tmp_path: Path) -> None:
        """format_version=2 走 _validate_v2 路径。"""
        pg_dir = tmp_path / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir()
        (pg_dir / BASE_TAR_GZ_FILENAME).write_bytes(b"base")
        (pg_dir / PG_WAL_TAR_GZ_FILENAME).write_bytes(b"wal")

        mirror_dir = tmp_path / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()
        (mirror_dir / "obj1").write_bytes(b"data")

        manifest = compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
        )

        validator = BackupManifestValidator()
        assert validator.validate(manifest, tmp_path) is True

    def test_unsupported_version_raises(self, tmp_path: Path) -> None:
        """不支持的 manifest 版本抛出 ManifestValidationError。"""
        manifest = BackupManifest(
            format_version=99,
            created_at=datetime.now(UTC),
            application_version="0.1.0",
            migration_version="0061",
            database_sha256="",
            object_count=0,
            objects_sha256="",
            encrypted=False,
            backup_id="",
            extra={},
        )

        validator = BackupManifestValidator()
        with pytest.raises(ManifestValidationError) as exc_info:
            validator.validate(manifest, tmp_path)

        assert "99" in str(exc_info.value)


# ============================================================
# v1 向后兼容
# ============================================================


class TestV1BackwardCompat:
    """v1 manifest 仍可通过验证（向后兼容）。"""

    def test_v1_manifest_validates_successfully(self, tmp_path: Path) -> None:
        """v1 格式备份目录可通过完整性校验。"""
        import hashlib

        from deployments.compose.backup_manifest import (
            DATABASE_DUMP_FILENAME,
            OBJECTS_DIRNAME,
            compute_objects_aggregate_sha256,
        )

        dump_path = tmp_path / DATABASE_DUMP_FILENAME
        dump_content = b"v1-database-dump"
        dump_path.write_bytes(dump_content)
        dump_sha = hashlib.sha256(dump_content).hexdigest()

        objects_dir = tmp_path / OBJECTS_DIRNAME
        objects_dir.mkdir()
        (objects_dir / "obj1").write_bytes(b"obj1-data")

        objects_sha, obj_count, _ = compute_objects_aggregate_sha256(objects_dir)

        manifest_v1 = BackupManifest(
            format_version=1,
            created_at=datetime.now(UTC),
            application_version="0.1.0",
            migration_version="0060",
            database_sha256=dump_sha,
            object_count=obj_count,
            objects_sha256=objects_sha,
            encrypted=False,
            backup_id="v1-backup",
            extra={},
        )

        validator = BackupManifestValidator()
        result = validator.validate(manifest_v1, tmp_path)
        assert result is True

    def test_v1_manifest_tampered_database_fails(self, tmp_path: Path) -> None:
        """v1 格式数据库 dump 被篡改时校验失败。"""
        import hashlib

        dump_path = tmp_path / "database.dump"
        dump_path.write_bytes(b"original-dump")

        objects_dir = tmp_path / "objects"
        objects_dir.mkdir()

        manifest_v1 = BackupManifest(
            format_version=1,
            created_at=datetime.now(UTC),
            application_version="0.1.0",
            migration_version="0060",
            database_sha256=hashlib.sha256(b"different-content").hexdigest(),
            object_count=0,
            objects_sha256="",
            encrypted=False,
            backup_id="",
            extra={},
        )

        validator = BackupManifestValidator()
        with pytest.raises(ManifestValidationError) as exc_info:
            validator.validate(manifest_v1, tmp_path)

        assert (
            "database" in exc_info.value.component.lower() or "sha" in str(exc_info.value).lower()
        )


# ============================================================
# v2 完整性校验
# ============================================================


class TestV2Validation:
    """v2 manifest 完整性校验测试。"""

    def _make_v2_backup(self, tmp_path: Path) -> BackupManifest:
        """构造一个完整的 v2 备份目录。"""
        pg_dir = tmp_path / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir()
        (pg_dir / BASE_TAR_GZ_FILENAME).write_bytes(b"base-content")
        (pg_dir / PG_WAL_TAR_GZ_FILENAME).write_bytes(b"wal-content")

        mirror_dir = tmp_path / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()
        (mirror_dir / "obj1").write_bytes(b"data-1")
        (mirror_dir / "obj2").write_bytes(b"data-2")

        return compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
            backup_id="test-id",
            backup_timestamp="2026-08-16T02:00:00.000+00:00",
            wal_start_lsn="0/2000000",
            wal_end_lsn="0/2000123",
        )

    def test_v2_validates_successfully(self, tmp_path: Path) -> None:
        """完整 v2 备份目录校验通过。"""
        manifest = self._make_v2_backup(tmp_path)
        validator = BackupManifestValidator()
        assert validator.validate(manifest, tmp_path) is True

    def test_v2_missing_base_tar_fails(self, tmp_path: Path) -> None:
        """base.tar.gz 缺失时 v2 校验失败。"""
        manifest = self._make_v2_backup(tmp_path)
        # 删除 base.tar.gz
        (tmp_path / PG_BASEBACKUP_DIRNAME / BASE_TAR_GZ_FILENAME).unlink()

        validator = BackupManifestValidator()
        with pytest.raises(ManifestValidationError) as exc_info:
            validator.validate(manifest, tmp_path)

        assert "base.tar.gz" in str(exc_info.value) or "pg_basebackup" in exc_info.value.component

    def test_v2_tampered_base_tar_fails(self, tmp_path: Path) -> None:
        """base.tar.gz 被篡改时 v2 校验失败。"""
        manifest = self._make_v2_backup(tmp_path)
        # 篡改 base.tar.gz
        (tmp_path / PG_BASEBACKUP_DIRNAME / BASE_TAR_GZ_FILENAME).write_bytes(b"tampered")

        validator = BackupManifestValidator()
        with pytest.raises(ManifestValidationError) as exc_info:
            validator.validate(manifest, tmp_path)

        assert "pg_basebackup" in exc_info.value.component

    def test_v2_tampered_wal_tar_fails(self, tmp_path: Path) -> None:
        """pg_wal.tar.gz 被篡改时 v2 校验失败。"""
        manifest = self._make_v2_backup(tmp_path)
        # 篡改 pg_wal.tar.gz
        (tmp_path / PG_BASEBACKUP_DIRNAME / PG_WAL_TAR_GZ_FILENAME).write_bytes(b"tampered-wal")

        validator = BackupManifestValidator()
        with pytest.raises(ManifestValidationError) as exc_info:
            validator.validate(manifest, tmp_path)

        assert "pg_wal" in exc_info.value.component

    def test_v2_tampered_mirror_fails(self, tmp_path: Path) -> None:
        """minio_mirror 对象被篡改时 v2 校验失败。"""
        manifest = self._make_v2_backup(tmp_path)
        # 篡改 mirror 对象
        (tmp_path / MINIO_MIRROR_DIRNAME / "obj1").write_bytes(b"tampered")

        validator = BackupManifestValidator()
        with pytest.raises(ManifestValidationError) as exc_info:
            validator.validate(manifest, tmp_path)

        assert "minio_mirror" in exc_info.value.component

    def test_v2_missing_mirror_dir_with_count_fails(self, tmp_path: Path) -> None:
        """minio_mirror 目录缺失但 manifest 记录有对象时校验失败。"""
        manifest = self._make_v2_backup(tmp_path)
        # 删除 minio_mirror 目录
        import shutil

        shutil.rmtree(tmp_path / MINIO_MIRROR_DIRNAME)

        validator = BackupManifestValidator()
        with pytest.raises(ManifestValidationError) as exc_info:
            validator.validate(manifest, tmp_path)

        assert "minio_mirror" in exc_info.value.component


# ============================================================
# manifest 序列化与加载
# ============================================================


class TestManifestSerialization:
    """v2 manifest 序列化/反序列化测试。"""

    def test_v2_manifest_round_trip(self, tmp_path: Path) -> None:
        """v2 manifest 序列化为 JSON 再反序列化后保持一致。"""
        pg_dir = tmp_path / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir()
        (pg_dir / BASE_TAR_GZ_FILENAME).write_bytes(b"base")

        mirror_dir = tmp_path / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()

        manifest = compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
            backup_id="round-trip-id",
            backup_timestamp="2026-08-16T02:00:00.000+00:00",
            wal_start_lsn="0/2000000",
            wal_end_lsn="0/2000123",
        )

        json_str = manifest.to_json()
        restored = BackupManifest.from_json(json_str)

        assert restored.format_version == 2
        assert restored.backup_id == "round-trip-id"
        assert restored.extra["backup_timestamp"] == "2026-08-16T02:00:00.000+00:00"
        assert restored.extra["backup_method"] == "pitr"
        assert restored.extra["wal_start_lsn"] == "0/2000000"
        assert restored.extra["wal_end_lsn"] == "0/2000123"

    def test_v2_manifest_save_and_load(self, tmp_path: Path) -> None:
        """v2 manifest 保存到文件后再加载保持一致。"""
        pg_dir = tmp_path / "backup_id_001" / PG_BASEBACKUP_DIRNAME
        pg_dir.mkdir(parents=True)
        (pg_dir / BASE_TAR_GZ_FILENAME).write_bytes(b"base")

        mirror_dir = tmp_path / "backup_id_001" / MINIO_MIRROR_DIRNAME
        mirror_dir.mkdir()

        manifest = compute_manifest_v2(
            pg_basebackup_dir=pg_dir,
            minio_mirror_dir=mirror_dir,
            application_version="0.1.0",
            migration_version="0061",
            backup_id="save-load-id",
            backup_timestamp="2026-08-16T02:00:00.000+00:00",
        )

        backup_dir = tmp_path / "backup_id_001"
        save_manifest(manifest, backup_dir)

        loaded = load_manifest(backup_dir)
        assert loaded.format_version == 2
        assert loaded.backup_id == "save-load-id"
        assert loaded.extra["backup_method"] == "pitr"
