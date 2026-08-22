"""age 加密/解密 round-trip 自动化测试（阶段2 A3）。

将一次性手动验证固化为真实可重复的自动化测试，持续验证
``BackupService._encrypt_tar``（backup.py:860）与
``RestoreService._extract_archive``（restore.py:714）的 age 加解密逻辑正确：

1. **正向**：``age-keygen`` 生成 X25519 密钥对 → ``_encrypt_tar`` 用公钥加密
   tar → ``_extract_archive`` 用私钥解密并解压 → 断言内容 SHA-256 与原始一致，
   且密文与明文不同、头部含 ``age-encryption.org/v1`` 魔数。
2. **负向**：用另一把私钥（错误 identity）解密应失败，证明加密确实生效、非摆设。

测试走真实代码路径（构造最小 ``BackupService`` / ``RestoreService`` 实例调用真实
方法），而非复刻命令字符串。两个 service 的构造仅惰性创建 boto3 S3 客户端
（不发起网络请求），因此无需 MinIO / PostgreSQL / Docker 环境，只需 age 可执行文件。

前置条件：``age`` 与 ``age-keygen`` 在 PATH 中（本地 brew 已装、CI 的
test-recovery job 已装 v1.1.1）。工具缺失时 ``pytest.skip``。
"""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from deployments.compose.backup import (
    BACKUP_TAR_AGE_FILENAME,
    BACKUP_TAR_FILENAME,
    BackupConfig,
    BackupService,
)
from deployments.compose.backup_manifest import DATABASE_DUMP_FILENAME
from deployments.compose.restore import RestoreConfig, RestoreService

#: age 加密文件头部的标准魔数（ASCII 第一行）。
AGE_MAGIC: bytes = b"age-encryption.org/v1"

#: 私有方法为测试访问的真实方法，此处引用行号便于追溯（对应 backup.py:860 / restore.py:744）。
#:   - _encrypt_tar: age -r <recipient> -o <encrypted> <tar>      (backup.py:878-885)
#:   - _extract_archive: age -d [-i <identity>] -o <tar> <encrypted> (restore.py:744-747)

#: 最小的测试配置用数据库 URL（不实际连接，仅用于构造 config）。
_DB_URL: str = "postgresql+psycopg://irip:irip_dev_password@127.0.0.1:5432/irip"

_MINIO_ENDPOINT: str = "http://127.0.0.1:9000"


def _require_age() -> None:
    """检查 age 工具是否可用的前置条件，缺失时 skip（而非空壳测试）。

    本地已通过 brew 安装、CI 的 test-recovery job 已装 age v1.1.1，
    因此二者均会真实执行 round-trip，不会被隐式跳过。
    """
    missing: list[str] = [
        tool for tool in ("age", "age-keygen") if shutil.which(tool) is None
    ]
    if missing:
        pytest.skip(f"age tool(s) missing: {', '.join(missing)}")


