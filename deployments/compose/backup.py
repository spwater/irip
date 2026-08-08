"""IRIP 备份脚本（V3-T03）。

串联 PostgreSQL ``pg_dump``（custom 格式）+ MinIO 对象导出，计算各 payload
SHA-256 校验和，打包为 tar 归档（可选 age 加密），输出 ``BackupManifest``。

流程：
  1. ``pg_dump -Fc`` 导出 PostgreSQL 数据库 → ``database.dump``；
  2. 通过 ``S3Repository`` 列举并下载 MinIO 全部对象 → ``objects/``；
  3. 写入对象元数据 ``objects.json``（key + sha256 + size）；
  4. 查询 ``alembic_version`` 表获取迁移版本；
  5. 记录 IRIP 应用版本；
  6. 计算 ``database.dump`` SHA-256 + 对象聚合 SHA-256 → ``BackupManifest``；
  7. 打包为 ``backup.tar``；
  8. 若设置了 ``IRIP_BACKUP_AGE_RECIPIENT``，用 age 加密 → ``backup.tar.age``；
  9. 写入 ``manifest.json``。

用法（Docker Compose）：
  docker compose run --rm backup

用法（本机）：
  IRIP_DATABASE_URL=... IRIP_MINIO_ENDPOINT=... \\
  python -m deployments.compose.backup --output-dir /tmp/irip-backup

也可作为模块导入：
  from deployments.compose.backup import BackupService, run_backup
"""

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deployments.compose.backup_manifest import (
    DATABASE_DUMP_FILENAME,
    MINIO_MIRROR_DIRNAME,
    OBJECTS_DIRNAME,
    PG_BASEBACKUP_DIRNAME,
    BackupManifest,
    compute_manifest,
    compute_manifest_v2,
    save_manifest,
    write_objects_metadata,
)
from packages.common.ids import new_id
from packages.common.s3_repository import S3Repository

logger = logging.getLogger(__name__)

#: IRIP 应用版本（从环境变量或硬编码默认值读取）。
IRIP_APPLICATION_VERSION: str = os.getenv("IRIP_APPLICATION_VERSION", "0.8.0")

#: age 加密 recipient 环境变量名。
AGE_RECIPIENT_ENV: str = "IRIP_BACKUP_AGE_RECIPIENT"

#: tar 归档文件名。
BACKUP_TAR_FILENAME: str = "backup.tar"

#: 加密后的 tar 文件名。
BACKUP_TAR_AGE_FILENAME: str = "backup.tar.age"


def _to_sync_url(url: str) -> str:
    """将异步驱动 URL 转换为 psycopg3 同步驱动 URL（SQLAlchemy create_engine 用）。

    Args:
        url: 数据库连接字符串（可能含 ``postgresql+psycopg_async://``）。

    Returns:
        str: psycopg3 同步驱动 URL（``postgresql+psycopg://``）。
    """
    if url.startswith("postgresql+psycopg_async://"):
        return url.replace(
            "postgresql+psycopg_async://", "postgresql+psycopg://", 1
        )
    return url


