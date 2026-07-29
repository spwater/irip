"""IRIP 自定义数据库列类型。

提供两个 TypeDecorator，确保平台级不变量：
- UTCDateTime: ``TIMESTAMP(timezone=True)`` 强制 UTC。
  写入时拒绝 naive datetime（抛 ValueError），读取时确保返回 UTC aware。
- GUID: UUID 存为 PostgreSQL 原生 ``uuid`` 类型。
  Python 层统一使用 ``uuid.UUID`` 对象，避免字符串往返转换。

所有时间戳必须 timezone-aware，符合架构文档 §7.3 约定：
"所有持久化时间戳为 timestamptz，应用层只允许 datetime.now(UTC) 或 Clock.now()"。
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import TIMESTAMP, TypeDecorator
from sqlalchemy import UUID as SA_UUID


class UTCDateTime(TypeDecorator[datetime]):
    """``TIMESTAMP(timezone=True)`` 强制 UTC。

    - 写入：拒绝 naive datetime（抛 ``ValueError``），确保落库值带时区；
    - 读取：若数据库返回 naive（不应发生但防御性处理），补充 UTC 时区。

    此类型用于所有 ``created_at`` / ``updated_at`` / ``occurred_at`` 等时间列。
    """

    impl = TIMESTAMP(timezone=True)
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Any,
    ) -> datetime | None:
        """写入前校验：拒绝 naive datetime。"""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed; pass a timezone-aware datetime")
        return value

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Any,
    ) -> datetime | None:
        """读取后归一化：确保返回 UTC aware datetime。"""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class GUID(TypeDecorator[UUID]):
    """UUID 存为 PostgreSQL 原生 ``uuid`` 类型。

    - 写入：接受 ``UUID`` 对象或合法 UUID 字符串；
    - 读取：统一返回 ``UUID`` 对象（psycopg 驱动已原生返回 UUID）。

    PostgreSQL 原生 ``uuid`` 类型比 ``TEXT`` 存储更紧凑、索引更高效。
    """

    impl = SA_UUID
    cache_ok = True

    def process_bind_param(
        self,
        value: UUID | str | None,
        dialect: Any,
    ) -> UUID | str | None:
        """写入前归一化：字符串转 UUID 对象。"""
        if value is None:
            return None
        if isinstance(value, str):
            return UUID(value)
        return value

    def process_result_value(
        self,
        value: UUID | str | None,
        dialect: Any,
    ) -> UUID | None:
        """读取后归一化：确保返回 UUID 对象。"""
        if value is None:
            return None
        if isinstance(value, str):
            return UUID(value)
        return value
