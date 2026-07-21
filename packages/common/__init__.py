"""IRIP 通用内核包。

导出稳定共享原语：ID 生成、时钟、错误契约、哈希、分页游标、
数据库会话管理、自定义列类型。
所有上层模块仅依赖本包的公开接口，不反向依赖。
"""

from packages.common.clock import Clock, FixedClock, SystemClock
from packages.common.database import Base, build_session_factory, session_scope
from packages.common.db_types import GUID, UTCDateTime
from packages.common.errors import AppError
from packages.common.hashing import sha256_bytes
from packages.common.ids import new_id
from packages.common.pagination import PageCursor

__all__ = [
    "AppError",
    "Base",
    "Clock",
    "FixedClock",
    "GUID",
    "PageCursor",
    "SystemClock",
    "UTCDateTime",
    "build_session_factory",
    "new_id",
    "session_scope",
    "sha256_bytes",
]
