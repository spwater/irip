"""生产环境密钥安全校验。

启动时检查关键密钥是否使用了开发默认值，生产环境拒绝启动。
防止生产环境因配置遗漏而使用已知弱密钥。

检查项：
- IRIP_JWT_SECRET: 不能为空、不能为开发默认值、长度 >= 32 字节
- IRIP_MASTER_KEY: 不能为空、不能为开发默认值
- IRIP_BOOTSTRAP_ADMIN_PASSWORD: 不能为开发默认值
- IRIP_DATABASE_PASSWORD: 不能为开发默认值
- IRIP_REDIS_PASSWORD: 不能为空
- IRIP_MINIO_SECRET_KEY: 不能为开发默认值

用法::

    from packages.common.security_check import assert_production_keys

    # 在应用启动时调用
    assert_production_keys()  # 生产环境不达标时抛 RuntimeError
"""

import logging
import os

from packages.common.secret_files import read_secret

#: 开发环境已知弱密钥集合（这些值出现在 .env.example 和文档中，不可用于生产）。
WEAK_SECRETS: set[str] = {
    "dev_only_insecure_jwt_secret_change_me_0123456789abcdef",
    "agsdgfsdg21r34sf",
    "irip_dev_password",
    "test-secret",
    "ci-test-secret-2026",
    "irip-citation-dev-key",
    "Admin-IRIP-2026",
}

#: JWT secret 最小长度（字节）。
MIN_JWT_SECRET_LENGTH: int = 32


def assert_production_keys() -> None:
    """生产环境密钥安全校验。

    仅在 IRIP_ENV=production 时强制执行。非生产环境仅记录警告。

    检查项：
    1. IRIP_JWT_SECRET: 非空、非弱密钥、长度 >= 32
    2. IRIP_MASTER_KEY: 非空、非弱密钥
    3. IRIP_BOOTSTRAP_ADMIN_PASSWORD: 非弱密码
    4. IRIP_DATABASE_PASSWORD: 非弱密码
    5. IRIP_REDIS_PASSWORD: 非空
    6. IRIP_MINIO_SECRET_KEY: 非弱密钥
    7. IRIP_ALLOW_PRIVATE_NETWORK: 生产环境必须为 "0" 或未设置

    Raises:
        RuntimeError: 生产环境下密钥不达标时，阻止应用启动。
    """
    env: str = os.getenv("IRIP_ENV", "development")
    is_production: bool = env == "production"

    checks: list[tuple[str, str, bool, str]] = [
        (
            "IRIP_JWT_SECRET",
            read_secret("IRIP_JWT_SECRET", required=False) or "",
            True,
            "JWT 签名密钥不能为空",
        ),
        (
            "IRIP_MASTER_KEY",
            read_secret("IRIP_MASTER_KEY", required=False) or "",
            True,
            "信封加密主密钥不能为空",
        ),
        (
            "IRIP_REDIS_PASSWORD",
            read_secret("IRIP_REDIS_PASSWORD", required=False) or "",
            True,
            "Redis 密码不能为空",
        ),
    ]

    errors: list[str] = []

    for var_name, value, _required, empty_msg in checks:
        if not value:
            if is_production:
                errors.append(f"[{var_name}] {empty_msg}")
            else:
                _log_warning(f"[{var_name}] {empty_msg} (非生产环境，仅警告)")
            continue

        if value in WEAK_SECRETS:
            msg = f"[{var_name}] 使用了开发默认值，生产环境必须替换"
            if is_production:
                errors.append(msg)
            else:
                _log_warning(f"{msg} (非生产环境，仅警告)")

    # JWT secret 长度检查
    jwt_secret: str = read_secret("IRIP_JWT_SECRET", required=False) or ""
    if jwt_secret and len(jwt_secret) < MIN_JWT_SECRET_LENGTH:
        msg = (
            f"[IRIP_JWT_SECRET] 长度仅 {len(jwt_secret)} 字节，"
            f"生产环境要求 >= {MIN_JWT_SECRET_LENGTH} 字节"
        )
        if is_production:
            errors.append(msg)
        else:
            _log_warning(f"{msg} (非生产环境，仅警告)")

    # 开发默认密码检查（非必填但有值时检查）
    weak_password_vars = (
        "IRIP_BOOTSTRAP_ADMIN_PASSWORD",
        "IRIP_DATABASE_PASSWORD",
        "IRIP_MINIO_SECRET_KEY",
    )
    for var_name in weak_password_vars:
        pw_value = read_secret(var_name, required=False) or ""
        if pw_value and pw_value in WEAK_SECRETS:
            msg = f"[{var_name}] 使用了开发默认值，生产环境必须替换"
            if is_production:
                errors.append(msg)
            else:
                _log_warning(f"{msg} (非生产环境，仅警告)")

    # SSRF 防护检查
    allow_private: str = read_secret("IRIP_ALLOW_PRIVATE_NETWORK", required=False) or "0"
    if is_production and allow_private == "1":
        errors.append("[IRIP_ALLOW_PRIVATE_NETWORK] 生产环境不能设为 1（禁用 SSRF 防护）")

    if errors:
        msg = "生产环境密钥安全校验失败:\n" + "\n".join(f"  - {e}" for e in errors)
        msg += "\n请替换所有开发默认密钥后重试。"
        logging.getLogger("security_check").critical(msg)
        raise RuntimeError(msg)


def _log_warning(msg: str) -> None:
    """记录安全警告（非生产环境）。"""
    import logging

    logging.getLogger("security_check").warning(msg)
