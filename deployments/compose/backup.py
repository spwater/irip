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
from pathlib import Path
from typing import Any

from deployments.compose.backup_manifest import (
    DATABASE_DUMP_FILENAME,
    OBJECTS_DIRNAME,
    BackupManifest,
    compute_manifest,
    save_manifest,
    write_objects_metadata,
)
from packages.common.ids import new_id
from packages.common.s3_repository import S3Repository

logger = logging.getLogger(__name__)

#: IRIP 应用版本（从环境变量或硬编码默认值读取）。
IRIP_APPLICATION_VERSION: str = os.getenv("IRIP_APPLICATION_VERSION", "0.1.0")

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
        """执行完整备份流程。

        C-04: 在 0700 临时目录中生成 dump、objects 和 manifest，
        加密后原子移动唯一加密制品到最终目录，
        try/finally 确保清理临时明文（成功和失败路径）。

        每个备份在 output_dir 下创建独立的 {backup_id}/ 子目录，
        避免多次备份互相覆盖（docs/arch-db-backup.md §1.3）。

        Args:
            output_dir: 输出目录（默认使用配置中的 output_dir）。

        Returns:
            BackupManifest: 备份清单。
        """
        target_base: Path = output_dir or self._config.output_dir
        target_base.mkdir(parents=True, exist_ok=True)

        backup_id: str = str(new_id())
        # 每个备份创建独立子目录，避免覆盖
        target_dir: Path = target_base / backup_id
        target_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Backup %s: starting (output=%s)", backup_id, target_dir)

        # C-04: 1. 创建 0700 临时目录
        temp_dir: Path = Path(tempfile.mkdtemp(prefix="irip-backup-"))
        try:
            os.chmod(temp_dir, 0o700)

            # C-04: 2. 在临时目录中生成 dump 和 objects
            database_path: Path = temp_dir / DATABASE_DUMP_FILENAME
            logger.info("Backup %s: dumping PostgreSQL database ...", backup_id)
            self._dump_database(database_path)

            objects_dir: Path = temp_dir / OBJECTS_DIRNAME
            objects_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Backup %s: exporting MinIO objects ...", backup_id)
            object_count: int = self._export_minio_objects(objects_dir)

            # C-04: 3. 查询 alembic_version
            migration_version: str = await self._query_migration_version()
            logger.info(
                "Backup %s: migration_version=%s, object_count=%d",
                backup_id, migration_version, object_count,
            )

            # C-04: 4. 计算 manifest
            manifest: BackupManifest = compute_manifest(
                database_dump_path=database_path,
                objects_dir=objects_dir,
                application_version=self._config.application_version,
                migration_version=migration_version,
                backup_id=backup_id,
                encrypted=self._config.age_recipient is not None,
            )

            # C-04: 5. 写入 manifest（临时目录）
            save_manifest(manifest, temp_dir)
            logger.info("Backup %s: manifest written", backup_id)

            # C-04: 6. 打包 tar（临时目录）
            tar_path: Path = temp_dir / BACKUP_TAR_FILENAME
            self._create_tar(temp_dir, tar_path)

            # C-04: 7. 加密（临时目录）
            final_path: Path = tar_path
            if self._config.age_recipient is not None:
                encrypted_path: Path = temp_dir / BACKUP_TAR_AGE_FILENAME
                self._encrypt_tar(tar_path, encrypted_path, self._config.age_recipient)
                tar_path.unlink(missing_ok=True)
                final_path = encrypted_path
                logger.info("Backup %s: encrypted with age -> %s", backup_id, final_path)

            # C-04: 8. 原子移动唯一加密制品到子目录
            final_dest: Path = target_dir / final_path.name
            shutil.move(str(final_path), str(final_dest))
            logger.info("Backup %s: moved encrypted artifact to %s", backup_id, final_dest)

            # C-04: 9. 写入最小公开元数据到子目录
            save_manifest(manifest, target_dir)

            logger.info(
                "Backup %s: complete (db_sha256=%s..., objects=%d)",
                backup_id, manifest.database_sha256[:12], manifest.object_count,
            )
            return manifest

        finally:
            # C-04: 10. 成功和失败路径都可靠清理临时明文
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info("Backup %s: cleaned up temp dir %s", backup_id, temp_dir)

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
    db_url: str = os.getenv("IRIP_DATABASE_URL", "")
    if not db_url:
        raise RuntimeError("IRIP_DATABASE_URL environment variable is required")

    endpoint: str = os.getenv("IRIP_MINIO_ENDPOINT", "http://localhost:9000")
    if not endpoint.startswith("http"):
        endpoint = f"http://{endpoint}"

    if output_dir is None:
        output_dir_str: str = os.getenv("IRIP_BACKUP_OUTPUT_DIR", "")
        output_dir = Path(output_dir_str) if output_dir_str else Path(
            tempfile.gettempdir()
        ) / "irip-backup"

    age_recipient: str | None = os.getenv(AGE_RECIPIENT_ENV) or None

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


if __name__ == "__main__":
    main()
