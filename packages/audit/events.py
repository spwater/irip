"""审计事件定义：AuditEventData dataclass + AuditEvent ORM 模型。

对应 audit_event 表（docs/arch-v0.md §3.1 第 280-290 行）。
T03（迁移 0001）已创建根表骨架（含全部字段），本模块提供 ORM 映射
与数据载体，供 AuditRecorder 使用。

安全约束：应用角色 irip_app 对 audit_event 仅 INSERT + SELECT。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


@dataclass(frozen=True)
class AuditEventData:
    """审计事件数据载体（传递给 AuditRecorder.record）。

    frozen dataclass，确保事件创建后不可变。

    Attributes:
        organization_id: 组织 ID（NOT NULL）。
        action: 动作字符串（如 ``"auth.login"``、``"artifact.upload"``）。
        actor_user_id: 操作者用户 ID（系统事件可为 None）。
        resource_type: 资源类型（如 ``"fact"``、``"artifact"``）。
        resource_id: 资源 ID。
        payload: 事件载荷（**已脱敏**）。
        ip: 客户端 IP。
        user_agent: User-Agent。
    """

    organization_id: UUID
    action: str
    actor_user_id: UUID | None = None
    resource_type: str | None = None
    resource_id: UUID | None = None
    payload: dict[str, Any] | None = None
    ip: str | None = None
    user_agent: str | None = None


class AuditEvent(Base):
    """审计事件 ORM 模型（对应 audit_event 表）。

    仅追加表：应用角色 irip_app 无 UPDATE/DELETE 权限。
    所有时间戳为 UTC timestamptz。

    Attributes:
        id: 事件 UUID（PK）。
        occurred_at: 发生时间（UTC，默认 now()）。
        actor_user_id: 操作者用户 ID（系统事件可为 NULL）。
        organization_id: 组织 ID。
        action: 动作字符串。
        resource_type: 资源类型。
        resource_id: 资源 ID。
        payload: 事件载荷 JSONB（**已脱敏**）。
        ip: 客户端 IP。
        user_agent: User-Agent。
    """

    __tablename__ = "audit_event"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    organization_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    action: Mapped[str] = mapped_column(sa.Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    resource_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"AuditEvent(id={self.id!r}, action={self.action!r}, occurred_at={self.occurred_at!r})"
        )
