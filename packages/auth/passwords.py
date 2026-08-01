"""Argon2id 密码哈希与验证。

使用 argon2-cffi 的 PasswordHasher（默认参数：
time_cost=2, memory_cost=19456(19MiB), parallelism=1, type=argon2id）。

安全约定：
- hash_password 返回的字符串包含盐值、参数与哈希，可直接存入 password_hash 列；
- verify_password 在哈希不匹配时返回 False（不抛异常），调用方据此判断认证结果。
"""

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """对明文密码进行 Argon2id 哈希。

    Args:
        plain: 用户输入的明文密码。

    Returns:
        str: Argon2id 哈希字符串（含盐值与参数，可直接入库）。
    """
    return _hasher.hash(plain)


def verify_password(hashed: str, plain: str) -> bool:
    """验证明文密码与 Argon2id 哈希是否匹配。

    Args:
        hashed: 数据库中存储的 Argon2id 哈希字符串。
        plain: 用户输入的明文密码。

    Returns:
        bool: 匹配返回 True，不匹配返回 False（不抛异常）。
    """
    try:
        _hasher.verify(hashed, plain)
        return True
    except (VerifyMismatchError, Argon2Error):
        # VerifyMismatchError: 密码不匹配
        # Argon2Error: 哈希格式无效（如 _DUMMY_HASH 解码失败）
        return False
