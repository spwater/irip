"""备份清单数据结构 + 完整性校验（IRIP V3-T03）。

提供：
- ``BackupManifest``: frozen dataclass，携带各组件 SHA-256 校验和与版本元数据；
- ``compute_manifest(...)``: 计算 PostgreSQL dump SHA-256 + MinIO 对象 SHA-256；
- ``BackupManifestValidator``: 恢复前校验 manifest 完整性（逐文件重算哈希并比对）。

设计要点（docs/arch/v3-architecture.md §3.6）：
- manifest 为不可变值对象，序列化为 ``manifest.json`` 随备份包存储；
- 完整性校验采用「逐 payload 重算 SHA-256 → 与 manifest 记录值比对」策略，
  任一不匹配即中止恢复，拒绝加载被篡改的备份；
- ``objects_sha256`` 为 MinIO 全部对象的聚合哈希（对对象元数据 JSON 排序后取 SHA-256），
  保证确定性。
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packages.common.hashing import sha256_bytes

#: manifest 格式版本（结构变更时递增）。
#: v1 = pg_dump + S3Repository 逻辑备份；v2 = pg_basebackup + mc mirror 物理备份（PITR）。
MANIFEST_FORMAT_VERSION: int = 2

#: manifest 文件名。
MANIFEST_FILENAME: str = "manifest.json"

#: 对象元数据文件名（记录每个 MinIO 对象的 key + sha256 + size）。
OBJECTS_METADATA_FILENAME: str = "objects.json"

#: 数据库 dump 文件名（custom 格式）。
DATABASE_DUMP_FILENAME: str = "database.dump"

#: MinIO 对象目录名。
OBJECTS_DIRNAME: str = "objects"

#: v2: pg_basebackup 产出目录名。
PG_BASEBACKUP_DIRNAME: str = "pg_basebackup"

#: v2: base.tar.gz 文件名（pg_basebackup -Ft -z 数据目录）。
BASE_TAR_GZ_FILENAME: str = "base.tar.gz"

#: v2: pg_wal.tar.gz 文件名（pg_basebackup -X stream WAL）。
PG_WAL_TAR_GZ_FILENAME: str = "pg_wal.tar.gz"

#: v2: mc mirror 产出目录名。
MINIO_MIRROR_DIRNAME: str = "minio_mirror"


@dataclass(frozen=True)
class BackupManifest:
    """备份清单（不可变值对象）。

    携带备份的版本元数据与各组件 SHA-256 校验和，序列化为 ``manifest.json``。
    恢复时逐 payload 重算哈希并与此处记录值比对，任一不匹配即拒绝恢复。

    Attributes:
        format_version: manifest 格式版本（当前为 1）。
        created_at: 备份创建时间（UTC）。
        application_version: 备份时的 IRIP 应用版本（如 ``"0.8.0"``）。
        migration_version: 备份时的 Alembic 迁移版本（alembic_version 表的 head revision）。
        database_sha256: PostgreSQL dump 文件的 SHA-256 摘要（hex 小写）。
        object_count: MinIO 对象总数。
        objects_sha256: MinIO 全部对象的聚合 SHA-256 摘要（hex 小写）。
        encrypted: 备份包是否已加密（age）。
        backup_id: 备份唯一标识（UUID 字符串）。
    """

    format_version: int
    created_at: datetime
    application_version: str
    migration_version: str
    database_sha256: str
    object_count: int
    objects_sha256: str
    encrypted: bool = False
    backup_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 化的字典。

        Returns:
            dict: manifest 字典表示，``created_at`` 转为 ISO 8601 字符串。
        """
        data: dict[str, Any] = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        return data

    def to_json(self) -> str:
        """序列化为 JSON 字符串（缩进 2 空格，键排序）。

        Returns:
            str: manifest 的 JSON 表示。
        """
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackupManifest":
        """从字典反序列化 manifest。

        Args:
            data: manifest 字典（``created_at`` 可为 ISO 字符串或 datetime）。

        Returns:
            BackupManifest: 反序列化后的 manifest 实例。
        """
        created_at_raw: Any = data.get("created_at")
        if isinstance(created_at_raw, str):
            created_at: datetime = datetime.fromisoformat(created_at_raw)
        elif isinstance(created_at_raw, datetime):
            created_at = created_at_raw
        else:
            created_at = datetime.now(UTC)

        return cls(
            format_version=int(data.get("format_version", MANIFEST_FORMAT_VERSION)),
            created_at=created_at,
            application_version=str(data.get("application_version", "")),
            migration_version=str(data.get("migration_version", "")),
            database_sha256=str(data.get("database_sha256", "")),
            object_count=int(data.get("object_count", 0)),
            objects_sha256=str(data.get("objects_sha256", "")),
            encrypted=bool(data.get("encrypted", False)),
            backup_id=str(data.get("backup_id", "")),
            extra=dict(data.get("extra", {})),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "BackupManifest":
        """从 JSON 字符串反序列化 manifest。

        Args:
            json_str: manifest 的 JSON 字符串。

        Returns:
            BackupManifest: 反序列化后的 manifest 实例。
        """
        return cls.from_dict(json.loads(json_str))


def _sha256_file(path: Path) -> str:
    """计算文件 SHA-256 摘要（流式读取，支持大文件）。

    Args:
        path: 文件路径。

    Returns:
        str: 64 位小写十六进制 SHA-256 摘要。
    """
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk: bytes = f.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_objects_metadata(objects_dir: Path) -> list[dict[str, Any]]:
    """扫描对象目录，计算每个对象的 key + sha256 + size。

    对象目录结构：``objects/<key 路径>``，每个文件为一个 MinIO 对象。
    返回按 key 排序的元数据列表，用于确定性聚合哈希计算。

    Args:
        objects_dir: MinIO 对象目录路径。

    Returns:
        list[dict]: 排序后的对象元数据列表，每项含 ``key``、``sha256``、``size``。
    """
    metadata: list[dict[str, Any]] = []
    if not objects_dir.exists():
        return metadata

    for path in sorted(objects_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == OBJECTS_METADATA_FILENAME:
            continue
        rel_key: str = str(path.relative_to(objects_dir))
        size_bytes: int = path.stat().st_size
        metadata.append(
            {
                "key": rel_key,
                "sha256": _sha256_file(path),
                "size": size_bytes,
                "size_bytes": size_bytes,
            }
        )
    return metadata


def compute_objects_aggregate_sha256(objects_dir: Path) -> tuple[str, int, list[dict[str, Any]]]:
    """计算 MinIO 全部对象的聚合 SHA-256 + 对象计数。

    聚合策略：将全部对象元数据（key + sha256 + size）按 key 排序后序列化为
    JSON（sort_keys=True），对该 JSON 字节流取 SHA-256。保证确定性。

    Args:
        objects_dir: MinIO 对象目录路径。

    Returns:
        tuple: ``(aggregate_sha256, object_count, objects_metadata)``。
    """
    metadata: list[dict[str, Any]] = compute_objects_metadata(objects_dir)
    metadata_json: str = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
    aggregate_sha: str = sha256_bytes(metadata_json.encode("utf-8"))
    return aggregate_sha, len(metadata), metadata


def compute_manifest(
    database_dump_path: Path,
    objects_dir: Path,
    application_version: str,
    migration_version: str,
    backup_id: str = "",
    encrypted: bool = False,
    extra: dict[str, Any] | None = None,
) -> BackupManifest:
    """计算 PostgreSQL dump SHA-256 + MinIO 对象 SHA-256，生成 BackupManifest。

    Args:
        database_dump_path: PostgreSQL custom 格式 dump 文件路径。
        objects_dir: MinIO 对象目录路径。
        application_version: IRIP 应用版本（如 ``"0.8.0"``）。
        migration_version: Alembic 迁移版本（alembic_version head revision）。
        backup_id: 备份唯一标识（UUID 字符串）。
        encrypted: 备份包是否已加密。
        extra: 可选扩展字段（如 backup_type、name、description），向后兼容。

    Returns:
        BackupManifest: 包含全部校验和的备份清单。
    """
    database_sha: str = _sha256_file(database_dump_path)
    objects_sha: str
    object_count: int
    objects_sha, object_count, _ = compute_objects_aggregate_sha256(objects_dir)

    return BackupManifest(
        format_version=1,
        created_at=datetime.now(UTC),
        application_version=application_version,
        migration_version=migration_version,
        database_sha256=database_sha,
        object_count=object_count,
        objects_sha256=objects_sha,
        encrypted=encrypted,
        backup_id=backup_id,
        extra=extra if extra is not None else {},
    )


def _aggregate_sha256_dir(directory: Path) -> tuple[str, int]:
    """计算目录下全部文件的聚合 SHA-256 + 文件计数。

    聚合策略：遍历目录下所有文件（按路径排序），对每个文件计算 SHA-256，
    将文件路径 + SHA-256 + 文件大小序列化为 JSON（sort_keys=True），
    对该 JSON 字节流取 SHA-256。保证确定性。

    Args:
        directory: 目录路径。

    Returns:
        tuple: ``(aggregate_sha256, file_count)``。
    """
    metadata: list[dict[str, Any]] = []
    if directory.exists():
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            rel_key: str = str(path.relative_to(directory))
            file_size_bytes: int = path.stat().st_size
            metadata.append(
                {
                    "key": rel_key,
                    "sha256": _sha256_file(path),
                    "size": file_size_bytes,
                    "size_bytes": file_size_bytes,
                }
            )
    metadata_json: str = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
    aggregate_sha: str = sha256_bytes(metadata_json.encode("utf-8"))
    return aggregate_sha, len(metadata)


def _dir_total_size_bytes(directory: Path) -> int:
    """计算目录下全部文件的总字节数。

    Args:
        directory: 目录路径。

    Returns:
        int: 全部文件大小之和（字节）；目录不存在时返回 0。
    """
    total: int = 0
    if directory.exists():
        for path in directory.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
    return total


def compute_manifest_v2(
    pg_basebackup_dir: Path,
    minio_mirror_dir: Path,
    application_version: str,
    migration_version: str,
    backup_id: str = "",
    backup_timestamp: str = "",
    wal_start_lsn: str = "",
    wal_end_lsn: str = "",
    db_system_identifier: str = "",
) -> BackupManifest:
    """计算 PITR 备份的 BackupManifest v2。

    v2 manifest 的 extra dict 存储 PITR 元数据：
    - backup_timestamp: 联合时间戳
    - backup_method: 'pitr'
    - db_system_identifier: PostgreSQL 集群系统标识（pg_control_system）
    - pg_basebackup_sha256 / pg_basebackup_size_bytes: base.tar.gz 的 SHA-256 与字节数
    - pg_wal_sha256 / pg_wal_size_bytes: pg_wal.tar.gz 的 SHA-256 与字节数
    - minio_mirror_sha256 / minio_mirror_size_bytes: minio_mirror/ 聚合 SHA-256 与总字节数
    - minio_mirror_object_count: minio_mirror/ 对象数
    - wal_start_lsn: 备份开始 WAL LSN
    - wal_end_lsn: 备份结束 WAL LSN

    每个 artifact 均携带 ``sha256`` 与 ``size_bytes``，便于恢复前完整性 +
    容量校验。``created_at`` 记录备份创建时间戳，``application_version`` /
    ``migration_version`` 记录应用版本与迁移头。

    v2 复用 v1 的 database_sha256 / object_count / objects_sha256 字段以保持
    dataclass 兼容性：
    - database_sha256 = base.tar.gz 的 SHA-256
    - object_count = minio_mirror 对象数
    - objects_sha256 = minio_mirror 聚合 SHA-256

    Args:
        pg_basebackup_dir: pg_basebackup 产出目录（含 base.tar.gz + pg_wal.tar.gz）。
        minio_mirror_dir: mc mirror 产出目录。
        application_version: IRIP 应用版本。
        migration_version: Alembic 迁移版本（迁移头）。
        backup_id: 备份唯一标识。
        backup_timestamp: 联合时间戳（UTC ISO 8601）。
        wal_start_lsn: 备份开始时的 WAL LSN。
        wal_end_lsn: 备份结束时的 WAL LSN。
        db_system_identifier: PostgreSQL 数据库系统标识（集群唯一）。

    Returns:
        BackupManifest: format_version=2 的备份清单。
    """
    base_tar_path: Path = pg_basebackup_dir / BASE_TAR_GZ_FILENAME
    pg_wal_tar_path: Path = pg_basebackup_dir / PG_WAL_TAR_GZ_FILENAME

    base_sha: str = _sha256_file(base_tar_path) if base_tar_path.exists() else ""
    base_size: int = base_tar_path.stat().st_size if base_tar_path.exists() else 0
    wal_sha: str = _sha256_file(pg_wal_tar_path) if pg_wal_tar_path.exists() else ""
    wal_size: int = pg_wal_tar_path.stat().st_size if pg_wal_tar_path.exists() else 0

    mirror_sha: str
    mirror_count: int
    mirror_sha, mirror_count = _aggregate_sha256_dir(minio_mirror_dir)
    mirror_size_bytes: int = _dir_total_size_bytes(minio_mirror_dir)

    extra: dict[str, Any] = {
        "backup_timestamp": backup_timestamp,
        "backup_method": "pitr",
        "db_system_identifier": db_system_identifier,
        "pg_basebackup_sha256": base_sha,
        "pg_basebackup_size_bytes": base_size,
        "pg_wal_sha256": wal_sha,
        "pg_wal_size_bytes": wal_size,
        "minio_mirror_sha256": mirror_sha,
        "minio_mirror_object_count": mirror_count,
        "minio_mirror_size_bytes": mirror_size_bytes,
        "wal_start_lsn": wal_start_lsn,
        "wal_end_lsn": wal_end_lsn,
    }

    return BackupManifest(
        format_version=2,
        created_at=datetime.now(UTC),
        application_version=application_version,
        migration_version=migration_version,
        database_sha256=base_sha,
        object_count=mirror_count,
        objects_sha256=mirror_sha,
        encrypted=False,
        backup_id=backup_id,
        extra=extra,
    )


def write_objects_metadata(objects_dir: Path, metadata: list[dict[str, Any]]) -> Path:
    """将对象元数据写入 ``objects.json``（供恢复时逐对象校验）。

    Args:
        objects_dir: MinIO 对象目录路径。
        metadata: 对象元数据列表（由 compute_objects_metadata 生成）。

    Returns:
        Path: 写入的元数据文件路径。
    """
    metadata_path: Path = objects_dir / OBJECTS_METADATA_FILENAME
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return metadata_path


def read_objects_metadata(objects_dir: Path) -> list[dict[str, Any]]:
    """读取 ``objects.json`` 对象元数据。

    Args:
        objects_dir: MinIO 对象目录路径。

    Returns:
        list[dict]: 对象元数据列表。文件不存在时返回空列表。
    """
    metadata_path: Path = objects_dir / OBJECTS_METADATA_FILENAME
    if not metadata_path.exists():
        return []
    return json.loads(metadata_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


class BackupManifestValidator:
    """备份清单完整性校验器。

    恢复前逐 payload 重算 SHA-256 并与 manifest 记录值比对，
    任一不匹配即抛出 ``ManifestValidationError``，拒绝加载被篡改的备份。
    """

    def validate(self, manifest: BackupManifest, backup_dir: Path) -> bool:
        """校验备份目录中全部 payload 的哈希与 manifest 记录一致。

        根据 manifest.format_version 分流到 v1 或 v2 校验逻辑。

        Args:
            manifest: 备份清单。
            backup_dir: 备份目录。

        Returns:
            bool: 全部校验通过返回 True。

        Raises:
            ManifestValidationError: 任一哈希不匹配或文件缺失时。
        """
        if manifest.format_version == 1:
            return self._validate_v1(manifest, backup_dir)
        elif manifest.format_version == 2:
            return self._validate_v2(manifest, backup_dir)
        else:
            raise ManifestValidationError(
                f"不支持的 manifest 版本: {manifest.format_version}",
                component="format_version",
            )

    def _validate_v1(self, manifest: BackupManifest, backup_dir: Path) -> bool:
        """校验 v1 备份（pg_dump + S3Repository 格式）。

        校验 database.dump SHA-256 + objects/ 聚合 SHA-256。

        Args:
            manifest: 备份清单。
            backup_dir: 备份目录（包含 database.dump 与 objects/ 子目录）。

        Returns:
            bool: 全部校验通过返回 True。

        Raises:
            ManifestValidationError: 任一哈希不匹配或文件缺失时。
        """
        database_path: Path = backup_dir / DATABASE_DUMP_FILENAME
        objects_dir: Path = backup_dir / OBJECTS_DIRNAME

        # 校验数据库 dump
        if not database_path.exists():
            raise ManifestValidationError(
                f"数据库 dump 文件缺失: {database_path}",
                component="database",
            )
        actual_db_sha: str = _sha256_file(database_path)
        if not self.verify_checksum("database", database_path, manifest.database_sha256):
            raise ManifestValidationError(
                f"数据库 dump SHA-256 不匹配: expected={manifest.database_sha256}, "
                f"actual={actual_db_sha}",
                component="database",
                expected=manifest.database_sha256,
                actual=actual_db_sha,
            )

        # 校验 MinIO 对象聚合哈希
        if not objects_dir.exists():
            if manifest.object_count > 0:
                raise ManifestValidationError(
                    f"对象目录缺失但 manifest 记录 {manifest.object_count} 个对象",
                    component="objects",
                )
        else:
            actual_objects_sha: str
            actual_count: int
            actual_objects_sha, actual_count, _ = compute_objects_aggregate_sha256(objects_dir)
            if actual_count != manifest.object_count:
                raise ManifestValidationError(
                    f"对象数量不匹配: expected={manifest.object_count}, actual={actual_count}",
                    component="objects",
                    expected=str(manifest.object_count),
                    actual=str(actual_count),
                )
            if actual_objects_sha != manifest.objects_sha256:
                raise ManifestValidationError(
                    f"对象聚合 SHA-256 不匹配: expected={manifest.objects_sha256}, "
                    f"actual={actual_objects_sha}",
                    component="objects",
                    expected=manifest.objects_sha256,
                    actual=actual_objects_sha,
                )

        return True

    def _validate_v2(self, manifest: BackupManifest, backup_dir: Path) -> bool:
        """校验 v2 备份（pg_basebackup + mc mirror PITR 格式）。

        校验 base.tar.gz SHA-256 + pg_wal.tar.gz SHA-256 + minio_mirror/ 聚合 SHA-256。
        期望值从 manifest.extra dict 读取。

        Args:
            manifest: 备份清单（format_version=2）。
            backup_dir: 备份目录（包含 pg_basebackup/ 与 minio_mirror/ 子目录）。

        Returns:
            bool: 全部校验通过返回 True。

        Raises:
            ManifestValidationError: 任一哈希不匹配或文件缺失时。
        """
        extra: dict[str, Any] = manifest.extra
        pg_basebackup_dir: Path = backup_dir / PG_BASEBACKUP_DIRNAME
        minio_mirror_dir: Path = backup_dir / MINIO_MIRROR_DIRNAME

        # 校验 base.tar.gz
        expected_base_sha: str = str(extra.get("pg_basebackup_sha256", ""))
        base_tar_path: Path = pg_basebackup_dir / BASE_TAR_GZ_FILENAME
        if not base_tar_path.exists():
            raise ManifestValidationError(
                f"base.tar.gz 缺失: {base_tar_path}",
                component="pg_basebackup",
            )
        actual_base_sha: str = _sha256_file(base_tar_path)
        if expected_base_sha and actual_base_sha != expected_base_sha:
            raise ManifestValidationError(
                f"base.tar.gz SHA-256 不匹配: expected={expected_base_sha}, "
                f"actual={actual_base_sha}",
                component="pg_basebackup",
                expected=expected_base_sha,
                actual=actual_base_sha,
            )

        # 校验 pg_wal.tar.gz（如存在期望值）
        expected_wal_sha: str = str(extra.get("pg_wal_sha256", ""))
        pg_wal_tar_path: Path = pg_basebackup_dir / PG_WAL_TAR_GZ_FILENAME
        if expected_wal_sha:
            if not pg_wal_tar_path.exists():
                raise ManifestValidationError(
                    f"pg_wal.tar.gz 缺失: {pg_wal_tar_path}",
                    component="pg_wal",
                )
            actual_wal_sha: str = _sha256_file(pg_wal_tar_path)
            if actual_wal_sha != expected_wal_sha:
                raise ManifestValidationError(
                    f"pg_wal.tar.gz SHA-256 不匹配: expected={expected_wal_sha}, "
                    f"actual={actual_wal_sha}",
                    component="pg_wal",
                    expected=expected_wal_sha,
                    actual=actual_wal_sha,
                )

        # 校验 minio_mirror 聚合 SHA-256
        expected_mirror_sha: str = str(extra.get("minio_mirror_sha256", ""))
        expected_mirror_count: int = int(extra.get("minio_mirror_object_count", 0))
        if not minio_mirror_dir.exists():
            if expected_mirror_count > 0:
                raise ManifestValidationError(
                    f"minio_mirror 目录缺失但 manifest 记录 {expected_mirror_count} 个对象",
                    component="minio_mirror",
                )
        else:
            actual_mirror_sha: str
            actual_mirror_count: int
            actual_mirror_sha, actual_mirror_count = _aggregate_sha256_dir(minio_mirror_dir)
            if actual_mirror_count != expected_mirror_count:
                raise ManifestValidationError(
                    f"minio_mirror 对象数量不匹配: expected={expected_mirror_count}, "
                    f"actual={actual_mirror_count}",
                    component="minio_mirror",
                    expected=str(expected_mirror_count),
                    actual=str(actual_mirror_count),
                )
            if expected_mirror_sha and actual_mirror_sha != expected_mirror_sha:
                raise ManifestValidationError(
                    f"minio_mirror 聚合 SHA-256 不匹配: expected={expected_mirror_sha}, "
                    f"actual={actual_mirror_sha}",
                    component="minio_mirror",
                    expected=expected_mirror_sha,
                    actual=actual_mirror_sha,
                )

        return True

    def verify_checksum(self, component: str, file_path: Path, expected: str) -> bool:
        """校验单个文件的 SHA-256 是否与期望值一致。

        Args:
            component: 组件名（如 ``"database"``）。
            file_path: 文件路径。
            expected: 期望的 SHA-256 摘要（hex 小写）。

        Returns:
            bool: 一致返回 True，否则 False。
        """
        if not file_path.exists():
            return False
        actual: str = _sha256_file(file_path)
        return actual == expected


class ManifestValidationError(Exception):
    """manifest 完整性校验失败异常。

    Attributes:
        message: 错误描述。
        component: 失败的组件名（``"database"`` / ``"objects"``）。
        expected: 期望值（可选）。
        actual: 实际值（可选）。
    """

    def __init__(
        self,
        message: str,
        component: str = "",
        expected: str = "",
        actual: str = "",
    ) -> None:
        """初始化校验错误。

        Args:
            message: 错误描述。
            component: 失败的组件名。
            expected: 期望值。
            actual: 实际值。
        """
        super().__init__(message)
        self.message: str = message
        self.component: str = component
        self.expected: str = expected
        self.actual: str = actual

    def __repr__(self) -> str:
        return f"ManifestValidationError(component={self.component!r}, message={self.message!r})"


def load_manifest(backup_dir: Path) -> BackupManifest:
    """从备份目录加载 manifest.json。

    Args:
        backup_dir: 备份目录路径。

    Returns:
        BackupManifest: 反序列化后的 manifest。

    Raises:
        FileNotFoundError: manifest.json 不存在时。
    """
    manifest_path: Path = backup_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.json not found in {backup_dir}")
    return BackupManifest.from_json(manifest_path.read_text(encoding="utf-8"))


def save_manifest(manifest: BackupManifest, backup_dir: Path) -> Path:
    """将 manifest 写入备份目录的 manifest.json。

    Args:
        manifest: 备份清单。
        backup_dir: 备份目录路径。

    Returns:
        Path: 写入的 manifest 文件路径。
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest_path: Path = backup_dir / MANIFEST_FILENAME
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    return manifest_path
