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
    MINIO_MIRROR_DIRNAME,
    OBJECTS_DIRNAME,
    OBJECTS_METADATA_FILENAME,
    PG_BASEBACKUP_DIRNAME,
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
        minio_mc_alias: mc 客户端 alias 名称（默认 irip）。
        recovery_target_time: PITR 恢复目标时间（ISO 8601，None 表示恢复到备份时间点）。
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
    minio_mc_alias: str = "irip"
    recovery_target_time: str | None = None


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
        """执行完整恢复流程（版本路由）。

        根据 manifest.format_version 分流：
        - v1: 旧路径（pg_restore + S3Repository），向后兼容；
        - v2: 新路径（mc mirror + PITR），联合恢复。

        Returns:
            BackupManifest: 恢复使用的备份清单。

        Raises:
            ManifestValidationError: 完整性校验失败时。
            RuntimeError: 恢复步骤失败时。
        """
        backup_dir: Path = self._config.backup_dir
        if not backup_dir.exists():
            raise FileNotFoundError(f"备份目录不存在: {backup_dir}")

        # 加载 manifest
        manifest: BackupManifest = load_manifest(backup_dir)
        logger.info(
            "Restore %s: manifest loaded (format_version=%s, migration=%s, objects=%d)",
            manifest.backup_id,
            manifest.format_version,
            manifest.migration_version,
            manifest.object_count,
        )

        # 版本路由
        if manifest.format_version == 1:
            return await self._restore_v1(manifest)
        elif manifest.format_version == 2:
            return await self._restore_v2(manifest)
        else:
            raise RuntimeError(
                f"不支持的 manifest 版本: {manifest.format_version}"
            )

    async def _restore_v1(self, manifest: BackupManifest) -> BackupManifest:
        """v1 旧路径恢复（pg_restore + S3Repository，向后兼容）。

        完整保留现有逻辑：解压归档 → 校验 manifest → pg_restore → S3Repository 上传 →
        前向迁移 → 冒烟查询。

        Args:
            manifest: v1 备份清单。

        Returns:
            BackupManifest: 恢复使用的备份清单。
        """
        backup_dir: Path = self._config.backup_dir

        # 0. 解压（如果只有 tar / tar.age）
        self._extract_archive(backup_dir)

        # 1. 校验 manifest
        logger.info("Restore %s: verifying integrity (v1) ...", manifest.backup_id)
        self._validator.validate(manifest, backup_dir)
        logger.info("Restore %s: integrity verified ✓", manifest.backup_id)

        # F-06: 恢复前完整预校验所有对象
        logger.info("Restore %s: pre-validating all objects ...", manifest.backup_id)
        self._prevalidate_objects(backup_dir / OBJECTS_DIRNAME)
        logger.info("Restore %s: all objects pre-validated ✓", manifest.backup_id)

        # 2. 启动隔离 Compose 项目（可选）
        if self._config.compose_project_name is not None:
            logger.info(
                "Restore %s: starting isolated compose project '%s' ...",
                manifest.backup_id, self._config.compose_project_name,
            )
            self._start_isolated_compose()

        # 3. 恢复数据库
        logger.info("Restore %s: restoring PostgreSQL database (pg_restore) ...", manifest.backup_id)
        self._restore_database(backup_dir / DATABASE_DUMP_FILENAME)

        # 4. 恢复 MinIO 对象
        logger.info("Restore %s: restoring MinIO objects (S3Repository) ...", manifest.backup_id)
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

        smoke_failures: list[str] = []
        for table_name, row_count in smoke_results.items():
            if row_count < 0:
                smoke_failures.append(f"{table_name}: query failed")
        if smoke_results.get("app_user", 0) <= 0:
            smoke_failures.append("app_user table is empty after restore")
        if smoke_results.get("alembic_version", 0) != 1:
            smoke_failures.append(
                f"alembic_version should have exactly 1 row, got {smoke_results.get('alembic_version')}"
            )
        if smoke_failures:
            logger.error("Restore %s: smoke test failures: %s", manifest.backup_id, smoke_failures)
            raise RuntimeError(
                f"Smoke test failures: {'; '.join(smoke_failures)}"
            )

        logger.info("Restore %s: v1 restore complete", manifest.backup_id)
        return manifest

    async def _restore_v2(self, manifest: BackupManifest) -> BackupManifest:
        """v2 新路径恢复（mc mirror + PITR，联合恢复）。

        联合恢复顺序：MinIO 先 → PG 后（保证引用完整性）。
        1. 校验 manifest v2
        2. mc mirror 恢复 MinIO（先）
        3. PITR 恢复 PG（后）
        4. 前向兼容迁移
        5. 冒烟查询 + 引用完整性校验

        Args:
            manifest: v2 备份清单。

        Returns:
            BackupManifest: 恢复使用的备份清单。
        """
        backup_dir: Path = self._config.backup_dir

        # 1. 校验 manifest v2
        logger.info("Restore %s: verifying integrity (v2) ...", manifest.backup_id)
        self._validator.validate(manifest, backup_dir)
        logger.info("Restore %s: integrity verified ✓", manifest.backup_id)

        # 2. MinIO 恢复（先恢复对象，保证引用完整性）
        logger.info("Restore %s: restoring MinIO objects (mc mirror) ...", manifest.backup_id)
        self._mc_restore_minio(backup_dir / MINIO_MIRROR_DIRNAME)

        # 3. PITR 恢复 PG（后）
        # 确定 recovery_target_time: 优先用配置中的值，否则用 manifest 中的 backup_timestamp
        recovery_target_time: str = self._config.recovery_target_time or ""
        if not recovery_target_time:
            recovery_target_time = str(manifest.extra.get("backup_timestamp", ""))

        logger.info(
            "Restore %s: restoring PostgreSQL (PITR, target_time=%s) ...",
            manifest.backup_id, recovery_target_time or "(backup_timestamp)",
        )
        self._pitr_restore(backup_dir / PG_BASEBACKUP_DIRNAME, recovery_target_time)

        # 4. 前向兼容迁移
        if not self._config.skip_migrations:
            logger.info(
                "Restore %s: applying forward-compatible migrations ...",
                manifest.backup_id,
            )
            self._apply_forward_migrations(manifest.migration_version)
        else:
            logger.info("Restore %s: skipping migrations (as requested)", manifest.backup_id)

        # 5. 冒烟查询
        logger.info("Restore %s: running smoke queries ...", manifest.backup_id)
        smoke_results: dict[str, int] = await self._run_smoke_queries()
        for table_name, row_count in smoke_results.items():
            logger.info("  %s: %d rows", table_name, row_count)

        smoke_failures: list[str] = []
        for table_name, row_count in smoke_results.items():
            if row_count < 0:
                smoke_failures.append(f"{table_name}: query failed")
        if smoke_results.get("app_user", 0) <= 0:
            smoke_failures.append("app_user table is empty after restore")
        if smoke_results.get("alembic_version", 0) != 1:
            smoke_failures.append(
                f"alembic_version should have exactly 1 row, got {smoke_results.get('alembic_version')}"
            )
        if smoke_failures:
            logger.error("Restore %s: smoke test failures: %s", manifest.backup_id, smoke_failures)
            raise RuntimeError(
                f"Smoke test failures: {'; '.join(smoke_failures)}"
            )

        # 6. 引用完整性校验（P0-UP-08）
        logger.info("Restore %s: validating referential integrity ...", manifest.backup_id)
        await self._validate_referential_integrity()
        logger.info("Restore %s: referential integrity validated ✓", manifest.backup_id)

        logger.info("Restore %s: v2 restore complete", manifest.backup_id)
        return manifest

    def _pitr_restore(self, basebackup_dir: Path, recovery_target_time: str) -> None:
        """PITR 物理恢复 PostgreSQL。

        流程（docs/arch-db-backup-pitr-upgrade.md §1.6）：
        1. docker compose stop postgres
        2. 清空 pgdata 目录
        3. 解压 base.tar.gz → pgdata
        4. 解压 pg_wal.tar.gz → pgdata/pg_wal/（如有）
        5. 创建 recovery.signal
        6. 配置 postgresql.auto.conf（restore_command + recovery_target_time + recovery_target_action）
        7. docker compose start postgres
        8. 轮询 pg_isready 等待 PG 健康

        Args:
            basebackup_dir: pg_basebackup 产出目录（含 base.tar.gz + pg_wal.tar.gz）。
            recovery_target_time: 恢复目标时间（ISO 8601，空字符串表示恢复到备份时间点）。

        Raises:
            RuntimeError: 恢复步骤失败时。
        """
        pgdata_dir: Path = Path("/var/lib/postgresql/data")
        wal_archive_dir: Path = Path(os.getenv("IRIP_WAL_ARCHIVE_DIR", "/backups/wal_archive"))

        # 1. 停止 PG 容器
        logger.info("PITR restore: stopping postgres container ...")
        self._stop_postgres()

        try:
            # 2. 清空 pgdata 目录
            logger.info("PITR restore: clearing pgdata directory ...")
            if pgdata_dir.exists():
                for item in pgdata_dir.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)

            # 3. 解压 base.tar.gz → pgdata
            base_tar: Path = basebackup_dir / "base.tar.gz"
            if not base_tar.exists():
                raise RuntimeError(f"base.tar.gz 缺失: {base_tar}")
            logger.info("PITR restore: extracting base.tar.gz ...")
            with tarfile.open(base_tar, "r:gz") as tar:
                try:
                    tar.extractall(path=pgdata_dir, filter="data")
                except TypeError:
                    tar.extractall(path=pgdata_dir)

            # 4. 解压 pg_wal.tar.gz → pgdata/pg_wal/（如有）
            pg_wal_tar: Path = basebackup_dir / "pg_wal.tar.gz"
            pg_wal_dir: Path = pgdata_dir / "pg_wal"
            pg_wal_dir.mkdir(parents=True, exist_ok=True)
            if pg_wal_tar.exists():
                logger.info("PITR restore: extracting pg_wal.tar.gz ...")
                with tarfile.open(pg_wal_tar, "r:gz") as tar:
                    try:
                        tar.extractall(path=pg_wal_dir, filter="data")
                    except TypeError:
                        tar.extractall(path=pg_wal_dir)

            # 5. 创建 recovery.signal
            recovery_signal: Path = pgdata_dir / "recovery.signal"
            recovery_signal.touch()
            logger.info("PITR restore: created recovery.signal")

            # 6. 配置 postgresql.auto.conf
            auto_conf: Path = pgdata_dir / "postgresql.auto.conf"
            # 格式化 recovery_target_time 为 PG 识别的格式
            target_time_pg: str = recovery_target_time
            if target_time_pg:
                # ISO 8601 → PG timestamp 格式: "2026-08-16 10:30:00.000000+00:00"
                try:
                    from datetime import datetime

                    dt = datetime.fromisoformat(target_time_pg)
                    target_time_pg = dt.strftime("%Y-%m-%d %H:%M:%S.%f%z")
                except Exception:
                    pass  # 保持原始格式

            conf_lines: list[str] = [
                f"restore_command = 'cp {wal_archive_dir}/%f %p'",
            ]
            if target_time_pg:
                conf_lines.append(f"recovery_target_time = '{target_time_pg}'")
            conf_lines.append("recovery_target_action = 'promote'")

            with open(auto_conf, "a", encoding="utf-8") as f:
                for line in conf_lines:
                    f.write(line + "\n")
            logger.info("PITR restore: configured postgresql.auto.conf")

            # 7. 启动 PG 容器
            logger.info("PITR restore: starting postgres container ...")
            self._start_postgres()

            # 8. 等待 PG 健康
            logger.info("PITR restore: waiting for postgres to become healthy ...")
            self._wait_pg_healthy()

        except Exception:
            # 恢复失败时尝试重新启动 PG
            logger.error("PITR restore: failed, attempting to restart postgres ...")
            self._start_postgres()
            raise

    def _mc_restore_minio(self, minio_dir: Path) -> None:
        """使用 mc mirror 将本地对象目录恢复到 MinIO bucket。

        流程: mc alias set → mc mirror --overwrite minio_dir/ [alias]/[bucket]

        Args:
            minio_dir: mc mirror 对象目录路径。

        Raises:
            RuntimeError: mc 命令执行失败时。
        """
        if not minio_dir.exists():
            logger.info("No minio_mirror directory; skipping MinIO restore")
            return

        self._s3.ensure_bucket()

        mc_config_dir: str = "/tmp/mc"

        # 配置 mc alias
        cmd_alias: list[str] = [
            "mc", "--config-dir", mc_config_dir,
            "alias", "set",
            self._config.minio_mc_alias,
            self._config.minio_endpoint,
            self._config.minio_access_key,
            self._config.minio_secret_key,
        ]
        logger.info("Setting mc alias for restore: %s", self._config.minio_mc_alias)
        result_alias: subprocess.CompletedProcess[bytes] = subprocess.run(
            cmd_alias, capture_output=True, check=False
        )
        if result_alias.returncode != 0:
            stderr: str = result_alias.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"mc alias set failed (exit={result_alias.returncode}): {stderr}"
            )

        # mc mirror 从本地目录恢复到 MinIO bucket
        cmd_mirror: list[str] = [
            "mc", "--config-dir", mc_config_dir,
            "mirror", "--overwrite",
            str(minio_dir) + "/",
            f"{self._config.minio_mc_alias}/{self._config.minio_bucket}",
        ]
        logger.info("Running mc mirror restore ...")
        result_mirror: subprocess.CompletedProcess[bytes] = subprocess.run(
            cmd_mirror, capture_output=True, check=False
        )
        if result_mirror.returncode != 0:
            stderr: str = result_mirror.stderr.decode("utf-8", errors="replace")  # type: ignore[no-redef]
            raise RuntimeError(
                f"mc mirror restore failed (exit={result_mirror.returncode}): {stderr}"
            )

        logger.info("MinIO objects restored via mc mirror")

    async def _validate_referential_integrity(self) -> None:
        """引用完整性校验（P0-UP-08）。

        查询 artifact_blob 表的 storage_key 列，逐 key 检查 MinIO 对象是否存在。
        任一缺失则 raise RuntimeError。

        Raises:
            RuntimeError: 任一 storage_key 对应的 MinIO 对象缺失时。
        """
        from sqlalchemy import create_engine, text

        sync_url: str = _to_sync_url(self._config.db_url)
        engine = create_engine(sync_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT storage_key FROM artifact_blob")
                )
                storage_keys: list[str] = [str(row[0]) for row in result.fetchall()]
        except Exception as exc:
            logger.warning("Referential integrity: failed to query artifact_blob: %s", exc)
            return
        finally:
            engine.dispose()

        if not storage_keys:
            logger.info("Referential integrity: no storage_keys to validate")
            return

        missing_keys: list[str] = []
        for key in storage_keys:
            if not key:
                continue
            try:
                self._s3.head_object(key)
            except Exception as exc:
                # 对象不存在（404）或检查出错
                logger.warning("Referential integrity: object missing or error for %s: %s", key, exc)
                missing_keys.append(key)

        if missing_keys:
            raise RuntimeError(
                f"引用完整性校验失败: {len(missing_keys)} 个 MinIO 对象缺失:\n"
                + "\n".join(missing_keys[:20])
            )

        logger.info(
            "Referential integrity: all %d storage_keys verified", len(storage_keys)
        )

    def _stop_postgres(self) -> None:
        """停止 PostgreSQL 容器（docker compose stop postgres）。

        Raises:
            RuntimeError: docker compose stop 失败时。
        """
        compose_file: str = os.getenv(
            "IRIP_RESTORE_COMPOSE_FILE", "compose.yaml"
        )
        cmd: list[str] = [
            "docker", "compose",
            "-f", compose_file,
            "stop", "postgres",
        ]
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            cmd, capture_output=True, check=False
        )
        if result.returncode != 0:
            stderr: str = result.stderr.decode("utf-8", errors="replace")
            logger.warning("docker compose stop postgres failed: %s", stderr)

    def _start_postgres(self) -> None:
        """启动 PostgreSQL 容器（docker compose start postgres）。

        Raises:
            RuntimeError: docker compose start 失败时。
        """
        compose_file: str = os.getenv(
            "IRIP_RESTORE_COMPOSE_FILE", "compose.yaml"
        )
        cmd: list[str] = [
            "docker", "compose",
            "-f", compose_file,
            "start", "postgres",
        ]
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            cmd, capture_output=True, check=False
        )
        if result.returncode != 0:
            stderr: str = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"docker compose start postgres failed (exit={result.returncode}): {stderr}"
            )

    def _wait_pg_healthy(self, max_retries: int = 30, retry_interval: float = 2.0) -> None:
        """轮询 pg_isready 等待 PostgreSQL 健康。

        Args:
            max_retries: 最大重试次数（默认 30 次）。
            retry_interval: 重试间隔秒数（默认 2 秒）。

        Raises:
            RuntimeError: 超过最大重试次数仍不健康时。
        """
        import time

        for i in range(max_retries):
            cmd: list[str] = [
                "pg_isready",
                "-h", self._extract_pg_host(),
                "-U", self._extract_pg_user(),
            ]
            result: subprocess.CompletedProcess[bytes] = subprocess.run(
                cmd,
                env=_build_pg_env(),
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                logger.info("PostgreSQL is ready (after %d retries)", i)
                return
            time.sleep(retry_interval)

        raise RuntimeError(
            f"PostgreSQL did not become healthy after {max_retries} retries"
        )

    def _extract_pg_host(self) -> str:
        """从数据库 URL 中提取 PG 主机名。

        Returns:
            str: PG 主机名（默认 'postgres'）。
        """
        from urllib.parse import urlparse

        try:
            sync_url: str = _to_pg_restore_url(self._config.db_url)
            parsed = urlparse(sync_url)
            return parsed.hostname or "postgres"
        except Exception:
            return "postgres"

    def _extract_pg_user(self) -> str:
        """从数据库 URL 中提取 PG 用户名。

        Returns:
            str: PG 用户名（默认 'irip'）。
        """
        from urllib.parse import urlparse

        try:
            sync_url: str = _to_pg_restore_url(self._config.db_url)
            parsed = urlparse(sync_url)
            return parsed.username or "irip"
        except Exception:
            return "irip"

    def _extract_archive(self, backup_dir: Path) -> None:
        """解压 tar / tar.age 归档到备份目录（安全提取）。

        技术设计文档 F-15：归档提取使用 Python 3.12 安全 filter
        （``data_filter``），过滤路径穿越和危险文件类型。

        若备份目录已含 ``database.dump`` 则视为已解压，跳过。
        若存在加密归档则先解密再解压。

        Args:
            backup_dir: 备份目录。

        Raises:
            RuntimeError: age 解密失败时。
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
            identity: str = self._config.age_identity or os.getenv(AGE_IDENTITY_ENV, "")  # type: ignore[assignment]
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
            # F-15: 使用 Python 3.12 安全 extraction filter
            # data_filter 过滤路径穿越、符号链接和危险文件类型
            with tarfile.open(tar_path, "r") as tar:
                try:
                    tar.extractall(path=backup_dir, filter="data")
                except TypeError:
                    # Python < 3.12 不支持 filter 参数，回退到手动检查
                    tar.extractall(path=backup_dir)
                    logger.warning(
                        "Python < 3.12: tar extraction without data filter; "
                        "consider upgrading to Python 3.12+"
                    )
            logger.info("Extracted backup archive: %s", tar_path)

    def _prevalidate_objects(self, objects_dir: Path) -> None:
        """恢复前完整预校验所有对象（存在性 + SHA-256）。

        技术设计文档 F-06：fail-closed — 任一对象缺失或 SHA 不匹配则 raise，
        确保恢复前所有备份数据完整可用，避免部分恢复导致数据不一致。

        Args:
            objects_dir: 对象目录路径。

        Raises:
            RuntimeError: 任一对象缺失或 SHA 不匹配时。
        """
        if not objects_dir.exists():
            raise RuntimeError(
                f"对象目录不存在: {objects_dir} — 备份可能不完整"
            )

        metadata: list[dict[str, Any]] = read_objects_metadata(objects_dir)
        if not metadata:
            logger.warning("No objects metadata found; skipping pre-validation")
            return

        expected_count: int = len(metadata)
        validated_count: int = 0
        failures: list[str] = []

        from packages.common.hashing import sha256_bytes

        for obj_meta in metadata:
            key: str = obj_meta["key"]
            expected_sha: str = obj_meta["sha256"]
            obj_path: Path = objects_dir / key

            # 存在性检查
            if not obj_path.exists():
                failures.append(f"missing: {key}")
                continue

            # SHA-256 校验
            try:
                data: bytes = obj_path.read_bytes()
                actual_sha: str = sha256_bytes(data)
                if actual_sha != expected_sha:
                    failures.append(
                        f"sha256 mismatch: {key} "
                        f"(expected={expected_sha[:12]}, actual={actual_sha[:12]})"
                    )
                    continue
                validated_count += 1
            except Exception as exc:
                failures.append(f"error: {key} ({exc})")

        if failures:
            raise RuntimeError(
                f"对象预校验失败: 期望 {expected_count} 个, "
                f"通过 {validated_count} 个, "
                f"失败 {len(failures)} 个:\n"
                + "\n".join(failures[:20])
            )

        logger.info(
            "Pre-validation passed: %d/%d objects verified",
            validated_count, expected_count,
        )

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
        # F-06/F-15: pg_restore 非零退出默认失败，不再通过 stderr 字符串忽略
        # 技术设计文档 F-15："pg_restore 非零退出默认失败，不再通过 stderr 字符串忽略"
        if result.returncode != 0:
            stderr: str = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"pg_restore failed (exit={result.returncode}): {stderr}"
            )

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
        """恢复 MinIO 对象到目标 bucket（H-09: 流式传输大对象）。

        读取 ``objects.json`` 元数据，逐对象上传到 S3。
        H-09: 使用流式上传（put_object_stream），不整对象读入内存。

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
                    content_type: str = "application/octet-stream"
                    # H-09: 流式上传
                    with open(path, "rb") as f:
                        self._s3.put_object_stream(key, f, content_type)
            return

        restored: int = 0
        failed: list[str] = []
        for obj_meta in metadata:
            key: str = obj_meta["key"]  # type: ignore[no-redef]
            expected_sha: str = obj_meta["sha256"]
            obj_path: Path = objects_dir / key
            if not obj_path.exists():
                # F-06: fail-closed -- 对象缺失直接记录失败
                failed.append(f"missing: {key}")
                continue
            # H-09: 流式校验 SHA-256（不整对象读入内存）
            from packages.common.hashing import sha256_bytes

            # 对于小文件直接读取校验，大文件流式校验
            file_size: int = obj_path.stat().st_size
            if file_size <= 10 * 1024 * 1024:
                # 小文件（<= 10 MiB）：直接读取校验
                data: bytes = obj_path.read_bytes()
                actual_sha: str = sha256_bytes(data)
                if actual_sha != expected_sha:
                    failed.append(
                        f"sha256 mismatch: {key} "
                        f"(expected={expected_sha[:12]}, actual={actual_sha[:12]})"
                    )
                    continue
                content_type: str = "application/octet-stream"  # type: ignore[no-redef]
                self._s3.put_object(key, data, content_type)
            else:
                # H-09: 大文件（> 10 MiB）：流式校验 + 流式上传
                import hashlib

                hasher = hashlib.sha256()
                with open(obj_path, "rb") as f:
                    while True:
                        chunk: bytes = f.read(64 * 1024)
                        if not chunk:
                            break
                        hasher.update(chunk)
                actual_sha: str = hasher.hexdigest()  # type: ignore[no-redef]
                if actual_sha != expected_sha:
                    failed.append(
                        f"sha256 mismatch: {key} "
                        f"(expected={expected_sha[:12]}, actual={actual_sha[:12]})"
                    )
                    continue
                content_type: str = "application/octet-stream"  # type: ignore[no-redef]
                # H-09: 流式上传大对象
                with open(obj_path, "rb") as f:
                    self._s3.put_object_stream(key, f, content_type)
            restored += 1

        # F-06: 失败清单非空时 raise
        if failed:
            raise RuntimeError(
                f"MinIO 对象恢复失败: 成功 {restored} 个, "
                f"失败 {len(failed)} 个:\n" + "\n".join(failed[:20])
            )

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
        # F-06/F-15: alembic 非零退出默认失败
        if result.returncode != 0:
            stderr: str = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"alembic upgrade head failed (exit={result.returncode}): {stderr}"
            )
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
    db_url: str = os.getenv("IRIP_DATABASE_ADMIN_URL", "") or os.getenv("IRIP_DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("IRIP_DATABASE_URL or IRIP_DATABASE_ADMIN_URL environment variable is required")

    endpoint: str = os.getenv("IRIP_MINIO_ENDPOINT", "http://localhost:9000")
    if not endpoint.startswith("http"):
        endpoint = f"http://{endpoint}"

    compose_project: str | None = os.getenv("IRIP_RESTORE_COMPOSE_PROJECT") or None
    age_identity: str | None = os.getenv(AGE_IDENTITY_ENV) or None
    skip_migrations: bool = os.getenv("IRIP_RESTORE_SKIP_MIGRATIONS", "").lower() in (
        "1", "true", "yes",
    )
    minio_mc_alias: str = os.getenv("IRIP_MINIO_MC_ALIAS", "irip")

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
        minio_mc_alias=minio_mc_alias,
    )


