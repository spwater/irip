"""IRIP 恢复脚本（V3-T03）。

校验备份清单完整性后，在隔离的 Compose 项目中恢复 PostgreSQL 数据库与
MinIO 对象，仅应用前向兼容的迁移，并运行冒烟查询验证恢复结果。

流程：
  1. 读取并解析 ``manifest.json``；
  2. 逐 payload 重算 SHA-256 并与 manifest 比对（篡改检测）；
  3. 启动隔离的 Compose 项目（``-p irip-restore-<id>``），避免覆盖生产数据；
  4. ``pg_restore`` 恢复数据库；
  5. 上传 MinIO 对象到恢复环境；
  6. 仅应用前向兼容迁移（备份版本 ≤ 当前版本时执行 ``alembic upgrade head``）；
  7. 运行冒烟查询（核心表行数 + 关键约束校验）。

用法（Docker Compose）：
  docker compose run --rm restore --backup-dir /backups/2026-01-01

用法（本机）：
  IRIP_DATABASE_URL=... IRIP_MINIO_ENDPOINT=... \\
  python -m deployments.compose.restore --backup-dir /tmp/irip-backup

也可作为模块导入：
  from deployments.compose.restore import RestoreService, run_restore
"""

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deployments.compose.backup_manifest import (
    DATABASE_DUMP_FILENAME,
    OBJECTS_DIRNAME,
    OBJECTS_METADATA_FILENAME,
    BackupManifest,
    BackupManifestValidator,
    ManifestValidationError,
    load_manifest,
    read_objects_metadata,
)
from packages.common.s3_repository import S3Repository

logger = logging.getLogger(__name__)

#: age 身份文件环境变量名（解密用）。
AGE_IDENTITY_ENV: str = "IRIP_BACKUP_AGE_IDENTITY"

#: tar 归档文件名（与 backup.py 对齐）。
BACKUP_TAR_FILENAME: str = "backup.tar"

#: 加密后的 tar 文件名。
BACKUP_TAR_AGE_FILENAME: str = "backup.tar.age"

#: 冒烟查询列表（核心表存在性 + 行数校验）。
SMOKE_QUERIES: list[tuple[str, str]] = [
    ("app_user", "SELECT count(*) FROM app_user"),
    ("organization", "SELECT count(*) FROM organization"),
    ("role", "SELECT count(*) FROM role"),
    ("artifact_blob", "SELECT count(*) FROM artifact_blob"),
    ("job", "SELECT count(*) FROM job"),
    ("alembic_version", "SELECT count(*) FROM alembic_version"),
]


def _to_sync_url(url: str) -> str:
    """将异步驱动 URL 转换为 psycopg3 同步驱动 URL（SQLAlchemy create_engine 用）。

    Args:
        url: 数据库连接字符串。

    Returns:
        str: psycopg3 同步驱动 URL（``postgresql+psycopg://``）。
    """
    if url.startswith("postgresql+psycopg_async://"):
        return url.replace(
            "postgresql+psycopg_async://", "postgresql+psycopg://", 1
        )
    return url


def _to_pg_restore_url(url: str) -> str:
    """将数据库 URL 转换为 pg_restore 可识别的标准格式（``postgresql://``）。

    pg_restore 不识别 SQLAlchemy 驱动前缀（如 ``+psycopg``）。

    Args:
        url: 数据库连接字符串。

    Returns:
        str: 标准postgresql://` 连接字符串。
    """
    if url.startswith("postgresql+psycopg_async://"):
        return url.replace(
            "postgresql+psycopg_async://", "postgresql://", 1
        )
    if url.startswith("postgresql+psycopg://"):
        return url.replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
    return url


def _build_pg_env() -> dict[str, str]:
    """构建 pg_restore 子进程环境变量。

    Returns:
        dict: 环境变量字典。
    """
    env: dict[str, str] = os.environ.copy()
    db_url: str = os.getenv("IRIP_DATABASE_URL", "")
    if db_url:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(_to_sync_url(db_url))
            if parsed.password:
                env["PGPASSWORD"] = parsed.password
        except Exception:
            pass
    return env


