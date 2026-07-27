"""IRIP 认证实体（ORM 模型）。

定义 app_user 与 refresh_session 两张表，字段严格遵循
docs/arch-v0.md §3.1（第 232-256 行）。

- app_user: 用户主表，CITEXT 邮箱（大小写不敏感）、Argon2id 密码哈希；
- refresh_session: 家族化刷新会话，仅存 SHA-256 摘要，支持旋转与重放检测。

索引（架构文档第 256 行）：
  ix_refresh_session_family_id          (family_id)
  ix_refresh_session_user_id_revoked_at  (user_id, revoked_at)
  ix_refresh_session_expires_at          (expires_at)
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.common.database import Base
from packages.common.db_types import GUID, UTCDateTime
from packages.common.ids import new_id


class AppUser(Base):
    """用户实体（对应 app_user 表）。

    Attributes:
        id: 用户唯一标识。
        organization_id: 所属组织 ID（单组织模型，V0 暂无 organization 表，允许 NULL）。
        email: 登录邮箱（CITEXT，大小写不敏感，UNIQUE）。
        display_name: 中文显示名。
        password_hash: Argon2id 密码哈希字符串。
        status: 账户状态（active / disabled）。
        created_at: 创建时间。
        updated_at: 更新时间。
        lock_version: 乐观锁版本号。
        roles: 用户角色代码列表（JSONB，如 ["platform_administrator"]）。
    """

    __tablename__ = "app_user"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    organization_id: Mapped[UUID | None] = mapped_column(GUID, nullable=True)
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'active'")
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    lock_version: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0"), nullable=False
    )
    roles: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
        default=list,
    )
    department_id: Mapped[UUID | None] = mapped_column(
        GUID, nullable=True, comment="所属实验室 ID（FK→department.id）"
    )

    refresh_sessions: Mapped[list["RefreshSession"]] = relationship(
        "RefreshSession",
        foreign_keys="RefreshSession.user_id",
        back_populates="user",
    )

    def __repr__(self) -> str:
        return f"AppUser(id={self.id!r}, email={self.email!r}, status={self.status!r})"


class RefreshSession(Base):
    """刷新会话实体（对应 refresh_session 表）—— 家族化单用途旋转。

    安全约束：
    - token_digest 仅存 refresh token 的 SHA-256 摘要，绝不存明文；
    - 旋转时旧行 replaced_by 指向新行，revoked_at 置为当前时刻；
    - 重放检测：若旧行已有 replaced_by，则整族撤销并返回 refresh_replayed。

    Attributes:
        id: 本次会话唯一标识。
        family_id: 家族 ID（同一次登录产生的所有旋转会话共享）。
        user_id: 所属用户 ID（FK→app_user.id）。
        token_digest: refresh token 的 SHA-256 十六进制摘要。
        issued_at: 签发时间。
        expires_at: 过期时间（7 天有效期）。
        revoked_at: 撤销时间（NULL 表示未撤销）。
        replaced_by: 旋转后的下一棒会话 ID（NULL 表示未旋转）。
        created_ip: 创建时客户端 IP（审计辅助）。
        user_agent: 创建时 User-Agent（审计辅助）。
    """

    __tablename__ = "refresh_session"

    id: Mapped[UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    family_id: Mapped[UUID] = mapped_column(GUID, nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        GUID, sa.ForeignKey("app_user.id"), nullable=False
    )
    token_digest: Mapped[str] = mapped_column(sa.Text, unique=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=sa.func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    replaced_by: Mapped[UUID | None] = mapped_column(
        GUID, sa.ForeignKey("refresh_session.id"), nullable=True
    )
    created_ip: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    user: Mapped[AppUser] = relationship(
        "AppUser",
        foreign_keys=[user_id],
        back_populates="refresh_sessions",
    )

    def __repr__(self) -> str:
        return (
            f"RefreshSession(id={self.id!r}, family_id={self.family_id!r}, "
            f"revoked={self.revoked_at is not None}, "
            f"replaced={self.replaced_by is not None})"
        )
