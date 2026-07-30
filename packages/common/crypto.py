"""Envelope Encryption：AES-GCM 信封加密 + master key 轮换。

提供 ``EnvelopeCrypto`` 类，使用 AES-256-GCM 对称加密保护敏感数据
（API key、连接器密钥等）。支持 master key 多版本轮换，解密时按
key_version 选择对应的 master key。

安全约定（H-06 增强）：
- Master key 从环境变量 ``IRIP_MASTER_KEY`` 读取（base64 编码的 32 字节密钥）；
- 非测试环境（IRIP_ENV != "test"）缺少 key 时拒绝启动（fail-closed）；
- 测试环境使用固定测试密钥（不随机生成，确保重启可解密）；
- 单例模式：``from_env()`` 返回同一实例，避免重复初始化；
- 解密失败直接 raise（不回退到明文）；
- 支持旧版本 key 轮换：``IRIP_MASTER_KEY_OLD_v1``、``IRIP_MASTER_KEY_OLD_v2`` 等；
- 加密输出包含 key_version、nonce、ciphertext、tag，序列化为 base64 字符串；
- 使用 ``cryptography`` 库的 AESGCM 实现（FIPS 兼容）。

用法::

    crypto = EnvelopeCrypto.from_env()
    encrypted = crypto.encrypt("my-secret-api-key")
    # encrypted = "v1:base64nonce:base64ciphertext:base64tag"
    plaintext = crypto.decrypt(encrypted)  # -> "my-secret-api-key"

密钥轮换::

    # 环境变量设置新 key，旧 key 保留用于解密
    IRIP_MASTER_KEY=<new-key>
    IRIP_MASTER_KEY_OLD_v1=<old-key>
    # 加密时使用新 key (version=1)，解密时按 version 选择 key
"""

from __future__ import annotations

import base64
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _generate_master_key() -> bytes:
    """生成随机 32 字节 master key。

    Returns:
        bytes: 32 字节随机密钥。
    """
    return secrets.token_bytes(32)


def _encode_key(key: bytes) -> str:
    """将 master key 编码为 base64 字符串。

    Args:
        key: 32 字节密钥。

    Returns:
        str: base64 编码的密钥。
    """
    return base64.b64encode(key).decode("ascii")


def _decode_key(encoded: str) -> bytes:
    """将 base64 编码的密钥解码为 bytes。

    Args:
        encoded: base64 编码的密钥。

    Returns:
        bytes: 解码后的密钥。

    Raises:
        ValueError: 当解码失败或密钥长度不是 32 字节时。
    """
    try:
        key = base64.b64decode(encoded)
    except Exception as exc:
        raise ValueError(f"Invalid master key encoding: {exc}") from exc
    if len(key) != 32:
        raise ValueError(f"Master key must be 32 bytes, got {len(key)} bytes")
    return key


#: 测试环境固定主密钥（H-06: 不随机生成，确保重启可解密）。
_TEST_MASTER_KEY: str = "test-master-key-do-not-use-in-production"

#: 测试环境固定主密钥的 base64 编码（32 字节）。
_TEST_MASTER_KEY_B64: str = base64.b64encode(b"0" * 32).decode("ascii")