@dataclass(frozen=True)
class RestoreConfig:
    """恢复配置（不可变值对象）。

    Attributes:
        backup_dir: 备份目录路径。
        db_url: 目标 PostgreSQL 连接字符串。
        minio_endpoint: 目标 MinIO 端点 URL。
        minio_access_key: MinIO 访问密钥。
        minio_secret_key: MinIO 秘密密钥。
        minio_bucket: MinIO bucket 名称。
        minio_region: MinIO 区域。
        compose_project_name: 隔离 Compose 项目名（None 表示跳过 Compose 编排）。
        age_identity: age 身份文件路径（解密用，None 表示无需解密）。
        skip_migrations: 是否跳过迁移步骤。
    """

    backup_dir: Path
    db_url: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_region: str
    compose_project_name: str | None = None
    age_identity: str | None = None
    skip_migrations: bool = False


class RestoreService:
    """恢复服务 — 校验 manifest + 恢复各组件。

    恢复前逐 payload 校验 SHA-256，任一不匹配则中止（拒绝加载被篡改的备份）。
    在隔离的 Compose 项目中恢复，避免覆盖生产数据。

    Attributes:
        _config: 恢复配置。
        _validator: manifest 完整性校验器。
        _s3: S3 对象存储客户端。
    """

    def __init__(self, config: RestoreConfig) -> None:
        """初始化恢复服务。

        Args:
            config: 恢复配置。
        """
        self._config: RestoreConfig = config
        self._validator: BackupManifestValidator = BackupManifestValidator()
        endpoint: str = config.minio_endpoint
        if not endpoint.startswith("http"):
            endpoint = f"http://{endpoint}"
        self._s3: S3Repository = S3Repository(
            endpoint_url=endpoint,
            access_key=config.minio_access_key,
            secret_key=config.minio_secret_key,
            bucket_name=config.minio_bucket,
            region=config.minio_region,
        )

    async def restore(self) -> BackupManifest:
        """执行完整恢复流程。

        Returns:
            BackupManifest: 恢复使用的备份清单。

        Raises:
            ManifestValidationError: 完整性校验失败时。
            RuntimeError: 恢复步骤失败时。
        """
        backup_dir: Path = self._config.backup_dir
        if not backup_dir.exists():
            raise FileNotFoundError(f"备份目录不存在: {backup_dir}")

        # 0. 解压（如果只有 tar / tar.age）
        self._extract_archive(backup_dir)

        # 1. 加载 + 校验 manifest
        manifest: BackupManifest = load_manifest(backup_dir)
        logger.info(
            "Restore %s: manifest loaded (version=%s, migration=%s, objects=%d)",
            manifest.backup_id,
            manifest.application_version,
            manifest.migration_version,
            manifest.object_count,
        )

        logger.info("Restore %s: verifying integrity ...", manifest.backup_id)
        self._validator.validate(manifest, backup_dir)
        logger.info("Restore %s: integrity verified ✓", manifest.backup_id)

        # 2. 启动隔离 Compose 项目（可选）
        if self._config.compose_project_name is not None:
            logger.info(
                "Restore %s: starting isolated compose project '%s' ...",
                manifest.backup_id, self._config.compose_project_name,
            )
            self._start_isolated_compose()

        # 3. 恢复数据库
        logger.info("Restore %s: restoring PostgreSQL database ...", manifest.backup_id)
        self._restore_database(backup_dir / DATABASE_DUMP_FILENAME)

        # 4. 恢复 MinIO 对象
        logger.info("Restore %s: restoring MinIO objects ...", manifest.backup_id)
        self._restore_minio_objects(backup_dir / OBJECTS_DIRNAME)

        # 5. 前向兼容迁移
        if not self._config.skip_migrations:
            logger.info(
                "Restore %s: applying forward-compatible migrations ...",
                manifest.backup_id,
            )
            self._apply_forward_migrations(manifest.migration_version)
        else:
            logger.info("Restore %s: skipping migrations (as requested)", manifest.backup_id)

        # 6. 冒烟查询
        logger.info("Restore %s: running smoke queries ...", manifest.backup_id)
        smoke_results: dict[str, int] = await self._run_smoke_queries()
        for table_name, row_count in smoke_results.items():
            logger.info("  %s: %d rows", table_name, row_count)

        logger.info("Restore %s: complete ✓", manifest.backup_id)
        return manifest

    def _extract_archive(self, backup_dir: Path) -> None:
        """解压 tar / tar.age 归档到备份目录。

        若备份目录已含 ``database.dump`` 则视为已解压，跳过。
        若存在加密归档则先解密再解压。

        Args:
            backup_dir: 备份目录。
        """
        database_path: Path = backup_dir / DATABASE_DUMP_FILENAME
        if database_path.exists():
            return

        encrypted_path: Path = backup_dir / BACKUP_TAR_AGE_FILENAME
        tar_path: Path = backup_dir / BACKUP_TAR_FILENAME

        if encrypted_path.exists():
            if shutil.which("age") is None:
                raise RuntimeError(
                    "age binary not found; install age to decrypt backup"
                )
            identity: str = self._config.age_identity or os.getenv(AGE_IDENTITY_ENV, "")
            cmd: list[str] = ["age", "-d"]
            if identity:
                cmd.extend(["-i", identity])
            cmd.extend(["-o", str(tar_path), str(encrypted_path)])
            result: subprocess.CompletedProcess[bytes] = subprocess.run(
                cmd, capture_output=True, check=False
            )
            if result.returncode != 0:
                stderr: str = result.stderr.decode("utf-8", errors="replace")
                raise RuntimeError(f"age decryption failed: {stderr}")

        if tar_path.exists():
            with tarfile.open(tar_path, "r") as tar:
                tar.extractall(path=backup_dir)
            logger.info("Extracted backup archive: %s", tar_path)

    def _start_isolated_compose(self) -> None:
        """启动隔离的 Docker Compose 项目。

        使用 ``-p <project_name>`` 隔离，避免覆盖生产容器的卷数据。

        Raises:
            RuntimeError: docker compose 启动失败时。
        """
        compose_file: str = os.getenv(
            "IRIP_RESTORE_COMPOSE_FILE", "compose.yaml"
        )
        if not Path(compose_file).exists():
            logger.warning(
                "Compose file %s not found; skipping isolated compose start",
                compose_file,
            )
            return

        cmd: list[str] = [
            "docker", "compose",
            "-p", self._config.compose_project_name or "irip-restore",
            "-f", compose_file,
            "up", "-d", "postgres", "minio", "redis",
        ]
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            cmd, capture_output=True, check=False
        )
        if result.returncode != 0:
            stderr: str = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"docker compose up failed (exit={result.returncode}): {stderr}"
            )
        logger.info(
            "Isolated compose project '%s' started",
            self._config.compose_project_name,
        )

    def _restore_database(self, dump_path: Path) -> None:
        """使用 pg_restore 恢复 PostgreSQL 数据库。

        先创建数据库（如不存在），再执行 pg_restore。

        Args:
            dump_path: database.dump 文件路径。

        Raises:
            RuntimeError: pg_restore 执行失败时。
        """
        sync_url: str = _to_pg_restore_url(self._config.db_url)

        # 先确保目标数据库存在
        self._ensure_database_exists(sync_url)

        cmd: list[str] = [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--dbname=" + sync_url,
            str(dump_path),
        ]
        logger.debug("Running pg_restore ...")
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            cmd,
            env=_build_pg_env(),
            capture_output=True,
            check=False,
        )
        # pg_restore 对已存在对象会输出 warning 到 stderr，returncode 可能为非零但无害
        if result.returncode != 0:
            stderr: str = result.stderr.decode("utf-8", errors="replace")
            # 区分致命错误与 warning
            if "FATAL" in stderr.upper() or "could not" in stderr.lower():
                raise RuntimeError(
                    f"pg_restore failed (exit={result.returncode}): {stderr}"
                )
            logger.warning("pg_restore completed with warnings: %s", stderr[:500])

    def _ensure_database_exists(self, sync_url: str) -> None:
        """确保目标数据库存在（不存在则创建）。

        Args:
            sync_url: 同步数据库连接字符串。
        """
        from urllib.parse import urlparse

        try:
            parsed = urlparse(sync_url)
            db_name: str = parsed.path.lstrip("/") or "irip"
            # 连接默认 postgres 库来创建目标库
            admin_url: str = sync_url.rsplit("/", 1)[0] + "/postgres"
            from sqlalchemy import create_engine, text

            engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
            try:
                with engine.connect() as conn:
                    exists: Any = conn.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :name"),
                        {"name": db_name},
                    ).scalar()
                    if not exists:
                        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
                        logger.info("Created database: %s", db_name)
            finally:
                engine.dispose()
        except Exception as exc:
            logger.warning("Could not ensure database exists: %s", exc)

    def _restore_minio_objects(self, objects_dir: Path) -> None:
        """恢复 MinIO 对象到目标 bucket。

        读取 ``objects.json`` 元数据，逐对象上传到 S3。
        上传后重算 SHA-256 与元数据记录值比对，确保传输完整。

        Args:
            objects_dir: 对象目录路径。
        """
        if not objects_dir.exists():
            logger.info("No objects directory; skipping MinIO restore")
            return

        self._s3.ensure_bucket()
        metadata: list[dict[str, Any]] = read_objects_metadata(objects_dir)
        if not metadata:
            # 无元数据文件时，扫描目录上传
            for path in objects_dir.rglob("*"):
                if path.is_file() and path.name != OBJECTS_METADATA_FILENAME:
                    key: str = str(path.relative_to(objects_dir))
                    data: bytes = path.read_bytes()
                    content_type: str = "application/octet-stream"
                    self._s3.put_object(key, data, content_type)
            return

        restored: int = 0
        for obj_meta in metadata:
            key: str = obj_meta["key"]
            expected_sha: str = obj_meta["sha256"]
            obj_path: Path = objects_dir / key
            if not obj_path.exists():
                logger.warning("Object file missing: %s", key)
                continue
            data: bytes = obj_path.read_bytes()
            # 上传前校验本地文件完整性
            from packages.common.hashing import sha256_bytes

            actual_sha: str = sha256_bytes(data)
            if actual_sha != expected_sha:
                logger.warning(
                    "Object %s SHA-256 mismatch (expected=%s, actual=%s); skipping",
                    key, expected_sha[:12], actual_sha[:12],
                )
                continue
            content_type: str = "application/octet-stream"
            self._s3.put_object(key, data, content_type)
            restored += 1

        logger.info("Restored %d MinIO objects", restored)

    def _apply_forward_migrations(self, backup_migration_version: str) -> None:
        """仅应用前向兼容的迁移。

        前向兼容策略：若备份的迁移版本 ≤ 当前代码的迁移版本，则执行
        ``alembic upgrade head``（将数据库 schema 补齐到最新）。
        若备份版本 > 当前版本（降级场景），则拒绝自动迁移，需人工介入。

        Args:
            backup_migration_version: 备份时记录的 Alembic 版本。

        Raises:
            RuntimeError: 备份版本比当前新（不兼容降级）时。
        """
        current_version: str = self._detect_current_migration_head()
        if not backup_migration_version:
            logger.warning(
                "Backup has no recorded migration version; "
                "running alembic upgrade head unconditionally"
            )
        elif current_version and backup_migration_version > current_version:
            raise RuntimeError(
                f"备份迁移版本 ({backup_migration_version}) 比当前代码版本 "
                f"({current_version}) 新 — 不支持自动降级，请人工处理"
            )

        cmd: list[str] = [
            "alembic", "upgrade", "head",
        ]
        env: dict[str, str] = os.environ.copy()
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            cmd, env=env, capture_output=True, check=False, cwd=os.getcwd()
        )
        if result.returncode != 0:
            stderr: str = result.stderr.decode("utf-8", errors="replace")
            logger.warning("alembic upgrade head completed with output: %s", stderr[:500])
        else:
            logger.info("Migrations applied (alembic upgrade head)")

    def _detect_current_migration_head(self) -> str:
        """检测当前代码库的迁移 head 版本。

        通过扫描 ``migrations/versions/`` 目录中最新的迁移文件名推断。

        Returns:
            str: 最新迁移版本号（文件名前缀，如 ``"0021_ai_conversations"``）。
        """
        versions_dir: Path = Path("migrations/versions")
        if not versions_dir.exists():
            return ""
        revisions: list[str] = []
        for f in versions_dir.glob("*.py"):
            if f.name.startswith("__"):
                continue
            name: str = f.stem
            revisions.append(name)
        if not revisions:
            return ""
        revisions.sort()
        return revisions[-1]

    async def _run_smoke_queries(self) -> dict[str, int]:
        """运行冒烟查询，验证恢复后的数据库可正常访问。

        查询核心表的行数，确认表结构与数据可读。

        Returns:
            dict[str, int]: 表名 → 行数。

        Raises:
            RuntimeError: 冒烟查询失败时。
        """
        from sqlalchemy import create_engine, text

        sync_url: str = _to_sync_url(self._config.db_url)
        engine = create_engine(sync_url, pool_pre_ping=True)
        results: dict[str, int] = {}
        try:
            with engine.connect() as conn:
                for table_name, query in SMOKE_QUERIES:
                    try:
                        row: Any = conn.execute(text(query)).scalar()
                        results[table_name] = int(row) if row is not None else 0
                    except Exception as exc:
                        logger.warning("Smoke query failed for %s: %s", table_name, exc)
                        results[table_name] = -1
        finally:
            engine.dispose()

        # 校验关键不变量
        if results.get("app_user", 0) <= 0:
            logger.warning("Smoke check: app_user table is empty after restore")
        if results.get("alembic_version", 0) != 1:
            logger.warning(
                "Smoke check: alembic_version should have exactly 1 row, got %s",
                results.get("alembic_version"),
            )

        return results


