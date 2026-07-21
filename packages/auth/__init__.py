"""IRIP 认证与授权包。

Phase V0 T04: 认证与会话生命周期（Argon2id + JWT + 旋转刷新）。
提供密码哈希、JWT 签发/验证、刷新令牌旋转与重放检测、
AuthBackend 协议与本地实现、AuthService 业务编排。
"""

from packages.auth.backends import AuthBackend, AuthenticatedIdentity, LocalAuthBackend
from packages.auth.entities import AppUser, RefreshSession
from packages.auth.passwords import hash_password, verify_password
from packages.auth.repository import AuthRepository
from packages.auth.service import AuthService
from packages.auth.tokens import TokenPair

__all__ = [
    "AppUser",
    "AuthBackend",
    "AuthRepository",
    "AuthService",
    "AuthenticatedIdentity",
    "LocalAuthBackend",
    "RefreshSession",
    "TokenPair",
    "hash_password",
    "verify_password",
]