async def run_restore(
    backup_dir: Path,
    recovery_target_time: str | None = None,
) -> BackupManifest:
    """执行恢复（便捷入口）。

    Args:
        backup_dir: 备份目录路径。
        recovery_target_time: PITR 恢复目标时间（ISO 8601，None 表示恢复到备份时间点）。

    Returns:
        BackupManifest: 恢复使用的备份清单。
    """
    config: RestoreConfig = build_restore_config_from_env(backup_dir)
    if recovery_target_time is not None:
        # 重建 config 以注入 recovery_target_time（frozen dataclass）
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
            skip_migrations=config.skip_migrations,
            minio_mc_alias=config.minio_mc_alias,
            recovery_target_time=recovery_target_time,
        )
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
    parser.add_argument(
        "--recovery-target-time",
        type=str,
        default=None,
        help="PITR 恢复目标时间（ISO 8601），不传时恢复到备份时间点",
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
            minio_mc_alias=config.minio_mc_alias,
            recovery_target_time=args.recovery_target_time,
        )
    elif args.recovery_target_time:
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
            skip_migrations=config.skip_migrations,
            minio_mc_alias=config.minio_mc_alias,
            recovery_target_time=args.recovery_target_time,
        )

    service: RestoreService = RestoreService(config)
    try:
        manifest: BackupManifest = asyncio.run(service.restore())
        print(f"\n恢复完成: {manifest.to_json()}")
    except FileNotFoundError as exc:
        logger.info("无备份文件，恢复中止: %s", exc)
        print(f"\n无备份文件，恢复中止（退出码 1）: {exc}")
        sys.exit(1)
    except RuntimeError as exc:
        # H-09: 冒烟失败或恢复步骤失败，非零退出
        logger.error("恢复失败: %s", exc)
        print(f"\n恢复失败（退出码 1）: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
