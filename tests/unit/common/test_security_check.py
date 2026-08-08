"""单元测试：security_check 生产环境密钥安全校验。

覆盖：
- 非生产环境：空密钥仅警告 / 弱密钥仅警告 / 短 JWT secret 仅警告；
- 生产环境：空密钥报错 / 弱密钥报错 / 短 JWT secret 报错 / SSRF 关闭报错；
- 生产环境合法密钥通过；
- 弱密码变量（admin/db/minio）检查。
"""

import pytest

import packages.common.security_check as sc


class TestAssertProductionKeysDev:
    """非生产环境测试。"""

    def test_empty_keys_warns_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非生产环境空密钥仅警告不报错。"""
        monkeypatch.setenv("IRIP_ENV", "development")
        monkeypatch.setenv("IRIP_JWT_SECRET", "")
        monkeypatch.setenv("IRIP_MASTER_KEY", "")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "")
        sc.assert_production_keys()  # 不抛异常

    def test_weak_keys_warns_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非生产环境弱密钥仅警告。"""
        monkeypatch.setenv("IRIP_ENV", "development")
        monkeypatch.setenv(
            "IRIP_JWT_SECRET",
            "dev_only_insecure_jwt_secret_change_me_0123456789abcdef",
        )
        monkeypatch.setenv("IRIP_MASTER_KEY", "agsdgfsdg21r34sf")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "irip_dev_password")
        sc.assert_production_keys()

    def test_short_jwt_secret_warns_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非生产环境短 JWT secret 仅警告。"""
        monkeypatch.setenv("IRIP_ENV", "development")
        monkeypatch.setenv("IRIP_JWT_SECRET", "short")
        monkeypatch.setenv("IRIP_MASTER_KEY", "valid_master_key_0123456789abcdef")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "valid_redis_password")
        sc.assert_production_keys()


class TestAssertProductionKeysProd:
    """生产环境测试。"""

    def test_empty_jwt_secret_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """生产环境空 JWT secret 报错。"""
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv("IRIP_JWT_SECRET", "")
        monkeypatch.setenv("IRIP_MASTER_KEY", "valid_master_key_0123456789")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "valid_redis_pw")
        with pytest.raises(RuntimeError, match="JWT"):
            sc.assert_production_keys()

    def test_empty_master_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """生产环境空 master key 报错。"""
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv("IRIP_JWT_SECRET", "x" * 32)
        monkeypatch.setenv("IRIP_MASTER_KEY", "")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "valid_redis_pw")
        with pytest.raises(RuntimeError, match="信封加密"):
            sc.assert_production_keys()

    def test_empty_redis_password_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """生产环境空 Redis 密码报错。"""
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv("IRIP_JWT_SECRET", "x" * 32)
        monkeypatch.setenv("IRIP_MASTER_KEY", "valid_master_key_0123456789")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "")
        with pytest.raises(RuntimeError, match="Redis"):
            sc.assert_production_keys()

    def test_weak_jwt_secret_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """生产环境弱 JWT secret 报错。"""
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv(
            "IRIP_JWT_SECRET",
            "dev_only_insecure_jwt_secret_change_me_0123456789abcdef",
        )
        monkeypatch.setenv("IRIP_MASTER_KEY", "valid_master_key_0123456789")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "valid_redis_pw")
        with pytest.raises(RuntimeError, match="开发默认值"):
            sc.assert_production_keys()

    def test_short_jwt_secret_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """生产环境短 JWT secret 报错。"""
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv("IRIP_JWT_SECRET", "short_but_not_weak_0123")  # < 32 chars
        monkeypatch.setenv("IRIP_MASTER_KEY", "valid_master_key_0123456789")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "valid_redis_pw")
        with pytest.raises(RuntimeError, match="长度"):
            sc.assert_production_keys()

    def test_ssrf_disabled_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """生产环境 SSRF 防护关闭报错。"""
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv("IRIP_JWT_SECRET", "x" * 32)
        monkeypatch.setenv("IRIP_MASTER_KEY", "valid_master_key_0123456789")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "valid_redis_pw")
        monkeypatch.setenv("IRIP_ALLOW_PRIVATE_NETWORK", "1")
        with pytest.raises(RuntimeError, match="SSRF"):
            sc.assert_production_keys()

    def test_valid_keys_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """生产环境合法密钥通过。"""
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv("IRIP_JWT_SECRET", "a_very_secure_jwt_secret_key_0123456789")
        monkeypatch.setenv("IRIP_MASTER_KEY", "a_very_secure_master_key_0123456789")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "a_very_secure_redis_pw_0123456789")
        monkeypatch.setenv("IRIP_ALLOW_PRIVATE_NETWORK", "0")
        # 清除可选弱密码变量，避免 CI 注入的默认值干扰
        monkeypatch.delenv("IRIP_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
        monkeypatch.delenv("IRIP_DATABASE_PASSWORD", raising=False)
        monkeypatch.delenv("IRIP_MINIO_SECRET_KEY", raising=False)
        sc.assert_production_keys()  # 不抛异常

    def test_weak_admin_password_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """生产环境弱管理员密码报错。"""
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv("IRIP_JWT_SECRET", "x" * 32)
        monkeypatch.setenv("IRIP_MASTER_KEY", "valid_master_key_0123456789")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "valid_redis_pw")
        monkeypatch.setenv("IRIP_BOOTSTRAP_ADMIN_PASSWORD", "Admin-IRIP-2026")
        with pytest.raises(RuntimeError, match="开发默认值"):
            sc.assert_production_keys()

    def test_weak_db_password_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """生产环境弱数据库密码报错。"""
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv("IRIP_JWT_SECRET", "x" * 32)
        monkeypatch.setenv("IRIP_MASTER_KEY", "valid_master_key_0123456789")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "valid_redis_pw")
        monkeypatch.setenv("IRIP_DATABASE_PASSWORD", "irip_dev_password")
        with pytest.raises(RuntimeError, match="开发默认值"):
            sc.assert_production_keys()

    def test_weak_minio_secret_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """生产环境弱 MinIO 密钥报错。"""
        monkeypatch.setenv("IRIP_ENV", "production")
        monkeypatch.setenv("IRIP_JWT_SECRET", "x" * 32)
        monkeypatch.setenv("IRIP_MASTER_KEY", "valid_master_key_0123456789")
        monkeypatch.setenv("IRIP_REDIS_PASSWORD", "valid_redis_pw")
        monkeypatch.setenv("IRIP_MINIO_SECRET_KEY", "irip_dev_password")
        with pytest.raises(RuntimeError, match="开发默认值"):
            sc.assert_production_keys()


class TestWeakSecretsSet:
    """WEAK_SECRETS 集合测试。"""

    def test_known_weak_secrets_present(self) -> None:
        """已知弱密钥在集合中。"""
        assert "dev_only_insecure_jwt_secret_change_me_0123456789abcdef" in sc.WEAK_SECRETS
        assert "irip_dev_password" in sc.WEAK_SECRETS
        assert "test-secret" in sc.WEAK_SECRETS

    def test_min_jwt_length(self) -> None:
        """JWT secret 最小长度为 32。"""
        assert sc.MIN_JWT_SECRET_LENGTH == 32