class EnvelopeCrypto:
    """AES-GCM 信封加密，支持 master key 轮换。

    H-06 增强：单例模式 + fail-closed。

    Attributes:
        _current_key: 当前加密使用的 master key（version 0）。
        _current_version: 当前 key 版本号。
        _old_keys: 旧版本 key 字典 {version: key_bytes}，用于解密。
    """

    #: 单例实例（H-06: 单例模式）。
    _instance: "EnvelopeCrypto | None" = None

    def __init__(
        self,
        current_key: bytes,
        current_version: int = 0,
        old_keys: dict[int, bytes] | None = None,
    ) -> None:
        """初始化信封加密。

        Args:
            current_key: 当前 master key（32 字节）。
            current_version: 当前 key 版本号（默认 0）。
            old_keys: 旧版本 key 字典 {version: key_bytes}。

        Raises:
            ValueError: 当 master key 长度不是 32 字节时。
        """
        if len(current_key) != 32:
            raise ValueError(f"Master key must be 32 bytes, got {len(current_key)} bytes")
        self._current_key: bytes = current_key
        self._current_version: int = current_version
        self._old_keys: dict[int, bytes] = old_keys or {}

    @classmethod
    def from_env(cls) -> "EnvelopeCrypto":
        """从环境变量构建 EnvelopeCrypto（H-06: 单例 + fail-closed）。

        读取 ``IRIP_MASTER_KEY``（base64 编码的 32 字节密钥）。
        - 非测试环境（IRIP_ENV != "test"）缺少 key 时拒绝启动；
        - 测试环境使用固定测试密钥（不随机生成，确保重启可解密）；
        - 单例模式：多次调用返回同一实例。

        Returns:
            EnvelopeCrypto: 加密实例（单例）。

        Raises:
            RuntimeError: 非测试环境缺少 IRIP_MASTER_KEY 时。
            ValueError: 当 master key 格式无效时。
        """
        # H-06: 单例模式
        if cls._instance is not None:
            return cls._instance

        import logging

        logger = logging.getLogger(__name__)

        raw_key = os.getenv("IRIP_MASTER_KEY", "")
        is_test_env = os.getenv("IRIP_ENV") == "test"

        if not raw_key:
            if not is_test_env:
                # H-06: 非测试环境缺 key 拒绝启动（fail-closed）
                raise RuntimeError(
                    "IRIP_MASTER_KEY is required in non-test environment. "
                    "Set IRIP_ENV=test for test environments or provide IRIP_MASTER_KEY."
                )
            # H-06: 测试环境使用固定测试密钥（不随机生成）
            logger.warning(
                "IRIP_MASTER_KEY not set in test environment; using fixed test key."
            )
            current_key = _decode_key(_TEST_MASTER_KEY_B64)
            current_version = 0
        else:
            current_key = _decode_key(raw_key)
            current_version = 0

        # 加载旧版本 key（用于解密）
        old_keys: dict[int, bytes] = {}
        for version in range(1, 10):
            env_name = f"IRIP_MASTER_KEY_OLD_v{version}"
            old_raw = os.getenv(env_name, "")
            if not old_raw:
                break
            try:
                old_keys[version] = _decode_key(old_raw)
            except ValueError as exc:
                logger.warning("Failed to load %s: %s", env_name, exc)

        cls._instance = cls(
            current_key=current_key,
            current_version=current_version,
            old_keys=old_keys,
        )
        return cls._instance

    @classmethod
    def reset_singleton(cls) -> None:
        """重置单例实例（仅用于测试）。

        测试中修改环境变量后需要重新构建实例时调用。
        """
        cls._instance = None

    def encrypt(self, plaintext: str) -> str:
        """加密明文字符串。

        使用 AES-256-GCM 加密，输出格式：
        ``v{version}:{base64nonce}:{base64ciphertext_with_tag}``

        AESGCM 的 encrypt 方法返回 ciphertext + tag 拼接的 bytes，
        因此输出中不再单独分离 tag。

        Args:
            plaintext: 待加密的明文。

        Returns:
            str: 加密后的字符串，格式 ``v{version}:{nonce}:{ciphertext}``。

        Raises:
            ValueError: 当 plaintext 为空时。
        """
        if not plaintext:
            raise ValueError("Cannot encrypt empty plaintext")

        nonce = os.urandom(12)  # 96-bit nonce (AES-GCM 标准)
        aesgcm = AESGCM(self._current_key)
        # AESGCM.encrypt 返回 ciphertext || tag
        ct_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

        nonce_b64 = base64.b64encode(nonce).decode("ascii")
        ct_b64 = base64.b64encode(ct_with_tag).decode("ascii")

        return f"v{self._current_version}:{nonce_b64}:{ct_b64}"

    def decrypt(self, encrypted: str) -> str:
        """解密字符串。

        根据 key_version 选择对应的 master key 进行解密。
        支持当前 key 和旧版本 key。

        Args:
            encrypted: 加密字符串，格式 ``v{version}:{nonce}:{ciphertext}``。

        Returns:
            str: 解密后的明文。

        Raises:
            ValueError: 当格式无效、版本未知或解密失败时。
        """
        parts = encrypted.split(":", 2)
        if len(parts) != 3:
            raise ValueError("Invalid encrypted format: expected 'v{version}:{nonce}:{ciphertext}'")

        version_str, nonce_b64, ct_b64 = parts
        if not version_str.startswith("v"):
            raise ValueError(f"Invalid version prefix: {version_str}")

        try:
            version = int(version_str[1:])
        except ValueError as exc:
            raise ValueError(f"Invalid version number: {version_str}") from exc

        # 选择对应版本的 key
        if version == self._current_version:
            key = self._current_key
        elif version in self._old_keys:
            key = self._old_keys[version]
        else:
            raise ValueError(f"Unknown key version: v{version}")

        try:
            nonce = base64.b64decode(nonce_b64)
            ct_with_tag = base64.b64decode(ct_b64)
        except Exception as exc:
            raise ValueError(f"Failed to decode base64: {exc}") from exc

        aesgcm = AESGCM(key)
        try:
            plaintext_bytes = aesgcm.decrypt(nonce, ct_with_tag, None)
        except Exception as exc:
            raise ValueError(f"Decryption failed: {exc}") from exc

        return plaintext_bytes.decode("utf-8")


def generate_master_key() -> str:
    """生成并打印一个新的 base64 编码的 master key。

    用于初始化或密钥轮换时生成新密钥。

    Returns:
        str: base64 编码的 32 字节密钥。
    """
    return _encode_key(_generate_master_key())
