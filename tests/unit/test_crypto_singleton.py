"""H-06 EnvelopeCrypto 单例与 fail-closed 单元测试。

覆盖 ``packages/common/crypto.py`` 的安全增强：
- 非 test 环境缺 key 抛 RuntimeError（fail-closed）；
- test 环境用固定密钥（不随机生成，确保重启可解密）；
- from_env 返回同一实例（单例模式）；
- 解密失败抛异常不回退明文；
- 加解密往返；
- 旧版本 key 轮换解密。

本测试为纯单元测试，不依赖数据库或外部服务。
"""

import base64

import pytest

from packages.common.crypto import _TEST_MASTER_KEY_B64, EnvelopeCrypto


@pytest.fixture(autouse=True)
def _reset_crypto_singleton():
    """每个测试前后重置单例，避免相互污染。"""
    EnvelopeCrypto.reset_singleton()
    yield
    EnvelopeCrypto.reset_singleton()


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除所有 crypto 相关环境变量。"""
    monkeypatch.delenv("IRIP_MASTER_KEY", raising=False)
    monkeypatch.delenv("IRIP_ENV", raising=False)
    for v in range(1, 10):
        monkeypatch.delenv(f"IRIP_MASTER_KEY_OLD_v{v}", raising=False)


class TestFailClosed:
    """非 test 环境缺 key 拒绝启动。"""

    def test_non_test_env_missing_key_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非 test 环境缺少 IRIP_MASTER_KEY 抛 RuntimeError。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "production")
        with pytest.raises(RuntimeError, match="IRIP_MASTER_KEY is required"):
            EnvelopeCrypto.from_env()

    def test_non_test_env_no_irip_env_missing_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未设置 IRIP_ENV 且缺少 key 时也拒绝启动。"""
        _clear_env(monkeypatch)
        with pytest.raises(RuntimeError, match="IRIP_MASTER_KEY is required"):
            EnvelopeCrypto.from_env()

    def test_non_test_env_with_key_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 test 环境提供了合法 key 时正常初始化。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv("IRIP_MASTER_KEY", _TEST_MASTER_KEY_B64)
        crypto = EnvelopeCrypto.from_env()
        assert crypto is not None
        encrypted = crypto.encrypt("secret-data")
        assert crypto.decrypt(encrypted) == "secret-data"


class TestTestEnvFixedKey:
    """test 环境使用固定密钥。"""

    def test_test_env_missing_key_uses_fixed_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """test 环境缺 key 时使用固定测试密钥。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "test")
        crypto = EnvelopeCrypto.from_env()
        assert crypto is not None
        # 能正常加解密
        encrypted = crypto.encrypt("test-secret")
        assert crypto.decrypt(encrypted) == "test-secret"

    def test_test_env_fixed_key_deterministic_across_instances(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """test 环境固定密钥保证不同实例可互相解密。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "test")

        crypto1 = EnvelopeCrypto.from_env()
        encrypted = crypto1.encrypt("persisted-secret")

        # 重置单例后重建，仍能解密旧密文
        EnvelopeCrypto.reset_singleton()
        crypto2 = EnvelopeCrypto.from_env()
        assert crypto2.decrypt(encrypted) == "persisted-secret"


class TestSingleton:
    """单例模式。"""

    def test_from_env_returns_same_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """多次 from_env 返回同一实例。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "test")
        a = EnvelopeCrypto.from_env()
        b = EnvelopeCrypto.from_env()
        assert a is b

    def test_reset_singleton_allows_rebuild(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """reset_singleton 后可重新构建。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "test")
        a = EnvelopeCrypto.from_env()
        EnvelopeCrypto.reset_singleton()
        b = EnvelopeCrypto.from_env()
        assert a is not b


class TestEncryptDecrypt:
    """加解密往返。"""

    def test_encrypt_decrypt_roundtrip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """加密后解密还原明文。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "test")
        crypto = EnvelopeCrypto.from_env()
        for plaintext in ["hello", "api-key-12345", "a" * 256, "中文密钥测试"]:
            encrypted = crypto.encrypt(plaintext)
            assert crypto.decrypt(encrypted) == plaintext

    def test_encrypt_output_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """加密输出格式为 v{version}:{nonce}:{ciphertext}。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "test")
        crypto = EnvelopeCrypto.from_env()
        encrypted = crypto.encrypt("test")
        parts = encrypted.split(":", 2)
        assert len(parts) == 3
        assert parts[0] == "v0"

    def test_encrypt_empty_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """加密空字符串抛 ValueError。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "test")
        crypto = EnvelopeCrypto.from_env()
        with pytest.raises(ValueError, match="Cannot encrypt empty"):
            crypto.encrypt("")

    def test_encrypt_different_nonces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """同一明文两次加密产生不同密文（随机 nonce）。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "test")
        crypto = EnvelopeCrypto.from_env()
        e1 = crypto.encrypt("same-plaintext")
        e2 = crypto.encrypt("same-plaintext")
        assert e1 != e2
        # 但都能解密为同一明文
        assert crypto.decrypt(e1) == "same-plaintext"
        assert crypto.decrypt(e2) == "same-plaintext"