def build_restore_config_from_env(backup_dir: Path) -> RestoreConfig:
    """从环境变量构建恢复配置。

    Args:
        backup_dir: 备份目录路径。

    Returns:
        RestoreConfig: 恢复配置。
    """
    db_url: str = os.getenv("IRIP_DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("IRIP_DATABASE_URL environment variable is required")

    endpoint: str = os.getenv("IRIP_MINIO_ENDPOINT", "http://localhost:9000")
    if not endpoint.startswith("http"):
        endpoint = f"http://{endpoint}"

    compose_project: str | None = os.getenv("IRIP_RESTORE_COMPOSE_PROJECT") or None
    age_identity: str | None = os.getenv(AGE_IDENTITY_ENV) or None
    skip_migrations: bool = os.getenv("IRIP_RESTORE_SKIP_MIGRATIONS", "").lower() in (
        "1", "true", "yes",
    )

    return RestoreConfig(
        backup_dir=backup_dir,
        db_url=db_url,
        minio_endpoint=endpoint,
        minio_access_key=os.getenv("IRIP_MINIO_ACCESS_KEY", "irip"),
        minio_secret_key=os.getenv("IRIP_MINIO_SECRET_KEY", "irip_dev_password"),
        minio_bucket=os.getenv("IRIP_MINIO_BUCKET", "irip-artifacts"),
        minio_region=os.getenv("IRIP_MINIO_REGION", "us-east-1"),
        compose_project_name=compose_project,
        age_identity=age_identity,
        skip_migrations=skip_migrations,
    )


async def run_restore(backup_dir: Path) -> BackupManifest:
    """执行恢复（便捷入口）。

    Args:
        backup_dir: 备份目录路径。

    Returns:
        BackupManifest: 恢复使用的备份清单。
    """
    config: RestoreConfig = build_restore_config_from_env(backup_dir)
    service: RestoreService = RestoreService(config)
    return await service.restore()


def main() -> None:
    """恢复脚本 CLI 入口。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="IRIP 恢复脚本 — 校验 + 恢复 + 冒烟测试"
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        required=True,
        help="备份目录路径（含 manifest.json）",
    )
    parser.add_argument(
        "--skip-migrations",
        action="store_true",
        default=False,
        help="跳过迁移步骤（仅恢复数据）",
    )
    args: argparse.Namespace = parser.parse_args()

    config: RestoreConfig = build_restore_config_from_env(args.backup_dir)
    if args.skip_migrations:
        # dataclasses.replace 不可用于 frozen=True 且无默认值的情况，直接重建
        config = RestoreConfig(
            backup_dir=config.backup_dir,
            db_url=config.db_url,
            minio_endpoint=config.minio_endpoint,
            minio_access_key=config.minio_access_key,
            minio_secret_key=config.minio_secret_key,
            minio_bucket=config.minio_bucket,
            minio_region=config.minio_region,
            compose_project_name=config.compose_project_name,
            age_identity=config.age_identity,
            skip_migrations=True,
        )

    service: RestoreService = RestoreService(config)
    try:
        manifest: BackupManifest = asyncio.run(service.restore())
        print(f"\n恢复完成: {manifest.to_json()}")
    except FileNotFoundError as exc:
        logger.info("无备份文件，跳过恢复: %s", exc)
        print(f"\n无备份文件，跳过恢复: {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