def _generate_age_identity(tmp_path: Path, name: str) -> tuple[Path, str]:
    """使用 ``age-keygen`` 生成 X25519 身份（私钥）并推导 recipient（公钥）。

    Args:
        tmp_path: 临时目录（存放身份文件）。
        name: 身份文件名。

    Returns:
        tuple[Path, str]: (身份文件路径, 公钥 recipient)。
    """
    identity_path: Path = tmp_path / name
    keygen: subprocess.CompletedProcess[str] = subprocess.run(
        ["age-keygen", "-o", str(identity_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if keygen.returncode != 0:
        pytest.fail(f"age-keygen failed: {keygen.stderr.strip()}")

    # 从身份文件推导 recipient（公钥），避免解析私钥文件注释行。
    recipient_proc: subprocess.CompletedProcess[str] = subprocess.run(
        ["age-keygen", "-y", str(identity_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if recipient_proc.returncode != 0:
        pytest.fail(f"age-keygen -y failed: {recipient_proc.stderr.strip()}")

    recipient: str = recipient_proc.stdout.strip().splitlines()[0].strip()
    if not recipient.startswith("age1"):
        pytest.fail(f"unexpected recipient format: {recipient!r}")
    return identity_path, recipient


def _build_backup_config(output_dir: Path, age_recipient: str | None) -> BackupConfig:
    """构造最小 BackupConfig（仅构造 service，不实际连接 PG / MinIO）。"""
    return BackupConfig(
        db_url=_DB_URL,
        minio_endpoint=_MINIO_ENDPOINT,
        minio_access_key="test-access-key",
        minio_secret_key="test-secret-key",
        minio_bucket="irip-test",
        minio_region="us-east-1",
        application_version="0.0.0-test",
        output_dir=output_dir,
        age_recipient=age_recipient,
    )


def _build_restore_config(backup_dir: Path, age_identity: str | None) -> RestoreConfig:
    """构造最小 RestoreConfig（仅构造 service，不实际连接 PG / MinIO）。"""
    return RestoreConfig(
        backup_dir=backup_dir,
        db_url=_DB_URL,
        minio_endpoint=_MINIO_ENDPOINT,
        minio_access_key="test-access-key",
        minio_secret_key="test-secret-key",
        minio_bucket="irip-test",
        minio_region="us-east-1",
        age_identity=age_identity,
    )


def _prepare_plaintext_staging(tmp_path: Path) -> tuple[Path, bytes]:
    """创建含 database.dump 的明文 staging 目录，返回目录与明文内容。"""
    staging_dir: Path = tmp_path / "staging"
    staging_dir.mkdir()
    plaintext: bytes = (
        b"IRIP backup payload -- age round-trip verification -- " * 4096
    )
    (staging_dir / DATABASE_DUMP_FILENAME).write_bytes(plaintext)
    return staging_dir, plaintext


class TestAgeRoundTrip:
    """age 加密 → 解密 → 恢复的真实代码路径 round-trip 测试。"""

    def test_encrypt_decrypt_roundtrip(self, tmp_path: Path) -> None:
        """加密 tar 后解密解压，内容 SHA-256 与原始明文一致。"""
        _require_age()
        identity_path, recipient = _generate_age_identity(tmp_path, "age-key.txt")

        # ---- 明文 payload ----
        staging_dir, plaintext = _prepare_plaintext_staging(tmp_path)

        # ---- 加密：走真实 BackupService 方法 ----
        backup: BackupService = BackupService(_build_backup_config(tmp_path, recipient))
        tar_path: Path = tmp_path / BACKUP_TAR_FILENAME
        backup._create_tar(staging_dir, tar_path)
        encrypted_path: Path = tmp_path / BACKUP_TAR_AGE_FILENAME
        backup._encrypt_tar(tar_path, encrypted_path, recipient)

        plaintext_tar: bytes = tar_path.read_bytes()
        encrypted_bytes: bytes = encrypted_path.read_bytes()

        # 密文非空，且与明文 tar 不同（加密确实生效）
        assert encrypted_path.exists()
        assert len(encrypted_bytes) > 0
        assert encrypted_bytes != plaintext_tar
        # age v1 头部魔数（附加断言，证明产物确为 age 加密格式）
        assert encrypted_bytes.startswith(AGE_MAGIC)

        # ---- 解密 + 解压：走真实 RestoreService 方法 ----
        restore_dir: Path = tmp_path / "restore"
        restore_dir.mkdir()
        shutil.copy(encrypted_path, restore_dir / BACKUP_TAR_AGE_FILENAME)

        restore: RestoreService = RestoreService(
            _build_restore_config(restore_dir, str(identity_path))
        )
        restore._extract_archive(restore_dir)

        # 解密解压后的内容与原始明文一致
        restored_dump: Path = restore_dir / DATABASE_DUMP_FILENAME
        assert restored_dump.exists()
        assert hashlib.sha256(restored_dump.read_bytes()).digest() == hashlib.sha256(
            plaintext
        ).digest()

    def test_wrong_identity_decrypt_fails(self, tmp_path: Path) -> None:
        """用错误 identity（另一把私钥）解密应失败，证明加密非摆设。"""
        _require_age()
        _, recipient_a = _generate_age_identity(tmp_path, "recipient-a.txt")
        wrong_identity, _ = _generate_age_identity(tmp_path, "wrong-b.txt")

        staging_dir, _ = _prepare_plaintext_staging(tmp_path)

        backup: BackupService = BackupService(
            _build_backup_config(tmp_path, recipient_a)
        )
        tar_path: Path = tmp_path / BACKUP_TAR_FILENAME
        backup._create_tar(staging_dir, tar_path)
        encrypted_path: Path = tmp_path / BACKUP_TAR_AGE_FILENAME
        backup._encrypt_tar(tar_path, encrypted_path, recipient_a)

        restore_dir: Path = tmp_path / "restore"
        restore_dir.mkdir()
        shutil.copy(encrypted_path, restore_dir / BACKUP_TAR_AGE_FILENAME)

        restore: RestoreService = RestoreService(
            _build_restore_config(restore_dir, str(wrong_identity))
        )
        with pytest.raises(RuntimeError, match="age decryption failed"):
            restore._extract_archive(restore_dir)

        # 解密失败后不应产出明文 database.dump（fail-closed）
        assert not (restore_dir / DATABASE_DUMP_FILENAME).exists()