class TestDecryptFailureNoFallback:
    """解密失败抛异常不回退明文。"""

    def test_decrypt_tampered_ciphertext_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """篡改密文后解密抛 ValueError，不回退明文。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "test")
        crypto = EnvelopeCrypto.from_env()
        encrypted = crypto.encrypt("sensitive-data")
        # 篡改密文部分
        parts = encrypted.split(":", 2)
        tampered_ct = parts[2][:-4] + "AAAA"
        tampered = f"{parts[0]}:{parts[1]}:{tampered_ct}"
        with pytest.raises(ValueError, match="Decryption failed"):
            crypto.decrypt(tampered)

    def test_decrypt_invalid_format_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """格式无效的输入抛 ValueError。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "test")
        crypto = EnvelopeCrypto.from_env()
        with pytest.raises(ValueError, match="Invalid encrypted format"):
            crypto.decrypt("not-a-valid-format")

    def test_decrypt_wrong_version_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未知 key version 抛 ValueError。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "test")
        crypto = EnvelopeCrypto.from_env()
        # 构造 v99 版本的密文（不存在 v99 的 key）
        fake = "v99:AAAA:AAAA"
        with pytest.raises(ValueError, match="Unknown key version"):
            crypto.decrypt(fake)

    def test_decrypt_wrong_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """用不同 key 加密的密文无法用当前 key 解密。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "test")
        crypto = EnvelopeCrypto.from_env()
        # 用另一个 key 加密
        other_key = b"1" * 32
        other_crypto = EnvelopeCrypto(current_key=other_key)
        encrypted = other_crypto.encrypt("other-key-secret")
        # 当前 crypto 解密应失败
        with pytest.raises(ValueError, match="Decryption failed"):
            crypto.decrypt(encrypted)


class TestKeyRotation:
    """旧版本 key 轮换解密。"""

    def test_old_key_decrypt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """旧版本 key 加密的密文可用当前实例解密。"""
        _clear_env(monkeypatch)
        # 设置当前 key 和旧 key（旧 key 必须 32 字节）
        old_key_bytes = b"a" * 32
        old_key_b64 = base64.b64encode(old_key_bytes).decode("ascii")
        monkeypatch.setenv("IRIP_MASTER_KEY", _TEST_MASTER_KEY_B64)
        monkeypatch.setenv("IRIP_MASTER_KEY_OLD_v1", old_key_b64)

        crypto = EnvelopeCrypto.from_env()
        assert 1 in crypto._old_keys

        # 用旧 key 加密（模拟旧数据）
        old_crypto = EnvelopeCrypto(current_key=old_key_bytes, current_version=1)
        encrypted = old_crypto.encrypt("legacy-secret")

        # 当前实例用旧 key 解密
        assert crypto.decrypt(encrypted) == "legacy-secret"

    def test_current_key_encrypt_old_key_decrypt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """新 key 加密的密文无法用旧 key 解密。"""
        _clear_env(monkeypatch)
        old_key_bytes = b"a" * 32
        old_key_b64 = base64.b64encode(old_key_bytes).decode("ascii")
        monkeypatch.setenv("IRIP_MASTER_KEY", _TEST_MASTER_KEY_B64)
        monkeypatch.setenv("IRIP_MASTER_KEY_OLD_v1", old_key_b64)

        crypto = EnvelopeCrypto.from_env()
        encrypted = crypto.encrypt("new-key-secret")
        # 加密时用的是 v0（当前 key）

        # 用旧 key 实例解密应失败
        old_key = base64.b64decode(old_key_b64)
        old_crypto = EnvelopeCrypto(current_key=old_key, current_version=1)
        with pytest.raises(ValueError):
            old_crypto.decrypt(encrypted)


class TestKeyValidation:
    """密钥格式校验。"""

    def test_invalid_key_length_raises(self) -> None:
        """非 32 字节 key 抛 ValueError。"""
        with pytest.raises(ValueError, match="Master key must be 32 bytes"):
            EnvelopeCrypto(current_key=b"too-short")

    def test_invalid_base64_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无效 base64 编码的 key 抛异常。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv("IRIP_MASTER_KEY", "not-valid-base64!!!")
        with pytest.raises(ValueError):
            EnvelopeCrypto.from_env()

    def test_wrong_length_base64_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """base64 解码后长度不对抛 ValueError。"""
        _clear_env(monkeypatch)
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv(
            "IRIP_MASTER_KEY", base64.b64encode(b"only-16-bytes!!!!").decode("ascii")
        )
        with pytest.raises(ValueError, match="32 bytes"):
            EnvelopeCrypto.from_env()