def _to_pg_dump_url(url: str) -> str:
    """将数据库 URL 转换为 pg_dump 可识别的标准格式（``postgresql://``）。

    pg_dump 不识别 SQLAlchemy 驱动前缀（如 ``+psycopg``），需要纯 postgresql URL。

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
    """构建 pg_dump 子进程环境变量（透传 PGPASSWORD 等）。

    Returns:
        dict: 环境变量字典。
    """
    env: dict[str, str] = os.environ.copy()
    db_url: str = os.getenv("IRIP_DATABASE_URL", "")
    if db_url:
        # 解析密码到 PGPASSWORD，避免命令行暴露
        try:
            from urllib.parse import urlparse

            parsed = urlparse(_to_sync_url(db_url))
            if parsed.password:
                env["PGPASSWORD"] = parsed.password
        except Exception:
            pass
    return env


@dataclass(frozen=True)
class BackupConfig:
    """备份配置（不可变值对象）。

    Attributes:
        db_url: PostgreSQL 连接字符串（异步或同步驱动均可）。
        minio_endpoint: MinIO 端点 URL。
        minio_access_key: MinIO 访问密钥。
        minio_secret_key: MinIO 秘密密钥。
        minio_bucket: MinIO bucket 名称。
        minio_region: MinIO 区域。
        application_version: IRIP 应用版本。
        output_dir: 备份输出目录。
        age_recipient: age 加密 recipient（None 表示不加密）。
        minio_mc_alias: mc 客户端 alias 名称（默认 irip）。
        minio_mirror_exclude: mc mirror 排除规则（如 'tmp/*'，None 表示不排除）。
        pg_replication_slot: pg_basebackup 复制槽名（None 表示不使用复制槽）。
    """

    db_url: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_region: str
    application_version: str
    output_dir: Path
    age_recipient: str | None = None
    minio_mc_alias: str = "irip"
    minio_mirror_exclude: str | None = None
    pg_replication_slot: str | None = None


class BackupService:
    """备份服务 — 串联 pg_dump + MinIO 同步。

    通过 ``subprocess`` 调用 ``pg_dump``，通过 ``S3Repository`` 导出 MinIO 对象，
    计算各 payload SHA-256 校验和，打包为 tar 归档（可选 age 加密），输出 BackupManifest。

    Attributes:
        _config: 备份配置。
        _s3: S3 对象存储客户端。
    """

    def __init__(self, config: BackupConfig) -> None:
        """初始化备份服务。

        Args:
            config: 备份配置。
        """
        self._config: BackupConfig = config
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

    async def backup(self, output_dir: Path | None = None) -> BackupManifest:
        """执行联合备份流程（PITR v2）。

        联合备份流程（docs/arch-db-backup-pitr-upgrade.md §1.5）：
        1. 生成联合时间戳 backup_timestamp（UTC ISO 8601 毫秒精度）
        2. 查询 wal_start_lsn = pg_current_wal_lsn()
        3. PG basebackup: pg_basebackup -Ft -z -X stream -c fast → {backup_id}/pg_basebackup/
        4. 查询 wal_end_lsn = pg_current_wal_lsn()
        5. MinIO mirror: mc mirror --overwrite → {backup_id}/minio_mirror/
        6. 计算 SHA-256（base.tar.gz + pg_wal.tar.gz + minio_mirror 聚合）
        7. 查询 migration_version
        8. 生成 BackupManifest v2
        9. 写入 {backup_id}/manifest.json

        v2 不再打包 tar.age，各组件独立存储（pg_basebackup/ + minio_mirror/）。

        Args:
            output_dir: 输出目录（默认使用配置中的 output_dir）。

        Returns:
            BackupManifest: format_version=2 的备份清单。
        """
        target_base: Path = output_dir or self._config.output_dir
        target_base.mkdir(parents=True, exist_ok=True)

        backup_id: str = str(new_id())
        target_dir: Path = target_base / backup_id
        target_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Backup %s: starting PITR backup (output=%s)", backup_id, target_dir)

        # 1. 生成联合时间戳
        backup_timestamp: str = datetime.now(UTC).isoformat(timespec="milliseconds")
        logger.info("Backup %s: backup_timestamp=%s", backup_id, backup_timestamp)

        # 2. 创建子目录
        pg_basebackup_dir: Path = target_dir / PG_BASEBACKUP_DIRNAME
        pg_basebackup_dir.mkdir(parents=True, exist_ok=True)
        minio_mirror_dir: Path = target_dir / MINIO_MIRROR_DIRNAME
        minio_mirror_dir.mkdir(parents=True, exist_ok=True)

        # 3. PG basebackup + WAL LSN 记录
        wal_start_lsn, wal_end_lsn = self._basebackup(pg_basebackup_dir)
        logger.info(
            "Backup %s: pg_basebackup done (wal_start=%s, wal_end=%s)",
            backup_id, wal_start_lsn, wal_end_lsn,
        )

        # 4. MinIO mirror（紧接 PG basebackup 完成）
        object_count: int = self._mc_mirror_minio(minio_mirror_dir)
        logger.info("Backup %s: mc mirror done (objects=%d)", backup_id, object_count)

        # 5. 查询 migration_version
        migration_version: str = await self._query_migration_version()
        logger.info(
            "Backup %s: migration_version=%s, object_count=%d",
            backup_id, migration_version, object_count,
        )

        # 6. 生成 manifest v2
        manifest: BackupManifest = compute_manifest_v2(
            pg_basebackup_dir=pg_basebackup_dir,
            minio_mirror_dir=minio_mirror_dir,
            application_version=self._config.application_version,
            migration_version=migration_version,
            backup_id=backup_id,
            backup_timestamp=backup_timestamp,
            wal_start_lsn=wal_start_lsn,
            wal_end_lsn=wal_end_lsn,
        )

        # 7. 写入 manifest.json
        save_manifest(manifest, target_dir)
        logger.info("Backup %s: manifest v2 written", backup_id)

        logger.info(
            "Backup %s: complete (base_sha256=%s..., mirror_objects=%d)",
            backup_id, manifest.database_sha256[:12], manifest.object_count,
        )
        return manifest

    def _basebackup(self, target_dir: Path) -> tuple[str, str]:
        """使用 pg_basebackup 执行物理基础备份。

        命令: pg_basebackup -Ft -z -X stream -c fast [-C -S slot] -D target_dir
        产出: base.tar.gz（数据目录）+ pg_wal.tar.gz（备份期间 WAL）

        Args:
            target_dir: pg_basebackup 输出目录。

        Returns:
            tuple: (wal_start_lsn, wal_end_lsn)。

        Raises:
            RuntimeError: pg_basebackup 执行失败时。
        """
        # 查询备份开始时的 WAL LSN
        wal_start_lsn: str = self._query_wal_lsn()
        logger.info("Basebackup: wal_start_lsn=%s", wal_start_lsn)

        # 构建 pg_basebackup 命令
        pg_host: str = self._extract_pg_host()
        pg_port: int = self._extract_pg_port()
        cmd: list[str] = [
            "pg_basebackup",
            "-h", pg_host,
            "-p", str(pg_port),
            "-U", self._extract_pg_user(),
            "-D", str(target_dir),
            "-Ft",
            "-z",
            "-P",
            "-X", "stream",
            "-c", "fast",
        ]

        # 可选复制槽
        if self._config.pg_replication_slot:
            cmd.extend(["-C", "-S", self._config.pg_replication_slot])

        logger.info("Running pg_basebackup to %s", target_dir)
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            cmd,
            env=_build_pg_env(),
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            stderr: str = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"pg_basebackup failed (exit={result.returncode}): {stderr}"
            )

        # 验证产出文件存在
        base_tar: Path = target_dir / "base.tar.gz"
        if not base_tar.exists():
            raise RuntimeError(f"pg_basebackup 未产出 base.tar.gz: {base_tar}")

        # 查询备份结束时的 WAL LSN
        wal_end_lsn: str = self._query_wal_lsn()
        logger.info("Basebackup: wal_end_lsn=%s", wal_end_lsn)

        return wal_start_lsn, wal_end_lsn

    def _mc_mirror_minio(self, target_dir: Path) -> int:
        """使用 mc mirror 将 MinIO bucket 镜像到本地目录。

        流程: mc alias set → mc mirror --overwrite [alias]/[bucket] target_dir/ [--exclude ...]
        返回镜像的对象数（通过目录扫描统计）。

        使用 --config-dir /tmp/mc 避免 read-only 文件系统冲突（worker 容器
        设置了 read_only: true + tmpfs: /tmp）。

        Args:
            target_dir: mc mirror 输出目录。

        Returns:
            int: 镜像的对象总数。

        Raises:
            RuntimeError: mc 命令执行失败时。
        """
        mc_config_dir: str = "/tmp/mc"
        self._setup_mc_alias(mc_config_dir)

        cmd: list[str] = [
            "mc", "--config-dir", mc_config_dir,
            "mirror", "--overwrite",
            f"{self._config.minio_mc_alias}/{self._config.minio_bucket}",
            str(target_dir) + "/",
        ]

        # 可选排除规则
        if self._config.minio_mirror_exclude:
            cmd.extend(["--exclude", self._config.minio_mirror_exclude])

        logger.info("Running mc mirror: %s", " ".join(cmd[:5]) + " ...")
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            cmd, capture_output=True, check=False
        )
        if result.returncode != 0:
            stderr: str = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"mc mirror failed (exit={result.returncode}): {stderr}"
            )

        # 统计镜像的对象数
        object_count: int = 0
        for path in target_dir.rglob("*"):
            if path.is_file():
                object_count += 1

        logger.info("mc mirror: mirrored %d objects", object_count)
        return object_count

    def _query_wal_lsn(self) -> str:
        """查询当前 WAL LSN（pg_current_wal_lsn()）。

        Returns:
            str: 当前 WAL LSN（如 '0/2000000'）。查询失败时返回空字符串。
        """
        sync_url: str = _to_sync_url(self._config.db_url)
        from sqlalchemy import create_engine, text

        engine = create_engine(sync_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT pg_current_wal_lsn()"))
                row = result.fetchone()
                if row is not None:
                    return str(row[0])
        except Exception as exc:
            logger.warning("Failed to query pg_current_wal_lsn: %s", exc)
        finally:
            engine.dispose()
        return ""

    def _setup_mc_alias(self, config_dir: str = "/tmp/mc") -> None:
        """配置 mc alias（mc alias set）。

        使用 BackupConfig 中的 MinIO 连接信息配置 mc alias。
        使用 --config-dir 避免在 read-only 文件系统中写入失败。

        Args:
            config_dir: mc 配置目录（默认 /tmp/mc，兼容 read-only FS）。

        Raises:
            RuntimeError: mc alias set 执行失败时。
        """
        cmd: list[str] = [
            "mc", "--config-dir", config_dir,
            "alias", "set",
            self._config.minio_mc_alias,
            self._config.minio_endpoint,
            self._config.minio_access_key,
            self._config.minio_secret_key,
        ]
        logger.info("Setting mc alias: %s", self._config.minio_mc_alias)
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            cmd, capture_output=True, check=False
        )
        if result.returncode != 0:
            stderr: str = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"mc alias set failed (exit={result.returncode}): {stderr}"
            )

    def _extract_pg_host(self) -> str:
        """从数据库 URL 中提取 PG 主机名。

        Returns:
            str: PG 主机名（默认 'postgres'）。
        """
        from urllib.parse import urlparse

        try:
            sync_url: str = _to_pg_dump_url(self._config.db_url)
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
            sync_url: str = _to_pg_dump_url(self._config.db_url)
            parsed = urlparse(sync_url)
            return parsed.username or "irip"
        except Exception:
            return "irip"

    def _extract_pg_port(self) -> int:
        """从数据库 URL 中提取 PG 端口。

        Returns:
            int: PG 端口号（默认 5432）。
        """
        from urllib.parse import urlparse

        try:
            sync_url: str = _to_pg_dump_url(self._config.db_url)
            parsed = urlparse(sync_url)
            return parsed.port or 5432
        except Exception:
            return 5432

    def _dump_database(self, output_path: Path) -> None:
        """使用 pg_dump 以 custom 格式导出 PostgreSQL 数据库。

        Args:
            output_path: dump 文件输出路径。

        Raises:
            RuntimeError: pg_dump 执行失败时。
        """
        sync_url: str = _to_pg_dump_url(self._config.db_url)
        cmd: list[str] = [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--file={output_path}",
            sync_url,
        ]
        logger.debug("Running pg_dump: %s", " ".join(cmd[:3]) + " ...")
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            cmd,
            env=_build_pg_env(),
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            stderr: str = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"pg_dump failed (exit={result.returncode}): {stderr}"
            )
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(f"pg_dump produced empty output: {output_path}")

    def _export_minio_objects(self, objects_dir: Path) -> int:
        """导出 MinIO bucket 中的全部对象到本地目录（fail-closed）。

        技术设计文档 F-06：列表/下载失败时 raise 而非 warning。
        manifest 记录期望对象数、完成数、失败清单。
        失败清单非空时 raise，确保备份完整性。

        Args:
            objects_dir: 对象输出目录。

        Returns:
            int: 导出的对象总数。

        Raises:
            RuntimeError: 列举或下载失败时。
        """
        from deployments.compose.backup_manifest import compute_objects_metadata

        object_keys: list[str] = self._list_minio_objects()
        expected_count: int = len(object_keys)
        completed_count: int = 0
        failed_keys: list[str] = []

        for key in object_keys:
            if not key:
                continue
            local_path: Path = objects_dir / key
            local_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                data: bytes = self._s3.get_object(key)
                local_path.write_bytes(data)
                completed_count += 1
            except Exception as exc:
                # F-06: fail-closed — 记录失败并最终 raise
                failed_keys.append(key)
                logger.error("Failed to export object %s: %s", key, exc)

        # 写入对象元数据
        metadata: list[dict[str, Any]] = compute_objects_metadata(objects_dir)
        write_objects_metadata(objects_dir, metadata)

        # F-06: 失败清单非空时 raise，确保备份完整性
        if failed_keys:
            raise RuntimeError(
                f"MinIO 对象导出失败: 期望 {expected_count} 个, "
                f"完成 {completed_count} 个, "
                f"失败 {len(failed_keys)} 个: {failed_keys[:10]}"
            )

        logger.info(
            "Exported %d MinIO objects (expected=%d, completed=%d, failed=0)",
            completed_count, expected_count, completed_count,
        )
        return completed_count

    def _list_minio_objects(self) -> list[str]:
        """列举 MinIO bucket 中的全部对象 key（fail-closed）。

        技术设计文档 F-06：列举失败时 raise 而非 warning，
        确保备份不会遗漏对象。

        Returns:
            list[str]: 对象 key 列表。

        Raises:
            RuntimeError: 列举失败时。
        """
        from botocore.exceptions import ClientError

        keys: list[str] = []
        continuation_token: str | None = None
        try:
            while True:
                list_kwargs: dict[str, Any] = {"Bucket": self._s3.bucket}
                if continuation_token is not None:
                    list_kwargs["ContinuationToken"] = continuation_token
                response: dict[str, Any] = self._s3._client.list_objects_v2(
                    **list_kwargs
                )
                contents: list[dict[str, Any]] = response.get("Contents", [])
                for obj in contents:
                    key: str = obj.get("Key", "")
                    if key:
                        keys.append(key)
                if not response.get("IsTruncated", False):
                    break
                continuation_token = response.get("NextContinuationToken")
        except ClientError as exc:
            # F-06: fail-closed — 列举失败直接 raise
            raise RuntimeError(
                f"Failed to list MinIO objects: {exc}"
            ) from exc
        return keys

    async def _query_migration_version(self) -> str:
        """查询 alembic_version 表的当前 head revision。

        Returns:
            str: 迁移版本号（如 ``"0021_ai_conversations"``）。表不存在时返回空字符串。
        """
        sync_url: str = _to_sync_url(self._config.db_url)
        # 使用同步引擎查询 alembic_version
        from sqlalchemy import create_engine, text

        engine = create_engine(sync_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                )
                row = result.fetchone()
                if row is not None:
                    return str(row[0])
        except Exception as exc:
            logger.warning("Failed to query alembic_version: %s", exc)
        finally:
            engine.dispose()
        return ""

    def _create_tar(self, source_dir: Path, tar_path: Path) -> None:
        """将备份内容打包为 tar 归档。

        打包 ``database.dump``、``objects/``、``manifest.json``，不含 tar 自身。

        Args:
            source_dir: 源目录。
            tar_path: tar 文件输出路径。
        """
        with tarfile.open(tar_path, "w") as tar:
            for item in source_dir.iterdir():
                if item.name == BACKUP_TAR_FILENAME or item.name == BACKUP_TAR_AGE_FILENAME:
                    continue
                tar.add(item, arcname=item.name)
        logger.debug("Created tar archive: %s", tar_path)

    def _encrypt_tar(
        self, tar_path: Path, encrypted_path: Path, recipient: str
    ) -> None:
        """使用 age 加密 tar 归档。

        Args:
            tar_path: 待加密的 tar 文件路径。
            encrypted_path: 加密后输出路径。
            recipient: age recipient（公钥）。

        Raises:
            RuntimeError: age 执行失败或未安装时。
        """
        if shutil.which("age") is None:
            raise RuntimeError(
                "age binary not found; install age to use encryption "
                "(https://github.com/FiloSottile/age)"
            )
        cmd: list[str] = [
            "age",
            "-r",
            recipient,
            "-o",
            str(encrypted_path),
            str(tar_path),
        ]
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            cmd, capture_output=True, check=False
        )
        if result.returncode != 0:
            stderr: str = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"age encryption failed (exit={result.returncode}): {stderr}"
            )


def build_backup_config_from_env(output_dir: Path | None = None) -> BackupConfig:
    """从环境变量构建备份配置。

    Args:
        output_dir: 输出目录（默认从 ``IRIP_BACKUP_OUTPUT_DIR`` 读取或使用临时目录）。

    Returns:
        BackupConfig: 备份配置。
    """
    db_url: str = os.getenv("IRIP_DATABASE_ADMIN_URL", "") or os.getenv("IRIP_DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("IRIP_DATABASE_URL or IRIP_DATABASE_ADMIN_URL environment variable is required")

    endpoint: str = os.getenv("IRIP_MINIO_ENDPOINT", "http://localhost:9000")
    if not endpoint.startswith("http"):
        endpoint = f"http://{endpoint}"

    if output_dir is None:
        output_dir_str: str = os.getenv("IRIP_BACKUP_OUTPUT_DIR", "")
        output_dir = Path(output_dir_str) if output_dir_str else Path(
            tempfile.gettempdir()
        ) / "irip-backup"

    age_recipient: str | None = os.getenv(AGE_RECIPIENT_ENV) or None

    # PITR + mc mirror 配置
    minio_mc_alias: str = os.getenv("IRIP_MINIO_MC_ALIAS", "irip")
    minio_mirror_exclude: str | None = os.getenv("IRIP_MINIO_MIRROR_EXCLUDE") or None
    pg_replication_slot: str | None = os.getenv("IRIP_PG_REPLICATION_SLOT") or None

    return BackupConfig(
        db_url=db_url,
        minio_endpoint=endpoint,
        minio_access_key=os.getenv("IRIP_MINIO_ACCESS_KEY", "irip"),
        minio_secret_key=os.getenv("IRIP_MINIO_SECRET_KEY", "irip_dev_password"),
        minio_bucket=os.getenv("IRIP_MINIO_BUCKET", "irip-artifacts"),
        minio_region=os.getenv("IRIP_MINIO_REGION", "us-east-1"),
        application_version=IRIP_APPLICATION_VERSION,
        output_dir=output_dir,
        age_recipient=age_recipient,
        minio_mc_alias=minio_mc_alias,
        minio_mirror_exclude=minio_mirror_exclude,
        pg_replication_slot=pg_replication_slot,
    )


async def run_backup(output_dir: Path | None = None) -> BackupManifest:
    """执行备份（便捷入口）。

    Args:
        output_dir: 输出目录。

    Returns:
        BackupManifest: 备份清单。
    """
    config: BackupConfig = build_backup_config_from_env(output_dir)
    service: BackupService = BackupService(config)
    return await service.backup(output_dir)


def main() -> None:
    """备份脚本 CLI 入口。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="IRIP 备份脚本 — pg_dump + MinIO 导出 + 完整性清单"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="备份输出目录（默认从 IRIP_BACKUP_OUTPUT_DIR 读取）",
    )
    args: argparse.Namespace = parser.parse_args()

    manifest: BackupManifest = asyncio.run(run_backup(args.output_dir))
    print(f"\n备份完成: {manifest.to_json()}")

    # ---- 异地存储同步 ----
    remote_target: str = os.getenv("IRIP_BACKUP_REMOTE_TARGET", "")
    if remote_target:
        import subprocess as _sp

        backup_output_dir: str = os.getenv("IRIP_BACKUP_OUTPUT_DIR", "/backups")
        logger.info("Syncing backup to remote: %s", remote_target)
        try:
            # 支持 rclone（推荐）或 aws s3 sync
            if shutil.which("rclone"):
                _sp.run(
                    ["rclone", "copy", backup_output_dir, remote_target],
                    check=True,
                    timeout=3600,
                )
            elif shutil.which("aws"):
                _sp.run(
                    ["aws", "s3", "sync", backup_output_dir, remote_target],
                    check=True,
                    timeout=3600,
                )
            else:
                logger.warning(
                    "Neither rclone nor aws CLI found; skipping remote sync. "
                    "Install rclone or aws-cli to enable offsite backup."
                )
            logger.info("Remote sync completed: %s", remote_target)
        except Exception as exc:
            logger.error("Remote sync failed: %s", exc)
    else:
        logger.info(
            "IRIP_BACKUP_REMOTE_TARGET not set; skipping offsite backup sync. "
            "Set it to e.g. 's3:irip-backups/' or 'remote:backups/' to enable."
        )


if __name__ == "__main__":
    main()
